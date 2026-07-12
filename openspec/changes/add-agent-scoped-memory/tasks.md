## 阶段 1 — 数据模型与迁移

- [x] 1.1 `backend/app/db/models.py` `LongTermMemory`：新增 `scope` 列（`String(16)`, default `'global'`）和 `agent_id` 列（`String`, nullable）；新增索引 `idx_ltm_scope_agent` on `(scope, agent_id)`
- [x] 1.2 `backend/app/memory/consolidation.py` `Item`：新增 `scope: str = "global"` 和 `agent_id: str = ""` 字段
- [x] 1.3 `backend/app/memory/long_term.py` `load_from_storage`：加载 `scope` 和 `agent_id` 到 `Item`
- [x] 1.4 `backend/app/memory/long_term.py` `add` / `store_classified`：接收 `scope` 和 `agent_id` 参数，写入 PG
- [x] 1.5 Migration 脚本：`ALTER TABLE long_term_memory ADD COLUMN scope ...; ADD COLUMN agent_id ...; CREATE INDEX ...`；存量行自动 `scope='global'`
- [x] 1.6 验证：对测试 PG 跑 migration，确认存量行 scope='global'、agent_id=NULL，无数据丢失

## 阶段 2 — 写入路由

- [x] 2.1 `backend/app/memory/memory_writer.py` `extract_memory_from_reply`：接收 `agent_id` 参数，`store_classified` 时传入 `scope='agent', agent_id=agent_id`
- [x] 2.2 `backend/app/memory/memory_service.py` `on_message_end`：接收 `agent_id` 参数，透传给 `extract_memory_from_reply`
- [x] 2.3 `backend/app/memory/memory_service.py`：调用 `on_message_end` 的上游（`agent_runner.py` 或 `conversation_service.py`）传入当前 `agent_id`
- [x] 2.4 验证：Agent A 的对话产生的记忆写入 `scope='agent', agent_id=A`；Agent B 的写入 `scope='agent', agent_id=B`

## 阶段 3 — 召回过滤

- [x] 3.1 `backend/app/memory/long_term.py` `recall()`：新增 `agent_id` 参数；先查 agent-scoped items，不足时用 global items 补
- [x] 3.2 `backend/app/memory/long_term.py` `recall_by_filter()`：新增 `agent_id` 参数；过滤逻辑同上
- [x] 3.3 `backend/app/memory/long_term.py` `_graph_expand()`：只扩展同一 `(scope, agent_id)` 的图邻居
- [x] 3.4 `backend/app/services/prompt_assembler.py` `RecallSource`：从 context 获取 `agent_id`，传入 `recall()`
- [x] 3.5 `backend/app/memory/memory_service.py` `recall()`：接收并透传 `agent_id`
- [x] 3.6 验证：Agent A recall 只返回 A 的 agent-scoped 记忆 + global 记忆；不返回 Agent B 的

## 阶段 4 — Consolidation 适配

- [x] 4.1 `backend/app/memory/long_term.py` `consolidate()`：按 `(scope, agent_id)` 分组，组内做 dedup/merge/expire
- [x] 4.2 `backend/app/memory/long_term.py` `_sync_consolidation_to_db`：DELETE/UPDATE 不受影响（仍按 item.id）
- [x] 4.3 验证：Agent A 和 Agent B 各有相似记忆，consolidate 后不跨 Agent 合并

## 阶段 5 — GraphMemory 适配

- [x] 5.1 `backend/app/memory/graph_memory.py`：`add_to_graph` / `_upsert_memory_node` 写入 `scope` 和 `agent_id` 属性
- [x] 5.2 `backend/app/memory/graph_memory.py` `find_related()`：Cypher 查询加 `WHERE n.agent_id = $agent_id` 过滤
- [x] 5.3 `backend/app/memory/graph_memory.py` `bulk_index()`：按 scope 分组索引
- [x] 5.4 验证：Neo4j 可用时，graph 扩展不跨 Agent

## 阶段 6 — 回归

- [x] 6.1 `cd backend && ruff check .` 无新增错误
- [x] 6.2 `cd backend && pytest` memory 相关用例全绿；新增 scope 过滤的单元测试
- [x] 6.3 端到端验证：多 Agent 对话场景下，各 Agent recall 结果互不干扰
