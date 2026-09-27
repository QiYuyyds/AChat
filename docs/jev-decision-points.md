# Jev 判定点清单（AChat × System One Model 接入分析）

> **这份文档是什么**：AChat Agent Loop 及外围链路中所有「隐性决策点」的盘点清单——即代码里那些由 LLM 自评、硬编码阈值或根本没人管的小决定。每条标注当前决策方式、现状问题，以及若交给 Jev（只输出「是/否+概率、多选一、打分」三种类型化决策的快速模型，~200ms，输入 $0.042/MTok、输出免费）对应的自然问法。
>
> **背景**：mu 项目（进化酒馆黑客松冠军）的核心洞察——每轮对话里藏着几百个小决定，交给大模型太贵太慢，交给死规则错误率太高。本文档是把它套在 AChat 上的系统性盘点，作为后续 OpenSpec proposal 的输入。
>
> **方法**：全文精读 `agent_runner.py` / `agent_loop.py` / `react_loop_termination.py` / `compact_pipeline.py` / `prompt_assembler.py` / `dag_executor.py` / `task_scheduler.py` / `worktree_service.py` / `ask_peer.py` / `report_result.py` / `reranker.py` / `eval_judge.py` / `bash_command_approval.py`，行号以 2026-09-26 的 `feat-add-eval-harness` 分支为准。行号会漂移，判断逻辑为准。
>
> **状态**：分析文档，无实现。验收标准：每个 P0 判定点能回答「现在谁在决策、错了会怎样、Jev 接管后怎么兜底」三问。

---

## 0. 结论速览

- **共盘点 50+ 个决策点**，其中约 **18 个适合交给 Jev**（P0/P1），约 10 个收益有限（P2），其余应保持硬规则或属于应修的代码缺陷。
- **最佳试点（零安全争议）**：评测 Judge（9 维一打）与 RAG rerank——两者都是现成 LLM 打分点的直接替换，有天然 A/B 对比口径。
- **价值最高（需过安全约定关）**：审批分流与「假完成」拦截——直击平台两大痛点：人审疲劳、run 不崩就算完成。
- **全线接入铁律**：feature flag 开关、失败时回退到现状行为（fail-open 到旧路径）、`asyncio.Event` 可取消、判定结果进审计日志。类型安全 ≠ 事实正确，任何自动动作都要有置信度阈值和人工兜底。

### Jev 三种原语 ↔ AChat 问法的对应

| 原语 | 返回 | AChat 里的典型问题 |
|---|---|---|
| **Noul** | 是/否 + 校准概率 | 「这条旧工具结果还需要保留吗？」「任务真的完成了吗？」 |
| **Choice** | 预定义选项多选一 + 分布 | 「上游失败后下游节点怎么办：执行/降级/跳过？」「这次失败根因是哪类？」 |
| **Score** | 按标准打分 + 置信度 | 「这条 chunk 与 query 的相关性 0-10？」「这个合并结果质量打分？」 |

---

## 1. 循环内每轮决策（agent_runner + react_loop_termination）

这是 mu 文章「每轮几百个小决定」的主战场，也是决策最密集区。

| # | 判定点 | 位置 | 当前决策方式 | 现状问题 | Jev 接法 | 优先级 |
|---|---|---|---|---|---|---|
| 1.1 | **「什么算完成」**：模型停止调工具即 complete | `agent_runner.py:1223-1280`；终端工具 `TERMINAL_TOOLS` `:79` | **无人决策**（完全委托主模型行为信号） | 提前停 = 假完成；DAG 下游放行、任务自动完成全锚在这个信号上，无人复核 | Noul：「模型给出最终答复但未调工具——该答复是否实际完成任务目标？」低置信 → 注入一次追问而非直接终止 | **P0** |
| 1.2 | 每轮 pre-model 动作：压缩/软收尾/强制收尾/硬停 | `agent_runner.py:1011-1024`；阈值 `react_loop_termination.py:33-38`（0.75/0.88/0.85/0.93/0.95） | 硬规则（token 比例 + 调用计数断路器） | 误判会截断仍在有效工作的 run，或放任无效循环烧 token | Choice：「模型最后一轮在自然收尾、打转，还是仍有效工作？」替代/校准 SOFT/HARD 阈值触发 | P1 |
| 1.3 | 工具重复/错误断路器 | `agent_runner.py:1363` + `react_loop_termination.py`（指纹 + 计数） | 硬规则（相同指纹、连续错误计数） | 相似但非同一根因的调用会误触发；真死循环计数未到前持续烧钱 | Noul：「最近 N 次高度相似且均失败——是同一根因的死循环吗？」 | P1 |
| 1.4 | 工具异常就地吞并 | `agent_runner.py:1334-1350` | 异常分支（一律合成 error 结果继续） | 是否重试完全靠主模型自觉；参数错误与环境错误不区分 | Choice：「工具失败属于参数错误/环境瞬时故障/权限问题？」→ 决定立即重试还是上报 | P1 |
| 1.5 | 计划提醒注入（该不该建计划） | `agent_runner.py:555-556, 1449-1470`（≥2 轮且探索调用 ≥3 次） | 硬规则（用调用计数近似「任务复杂度」） | 漏报 → agent 散漫探索；误报 → 污染上下文。纯语义问题用计数近似 | Noul：「agent 已探索 3 次仍未建计划——这是值得建执行计划的复杂任务吗？」 | P2 |
| 1.6 | run 结束时计划步骤状态回填 | `agent_runner.py:2747-2753`（run complete → 所有 in_progress 步骤一律 done） | 硬规则（run 状态一票决定全部步骤） | 只做了 2/5 步的 run 也会把计划全标 done——用户看到的进度是假的 | Choice：逐步骤「本次运行中真正完成了吗：done/failed/skipped」 | **P0** |
| 1.7 | 项目产物自动生成 | `agent_runner.py:2385-2460`（有 fs_write 证据即打包） | 硬规则 | 写过文件 ≠ 可交付产物，噪音无判断 | Noul：「本次文件改动构成可交付的项目产物吗？」 | P2 |
| 1.8 | 遗留 mid-run 压缩裁剪 | `agent_runner.py:823-833`（recent_keep=6、>2000 token 才裁） | 硬规则（纯位置/数量） | 可能裁掉仍被引用的工具结果 | 并入 2.x 的逐块掩码判定 | P2 |
| 1.9 | Provider 错误分类 | `agent_runner.py:1195-1197`（`classify_provider_error` + 字符串匹配） | 异常类型/字符串匹配 | 不区分可重试/不可重试，上游拿到的失败信号同质化 | Choice：「瞬时网络错误可重试 / 配置鉴权错误 / 上下文超限需压缩后重试」 | P1 |
| 1.10 | 后置记忆钩子（值得记吗） | `agent_runner.py:183-250`（只做非空过滤） | **无人决策**（语义判断下沉给下游 LLM） | 临时性对话细节全量进入记忆管线，质量靠下游 | Noul：「这段输出包含值得长期保留的事实/偏好吗？」先门控再触发 LLM 提取（gate-before-generate，省 API 费） | P1 |
| 1.11 | 群聊语境注入 | `agent_runner.py:3628-3630`（agent 数 >1 即注入） | 硬规则（数量阈值） | 单 agent 但历史有 mention 的场景漏注入 | Noul：「历史中是否存在其他 agent/用户发言需要群聊语境说明？」 | P2 |

## 2. 上下文裁剪与组装（compact_pipeline + prompt_assembler）

mu 设计点②「上下文只放这一步需要的」的直接对应。**现状全部按比例/位置裁剪，不看内容相关性**——这是清单里「信息损失最确定」的一块。

> 📌 本节的设计探索结论已沉淀至 [jev-context-trimming-design.md](jev-context-trimming-design.md)（2026-09-26）：全 Jev 判决、run state card 参考物、「摘要是否足以替代原文」判定反转、veto-only 单调性红线、ratio 地板兜底等 8 项决策及落地路线，以该文档为对齐基准。

| # | 判定点 | 位置 | 当前决策方式 | 现状问题 | Jev 接法 | 优先级 |
|---|---|---|---|---|---|---|
| 2.1 | **掩码哪些旧工具结果** | `compact_pipeline.py:1130-1164`（白名单按工具名 `_is_whitelisted`；`:1163` 只有掩码更短才替换） | 硬规则（白名单一刀切 + 纯长度比较） | 探索完的 `code_explore` 永占 token；短而无关的不掩、长而关键的照掩——**信息价值完全不在考虑内** | Noul（逐条）：「这条旧工具结果对当前任务仍有引用价值吗？」概率高 → 保留原文，否则掩码。替代白名单 + 长度规则 | **P0** |
| 2.2 | 掩码/折叠触发时机 | `compact_pipeline.py:32-33`（0.75/0.88）；`react_loop_termination.py:502-515`、`dag_executor.py:581` | 硬阈值（ratio） | ratio 本身是合理的安全网，**不建议替换**；但档位选择（见 2.4）可语义化 | —（保留硬阈值作安全网） | — |
| 2.3 | 保留窗口「最近 3 轮」 | `compact_pipeline.py:36`（KEEP_RECENT_TURNS=3） | 硬编码 | 第 4 轮起无论内容一律可掩；活跃引用的第 4 轮照掩不误 | Noul（逐轮）：「这一轮工具结果对当前任务还活跃相关吗？」替代固定窗口 | P1 |
| 2.4 | 重试时的压缩档位 | `dag_executor.py:573-586`（ratio ≥0.75 压缩，≥0.88 stage 3） | 硬阈值（且 `:581` 字面量 `0.88` 未引用常量，存在漂移） | 失败关键细节可能被 stage 3 折叠掉，重试重蹈覆辙 | Choice：「这次重试需要完整历史/轻压缩/重折叠？」 | P1 |
| 2.5 | bash 输出摘要只留末 20 行 | `compact_pipeline.py:443-472`（`:455`） | 硬编码行数 | 编译错误/断言失败在中部时**必然丢失**，stage 2/3 进一步恶化到 5 行/1 行 | Noul：「这段输出包含错误或关键结果吗？」有 → 保留相关片段 | P1 |
| 2.6 | fs_grep 只留前 10 条匹配 | `compact_pipeline.py:475-500`（`:486`） | 硬编码条数（按返回顺序） | 第 11 条之后的真正目标文件无声丢弃 | Noul/Choice（逐条）：「这条匹配与当前任务相关吗？」 | P2 |
| 2.7 | web_search 只留前 5 条 | `compact_pipeline.py:710-741`（`:721`） | 硬编码条数（按**搜索引擎排名**） | 排名 ≠ 任务相关性；第 6 位的精确答案被丢 | Score（逐条）：「与当前任务的相关性 0-10」——与 6.1 rerank 同构 | P2 |
| 2.8 | ask_user 问答截断（不可恢复） | `compact_pipeline.py:926-960`（问题 50 字/回答 200 字符） | 硬编码字符数 | `:935` 自己承认「ask_user 不可恢复」——用户决策性内容被截即永久丢失 | Noul：「这段用户回答包含影响后续执行的决定吗？」有 → 完整保留 | **P0** |
| 2.9 | 折叠标记只保 first_user[:80] + last_reply[:80] | `compact_pipeline.py:1252-1267, 1478-1497` | 硬规则 | 中间的用户追问、错误、发现全部永久丢失（轮次定义使 user 消息必然落入被折叠段，`:262-289`） | Noul（逐条 user 消息）：「这条消息包含后续仍需要的约束/需求吗？」top-k 保留 | P1 |
| 2.10 | code_explore 无条件全文保留 | `compact_pipeline.py:503-507`（注释：已完整保留、不可恢复） | **无决策** | 多次 explore 累积可占上下文大头，任何阶段都不碰 | Noul：「这份 explore 摘要当前还需要吗？」 | P2 |
| 2.11 | 未知工具输出保前 1000 字符 | `compact_pipeline.py:972-983` | 硬编码（纯前缀） | MCP 自定义工具 JSON 中部的 `status: error` 字段被切掉 | Noul：「前 1000 字符已覆盖关键内容吗？」 | P2 |
| 2.12 | PromptAssembler 预算裁剪无视 score | `prompt_assembler.py:674-691`（`_trim_by_budget` 按输入顺序保前缀） | 硬规则 | `ContextItem.score` 字段存在但只有 ProfileSource 填——**结构上已为打分模型留好位置**，裁剪却只看顺序 | Score（逐条）：「与当前任务的相关性」→ 按分裁剪。改动面最小的接入点之一 | **P0** |
| 2.13 | 任务记忆只留最近 top_k 条 | `prompt_assembler.py:420-442`（`:433` `[-top_k:]`，纯 LIFO） | 硬规则 | 第 3 步的关键失败教训被第 15 步的成功输出挤掉；`success` 字段只用于拼文案不参与去留 | Noul（逐条）：「这条历史步骤观察对下一步还有参考价值吗？」 | P1 |
| 2.14 | 超预算时牺牲哪个槽位 | `prompt_assembler.py:141-146, 658-671`（recall 最先清空、constraints 永不动） | 硬编码优先级 | 预算紧张时**相关回忆整体先丢**，与任务具体需求无关 | Choice：「应该牺牲哪个槽位？」或逐槽打分 | P2 |
| 2.15 | Profile 偏好打分与 query 无关 | `prompt_assembler.py:318-337`（类别固定分数表） | 硬编码（identity 0.9 / policy 0.8 / …） | 「用户住在重庆」在任何任务里都排最前 | Score：「这条偏好与当前任务相关吗（0-1）」 | P2 |
| 2.16 | 死配置：max_age_hours / min_score | `prompt_assembler.py:45,47,113`（全仓零消费） | **无决策** | 24h 前的过期观察照样注入；召回分数无门槛过滤 | Noul：「这条步骤观察过期/仍适用吗？」（或先修死配置） | P1 |

## 3. 子任务派发与 DAG 编排（dag_executor + agent_loop + task_dispatch）

mu 设计点④「蜂群通信闸门」的直接对应。

| # | 判定点 | 位置 | 当前决策方式 | 现状问题 | Jev 接法 | 优先级 |
|---|---|---|---|---|---|---|
| 3.1 | **DAG 失败传播：上游失败 → 下游一律 skip** | `dag_executor.py:261-271, 315-334, 345-346` | 硬规则（结构传播，用 `depends_on` 而非 `task.inputs` 判断） | 下游声明 inputs 只取自另一个成功上游时仍被连带跳过——**过度跳过，整条下游作废**；也没有「降级继续」路径 | Choice：「上游 X 失败后，任务 Y：执行 / 降级执行 / 跳过？」——判定对象（任务描述+失败摘要）天然是文本 | **P0** |
| 3.2 | 重试无失败分析 | `dag_executor.py:489-492`（所有失败共用一句固定指令） | **无决策** | 重试 agent 拿不到「上次错在哪」，重蹈覆辙概率高 | Choice：「上次失败根因：工具环境 / 计划有误 / 上游输入错误 / 输出质量？」→ 注入针对性重试提示。近零成本提高重试成功率 | **P0** |
| 3.3 | 重试会话查找「最新即正确」 | `dag_executor.py:523-548` | 硬规则 | 可能绑到同会话内与本次 DAG 无关的最近 run，污染重试上下文 | Noul：「这条历史 run 确实是本次失败任务的上一次尝试吗？」 | P2 |
| 3.4 | 上游输出注入：预算均分截断 | `dag_executor.py:399-465`（总 2000 token、等分、按字符截断） | 硬规则 | 关键上游和次要上游同权重；截断点不看内容 | Score：「这几个上游输出的重要性排序」→ 按分配额 | P1 |
| 3.5 | 上游结果只透传 complete 的 | `dag_executor.py:434-440`（失败上游静默 continue） | 硬规则（静默丢弃） | 失败任务的部分成果（有价值的中间产物）对下游不可见 | Noul：「失败任务 X 的部分产出对任务 Y 有复用价值吗？」 | P2 |
| 3.6 | 降级波次写冲突仅 advisory | `dag_executor.py:366-396` + `dispatch_file_writes.py:66-96`（hash 不同才算冲突） | 硬规则（哈希不等式），结论不阻断 | 写**语义互斥**的不同文件（如一边删了另一边引用的接口）完全检测不到 | Score：「这批降级共享工作区的任务组合冲突风险 0-1」→ 高分改串行 | P1 |
| 3.7 | **report_result 无质量门** | `report_result.py:60-62`（空 summary 只 warning 照常 reported） | **无决策** | 空报告、一句「完成了」都照单全收，父 Agent 直接消费 | Noul×2：「这份汇报自包含且与实际改动相符吗？」「filesChanged 与 workspace 证据一致吗？」低置信 → 拒收并让子 Agent 重报（不终止 run） | **P0** |
| 3.8 | 协调者「结果不满意就重派」 | `agent_loop.py:114`（prompt 引导，LLM 自评） | LLM 自评 | 审查质量取决于主模型自觉；「不满意」无一致标准 | Noul：「子 Agent 的结果可信且覆盖任务要求吗？」低置信 → 提示协调者重派（辅助信号，不替代 LLM 判断） | P1 |
| 3.9 | ask_peer 硬限次（per-peer 3 次） | `ask_peer.py:31, 130-144` | 硬规则（计数） | 到限一律拒绝，不区分「值得的第三次」与「垃圾提问」；且每次 mini-run 重建**全量历史**（`:191-196`）成本高 | Noul（前置闸门）：「这个问题值得跑一次 mini-run 吗？对方历史里已有答案吗？」——省下的 mini-run 就是省下的真金白银 | P1 |
| 3.10 | mailbox 留言无过滤 | `ask_peer.py:74-99`（全部投递，DAG 结束后给主 Agent） | 无决策 | 垃圾留言照样污染主 Agent 复盘 | Noul：「这条留言值得主 Agent 看吗？」 | P2 |
| 3.11 | DAG 结构校验只查结构不查语义 | `dag_executor.py:145-211` | 硬规则（图算法） | 依赖边是否**语义必要**无人审（A 真的需要 B 的输出吗） | Noul（逐边）：「这个依赖必要吗，还是可解耦并行？」→ 建议性输出 | P2 |
| 3.12 | 会话注册表状态二值化 | `dag_executor.py:813-817`（skipped 记为 completed） | 硬规则 | 跳过节点显示「完成」，误导监控与续跑 | **应修代码**（映射补 skipped 枚举），非 Jev | 缺陷 |

## 4. 任务看板（task_scheduler）

| # | 判定点 | 位置 | 当前决策方式 | 现状问题 | Jev 接法 | 优先级 |
|---|---|---|---|---|---|---|
| 4.1 | **自动完成：run 正常结束即推 in_review** | `task_scheduler.py:398-421`（agent 忘调 task_complete 时强推，summary 是假占位文案） | **无决策**（run 不崩 = 做完） | 全链路最典型的「没人管」：半成品流入用户评审队列 | Noul：「对照任务描述，这次运行真的完成了吗？」低置信 → 回 todo 而非 in_review | **P0** |
| 4.2 | 失败 → 回滚 → 下轮再派（无限循环） | `task_scheduler.py:390-397`（无失败计数、无退避、无上限） | 硬规则（一律回滚重派） | 配置错误类永久失败以 300s 为周期永远失败-回滚-再派，烧钱无告警 | Choice（结合失败次数）：「重试 / 回滚待人工 / 标记 blocked」 | **P0** |
| 4.3 | 审批模式二值开关 | `task_scheduler.py:293`（`auto if not bound_path else review`） | 硬规则（workspace_mode 二分） | 沙箱任务**所有**写免审；本地任务**所有**写都人审——量大时是瓶颈 | Score：「这批写操作风险如何？」沙箱高风险仍拦截、本地低风险可放行（与 7.1 审批分流共用基建） | P1 |
| 4.4 | 用户评论过滤 | `task_scheduler.py:271-276`（只取 author_type=="user"） | 硬规则 | agent 补充说明一律丢弃；用户评论里的转述照收 | Noul：「这条评论对下一轮是可操作的修改指令吗？」 | P2 |
| 4.5 | 重派复用会话 vs 新建 | `task_scheduler.py:281-302`（有 conv_id 就复用） | 硬规则 | 失败多轮后历史里全是失败痕迹，无「干净重试」选项 | Choice：「沿用原会话 / 开新会话从干净状态重试？」 | P2 |
| 4.6 | 指派缺失静默跳过不回滚 | `task_scheduler.py:240-256`（对比 `:204-213` 异常路径有 rollback） | **无决策**（早退） | 任务可能永久卡 in_progress，只有一条日志 | **应修代码**（早退路径补 rollback），非 Jev | 缺陷 |
| 4.7 | 工作流骨架固定 | `task_scheduler.py:34-78`（二选一模板） | 硬编码模板 | 小任务也被要求完整计划-执行-汇报流程 | Choice：「完整流程 / 轻量直接执行？」 | P2 |

## 5. Worktree 与合并（worktree_service）

「Auto → LLM → Human」三层递进（`worktree-conflict-resolution` spec）的分岔点在这里——Jev 是天然的分层路由器。

| # | 判定点 | 位置 | 当前决策方式 | 现状问题 | Jev 接法 | 优先级 |
|---|---|---|---|---|---|---|
| 5.1 | **三层递进入口只有 git 退出码** | `worktree_service.py:672-692` | 返回码分支（rc≠0 → 直接 LLM 解） | 无「冲突虽存在但可自动取一边」的中间判断；琐碎冲突也烧 120s LLM 合并 | Choice：「这次冲突：自动策略可解 / 适合 LLM / 必须人工？」→ 高置信琐碎冲突跳过 LLM 层 | **P0** |
| 5.2 | LLM 合并质量闸门只查「语法不炸」 | `worktree_service.py:259-283`（py compile / json loads / ts 括号配对 / **md 及其余一律放行**） | 硬规则（按扩展名） | 语义合并错误（丢了某边的逻辑）100% 放行 | Score：「合并后文件与两侧原意兼容吗（0-1）」低分 → 转人工 | **P0** |
| 5.3 | 冲突文件列表解析失败 → 空表 → 误报成功 | `worktree_service.py:158-172`（异常返回 `[]`）+ `:316-317`（空表 = (True, []) 直接 commit） | 静默吞异常 | **冲突未解被记为 LLM 解决成功**并提交——先修 fail-closed，再谈 Jev | **应修代码**（空表/异常应中止而非放行），非 Jev | 缺陷 |
| 5.4 | 多文件冲突全有或全无 | `worktree_service.py:307-356`（任一文件失败 → 整体转人工） | 硬规则 | 9 个简单冲突陪 1 个疑难冲突一起进人工队列 | Noul（逐文件）：「这个文件可自动解决吗？」→ 部分自动、其余转人工 | P1 |
| 5.5 | cleanup 无条件 `branch -D` | `worktree_service.py:704-721`（`finally` 里 merge 失败仍 cleanup 且丢弃 MergeResult `:779-784`） | 硬规则 | timeout/abandon 场景下未冲突部分的成功改动随分支删除；DAG 节点结果看不到合并失败仍报 complete | Noul：「worktree 中未合并的改动值得保留为补丁/产物吗？」→ 保留再删 | **P0** |
| 5.6 | 降级共享工作区仅 advisory | `worktree_service.py:597-653, 726-784` + `dag_executor.py:841` | 异常分支（任何创建失败一律降级共享） | 降级节点直接写用户真实主工作区，无强化审查 | Choice：「共享继续 / 限写 / 排队重试隔离？」 | P1 |
| 5.7 | 非 git 目录合并 = 整目录覆盖 | `worktree_service.py:694-698`（后写者赢） | **无决策** | 并行任务互相覆盖且无提示 | Noul：「这两个任务的改动会互相覆盖吗？」（低成本 advisory） | P2 |
| 5.8 | 人工层超时前预筛 | `worktree_service.py:426-513`（超时 1800s 永不合并回） | 人审 + 硬规则超时 | 全部冲突等人工，哪怕 90% 明显可取 theirs | Noul（预筛建议）：「这些冲突大概率可以全部取 theirs 吗？」——给人工面板推荐默认动作 | P2 |

## 6. 评测与检索（eval_judge + reranker + Aeval graders）——零风险试点区

| # | 判定点 | 位置 | 当前决策方式 | 现状问题 | Jev 接法 | 优先级 |
|---|---|---|---|---|---|---|
| 6.1 | **RAG rerank（listwise LLM 打分）** | `rag/reranker.py:23-58`（一次 LLM 调用 + JSON 解析 + 失败回退 RRF 序） | LLM 调用 | 每次检索一次 LLM 调用；且 `preview_len=200`（`:19-21`）——只看 200 字符预览打分 | Score（逐条）：「该 chunk 与 query 的相关性 0-10」。**接口形状不变**（generate_fn 换成 Jev 批量打分），保留 RRF 回退。延迟秒级 → ~200ms | **P0 试点** |
| 6.2 | **离线 9 维 LLM-as-Judge** | `observability/eval_judge.py:22-47, 127-172`（单次 LLM 调用出 9 分 + explanation） | LLM 调用 | 每个 trace 一次完整 LLM 生成；`temperature=0.1` 仍可能漂 | 一次 Jev 调用并行问 9 个 Score（Jev 原语天然并行）。注意 Jev 出不了 explanation 文本：低置信维度 fallback 到 LLM 补理由（置信度路由），或接受无 rationale | **P0 试点** |
| 6.3 | RAG 评测 LLM-as-Judge | `rag/eval/`（独立 eval LLM `EVAL_LLM_*`） | LLM 调用 | 同 6.2，按 dataset item 数量线性放大 | 同 6.2 | P1 |
| 6.4 | Aeval Artifact/Dispatch graders | `eval_integration/graders/` | LLM/规则 grader | 100 trial × N 维度 = 数百次 judge 调用 | Jev 作为一个新 grader backend（框架 grader 接口已抽象） | P1 |

## 7. 审批链路（bash / fs_write / MCP / dispatch / merge）——需过安全约定关

> ⚠️ CLAUDE.md §3.7 明文「审批超时**永不自动批准**」、§6.2 修改安全约束必须停下来问人、spec 11 黑名单是契约。以下全部设想为**用户显式 opt-in** 的增量概率层，黑名单与「永不自动批准」语义不动摇。

| # | 判定点 | 位置 | 当前决策方式 | 现状问题 | Jev 接法 | 优先级 |
|---|---|---|---|---|---|---|
| 7.1 | **bash 审批触发：正则黑名单二值** | `bash_command_approval.py:25-88`（命中 → 人工，未命中 → 直接放行） | 硬规则（正则） | 中间有一大坨「正则够不着但可能危险」的灰色命令：未命中即裸奔 | Score（风险 0-100）/ Noul：「可能破坏 workspace 或越界吗？」——置信度路由：高置信安全放行 / 高置信危险拦截 / 中间进人工队列。黑名单层保持不动，Jev 只处理灰带 | **P0（opt-in）** |
| 7.2 | fs_write / fs_edit 审批 | `pending_writes.py` + `fs_write.py` / `fs_edit.py` | 人审（diff 面板） | 每次写都弹审批，疲劳后机械点通过——比不审更危险 | Score：「这个 diff 的风险」（大范围删除/敏感路径/构建产物）→ 低风险静默、高风险加显警示 | P1（opt-in） |
| 7.3 | MCP 调用审批 | `pending_mcp_calls.py` | 人审 | 外部 MCP 工具面大，逐个审批不现实 | Noul：「这次调用的参数在声明的安全范围内吗？」 | P1（opt-in） |
| 7.4 | dispatch_plan 计划审批 | `pending_dispatch_plans.py` | 人审（可选） | 计划合理性全靠人读 | Noul/Score：「这个 DAG 计划有明显结构/依赖问题吗？」→ 审批面板显示风险预筛 | P2 |

## 8. 明确不建议交给 Jev 的

| 类别 | 例子 | 理由 |
|---|---|---|
| 容量/时序参数 | 子代理并发 4（`agent_runner.py:1502`）、审批超时、扫描周期 300s、IP 探测 TTL 600s | 指标驱动或运维配置，语义模型无增益 |
| 并发正确性守卫 | abort 幂等（`agent_runner.py:1811-1825`）、cancel_event 归并 | 概率判断引入竞态，必须确定性 |
| 安全黑名单/护栏 | bash 双平台黑名单（spec 11 契约）、guide/task 工具过滤（`agent_runner.py:2215-2243`） | 安全边界必须确定性；Jev 只能在黑名单**之外**加概率层 |
| 纯测量口径 | 4 字符/token 估算、3600「token」实为字符（`prompt_assembler.py:78,654`） | 这是校准问题不是判断问题；顺带暴露的口径 bug 应修 |
| 应直接修的缺陷 | 见 §9 | 概率层不该用来盖 bug |

## 9. 盘点中顺带暴露的代码缺陷（与 Jev 无关，建议单独修）

> **状态更新（2026-09-26）**：本节 10 项已全部修复（分支 `feat-add-eval-harness`，改动文件：`dag_executor.py` / `worktree_service.py` / `prompt_assembler.py` / `task_scheduler.py` / `report_result.py` + 5 个测试文件）。核实阶段修正了两处探索报告的结论偏差，见 #5 / #8 的「核实修正」注。

1. ~~`dag_executor.py:581` — stage 3 阈值写字面量 `0.88`~~ **已修复**：改为引用 `COMPACT_FOLD_RATIO`
2. ~~`prompt_assembler.py:639` — `_fill_slot` 中 source 异常 `break`~~ **已修复**：改为 `continue`，单 source 故障不再阻断同槽后续 source（附测试）
3. ~~`prompt_assembler.py` — `required=True` 无强制力~~ **已修复（可见性方案）**：required 槽空缺时（无 source / source 空 / 预算裁空 / 全局预算清空）统一打 warning 日志。**不改硬失败语义**——agent_runner 对 assemble 异常的既有降级路径是「无富化继续跑」，硬失败会让单个槽缺失放大为全部富化丢失。核实发现 live 路径目前只请求 `mode="chat"`（`conversation_context.py:404`），带 required 的 REACT/TOOL schema 属休眠配置，告警不会刷屏
4. ~~`prompt_assembler.py` — `max_age_hours` / `min_score` 死配置~~ **已修复（接线而非删除）**：`StepObservation.created_at` 时间戳字段本就存在，`TaskMemSource` 按 `max_age_hours` 过滤过期观察、`RecallSource` 按 `min_score` 过滤低分召回（默认值 0 行为不变，附测试）
5. ~~`task_scheduler.py:240-256` — 指派缺失早退不回滚~~ **已修复（修法经核实修正）**：`get_dispatchable_tasks` 只查询不 claim，任务在 `bind_conversation` 才置 in_progress，两个早退点在其**之前**——真实后果不是「永久卡 in_progress」而是「永远留在 todo、每 300s 重复告警、用户无感知」。修复为早退时经 `task_service.move_task` 转 `blocked`（看板可见 + 停止扫描循环重试，附测试）
6. ~~`worktree_service.py` — 冲突文件解析失败返回空表 → 误报 LLM 合并成功~~ **已修复（fail-closed）**：`_parse_conflict_files` 失败改返回 `None`（区分「无冲突」与「枚举失败」）；merge 失败且冲突列表为 `None`/`[]` 时一律返回 `success=False, strategy="failed"`，不再走 LLM/提交路径。顺带修复同类问题：LLM 解决后与人工解决后的 `commit --no-edit` 原先**不检查返回码**，提交失败也报成功，现均改为失败即 `success=False`（附 4 个测试）
7. ~~`worktree_service.py` — merge 失败仍 cleanup 且丢弃 MergeResult~~ **已修复（两层）**：(a) `WorktreeRef` 新增 `last_merge_result` 字段，`isolated_workspace` 在 finally 中回填（含 merge 抛异常的合成失败结果）；`dag_executor._execute_node` 读取后把节点状态改判 `failed` 并写入 `error_detail` / `DispatchEndEvent.error` / plan step 状态——DAG 节点不再对合并失败报 complete（附测试）；(b) cleanup 删除分支前，若 merge 失败则 best-effort 保存 `git diff HEAD...<branch>` 为 diff 类型 Artifact（「未合并改动备份」），未合并工作不再随 `branch -D` 静默丢失（附测试）
8. ~~`dag_executor.py:813-817` — 会话注册表状态二值化~~ **已修复（修法经核实修正）**：skipped 节点**根本不注册会话**（无 child run），`mark_dag_completed` 也只翻 running→completed，原报告描述的「skipped 记为 completed」影响路径不存在。实际修复为防御性显式映射：`"completed" if node_status == "complete" else "failed"`（fail-closed，非 complete 一律记 failed）
9. ~~`worktree_service.py` — 未知 merge strategy 默认映射 success~~ **已修复（类型层收紧）**：`MergeResult.resolution_strategy` 改为 `Literal["auto","llm","manual","abandoned","timeout","failed"]`，新增 `"failed"` 值；事件映射的未知分支加 warning 日志
10. ~~`report_result.py:60-62` — 空 summary 只 warning 照常收货~~ **已修复**：空 summary 返回 error 结果且**不缓存 payload**，调用方（spawn_subagent_loop / ask_peer）回退到提取 run 的实际输出文本——严格优于原先的空汇报（附测试）

## 10. mu 四设计点 → AChat 落点对照

| mu 的设计 | AChat 对应机制 | 落点 |
|---|---|---|
| ① 35 个判定点交给 Jev | `agent_runner` 循环 + 终止逻辑 | §1 全表；建议先做「判定点 → Jev 问题」映射的 PoC（1.1/1.6 起） |
| ② 上下文只放需要的 | `compact_pipeline`（比例裁剪）+ `prompt_assembler`（顺序裁剪） | §2 全表；2.1 + 2.12 改动面最小 |
| ③ 人话看板（干活/汇报双模型） | AChat 本身是 IM：消息流 + SSE 事件 | Jev 筛「真正的新消息」再交叙事模型；汇报文字不回灌干活上下文。AChat 的 IM 形态比 CLI harness 更适合做（未列入上表，属新功能设计） |
| ④ 蜂群通信闸门 | `ask_peer` / `report_result` / DAG | 3.7 / 3.9 / 3.10 |

## 11. 推进路线建议

1. **Phase 0 — 判定基线（无代码）**：选 3.7 / 4.1 / 1.1 三个「假完成」场景 + 6.1/6.2 两个打分场景，标注 50-100 条人工判定作为金标集（eval 场景天然有数据）。
2. **Phase 1 — 零风险试点**：`decision_service.py` 薄封装（async client + `asyncio.Event` 取消 + feature flag + fail-open 回退）→ 接 6.1 reranker 与 6.2 eval judge，跑金标集 A/B 对比一致率。
3. **Phase 2 — 编排决策**：3.1 DAG 失败传播、3.2 重试根因、4.1/4.2 任务看板（都是 Choice 型，错误代价有界，且都有回滚路径兜底）。
4. **Phase 3 — 审批分流（opt-in）**：7.1 bash 灰带评分起步；单独立 OpenSpec change，明确「永不自动批准」语义保持、黑名单不动、审计接入。
5. **每阶段通用前置**：Jev API key 走三层 key 体系（新增 `JEV_API_KEY`，改 `.env.example` 需通知协作方）；引入新外部依赖按 CLAUDE.md §6.2/§6.3 走流程。

> 参考来源：mu（进化酒馆黑客松冠军，深度融合 Jev 的 harness）四设计点；TypeSafe AI 官方文档（Primitives / Patterns / Confidence-gated routing）；HN 发布讨论（1978 分）中对「类型安全 ≠ 事实正确」的集中批评。
