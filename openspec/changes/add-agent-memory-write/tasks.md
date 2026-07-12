## 阶段 1 — 数据模型

- [x] 1.1 `backend/app/db/models.py` `Agent`：新增 `memory_enabled` 列（`Boolean`, default `False`）
- [x] 1.2 Migration 脚本：`ALTER TABLE agents ADD COLUMN memory_enabled BOOLEAN DEFAULT FALSE`
- [x] 1.3 验证：migration 后存量 Agent memory_enabled=false

## 阶段 2 — memory_store 工具

- [x] 2.1 新增 `backend/app/tools/memory_store.py`：`memory_store_tool` ToolDef + `memory_store_handler`
- [x] 2.2 handler 实现：category 白名单校验、importance 下限校验、content 长度校验（1-500 字符）
- [x] 2.3 handler 实现：调 `ltm.store_classified()` 走 cosine dedup 路径
- [x] 2.4 handler 实现：返回 `{"stored": bool, "agent_memory_count": int}`
- [x] 2.5 `backend/app/tools/registry.py`：注册 `memory_store_tool`
- [x] 2.6 单元测试：`test_memory_store.py` 覆盖正常写入、category 拒绝、importance 拒绝、dedup 命中

## 阶段 3 — 限流器

- [x] 3.1 新增 `backend/app/tools/rate_limiter.py`：`SimpleRateLimiter` 类（内存 dict + TTL + asyncio.Lock）
- [x] 3.2 `memory_store_handler`：注入 `SimpleRateLimiter`，按 `(agent_id, run_id)` 限流 max 3/run
- [x] 3.3 单元测试：连续调用 4 次，第 4 次返回 rate limit 错误

## 阶段 4 — Agent 定义注入

- [x] 4.1 `backend/app/services/agent_runner.py` `build_adapter_input`：读取 `agent.memory_enabled`
- [x] 4.2 `memory_enabled=true` 且 `is_sdk`（Custom Agent）：在 `tool_names` 中添加 `memory_store` + `memory_recall`
- [x] 4.3 CLI Agent（`is_cli`）：即使 `memory_enabled=true` 也不注入
- [x] 4.4 验证：Custom Agent with `memory_enabled=true` 的 tool list 包含 `memory_store`；`false` 的不包含

## 阶段 5 — 与 agent-scoped-memory 集成

- [x] 5.1 若 `add-agent-scoped-memory` 已实施：`memory_store_handler` 写入 `scope='agent', agent_id=ctx.agent_id`
- [x] 5.2 若未实施：写入 `scope='global', agent_id=NULL`（向后兼容）
- [x] 5.3 `memory_store_handler` 从 `ctx` 获取 `agent_id` 和 `run_id`

## 阶段 6 — 回归

- [x] 6.1 `cd backend && ruff check .` 无新增错误
- [x] 6.2 `cd backend && pytest` 新增 memory_store 用例全绿；已有用例不受影响
- [x] 6.3 端到端验证：Agent 调用 `memory_store` 写入 → recall 能召回 → 限流生效 → dedup 正常
