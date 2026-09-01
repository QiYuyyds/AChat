# Eval-Harness（Aeval）面试学习文档

> 对应简历条目：**Eval-Harness**：为支撑 Agent 行为可量化、可迭代，独立设计并开源 Agent 评测框架（PyPI: aeval-framework）……
>
> 用法：先通读建立体系 → 背第 13 节问答说辞 → 用第 12 节的实战案例讲故事 → 第 0 节的三段陈述按面试时间选背。
> ⚠️ 本文档是个人面试准备材料，**不要提交或推送**。

---

## 0. 三段电梯陈述（按面试时间选背）

**30 秒版**：
我做了一个开源的 Agent 评测框架 Aeval。核心思想是：任何 Agent 系统只要实现一个 `run(task)` 接口、返回 trace_id、transcript 和 outcome 三样东西，就能获得完整的评测能力——多 trial 编排、9 种可组合评分器、pass@k/pass^k 统计、LLM 质量指标、数据集闭环、REST API 和 Dashboard。它已经发到 PyPI，在我们自己的多 Agent 项目 AChat 里作为回归防线真实落地。

**1 分钟版**（在 30 秒版基础上加）：
动机是 LLM 应用的经典痛点：改一个 prompt、换一个模型，agent 行为悄悄变差但没人发现——因为 LLM 有非确定性，单次运行不能说明问题，手工回归又测不过来。所以这个框架的核心设计都是围绕"对抗非确定性"和"自动化回归"：每个任务跑 N 次 trial，用 pass@k（能力：至少成功一次）和 pass^k（稳定性：每次都成）两个维度统计；评分器分层，确定性判分器做门禁、LLM-as-Judge 做主观质量分并报告置信度；失败案例自动提取成回归用例，形成质量闭环。

**3 分钟版**（再加）：
工程上几个值得一提的点：一是编排层的可靠性设计——每 trial 拍环境快照、结束后泄漏检测并自动恢复，瞬态错误才指数退避重试、评分失败不重试；二是评分流水线支持声明式依赖和拓扑排序，比如"忠实度评分依赖检索步骤先通过"；三是统计上 k 超出实际 trial 数时按二项分布外推；四是它本身作为一个开源工程来打磨——349 个测试、核心覆盖率 95%、AST 检查固化零反向依赖、CI 跑 3.11/3.12 双版本，fresh init 发布到 GitHub 和 PyPI。落地过程中还踩了几个有意思的坑，比如评测会话里 fs_write 因为等待人工审批把整个 run 挂死，我用进程内探针复现后定位到无人值守环境的审批死锁问题。

---

## 1. 背景与动机

**要解决的问题（三个）：**
1. **LLM 非确定性**：同一 prompt 两次运行结果可能不同 → 单次通过/失败没有统计意义
2. **行为回归不可见**：改 prompt / 换模型 / 动工具实现，agent 行为可能悄悄退化，手工回归测不过来
3. **评测资产不可沉淀**：失败案例、边界场景散落在聊天记录里，没有变成可重放的资产

**与传统单元测试的区别（高频面试题）：**
| | 单元测试 | Agent 评测 |
|---|---|---|
| 断言对象 | 函数输出（确定性） | 行为过程 + 最终结果（概率性） |
| 通过标准 | 精确相等 | 阈值 + 多次统计 |
| 失败含义 | 代码 bug | prompt/模型/工具的退化 |
| 环境依赖 | mock 掉 | 真实环境（工作区/工具/检索），需要隔离与清理 |

**业界定位（一句话说清差异化）**：
DeepEval 评的是 **LLM 输出质量**（输入→输出→指标），Aeval 评的是 **Agent 行为质量**（指令→多步执行→trace→多维度评分）——覆盖工具调用、状态变更、多 Agent 协作这些 DeepEval 不管的东西；两者互补，Aeval 通过 `metric` grader 把输出质量指标融入行为评分流水线。参照物：Anthropic 的 evals 方法论、SWE-bench、Terminal-Bench。

---

## 2. 架构总览（画图题，要能白板画）

```
Suite YAML ──▶ EvalRunner 编排 ──▶ 被评 Agent（实现 AgentRunner 契约）
                 │                       │
                 │                  trace_id (Phoenix / 任意 OTel 后端)
                 ▼
          Grader 流水线（9 种评分器，依赖拓扑排序）
                 ▼
     pass@k / pass^k 统计 ──▶ Storage(SQLite/Memory) ──▶ REST API / CLI / Dashboard
                 ▲
        Dataset（数据集闭环：5 类数据源 → 质量检查 → to-suite → 回归提取）
```

**五条设计原则**（每条都能展开 30 秒）：
1. **单接口契约**：被评系统只实现 `AgentRunner.run(task)`，其余全有默认实现 → 接入成本最低
2. **关注点分离**：被评系统 ≠ 评测框架，依赖方向单向（AChat → agent_eval，AST 测试固化）
3. **评分器可组合**：单任务评分 = 多 grader 组合，required 门禁 + 加权
4. **统计可靠**：多 trial + 两个统计维度，对抗非确定性
5. **渐进式采用**：写 YAML 就能用 → 自定义 grader → CI → 贡献框架

---

## 2A. 对象模型与真实数据样例（先把名词落地）

### 2A.1 对象层级：Suite / Task / Trial / Run

用"考试"类比，每个对象都真实存在：

```
Suite（考卷）         = 一个 YAML 文件，如 backend/eval_suites/t1-core.yaml
 └─ Task（一道题）    = YAML 里的一个条目：prompt（题目）+ env（草稿纸初始状态）+ graders（判分标准）
     └─ Trial（一次作答）= 这道题被执行了一次。默认每道题作答 3 次（对抗 LLM 随机性）
         └─ GraderResult（每个判分器对这一次作答给的分）

Run（一场考试）       = 对一份 Suite 触发一次执行的整体过程（后台运行，可实时观察）
 └─ RunSummary（成绩单）= 全部任务统计完后的汇总：pass@k、退化清单、饱和度
```

对应到实际操作：你在 Dashboard 点"运行" → 产生一个 **Run** → 框架遍历 Suite 里的每个 **Task** → 每个 Task 执行 N 个 **Trial** → 每个 Trial 过一遍 **Grader** 流水线 → 全部完成生成 **RunSummary** 落库。

Task 的完整字段（YAML 里能配什么）：

```yaml
- id: file-bugfix              # 唯一标识
  description: 修复种子代码中的除零缺陷
  prompt: ...                  # 给 agent 的指令（题目本身）
  max_trials: 2                # 这道题作答几次（默认 3）
  env:                         # 环境配置：trial 开始前写入 workspace 的种子文件、
    files:                     # task 级 agent 切换（env.agent_id）、
      src/calculator.py: |     # 会话形态（env.conversation）等
        def divide(a, b): return a / b
  graders:                     # 判分标准列表（可多个，见 §5A）
    - type: state
      name: state_check
      required: true           # required = 门禁：这个不过，整个 task 直接失败
      config: {...}            # 该评分器的具体判分参数
  score_strategy: hybrid       # all_pass / weighted / hybrid（默认）
  score_threshold: 0.7         # 通过阈值
  metadata: {difficulty: medium, capabilities: {...}}   # 供覆盖度分析
```

### 2A.2 transcript 是什么——"agent 说了什么"

**定义**：一次 trial 中 agent 与外界的**完整对话记录**，按时间顺序排列的消息列表。

**来源**：`AgentRunner.run()` 的第二个返回值。在 AChat 里，就是把这次评测会话的所有消息规范化成统一格式。

**真实形状**：

```json
[
  {"role": "user",      "content": "workspace 中 src/calculator.py 的 divide 函数缺少除零保护，请修复"},
  {"role": "assistant", "content": "我先读取文件看看现状。"},
  {"role": "tool",      "content": "def add(a, b):\n    return a + b\n\ndef divide(a, b):\n    return a / b"},
  {"role": "assistant", "content": "已修复：当 b 为 0 时返回 None。修改如下……"}
]
```

**谁消费它、怎么用**：
- `code_based`：把整个 transcript 序列化成文本，在里面找关键子串/正则（"回答里必须包含 9753"）
- `model_based` / `metric`：取第一条当"输入"、最后一条当"输出"，喂给 judge LLM 打分
- 判分的核心素材——**agent 说了什么**

### 2A.3 outcome 是什么——"agent 做成了什么"

**定义**：trial 结束时**外部环境的最终状态**。agent 光说不行，environment 里实际发生了什么变化。

**来源**：`AgentRunner.run()` 的第三个返回值。在 AChat 里 = 评测会话的 workspace 文件清单（含内容）+ 产物列表。

**真实形状**：

```json
{
  "conversation_id": "conv_abc123",
  "files": {
    "src/calculator.py": "def add(a, b):\n    return a + b\n\ndef divide(a, b):\n    if b == 0:\n        return None\n    return a / b"
  },
  "artifacts": [{"id": "art_x1", "name": "deploy-notes", "type": "document"}],
  "seed_files": ["src/calculator.py"]
}
```

**谁消费它**：`state_check`（文件存在/内容断言）、`artifact_check`（产物断言）、`code_based` 的 `target: "outcome"`。

**transcript 与 outcome 的关系（一句话说透）**：
transcript 是"它**说了**什么"，outcome 是"它**做成了**什么"——两者可以矛盾（agent 声称创建了文件但 workspace 里没有）。评测两个都看，这正是 Agent 评测区别于"LLM 输出评测"的地方。

### 2A.4 trace_id 与 spans 是什么——"agent 的执行过程录像"

**定义**：trace_id 是一次执行的 OpenTelemetry 追踪 ID。agent 执行过程中每一步（组装提示、调用工具、调 LLM）都产生一个 **span**（带名称、属性、起止时间的区间记录，有父子关系），全部挂在同一个 trace_id 下，导出到 Phoenix（trace 可视化后端）。

**spans 从 Phoenix 拉回来的归一化形状**：

```json
[
  {"name": "agent.run",  "attributes": {"run_id": "run_1", "agent_id": "ag_x"}},
  {"name": "tool.call",  "attributes": {"agenthub.tool_name": "fs_read", "agenthub.success": true}},
  {"name": "tool.call",  "attributes": {"agenthub.tool_name": "fs_edit", "agenthub.success": true}},
  {"name": "llm.call",   "attributes": {"agenthub.total_tokens": 1520}}
]
```

**谁消费它**：`tool_calls`（实际调了哪些工具）、`step_level`（步骤顺序对不对）、`transcript`（轮次/token 统计）、Trace Mining（从历史 trace 挖用例）。

**关键依赖提醒**：spans 需要 agent 侧有 OTel tracing 且接了 Phoenix。没有 trace_id 时 span 类评分器退化，transcript/outcome 类照常工作——这就是为什么确定性断言尽量建立在 transcript/outcome 上。

### 2A.5 一次完整执行的数据流（把所有对象串起来）

以 T1 的 `file-bugfix` 任务为例，从触发到落库：

```
1. Dashboard 点运行 → Suite(t1-core.yaml) 从存储加载（此前经 YAML 校验）
2. Run 创建，status=running，后台 asyncio task 持有执行
3. Task[file-bugfix] 开始 → Trial 0：
   a. 环境快照（workspace 文件清单 = 空）
   b. 建 sandbox 会话（fs_write 审批切 auto）→ 种子文件写入 src/calculator.py
   c. 发 prompt → agent 执行（fs_read 读文件 → fs_edit 改代码 → 文字汇报）
   d. 收集三个返回值：trace_id / transcript（4 条消息）/ outcome（修改后的 calculator.py）
4. 用 trace_id 拉 spans → 提取过程指标（n_turns / n_toolcalls / n_total_tokens）
5. Grader 流水线（按声明顺序）：
   state_check: outcome.files["src/calculator.py"] 含 "if b == 0" ✓ 含 "return None" ✓
                → 2/2 → score=1.0（required，门禁）
   code_based:  transcript 序列化文本 not_contains "ZeroDivisionError" ✓ → score=1.0
6. hybrid 评分：required 全过 AND 加权分 1.0 ≥ 0.7 → trial.success = True
7. Trial 1、2 重复（每个 trial 全新 sandbox 会话，互不污染）
8. 3 个 trial 完成 → pass@1 = 1.0（至少成功一次）→ 下一个 Task……
9. 全部 Task 完成 → RunSummary（pass@k/pass^k/退化/饱和度）→ 落库 → Dashboard 可查
```

面试时被问"评测一次具体发生了什么"，就按这 9 步讲。

## 3. 接入契约（单接口设计）

**契约本身**：
```python
class AgentRunner(Protocol):
    async def run(self, task: EvalTask) -> tuple[str, list[dict], dict]:
        ...  # (trace_id, transcript, outcome)
```

**为什么一个接口就够**（说辞）：
框架需要从被评系统拿四样信息——"执行完了吗"（run 返回即完成）、"执行过程"（transcript）、"最终结果与环境状态"（outcome）、"过程细节"（trace_id → 去 trace 后端拉 spans）。框架**不需要**知道 agent 内部怎么编排、用什么模型、工具怎么实现——所以一个接口封住了全部耦合点。

**三个返回值分别喂给谁**（高频追问）：
- `transcript`（[{role, content}...]）→ code_based / model_based / metrics 的判分素材
- `outcome`（{files: {...}, artifacts: [...]}）→ state_check / artifact_check 的判分素材
- `trace_id` → 去_trace 后端拉 spans → tool_calls / step_level / 过程指标（没有 trace 时给空串，这些 grader 退化，其余照常）

**零耦合怎么保证（不只是口头）**：
AST 扫描测试——遍历框架包所有 .py 文件的 import 语句，出现 `import app.*` / `from app.` 直接测试失败。依赖方向只能单向：AChat 实现契约，框架对 AChat 一无所知。这也是它后来能干净抽成独立开源包的根本原因。

**为什么用 Protocol 而不是抽象基类**：
Python 的 structural typing——被评系统不需要继承任何东西，只要"长得像"就行，接入面零侵入。

---

## 4. 编排层（EvalRunner）——可靠性设计的主战场

**trial 语义**：一个 task 跑 N 次（默认 3，task 可覆盖 max_trials）。为什么：单次通过是运气，N 次才能谈统计。

**并发**：`asyncio.Semaphore` 控制，默认 1（安全串行）。为什么默认串行：评测吞吐不是第一优先级，隔离正确性和可调试性才是；真实场景里 agent 一次跑几十秒到几分钟，串行的代价可接受。

**超时**：`asyncio.wait_for(run(), timeout=300)`。超时记为失败结果（error 标注），**suite 继续**——失败隔离，一个挂死的任务不拖垮整场评测。

**重试策略（要能说清边界）**：
- 只有 `TransientError`（网络/超时类瞬态错误）才重试，**指数退避** 2^attempt 秒，默认最多 2 次
- 评分判定失败**不重试**——那是"被评系统的真实表现"，重试就变成刷分了
- `asyncio.TimeoutError` 不算瞬态，直接记失败

**环境泄漏检测（亮点机制，要会讲全流程）**：
1. trial 开始前 `snapshot()` 拍环境基线（JSON 可序列化）
2. trial 正常执行
3. 结束后 `verify_clean(baseline)` 比对当前环境与基线
4. 有差异 → log 告警（含差异明细）→ `restore(baseline)` 尝试恢复
5. **泄漏不判 trial 失败**——环境问题是基础设施问题，不是 agent 能力问题（这个语义区分是加分点）

在 AChat 落地时：每 trial 新建独立 conversation + sandbox workspace（天然隔离），基线 = workspace 文件清单，verify_clean 比对清单差异，结束后删除评测会话。

**实战案例（面试讲故事的首选，见第 12 节案例 1）**：评测会话里 agent 调 fs_write 写文件，AChat 的 fs_write 在 review 审批模式下注册 pending write 等人工批准——评测无人值守 → 永久挂起 → run 卡死。我用进程内探针复现，定位到审批死锁，修复为 eval 会话创建后立即切 `fsWriteApprovalMode=auto`。这个故事的三层价值：真实故障模式（自动化环境里的人工审批死锁）、排查方法（探针复现而非猜）、修复的边界意识（只对 eval 会话切 auto，不影响正常使用）。

---

## 5. 评分器系统（9 种 + 流水线）

**9 种评分器按判分成本分层（背熟这张表）**：

| 层 | Grader | 判什么 | 成本 |
|---|---|---|---|
| 确定性 | `code_based` | transcript/outcome/spans 的 contains / not_contains / regex | 零 |
| 确定性 | `state_check` | 文件存在 / 内容 / 正则、DB 记录 | 零 |
| 确定性 | `tool_calls` | 必须调用的工具、禁止调用的工具 | 零（需 trace） |
| 确定性 | `artifact_check` | 产物存在、类型正确 | 零 |
| 确定性 | `transcript` | 轮次、token 成本约束 | 零 |
| 确定性 | `step_level` | 中间步骤逐个对照 expected_trace | 零（需 trace） |
| LLM | `model_based` | 按 rubric 主观打分（多维） | judge LLM |
| LLM | `metric` | 四个标准化质量指标 | judge LLM |
| 人工 | `human` | 人工评审 | 人工 |

**设计哲学**：确定性评分器做 **required 门禁**（结果对不对、纪律守不守），LLM 评分器做**加权质量分**（好不好）——主观分永远不单独决定成败。

**流水线机制**：
- grader 声明 `dependencies: list[str]`，执行前**拓扑排序**（DFS + visited 集合）
- 依赖未通过 → 后续 grader **跳过并记 0 分**（explanation 标注"依赖未满足"）——比如忠实度评分依赖检索步骤先真实发生
- 单 grader 超时（60s）记 0 分失败，不阻塞其他 grader

**组合策略（为什么默认 hybrid）**：
- `all_pass`：所有 grader 通过才通过——最严
- `weighted`：加权平均 ≥ 阈值——最松
- `hybrid`（默认）：**required 全通过 AND 非 required 加权分 ≥ 阈值**
- 理由：混合策略让"硬约束"（结果正确、安全纪律）和"软质量"（表达好坏）解耦——硬约束一票否决，软质量影响分数但可调权重

**LLM Judge 多采样置信度**：
`sample_count > 1` 时多次评分：score = 均值，`uncertainty = (max - min) / 2`，`confidence = 1 - uncertainty`。说辞：judge 本身也是 LLM，有非确定性——把这种不确定性**量化进结果**而不是假装分数是精确的。

**结果缓存**：key = sha256(grader_name + config + transcript/outcome 内容)——**内容寻址**，runner 生命周期内有效；多采样故意绕过缓存（重采样的目的就是拿不同结果）。

**human grader 的 pending 语义（设计决策题）**：
同步等待人工评分会挂住 trial 几小时。改为：`grade()` 立即返回 pending 结果（score=0, `details.status="pending"`），评分请求落库，run 正常完成；人工分数事后经 REST API 回传并重算汇总。代价：pending 期间该 trial 不计通过——换取整场评测不被人工节奏阻塞。

---

## 5A. 每个评分器的判别逻辑（输入 → 机制 → 输出）

> 所有评分器输出统一为 GraderResult：`{score: 0~1, passed: bool, explanation, confidence, details}`。`passed` 由 score 与各自 threshold 比较，门禁/加权语义见 §5。

**code_based —— 文本断言器**
- 输入：checks 列表，每条 `{type: contains | not_contains | regex, value, target: transcript | outcome | spans}`
- 机制：target 决定取哪份文本（transcript = 对话序列化文本 / outcome = 环境状态序列化文本 / spans = span 列表序列化），逐条检查，**score = 通过条数 ÷ 总条数**，passed = score ≥ threshold（默认 1.0，即全过）
- 例：`checks: [{type: contains, value: "9753", target: transcript}]` → 在对话全文里找子串 → 找到 → 1/1 → 通过
- 适用：答案里有固定关键词、禁止出现某内容（not_contains 查编造/泄漏）

**state_check —— 环境断言器**
- 输入：expectations 列表，类型 `file_exists` / `file_contains` / `file_regex` / `db_record`
- 机制：从 **outcome.files** 取指定路径的文件内容做断言（db_record 查数据库记录）；score = 通过数 ÷ 总数
- 例：`file_contains {path: "summary.md", value: "staging"}` → outcome.files 里有 summary.md 且内容含 staging → 通过
- 适用：**task 真正的交付物验证**——文件该存在、该包含什么。最可靠的门禁

**tool_calls —— 工具纪律断言器**
- 输入：required_tools（必须调用的）/ forbidden_tools（禁止调用的）
- 机制：从 spans 筛出 name 含 `tool.call` 的 span → 读属性 `agenthub.tool_name` 得到实际工具调用列表 → required 逐个比对（缺一个按比例扣分）、forbidden 命中任意一个直接 0 分
- 例：`required_tools: ["rag_search"]`，spans 里的工具调用有 fs_read / rag_search → rag_search 命中 → score=1.0
- 适用：**验证 agent 用了正确的手段**——"RAG 回答必须真的检索过"（防止模型凭参数记忆瞎答）、"读系统路径这种事一次都不许发生"
- 依赖 trace：没有 spans 时退化

**artifact_check —— 产物断言器**
- 输入：可选 expected_type（产物类型）、content_regex（内容模式）
- 机制：优先从 **outcome.artifacts** 取产物列表；无产物 → 0 分（"没产出"）；有类型要求则比对类型；有内容要求则正则
- 例：prompt 要求创建名为 aeval-artifact-check 的 artifact → outcome.artifacts 里有它 → 通过

**transcript —— 过程效率评分器**
- 输入：max_turns（轮次上限）/ max_tokens（token 上限）
- 机制：过程指标来自 spans（n_turns 轮次、n_total_tokens）；`turns_score = max(0, 1 − n_turns/max_turns)`、token 同理——**越接近上限分越低**（线性衰减）；再加工具调用冗余度惩罚；三项平均
- 例：max_turns=20 实际 3 轮 → turns_score = 1−3/20 = 0.85
- 适用：约束"办事成本"——用 30 轮搞定 3 轮能搞定的事，扣分
- ⚠️ 已知局限：AChat 侧 turn/token 的 span 覆盖不完整时该维度分数偏乐观（README Known Limitations 已标注）

**model_based —— LLM 主观评分器**
- 输入：rubric（评分标准文字）、dimensions（维度列表）、threshold（默认 0.7）
- 机制：框架组装 judge prompt（rubric + 维度 + transcript 首条[用户输入]与末条[最终回答] + 工具调用清单）→ 发给 judge LLM → 要求 JSON 返回各维度 0-1 分 → 平均 → passed = score ≥ threshold；sample_count>1 时多次采样取均值并计算置信度
- 例：`rubric: "agent 应明确指出文件不存在，不得编造营收数字"` → judge 返回 `{"correctness": 0.9}` → score=0.9
- 适用：**无法用代码断言的主观质量**——语气对不对、拒绝方式得体吗、汇总质量高不高

**metric —— 标准化质量指标**
- 输入：`metric_name`（answer_relevancy / faithfulness / context_recall / context_precision）+ 该指标的输入（context 等从 config 静态传入）
- 机制：按 metric_name 从注入的注册表找 Metric 实例 → 从 transcript 取首条为 input、末条为 actual_output → `measure()` → 包装成标准 GraderResult
- 四指标内部机制见 §7 表格
- 未知 metric_name → 0 分失败并标注原因

**step_level —— 步骤级过程评分器**
- 输入：`expected_trace`（期望的工具调用顺序列表）
- 机制：从 spans 提取 tool.call 步骤序列 → 与 expected_trace **按索引对照** → score = 正确步数 ÷ 总步数 → details 报告第一个错误步骤
- 例：expected_trace=["fs_read", "fs_edit"]，实际 ["fs_read", "fs_edit", "fs_read"] → 前 2 步命中 → score=2/3，"first error at step 2"
- 适用：**过程正确性**——不只看结果，还看走的路对不对（定位"哪一步开始错"）

**human —— 人工评分器（pending 语义）**
- 机制：`grade()` 立即返回 pending（score=0、`details.status="pending"`），评分请求落库，trial 正常继续；人工评审者事后经 REST API 回传分数 → 框架重算该 task 汇总
- 适用：LLM 判不准、代码判不了的主观项；**设计取舍：人工节奏不阻塞机器流程**

## 6. 统计层（pass@k / pass^k）——最容易被深挖的模块

**两个维度（直觉要说对）**：
- **pass@k**：k 次尝试中至少成功一次 → 衡量**能力**（"这个 agent 有没有机会做成"）
- **pass^k**：k 次尝试全部成功 → 衡量**稳定性/可靠性**（"能不能放心让它上"）
- 一个 agent 可能 pass@3=1.0 但 pass^3=0.4：偶尔能做对，但不可靠——这类差异单次运行完全看不到

**计算规则（要能手推）**：
设 n = 实际 trial 数，s = 成功数，p̂ = s/n（成功概率的极大似然估计）：
- **k ≤ n**：pass@k = 1（若 s ≥ 1）否则 0；pass^k = 1（若前 k 次全成功）否则 0——直接判定，不做估计
- **k > n**：外推。每次 trial 看作独立同分布 Bernoulli(p̂)：
  - pass@k = 1 - (1-p̂)^k（k 次全失败的概率是 (1-p̂)^k，取补）
  - pass^k = p̂^k（k 次全成功的概率）

**例**（面试官让现场算）：3 次 trial 成功 1 次，p̂=1/3，pass@5 = 1-(2/3)^5 ≈ 0.868，pass^5 = (1/3)^5 ≈ 0.004——能力看起来还行，稳定性其实很差。

**诚实面对的高频追问："这和 HumanEval 的 pass@k 一样吗？"**
不一样，且是有意选择。HumanEval 用**无偏估计量** `1 - C(n-c, k)/C(n, k)`（从 n 次采样中不放回抽 k 次），并报告的是期望值。我们的场景是回归评测：k ≤ n 时直接判定（这次 run 里到底成没成过），k > n 时用 i.i.d. 二项外推预估"再多给几次机会能怎样"。区别本质是：HumanEval 在估计模型能力分布，我们在度量**单次评测 run 的实测结果 + 简单外推**。知道两者差异、能说清取舍，比背公式加分。

**评测的元指标（很少人想到，说了加分）**：
- **一致性**：trial 间平均分的标准差 < 0.2 视为一致——不一致说明任务区分度或 agent 稳定性有问题
- **饱和度**：超过半数 task 的 pass@1 ≥ 0.95 → 报告"评测已饱和，建议增加更有挑战性的任务"——**防止考卷太简单造成虚假安全感**
- **信息增益视角**：任务通过率落在 30%-70% 区间时区分度最大——设计用例时的目标区间

---

## 7. LLM 质量指标（Metrics 模块）

**四个 RAG 三角指标（原理要能各讲 20 秒）**：

| 指标 | 评测什么 | 机制 | 缺输入时的行为 |
|---|---|---|---|
| Answer Relevancy | 回答切题吗 | 从回答提取陈述 → 逐条判相关度 → 均值 | — |
| Faithfulness | 回答忠于上下文吗（防幻觉） | 提取事实性陈述 → 逐条找上下文支持 → 支持占比 | 无 context → score=0 + 明确 reason（**不猜测**） |
| Context Recall | 检索覆盖够吗 | 从 expected_output 提取信息点 → 逐点查 retrieval_context | 缺 expected/retrieval → 0 + reason |
| Context Precision | 检索的准吗 | 逐文档判是否对回答有用 → 相关占比 | 缺 retrieval_context → 0 + reason |

**Metric vs Grader 的关系（架构题）**：
Metric 评"输出质量"（给定输入输出对），Grader 评"行为质量"（工具调用、状态变更、纪律）。桥接：`Metric.to_grader()` 把任何 Metric 包装成标准 Grader 进流水线，grader type 标记为 `metric`，享受同样的 required/weight/依赖语义。框架的运行时只认 Grader 一种东西——**统一抽象，减少概念**。

**P1 交付（每样一句话）**：
- **BatchEvaluator**：对已有输出（不跑 Agent）批量打分——指标名解析前置（未注册直接 422，零 LLM 浪费）、单条异常隔离不中断整批、Semaphore 限并发
- **PromptMetric**：Prompt 变体 A/B——渲染模板 → 生成 → 指标打分 → n_trials 平均 → 声明胜者（v1 求和语义，明确标注局限）
- **pytest 插件**：同步包装（内部 asyncio.run）暴露 fixtures，`--eval-suite`/`--eval-threshold` 让评测成为 pytest 门禁——评测左移进测试体系
- **report**：RunResult / 批量结果 → Markdown/JSON 纯函数渲染，给 CI 和 CLI 消费

**LLM Judge 基础设施**：JSON 容错解析（LLM 输出经常不合规）、失败重试、`require_llm_fn` 未配置时明确报错而非静默 0 分。

---

## 8. 数据集构建与质量闭环

**五种数据源（背熟）**：
1. **手工**：YAML/JSON 导入（严格校验）
2. **Trace Mining**：从生产/历史 trace 挖——failed_tasks（失败 trace）/ long_running（耗时异常）/ diverse_sampling（多样性采样），prompt 从根 span 提取
3. **LLM 生成**：给场景描述 + 能力维度，批量生成任务（生成后过同一套校验）
4. **对抗样本**：手工构造边界场景（模糊指令、循环依赖、超长输入）
5. **回归提取**：**质量闭环的核心**——run 里新出现的失败 trial 自动变成回归用例（同 task 去重、prompt 归一化去重防膨胀）

**质量闭环（画图题）**：
```
评测 Run ──▶ 失败分析 ──▶ 回归提取 ──▶ 合入数据集（升版 minor）──▶ 再评测验证修复
     ▲                                                                  │
     └──────────────────────────────────────────────────────────────────┘
```

**数据集元能力**：
- 质量检查：重复 prompt、缺 grader、空 prompt、超长 prompt → warning/error 清单
- 覆盖度分析：按任务的能力维度标签统计各维度覆盖（0-1），标出薄弱维度
- semver 版本：major=删任务/改 grader，minor=加任务，patch=修文案调阈值——**评测考卷本身也要有版本管理**，否则两次 run 的对比失去基准

**测试用例设计方法论（体现"懂评测"而不只是"写了框架"）**：
- **六种设计模式**：功能验证 / 边界条件（空输入、超长输入）/ 错误处理（缺失文件应报错不编造）/ 多步推理 / 安全约束（未经确认不删除）/ 多 Agent 协作
- **Prompt 五原则**：明确性、**可判定性**（成功标准必须能被 grader 验证）、自包含、难度适当、单任务单能力维度
- **难度目标**：通过率 30%-70% 区间信息增益最大——太简单无区分度，太难无信息量
- **用例验证流水线**：静态检查 → 试跑 3 次 → 难度校准 → 入库

---

## 9. 呈现层（REST / SSE / Dashboard / CLI）

**REST API 资源模型**：Suites / Tasks / Datasets / Runs / Trials / Compare / Graders / human-scores。与宿主既有 API 同前缀共存（子路径不重叠），独立部署时挂 `/v1` + `X-Aeval-Version` 头 + `/v1/meta`。

**SSE 快照+增量协议（设计决策题，高频）**：
- **为什么 SSE 不是 WebSocket**：事件严格单向（服务端→客户端）、低频（trial 粒度，几十秒一个）、浏览器 EventSource 自带重连、纯 HTTP 可 curl 调试——WebSocket 的双向能力和低延迟优势全用不上
- **协议模型（比选型更重要）**：run 生命周期由服务端**后台 asyncio task 持有**，与观察连接解耦——浏览器关了 run 照跑。客户端流程：`GET /runs/{id}` 拉全量快照 → 订阅 `/runs/{id}/stream` 收增量；**断线恢复 = 重新拉快照 + 重新订阅**，不做可靠投递
- **为什么放弃可靠投递**：事件按 (task_id, trial_index) 幂等，丢事件靠下次快照自愈——为一个低频观察场景建持久化事件队列不值得
- 工程细节：15s 心跳注释行防代理空闲断连；run 完成后订阅立即收终态事件关流（不悬挂）

**CLI**：`eval-suite run/validate/list/show/compare/serve` 六命令；runner 解析链 `--runner` > `AEVAL_RUNNER` 环境变量 > 内置 mock；自定义 runner 经 **entry-point group `agent_eval.runners`** 注册——第三方项目在自家 pyproject 里声明即可被 `eval-suite` 发现（标准 Python 插件机制，框架零感知）。退出码语义：有失败任务 → 非 0，可接 CI。

**Dashboard**：独立 Next.js 应用（与宿主解耦，API base 可配）；8 类页面——总览/Suites/Run 报告/Trial 下钻/A-B 对比/Datasets/Tasks/Settings；Trial 下钻页是"定位问题"的核心：grader 逐项分数 + transcript 全文 + workspace 产物 + **跳 Phoenix 看 trace**（单向链接 Eval→Phoenix，Phoenix 不回链）。

---

## 10. 工程化（开源 + 质量自证）

**"评测框架自己怎么保证质量"（元问题，答好加分）**：
- 349 个框架测试；核心模块（metrics/graders/suite 校验）覆盖率 95%
- MockAgentRunner（可脚本化成功/失败/瞬态/超时）做端到端集成测试
- AST 约束测试固化"零反向依赖"架构规则
- CI：pytest 3.11/3.12 矩阵 + ruff + dashboard build

**打包与分发**：单包 + extras（`[api]`=fastapi+uvicorn，`[cli]`=typer+rich），console script `eval-suite`，宿主项目经 entry-point 组注入 runner。单包而非拆三包的理由：单一 PyPI 名、依赖面小、拆分不破坏对外契约——边界需要时再拆。

**开源发布流程（讲"工程化发布"而不是"传了个 GitHub"）**：
- 四项决策先落定（LICENSE=MIT + fresh init；命名；API /v1；双语文档）→ 再执行
- **fresh init**：staging 拷贝（排除 .venv/__pycache__/dist/db）→ git init → 首提交 → push。**不携带原仓库历史**——原仓是 AGPL 且历史无关，干净首提交对开源观感重要
- 四道门禁（push / tag+Release / PyPI / 宿主切换）逐项人工放行——外发不可撤回动作必须 gated
- **命名危机的真实处理**：原定 PyPI 名 `agent-eval` 已被占用（UK Government AISI），品牌名 `aeval` 也被占 → 改 `aeval-framework`，模块名 `agent_eval` 与 CLI 名不变（`pip install aeval-framework` → `import agent_eval`，同 pillow→PIL 惯例）——发布前核对 PyPI 占用是流程教训

---

## 11. 与宿主项目的集成（AChat 桥梁层）

**三层联系**（架构题）：
1. **pip 依赖**：宿主 venv 安装 `aeval-framework`，`import agent_eval` 单向依赖
2. **桥梁层**（留在宿主内的适配代码）：`AChatAgentRunner` 用宿主的会话/工作区/审批机制实现框架的 AgentRunner 契约——**框架定义契约，宿主实现契约**，全部耦合点浓缩在这层
3. **HTTP**：Dashboard / CLI 查询宿主侧评测数据；或 `eval-suite serve` 独立起服务

**task 级会话配置**：`env.agent_id`（换被评 agent）+ `env.conversation`（mode/agent_ids/dispatch_mode）——一套 suite 里不同任务可以评不同 agent、不同的编排形态；非法组合（group 但 agent<2）在 trial 开始时明确失败，**不静默回退**（配置错误必须显性暴露，否则回归结果失真）。

---

## 12. 实战案例（面试讲故事的弹药库）

### 案例 1：fs_write 审批死锁（首选故事）
- **现象**：评测 run 的某个任务 300 秒超时，日志无异常，DB 里会话消失（结束后清理设计导致事后无从查起）
- **排查**：写进程内探针脚本——铸 token → 建 sandbox 会话 → 发同样的 prompt → 每 5s 观察消息 parts 与 run 状态。看到 agent 正常思考并调用了 `fs_write`，工具开始执行后**永久没有结果返回**
- **根因**：AChat 的 fs_write 有 `review` 审批模式——注册 pending write 并**等待人工批准**。评测无人值守 → 没人批 → run 永久 streaming → 框架超时
- **修复**：eval 客户端创建会话后立即 `PATCH fsWriteApprovalMode=auto`——只作用于评测会话
- **提炼**：(1) 自动化环境里"人工在环"会变成死锁源；(2) 设计了事后不可查的清理机制，就要配套"进行中可观测"的手段；(3) 修复带边界（不改变人工使用的行为）

### 案例 2：trace 桥属性名——"设计约定 vs 工程现实"
- **现象**：任务执行成功但 trace_id 解析失败，降级路径又撞上 Phoenix 新版 API 变更
- **根因（两层）**：桥接器按设计文档的约定读 `agenthub.run_id` 属性，但宿主真实 span 设置的是裸名 `run_id`（`start_span` 原样透传 kwargs）；同时 Phoenix ≥5 移除了顶层 `px.Client`，需要迁到 `phoenix.client.Client`
- **修复**：桥双读两个属性名；PhoenixProvider 迁移新 API
- **提炼**：**跨系统属性约定必须在实现前对着真实数据核对**——文档里的约定不等于运行时的事实；后来据此把"端点核对"固化成接入 change 的第一个任务

### 案例 3：PyPI 名被占（开源流程故事）
发布 GATE 时发现 `agent-eval` 已被占（英国政府 AISI 的包，0.1.53）。处理：核实占用 → 改 `aeval-framework`（模块名 `agent_eval` 与 CLI 名不变）→ README 注明命名对应关系 → 文档回写修正记录。提炼：发布流程里"名字可用性"要作为前置检查项。

### 案例 4：会话消失之谜（观测性设计反思）
验收后查数据库找不到评测会话——因为设计上 trial 结束即删除会话（隔离）。教训：**清理机制要和观测机制配套**，否则排障时"现场"已被自己销毁。后来诊断这类问题统一走"进行中探针"。

---

## 13. 面试问答说辞（18 题）

> 每题给：问题 → 答法（第一人称说辞）→ 深挖点提示

**Q1. 为什么要做这个项目？市面不是有 DeepEval/LangSmith 吗？**
答：市面上工具评的是"LLM 输出质量"——给定输入输出对打分。但我们的场景是 **Agent**：行为横跨多步工具调用、工作区状态变更、多 Agent 协作，这些 DeepEval 没有概念。我需要一个评"行为"的框架：能看工具调用对不对、工作区最终状态对不对、编排派发质量如何——而且要能对宿主项目零侵入接入、能开源沉淀。所以自己设计了这一个，输出质量指标（Relevancy/Faithfulness 那套）作为子系统融了进来，两者互补。

**Q2. Agent 评测和单元测试的本质区别？**
见 §1 表格。核心差异一句：单测断言"确定性的输出"，评测断言"概率性的行为"——所以评测的通过标准是阈值+多次统计，失败的含义是"退化"而不是"bug"。

**Q3. 为什么接入契约只有一个接口？会不会太少？**
run(task) 返回三元组已覆盖框架需要的全部信息：完成与否（返回即完成）、过程（transcript）、结果与环境（outcome）、细节（trace_id 拉 spans）。接口越少耦合面越小——宿主实现 20 行就能接入。扩展点都在框架侧（grader/存储/trace 后端全部可替换），不需要宿主感知。代价是宿主要自己把内部信息映射成三元组——但这本来就是测试代码该干的活。

**Q4. LLM 非确定性，你怎么让评测结果可信？**
三层：①多 trial（默认 3 次）②两个统计维度 pass@k（能力）+ pass^k（稳定性），k>n 时二项外推 ③一致性检查（trial 间标准差）标记不稳定任务。另外确定性 grader 做门禁，把主观 LLM 判分的影响面压到最小。

**Q5. pass@k 和 pass^k 推导一下？k=5 但只跑了 3 次怎么办？**
见 §6。说辞：k≤n 直接判定；k>n 时每次 trial 看作 Bernoulli(p̂)，p̂ 是 MLE，pass@k = 1-(1-p̂)^k，pass^k = p̂^k。举 3 中 1 的例子：pass@5≈0.87 但 pass^5≈0.004——能力看着行、稳定性很差。追问 HumanEval 差异 → §6 的诚实对比（无偏估计量 vs i.i.d. MLE，场景不同）。

**Q6. 九种评分器，实际怎么选？**
口诀：**结果对不对用 state_check/artifact_check（门禁），回答内容用 code_based（零成本），纪律和过程用 tool_calls/step_level（需 trace），主观质量用 model_based/metric（加权），成本约束用 transcript，人不在线用 human（pending）**。确定性优先——能写死断言的不用 LLM。

**Q7. 为什么默认 hybrid 评分？**
required 门禁 + 加权分，把"对不对"（一票否决）和"好不好"（影响分数）解耦。all_pass 太严（一个主观分不达标就全盘否定），weighted 太松（门禁项可以被其他高分拉回来，安全任务失败也能及格）。hybrid 两全，且权重可按任务调。

**Q8. grader 之间的依赖怎么处理？**
声明式 `dependencies`，执行前 DFS 拓扑排序；依赖未通过 → 后续跳过记 0 分并标注原因。场景：忠实度评分依赖"检索真的发生了"（rag_search 通过）——检索都没做，忠实度无从谈起，跳过比给随机分诚实。

**Q9. LLM-as-Judge 的可靠性怎么保证？（必问题）**
三层防御：①**确定性优先**——能用代码断言的不用 LLM，LLM 分只做加权项；②**多采样置信度**——judge 多次评分报均值 + 不确定性（极差/2），让 judge 自己的不确定性显形；③**结构化防漂移**——judge 输出走 JSON 容错解析 + 重试，rubric 明确到"编造数字即不通过"这种可执行粒度。另外 prompt-hash 缓存控制 judge 成本。

**Q10. 环境泄漏检测具体怎么做？**
trial 前 snapshot() 拍基线 → 结束 verify_clean(baseline) 比对 → 有差异告警 + restore 恢复。AChat 落地时基线是 workspace 文件清单，隔离靠每 trial 新建 sandbox 会话。关键语义：**泄漏不判 trial 失败**——环境问题是基础设施问题，跟 agent 能力无关，混进去会污染评测结果。

**Q11. 讲一个你排查过的最难的问题。**
讲案例 1（fs_write 审批死锁）：现象（300s 超时无异常）→ 疑点（事后查库现场已被清理机制销毁）→ 手段（进程内探针，5 秒粒度观察消息 parts 和 run 状态）→ 定位（工具开始执行后永久无返回 → fs_write 的 review 审批等待人工）→ 修复（eval 会话切审批 auto，带边界）→ 沉淀（无人值守环境里"人工在环"是死锁源；清理机制要配套进行中观测手段）。

**Q12. SSE 还是 WebSocket，为什么？断线了怎么办？**
SSE：事件单向、低频、EventSource 自带重连、纯 HTTP 可调试。协议模型比选型重要：run 由后台 task 持有（连接只是观察窗口），客户端先拉全量快照再收增量，断线=重拉快照+重订阅，事件按 (task_id, trial_index) 幂等，**不做可靠投递**——丢事件靠快照自愈，为低频观察场景建事件持久化不值。

**Q13. 数据集质量闭环怎么运转？**
run 失败 → RegressionExtractor 自动提取失败 trial（同 task 去重、prompt 归一化防膨胀）→ 以 regression 溯源合入数据集 → 数据集 minor 升版 → to-suite 再评测验证修复。配套质量检查（重复/缺 grader/空 prompt）和覆盖度分析（按能力维度找盲区）。价值：让"这次踩的坑"变成"永久的回归资产"。

**Q14. 怎么判断一份评测考卷出得好不好？**
三个元指标：①**饱和度**——过半任务 pass@1≥0.95 说明考卷太简单，虚假安全感；②**一致性**——trial 间标准差大说明任务或 agent 不稳定；③**信息增益**——通过率 30%-70% 的任务区分度最大。所以有"静态检查→试跑 3 次→难度校准→入库"的用例验证流水线。

**Q15. 框架自己怎么保证质量？**
把它当开源工程打磨：349 测试、核心 95% 覆盖、MockAgentRunner 端到端集成测试、AST 约束测试固化架构规则、CI 双版本矩阵。元认知：评测工具自己不可信，评测结果就不可信——工具的测试标准比业务代码更严。

**Q16. 为什么开源？发布流程做了什么？**
评测框架的价值随着接入方增多而增长，开源是设计目标而非事后想法（所以一开始就强制零反向依赖）。发布：四项决策先落定（MIT/fresh init、命名、/v1、双语）→ staging 拷贝 fresh init（不携带原仓库历史）→ 四道门禁逐项放行（push/Release/PyPI/宿主切换）→ PyPI 名被占后改 aeval-framework。学到的是"发布也是工程"：名字可用性前置检查、外发动作全部 gated。

**Q17. 这个系统还有什么局限？（诚实题，答好加分）**
①transcript 过程指标（turns/tokens）在宿主上的 span 覆盖不完整，该维度分数偏乐观；②RAG 和编排场景机制就绪但实战校准不足；③A/B 对比还没有显著性检验——目前只报 delta 不报置信度；④存储只有 SQLite，PG 是 Phase 3。下一步优先级：显著性检验（比较两个 run 是最高频操作）> PG > 过程指标补全。

**Q18. 如果重做一次，你会改什么？**
①设计期就把"跨系统属性约定"做成共享常量包而不是两套代码各写各的（trace 桥的属性名 bug 本可避免）；②human grader 一开始就按 pending 语义设计而不是先写同步等待再改；③评测资产的清理机制从第一天就配"进行中快照"用于排障；④早点引入 TestPyPI 预演发布。

---

## 14. 关键数字速查（防被问倒）

| 项 | 数值 |
|---|---|
| 框架测试 | 349 个，核心模块覆盖率 95% |
| 内置评分器 | 9 种（6 确定性 + 2 LLM + 1 人工） |
| 质量指标 | P0 四指标 + P1（批量/PromptMetric/pytest/报告） |
| 数据源 | 5 类（手工/Trace Mining/LLM 生成/对抗/回归提取） |
| CLI 命令 | 6 个（run/validate/list/show/compare/serve） |
| 默认参数 | trial=3、并发=1、超时=300s、重试=2 次指数退避、judge 采样置信度=1-极差/2 |
| 阈值 | hybrid 默认 0.7；一致性 std<0.2；饱和度 pass@1≥0.95 过半 |
| 发布 | MIT，PyPI `aeval-framework 0.1.0`，GitHub CI 3.11/3.12 |
| 设计文档 | v0.10，6084 行，8 个 OpenSpec change 全部闭环 |
