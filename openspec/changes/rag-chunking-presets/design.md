## Context

现有 `RAGEngine.ingest()` 内部使用 `RecursiveSplitter` 做父子分块（parent_splitter + child_splitter）。方案决策点 1 要求保留 RecursiveSplitter 作为 `general` preset 的底层实现，新增 preset 调度器。

目标场景（决策点 8）：个人知识库（笔记/日记/面经/中英论文），不做 `book` 和 `laws` preset。决策点 11 不做独立的 `academic` preset，论文由 `general` + `semantic` 覆盖。

## Goals / Non-Goals

**Goals:**
- 实现 4 种 preset：`general`（复用 RecursiveSplitter）、`qa`、`semantic`、`separator`
- 提供 `chunk_markdown(content, preset_id, config)` 统一调度入口
- `RAGEngine.ingest()` 接受 `preset_id` 参数

**Non-Goals:**
- 不实现 `book` 和 `laws` preset
- 不实现 `academic` preset
- 不修改 `RecursiveSplitter` 类本身
- 不修改前端 UI
- 不实现 preset 的运行时切换 API（preset 在 ingest 时确定，不可事后更改）

## Decisions

### Decision 1: general preset 委托给 RecursiveSplitter 而非重写

**Choice**: `general` preset 的 `chunk_markdown()` 内部调用 `RecursiveSplitter`，适配为 `list[str]` 返回。

**Rationale**: 方案决策点 1 要求保留 RecursiveSplitter 兼容。重写会引入回归风险且无收益。

**Alternative considered**: 将 RecursiveSplitter 逻辑直接复制到 general.py。否决——代码重复，后续 RecursiveSplitter 更新需要同步两处。

### Decision 2: qa preset 使用正则 + 启发式而非 LLM

**Choice**: `qa` preset 通过正则模式匹配问题-回答结构（如 `Q: ... A: ...`、`问：... 答：...`），非 QA 内容回退到 general 策略。

**Rationale**: 面经/FAQ 文档通常有明确的问题-回答格式标记，正则匹配即可。用 LLM 做结构抽取成本高且不稳定。

### Decision 3: semantic preset 需要 embed_fn 注入

**Choice**: `semantic` preset 在切分时需要调用 embedding 做句子聚类，`ChunkDispatcher` 在调用 semantic preset 前检查 embed_fn 是否可用，不可用时回退到 general。

**Rationale**: semantic 切分依赖嵌入向量做聚类，没有 embed_fn 无法工作。回退到 general 保证不阻断 ingest。

### Decision 4: 不做 preset 的运行时切换

**Choice**: preset 在文档 ingest 时确定，记录在 `Document.chunk_preset` 列中，不支持事后切换。

**Rationale**: 切换 preset 需要重新分块+重新嵌入+重新索引，代价高。如果用户想换 preset，应该重新上传文档。

## Risks / Trade-offs

- **[Risk] semantic preset 增加额外 embedding 调用成本** → 仅在用户显式选择 `semantic` preset 时触发；general preset 不受影响
- **[Risk] qa preset 的正则模式可能无法覆盖所有 QA 格式** → 非 QA 内容回退到 general 策略，不会丢内容
- **[Risk] separator preset 的分隔符配置不当导致切分过碎** → 配置项有合理默认值，用户可调整

## Open Questions

无。
