## 1. 后端 model_registry 改造

- [x] 1.1 在 `backend/app/utils/model_registry.py` 顶部新增常量 `EFFECTIVE_CONTEXT_CAP = 200_000`
- [x] 1.2 在 `ModelLimits` dataclass 中新增字段 `effective_context_window: int`
- [x] 1.3 修改 `get_model_limits()` 返回 `effective_context_window = min(context_window, EFFECTIVE_CONTEXT_CAP)`
- [x] 1.4 DeepSeek 全系列模型加 `outputReserve: 13_000`（deepseek-chat / v4-flash / v4 / v4-pro 新增；deepseek-reasoner / r1 从 16_384 改为 13_000）

## 2. 后端 agent_runner 改用 effective_context_window

- [x] 2.1 `history_budget` 计算改用 `limits.effective_context_window`（~第 3325 行）
- [x] 2.2 `build_history_for` 的 `model_context_limit` 参数改用 `limits.effective_context_window`（~第 3333 行）
- [x] 2.3 `_get_agent_model_limit()` 返回 `effective_context_window`（~第 386-398 行）

## 3. 前端 model-registry 同步

- [x] 3.1 在 `src/shared/model-registry.ts` 顶部新增常量 `EFFECTIVE_CONTEXT_CAP = 200_000`
- [x] 3.2 `ModelLimits` 接口新增字段 `effectiveContextWindow: number`
- [x] 3.3 `getModelLimits()` 返回 `effectiveContextWindow = min(context, EFFECTIVE_CONTEXT_CAP)`
- [x] 3.4 DeepSeek 全系列模型加 `outputReserve: 13_000`（同后端）

## 4. 前端 UsageBadge 改用有效窗口

- [x] 4.1 `src/components/usage-badge.tsx` 第 51 行 `limits.contextWindow` → `limits.effectiveContextWindow`

## 5. 测试更新

- [x] 5.1 `backend/tests/test_model_registry.py`：断言 DeepSeek `effective_context_window == 200_000`，断言 `outputReserve == 13_000`
- [x] 5.2 `src/shared/model-registry.test.ts`：同上同步断言
- [x] 5.3 后端 `ruff check .` 通过
- [x] 5.4 后端 `pytest` 通过
- [x] 5.5 前端 `pnpm typecheck` 通过
- [x] 5.6 前端 `pnpm lint` 通过
