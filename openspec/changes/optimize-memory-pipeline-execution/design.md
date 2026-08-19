# Design — optimize-memory-pipeline-execution

## Context

当前 AChat 记忆系统已落地 file-native 架构（`rewrite-memory-file-native` change），但 pipeline 执行层相比 ReMe 存在质量差距：

**auto_memory 现状**：
- 单次 LLM chat completion → 返回 `{"facts": [{text, tags, importance}]}` → 程序拼 bullet list 写文件
- 已有笔记合并：行级完全匹配去重（`if fact_line not in existing_set`）
- 文件命名：程序生成 `session_<conv_id[:8]>`
- session/ jsonl：简单 append，无去重，不净化 tool_result

**auto_dream 现状**：
- extract：扫描最近 7 天全部 daily 文件（无变更检测），prompt 无跨文件合并指导
- integrate：程序调 `HybridSearch.search()` 召回 → LLM 返回 `{action, content, reason}` → 程序代写。1 个通用 prompt（无 per-bucket 特化）
- topics：纯规则取前 N 个 fresh topic（无 LLM 参与）

**wikilink 现状**：
- 仅 `[[target]]`，无 predicate / anchor
- 无断链检测，无 move+retarget

**关键约束**：
- auto_memory 是后台 hook，不切换到完整 Agent Loop（工具沙箱/权限/超时复杂度过高）
- 保持内嵌 FastAPI 进程，不引入独立服务
- 不改 RAG 系统 / Preference 系统 / SessionMemory
- 不引入新外部依赖

参考来源：ReMe `待融合项目/ReMe/reme/steps/evolve/` 目录下的 `auto_memory.yaml`、`dream/extract.yaml`、`dream/integrate.yaml`、`dream/topics.yaml`。

## Goals / Non-Goals

**Goals:**

- auto_memory prompt 融入 ReMe 合并规则（时间线追加 / 状态重写 / 语义去重），支持新建+更新两种路径
- auto_memory 文件命名改为 LLM 生成语义 name
- session/ jsonl 增加 message ID 去重 + tool_result/base64 净化
- dream_extract 增加变更检测（file catalog 跟踪 mtime），只处理 changed 文件
- dream_extract prompt 增加跨文件合并指导 + topic 候选结构化输出
- dream_integrate 增加 per-bucket 专用 prompt（procedure / wiki 各有 body shape 指导）
- dream_integrate 增加 provenance wikilink（`derived_from:: [[path]]`）+ wikilink 织入指令
- 新增 node_search 专用搜索（digest-only + 节点级聚合 + 内联 frontmatter）
- dream_topics 改为 LLM 选择（有 Topic Quality 指导 + same-day 保留）
- wikilink 支持 predicate 语法 + 断链检测 + move+retarget
- HybridSearch 返回 score breakdown + link expansion meta

**Non-Goals:**

- 不切换 auto_memory / auto_dream 到完整 Agent Loop（保持单次/少量 LLM 调用）
- 不做 auto_resource（RAG 系统已覆盖）
- 不做 file watcher / 实时文件监听（后续迭代）
- 不做 file chunker / embedding store（后续迭代）
- 不做 MCP Server / CLI 接入（后续迭代）
- 不做 Claude Code 会话集成（后续迭代）
- 不改 Preference 系统 / RAG 系统 / SessionMemory
- 不引入新依赖

## Decisions

### D1. auto_memory：保持单次 LLM 调用，但 prompt 升级为结构化输出

**选择**：保持「单次 LLM chat completion」执行方式，但 prompt 从"提取 facts 列表"升级为"生成完整 daily card（含 name / description / body / tags / action）"。LLM 输出格式从 `{"facts": [...]}` 改为 `{"action": "create"|"update", "name": "...", "description": "...", "body": "...", "tags": [...], "importance": 0.5}`。

**替代**：ReMe 的 Agent Loop（起 Agent → 给工具 daily_write / read / edit → Agent 自主写文件）

**理由**：
- Agent Loop 在后台 hook 中复杂度过高（需要工具沙箱、权限控制、超时管理、重试逻辑）
- 单次 LLM 调用的核心问题是 **prompt 不够好**，而非执行方式不对
- ReMe 的 prompt 工程（合并规则、skip check、name 生成）可以在单次调用中复刻
- 将 `action` 字段引入输出，让 LLM 自己判断新建 vs 更新，比程序用"文件是否存在"判断更灵活

**与 ReMe 的差距**：ReMe 的 Agent 能在更新时 `read` 已有笔记 → 理解内容 → `edit` 精确替换。AChat 用 LLM 生成完整 body + 程序做行级合并，合并精度略低。通过在 prompt 中要求 LLM "if updating, generate the complete merged body" 来弥补。

### D2. auto_memory prompt：融入 ReMe 合并规则

**选择**：prompt 分为 `system_prompt` + `user_message_create` + `user_message_update` 两个模板，直接参考 ReMe `auto_memory.yaml` 的结构：

- **system_prompt**：角色定义 + 记录什么/不记录什么 + frontmatter 规则
- **user_message_create**：对话历史 + skip check + 生成 name/description/body
- **user_message_update**：对话历史 + 已有笔记内容 + 合并规则（时间线追加 / 状态重写 / 语义去重）+ 生成完整合并后 body

**替代**：当前单一 prompt（不区分新建/更新，输出扁平 facts 列表）

**理由**：ReMe 的双路径 prompt 是其合并质量的核心。新建时 LLM 自由生成；更新时 LLM 看到已有内容并按规则合并。AChat 可以复刻这套 prompt 结构，只是执行方式从 Agent Loop 改为单次调用。

### D3. session/ jsonl：message ID 去重 + tool_result 净化

**选择**：
- 按 message ID 去重（已有消息不重复写入，支持 append-only 增量）
- `_sanitize_msg_for_save`：剥离 tool_result block 和 base64 data block（参考 ReMe `_sanitize_msg_for_save`）

**替代**：当前简单 append

**理由**：tool_result 中常包含检索到的记忆/搜索结果，保留在 session 中会让未来 auto_memory 误把检索到的事实当成用户提供的上下文。message ID 去重避免多次 round-trip 时的重复写入。

### D4. file catalog：SQLite 表跟踪 path + st_mtime

**选择**：新增 `memory/file_store/file_catalog.py`，SQLite 表 `memory_catalog(path TEXT PK, st_mtime REAL, bucket TEXT)`。启动时全量扫描重建；auto_memory / auto_dream 写入后更新对应条目；dream_extract 查询 catalog 对比文件系统 mtime 检测变更。

**替代**：ReMe 的 4 个独立 catalog 实例（default / resource / digest / dream）

**理由**：AChat 不需要 ReMe 的多 catalog 分离（无 resource/）。单个 SQLite 表足够。dream_extract 通过 `catalog.get(path)` vs `os.stat(path).st_mtime` 比对，只处理 changed 文件。已删除文件（catalog 有记录但文件系统无）也需处理。

### D5. dream_integrate：per-bucket 专用 prompt

**选择**：2 个 integrate prompt（`integrate_system_prompt_procedure` / `integrate_system_prompt_wiki`），参考 ReMe `integrate.yaml`：

- **procedure**：body shape = runbook（Trigger / Steps / Pre-conditions / Failure modes / derived_from）
- **wiki**：body shape = encyclopedia（First line definition / Body properties / derived_from）

每个 prompt 都包含：
- Workflow（recall → classify → action → weave）
- Action 定义（CREATE / CORROBORATE / REFINE / CORRECT）
- Wikilink graph 规则（predicate 词表 + provenance 要求）

**替代**：当前 1 个通用 `_INTEGRATE_SYSTEM_PROMPT`

**理由**：不同 bucket 的记忆有不同的最佳写入结构。procedure 需要可执行的步骤列表；wiki 需要百科式定义。per-bucket prompt 让 LLM 生成更结构化的内容。

### D6. dream_integrate：provenance wikilink + 织入指令

**选择**：
- integrate prompt 要求 LLM 在 body 中加入 `derived_from:: [[daily/<date>/<session>.md]]` 指向来源 daily 卡片
- prompt 要求 LLM 在 CREATE 和 UPDATE 时都织入 related digest 节点的 wikilink（`[[digest/<bucket>/<name>.md]]`）
- 程序在写入前验证 body 中是否包含至少一条 `derived_from::` wikilink（不包含则 warning 但不阻断）

**替代**：当前无 provenance 要求

**理由**：provenance wikilink 是记忆溯源的基础。没有它，digest 文件无法追溯到来源 daily 卡片。织入指令让 wikilink 图自然生长，而非依赖后续手动添加。

### D7. node_search：digest 专用搜索

**选择**：新增 `memory/search/node_search.py`，与 `HybridSearch` 并列：
- digest-only 过滤（只搜索 `digest/` 目录下的文件）
- 节点级聚合（同 path 的多个命中按最高分聚合为一条结果）
- 内联 frontmatter（返回 name + description，免去 follow-up read）
- 无 link expansion（dream synapse 需要找未链接的节点）
- 无 agent_id 过滤（dream integrate 需要跨 agent 召回）

**替代**：dream integrate 用通用 `HybridSearch.search()`

**理由**：通用搜索返回文件级结果 + link expansion，但 dream integrate 需要的是「哪些已有 digest 节点与新 unit 相似或相关」。node_search 的节点级聚合 + 内联 frontmatter 让 LLM 一次看到所有候选的 name + description，无需多次 read。

### D8. dream_topics：LLM 选择

**选择**：topics 步骤从纯规则改为 LLM 调用：
- 将候选 topics + same-day existing + recent 7 天 topics 一起发给 LLM
- LLM 根据 Topic Quality 指导选择最终 topics
- 保留 same-day existing topics（不覆盖）
- LLM 不可用时 fallback 到纯规则（取前 N 个 fresh）

**替代**：当前纯规则取前 N 个 fresh topic

**理由**：纯规则无法判断 topic 质量。LLM 能理解"具体、反复出现、可行动的兴趣"比"泛泛标签"更有价值。fallback 保障 LLM 不可用时不中断。

### D9. wikilink predicate 语法

**选择**：wikilink 解析器升级，支持 `predicate:: [[path]]` 语法（如 `derived_from:: [[daily/2026-08-04/sess.md]]`）。邻接表增加 `predicate TEXT` 列。

**替代**：当前仅 `[[target]]`

**理由**：predicate 让 wikilink 图携带关系语义。`derived_from::` 溯源、`relates_to::` 关联、`depends_on::` 依赖——这些关系类型对未来图遍历和检索都有价值。解析器用正则 `^(\w+)::\s*\[\[([^\]]+)\]\]` 匹配，无 predicate 的 `[[target]]` 存为 `predicate = NULL`。

### D10. 断链检测 + move+retarget

**选择**：
- auto_index 在 `index_file` 时检查 wikilink target 是否存在；不存在的记入 `broken_links` 表
- `full_reindex` 时清理所有 broken links
- 新增 `move_file(src, dst, retarget=True)` 方法：移动文件 + 扫描全工作空间 `[[old_path]]` → `[[new_path]]` 重写
- API 层新增 `POST /api/memory/move` endpoint

**替代**：当前无断链检测、无 move

**理由**：用户重命名 digest 文件后，所有指向它的 wikilink 都会断链。move+retarget 在重命名时自动修复。断链检测在索引时发现并清理失效链接。

### D11. HybridSearch score breakdown + link expansion meta

**选择**：
- `SearchResult` 增加 `scores: dict` 字段（`{"bm25": 0.5, "wikilink": 0.3, "rrf": 0.8}`）
- 搜索结果增加 `expansion: dict` 字段（outlinks + inlinks，每个 neighbor 附 name + description）
- link expansion 通过 wikilink_expander 的 `get_outlinks(path)` + `get_inlinks(path)` 实现

**替代**：当前仅返回 fused score，无 link expansion meta

**理由**：score breakdown 让调用方判断命中来源（关键词匹配 vs 关系扩展）。link expansion meta 让 Agent 在搜索结果中直接看到关联文件，无需二次查询。

## Risks / Trade-offs

- **[auto_memory prompt 兼容性]** prompt 重写后 LLM 输出格式变化，已有 daily 卡片的格式可能不兼容 → 新 prompt 的 update 路径设计为读取已有 body → 生成完整合并 body，不依赖旧格式解析
- **[file catalog 一致性]** catalog 与文件系统可能不同步 → 启动时全量重建；写入后同步更新；dream_extract 前做一次 mtime 比对
- **[per-bucket prompt LLM 调用次数]** 每个 unit 一次 LLM 调用（不变），但 prompt 更长 → prompt 精准度提升可能减少 LLM 迭代；max_units=5 限制总调用量
- **[wikilink predicate 向后兼容]** 旧文件只有 `[[target]]` 无 predicate → 解析器 predicate=NULL 兼容；邻接表 predicate 列允许 NULL
- **[move+retarget 性能]** 全工作空间扫描重写 wikilink 在大量文件时可能慢 → 限制 retarget 到同 bucket 目录；或用 BM25 索引反查包含 `[[old_path]]` 的文件
- **[topics LLM 依赖]** LLM 不可用时 topics 无法生成 → fallback 到纯规则取前 N 个 fresh

## Open Questions

- auto_memory 的 LLM 调用是否需要支持取消？当前 `asyncio.to_thread` 不可取消 → 考虑改为 `asyncio.create_task` + timeout
- node_search 是否需要 agent_id 过滤？当前设计为无过滤（dream 需要跨 agent 召回），但搜索结果可能泄露其他 agent 的记忆 → dream 是后台 pipeline，不直接暴露给用户，风险可接受
- move+retarget 是否需要 API 审批？当前设计为直接执行 → 可考虑在前端 UI 加确认弹窗
