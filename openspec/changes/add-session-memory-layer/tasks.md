## 阶段 1 — 数据模型

- [x] 1.1 `backend/app/db/models.py` `ContextSummary`：新增 `summary_type` 列（`String(16)`, default `'compaction'`），区分 `'session'` / `'compaction'`；新增 `covers_up_to` 列（`Float`, nullable）记录 Session Memory 覆盖到的最后消息 `created_at`
- [x] 1.2 Migration 脚本：`ALTER TABLE context_summaries ADD COLUMN summary_type VARCHAR(16) NOT NULL DEFAULT 'compaction'; ADD COLUMN covers_up_to FLOAT NULL;`；存量行自动 `'compaction'`、`covers_up_to=NULL`
- [x] 1.3 验证：migration 后存量行 summary_type='compaction'、covers_up_to=NULL，无数据丢失

## 阶段 2 — SessionMemory 模块

- [x] 2.1 新增 `backend/app/memory/session_memory.py`：`SessionMemory` 类，负责增量摘要提取与持久化
- [x] 2.2 `SessionMemory.should_extract()`：实现 token 阈值 + 工具调用次数双重触发 + 自然断点检测
- [x] 2.3 `SessionMemory.extract()`：异步调 `_generate_fn`，增量拼接（已有摘要 + 增量消息 → 新摘要）；更新 `covers_up_to` 为最后一条被摘要消息的 `created_at`
- [x] 2.4 `SessionMemory.get()`：读取当前会话的 Session Memory 记录（含 `summary` 和 `covers_up_to`）
- [x] 2.5 `SessionMemory` 降级：`_generate_fn` 不可用时静默跳过
- [x] 2.6 单元测试：`test_session_memory.py` 覆盖初始化、增量更新、covers_up_to 推进、降级、自然断点检测

## 阶段 3 — 触发集成

- [x] 3.1 `backend/app/memory/memory_service.py`：在 `on_message_end` 后检查 `SessionMemory.should_extract()`，触发 `asyncio.create_task`
- [x] 3.2 `backend/app/memory/memory_service.py`：注入 `SessionMemory` 实例，接入 `_generate_fn`
- [x] 3.3 `backend/app/main.py`：lifespan 中初始化 `SessionMemory`，注入到 `MemoryService`
- [x] 3.4 验证：真实对话达到阈值后，Session Memory 记录被创建/更新，`covers_up_to` 正确推进

## 阶段 4 — Compaction 三路复用 Session Memory

- [x] 4.1 `backend/app/services/context_compaction_service.py` `compact_conversation`：加载 Session Memory 记录（`summary_type='session'`）
- [x] 4.2 情况 1（完全覆盖）：`session_mem.covers_up_to >= to_compact[-1].created_at` → 直接用 `session_mem.summary`，跳过 LLM
- [x] 4.3 情况 2（部分覆盖）：提取缺口消息（`created_at > covers_up_to`），缺口 transcript + Session Memory 摘要调 `_summarise()`
- [x] 4.4 情况 3（无 Session Memory）：回退原路径（全量 transcript 调 `_summarise()`），行为不变
- [x] 4.5 验证：三种情况各自的 LLM 调用次数和输入大小符合预期

## 阶段 5 — 断点保护

- [x] 5.1 `context_compaction_service.py`：新增 `_find_safe_cut_point()` 函数
- [x] 5.2 `_is_orphan_tool_result()`：检测裁切位置是否产生孤立 tool_result
- [x] 5.3 `_is_pending_tool_use()`：检测裁切位置是否留下待处理 tool_use
- [x] 5.4 修改消息选择逻辑：用 `_find_safe_cut_point()` 替代固定 `KEEP_RECENT_MESSAGES` 裁切
- [x] 5.5 单元测试：tool_use/tool_result 链跨裁切边界的场景

## 阶段 6 — 能力复灌

- [x] 6.1 `context_compaction_service.py`：Compaction 完成后注入能力上下文 system-reminder
- [x] 6.2 收集当前工具列表（从 tool_registry）、附件列表、dispatch plan 状态
- [x] 6.3 格式化为 system-reminder 块，追加到 Summary 消息后
- [x] 6.4 验证：Compaction 后模型能感知当前可用工具

## 阶段 7 — 回归

- [x] 7.1 `cd backend && ruff check .` 无新增错误
- [x] 7.2 `cd backend && pytest` memory + compaction 相关用例全绿
- [x] 7.3 端到端验证：长对话场景下 Session Memory 增量更新 → Tier 2 触发时三路分支正确选择 → 断点无 API 报错 → 能力复灌生效
- [x] 7.4 端到端验证：Tier 1（`_mid_run_compact`）行为不受影响——纯内存裁剪与 Session Memory 无交集
