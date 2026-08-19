## 1. Frontend crash fix

- [x] 1.1 在 `src/components/settings/memory-management/long-term-memory-panel.tsx` 的 lucide-react import 中加入 `X`
- [x] 1.2 手动确认：搜索模式「清除搜索」与编辑态「返回列表」均不抛 `X is not defined`（静态：import 已覆盖两处 JSX；运行时手验见 §4）

## 2. Index + search relative paths

- [x] 2.1 改 `AutoIndex`：索引写入 / 删除 / wikilink 边使用相对 workspace root 的 path；读盘时用 root 拼回绝对路径
- [x] 2.2 改 `HybridSearch`：构造时持有 workspace root；`read_markdown` 用 root 解析相对 path；结果 `path` 输出相对 path
- [x] 2.3 接线 `MemoryService._build_search`（或等价构造点）传入 workspace root
- [x] 2.4 （可选兜底）`GET /api/memory/search` 序列化时若 path 仍绝对且落在 workspace 下，再 `relative_to` 一次

## 3. Tests

- [x] 3.1 在 `backend/tests/test_api_memory.py`（或专用测试）seed 文件 → reindex → `GET /api/memory/search?query=...` 断言 200、path 相对、且 `GET /api/memory/files/{path}` 200
- [x] 3.2 跑相关 pytest 通过；前端改动做 typecheck/lint 抽查（`test_api_memory.py` 17 passed；hybrid_search/auto_index/memory_service ruff clean）

## 4. Verification

- [x] 4.1 本地打开沉淀 → 长期记忆 → 搜索关键词：不白屏、有结果时可点开详情（浏览器手验：搜 Python 出结果，点开详情 dialog 正常）
- [x] 4.2 确认清除搜索可回到列表（浏览器手验：清除搜索按钮可用，回到列表）
