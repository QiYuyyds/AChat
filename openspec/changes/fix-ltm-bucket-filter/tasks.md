## 1. API 过滤修复

- [x] 1.1 修改 `backend/app/api/memory.py` 的 `list_memory_files`：仅当 `bucket is None or bucket == "daily"` 时并入 `list_daily_files()`
- [x] 1.2 同函数：仅当 `bucket is None or bucket in ("procedure", "wiki")` 时枚举 digest（`bucket == "daily"` 或未知值时跳过 digest）
- [x] 1.3 确认未知 bucket 返回空 `items`（HTTP 200），不 fallback 全量

## 2. 测试

- [x] 2.1 在 `backend/tests/test_api_memory.py`（或等价测试文件）准备含 daily + procedure + wiki 样例文件的 fixture
- [x] 2.2 覆盖：`bucket` 省略 → 含 daily 与 digest
- [x] 2.3 覆盖：`bucket=procedure` → 仅 procedure，无 daily
- [x] 2.4 覆盖：`bucket=wiki` → 仅 wiki，无 daily
- [x] 2.5 覆盖：`bucket=daily` → 仅 daily，无 digest
- [x] 2.6 覆盖：未知 `bucket` → 空列表

## 3. 验证

- [x] 3.1 跑相关 pytest 全部通过
- [x] 3.2 `ruff check` 涉及文件无新增问题
- [x] 3.3 确认前端 `long-term-memory-panel.tsx` 无需改动（传参已正确）
