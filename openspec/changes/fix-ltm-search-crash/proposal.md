## Why

沉淀板块「长期记忆」一点搜索就白屏：`long-term-memory-panel.tsx` 在 `searchMode=true` 时渲染 lucide `<X />` 图标，但未从 `lucide-react` 导入，触发 `ReferenceError: X is not defined`。修完崩溃后，搜索结果的 `path` 仍是索引里的绝对路径，与列表 API 的相对路径契约不一致，点开详情会失败。

## What Changes

- 修复前端：`long-term-memory-panel.tsx` 补齐 `X` 图标 import（清除搜索 + 编辑返回列表共用）
- 统一搜索结果 `path` 为相对 workspace root 的路径（与 `GET /api/memory/files` 一致），保证 `GET /api/memory/files/{path}` 可打开
- 索引写入与 HybridSearch 结果输出都走相对路径，避免 Windows 绝对路径拼接问题
- 补 API / 前端相关回归点（搜索不崩、结果可打开）
- **不改** bucket 筛选 UI、auto_memory / auto_dream pipeline 语义、Preference、session memory
- **暂不改** search API 的 `bucket` 参数断链、daily 文件在 BM25 索引里的 bucket 语义（列为后续）

## Capabilities

### New Capabilities

- `memory-file-search`: 记忆文件搜索 API 与前端搜索模式的契约（不崩溃、path 相对化、结果可打开详情）

### Modified Capabilities

- （无）主 specs 尚未落地独立 memory search capability；本次以新 capability 固化搜索契约

## Impact

- **前端**: `src/components/settings/memory-management/long-term-memory-panel.tsx` — lucide `X` import
- **索引**: `backend/app/memory/pipeline/auto_index.py` — BM25 / wikilink 写入相对 path
- **搜索**: `backend/app/memory/search/hybrid_search.py`（及必要时 API 映射层）— 返回相对 path
- **API**: `GET /api/memory/search` 响应 `items[].path` 语义对齐列表接口
- **测试**: `backend/tests/test_api_memory.py`（或新增 search 用例）
- **非影响**: 列表 bucket 过滤（已由 `fix-ltm-bucket-filter` 处理）、Preference、session memory、读写删语义（除 path 形态）
