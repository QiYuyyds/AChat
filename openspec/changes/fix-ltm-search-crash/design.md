## Context

长期记忆面板在 file-native 改写后，列表与搜索分两条路径：

- 列表：`GET /api/memory/files` → `workspace.list_*` + `relative_to(workspace.root)` → 相对 path
- 搜索：`GET /api/memory/search` → `MemoryService.recall` → `HybridSearch` → BM25 path（`str(filepath)` 绝对路径）

前端 `LongTermMemoryPanel` 在 Enter 搜索时设 `searchMode=true`，条件渲染「清除搜索」按钮使用 `<X />`，但 lucide import 漏了 `X`，导致一进搜索模式就 Runtime ReferenceError。编辑态「返回列表」同样依赖 `X`。

同目录 `preference-panel.tsx` 已正确导入 `X`，属于漏对齐。

## Goals / Non-Goals

**Goals:**

- 搜索模式与编辑态不再因缺失 `X` 崩溃
- 搜索结果 `path` 与列表结果一致：相对 memory workspace root（POSIX 风格 `/` 分隔更佳，至少与 `relative_to` 输出一致）
- 用搜索结果的 path 调用 `GET /api/memory/files/{path}` 可成功读到文件
- 新写入索引使用相对 path；启动 full reindex 后旧绝对 path 条目被清掉重建

**Non-Goals:**

- 不实现 search 的 `bucket` / `agent_id` 过滤贯通（API 参数已在、HybridSearch 支持，但 recall 未传 —— 后续 change）
- 不修正 daily 卡片在 BM25 索引中的 `bucket` 字段（frontmatter 默认 `wiki` vs 列表硬编码 `daily`）
- 不改搜索算法（BM25 + wikilink RRF）、不改 UI 布局
- 不处理 Enter 时 `void load()` 闭包仍走列表的时序抖动（非崩溃；可选顺手但不作为验收）

## Decisions

### D1. 前端只补 import，不重写搜索状态机

- **选择**: 在 `long-term-memory-panel.tsx` 的 lucide import 中加入 `X`
- **备选**: 去掉图标只用文字「清除搜索」—— 与 preference 面板不一致，拒绝
- **理由**: 最小修复，与现有 UI 一致

### D2. 相对 path 在索引写入点统一，而不是仅在 API 响应层 strip

- **选择**: `AutoIndex.index_file` 写入 BM25 / wikilink 时存 `path.relative_to(workspace.root)`（字符串化）；`HybridSearch` 读盘时用 `workspace.root / relative_path`（或 Path 拼接）
- **备选 A**: 仅在 `search_memory` API 把绝对 path 转相对 —— 索引里仍是绝对 path，换机器/换 DATA_DIR 后索引失效更隐蔽
- **备选 B**: 前端 `encodeURIComponent` 后自己 strip 前缀 —— 不可靠（盘符、不同 root）
- **理由**: 索引是 source of truth；full reindex 会重建；读盘必须能从相对 path 还原绝对路径

### D3. HybridSearch 读文件时解析相对 path

当前：

```python
mem_file = read_markdown(Path(path))
```

若 path 变为相对，需要 workspace root。两种做法：

1. `HybridSearch` 构造时注入 `workspace_root: Path`，`read_markdown(self.root / path)`
2. 索引继续存绝对 path，仅 API 输出时 relative —— 与 D2 冲突

采用 (1)：`HybridSearch(settings, bm25, expander, workspace_root=...)`，或从 `MemoryService` 构建时传入。

### D4. 兼容已存在的绝对 path 索引条目（过渡）

- **选择**: full reindex 已在 `MemoryService.initialize` 启动时执行；实现后重启即干净。搜索结果序列化时若 path 仍是绝对且落在 workspace 下，API 层再 `relative_to` 一次作为兜底
- **理由**: 开发机可能热重载未 full reindex；双保险便宜

### D5. 测试策略

- API：seed 文件 → reindex → `GET /api/memory/search?query=...` → 200，`path` 不以盘符/绝对 root 开头，且 `GET /api/memory/files/{path}` 200
- 前端：import 修复为类型/静态检查可覆盖；不强制加 RTL 用例（项目前端 memory 面板测试覆盖薄）

## Risks / Trade-offs

- **[Risk] HybridSearch 其它调用方未传 root** → Mitigation：仅 `MemoryService._build_search` 一处构造；单测覆盖
- **[Risk] Windows `relative_to` 产生 `\`** → Mitigation：序列化时 `.as_posix()` 或 `replace("\\", "/")`，与列表 API 对齐（列表目前直接 `str(relative_to)`，Windows 可能是 `\`；前端 `encodeURIComponent` 可处理；优先与列表现有行为一致，不强行改列表）
- **[Risk] 热更新未 reindex，旧绝对 path 仍在** → Mitigation：API 层 relative 兜底
- **[Trade-off] 不修 bucket 断链** → 用户先恢复可用搜索；过滤一致性留给后续

## Migration Plan

1. 部署含相对 path 的代码
2. 进程启动 `full_reindex` 自动重建索引
3. 无需手工迁移脚本；无 DB schema 变更
4. 回滚：恢复旧代码即可（索引会再被 full reindex 成绝对 path）

## Open Questions

- （无阻塞）是否顺手把列表 path 也强制 `.as_posix()` —— 本次不强制，保持与现网列表一致
