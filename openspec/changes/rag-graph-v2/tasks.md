## 1. RetrievalConfig 图谱种子参数

- [x] 1.1 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `graph_seed_weight_by_type: bool = True`
- [x] 1.2 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `graph_seed_weight_by_score: bool = True`
- [x] 1.3 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `graph_triple_inject_seeds: bool = True`
- [x] 1.4 `backend/app/infra/hybrid.py`: `RetrievalConfig` 新增 `graph_entity_type_weights: dict[str, float] | None = None`（自定义类型权重覆盖默认值）
- [x] 1.5 `backend/app/infra/hybrid.py`: `_fetch_kg()` 透传 `RetrievalConfig` 到 `GraphRetrieval.search()`

## 2. Entity 类型权重映射

- [x] 2.1 `backend/app/graph/types.py`: 新增 `_ENTITY_TYPE_WEIGHTS` 字典常量（`Concept: 1.5`, `Product: 1.4`, `Event: 1.3`, `Organization: 1.0`, `Person: 0.8`, `Location: 0.7`, `Unknown: 1.0`）
- [x] 2.2 `backend/app/graph/types.py`: 新增 `get_entity_type_weight(entity_type: str, overrides: dict | None = None) -> float` 函数

## 3. KGStore PPR seed weighting

- [x] 3.1 `backend/app/graph/kgstore.py`: `search_with_ppr()` 方法签名新增 `seed_weights: list[float] | None = None` 和 `weight_by_type: bool = True` 参数
- [x] 3.2 `backend/app/graph/kgstore.py`: `_ppr_via_gds()` 使用 `sourceNodeWeights` 参数（GDS >= 2.0 支持），传入 `seed_weights`；GDS 报错不支持时降级为等权重
- [x] 3.3 `backend/app/graph/kgstore.py`: `_ppr_via_apoc()` 手动 scoring 改为 `sum(seed_weight * type_weight)` 代替 `len(seeds) * 0.6`
- [x] 3.4 `backend/app/graph/kgstore.py`: `search_with_ppr()` 接受 `entity_types: list[str] | None = None` 参数（种子实体类型列表，用于查类型权重）

## 4. GraphRetrieval triple 注入种子

- [x] 4.1 `backend/app/rag/graph_retrieval.py`: 新增 `async def _milvus_triple_recall(cls, query, top_k) -> list[dict]` 方法，调用 `MilvusGraphVectorStore.search_triples()`
- [x] 4.2 `backend/app/rag/graph_retrieval.py`: `search()` 方法新增 `retrieval_config: RetrievalConfig | None = None` 参数
- [x] 4.3 `backend/app/rag/graph_retrieval.py`: `search()` 中先做 entity 召回 + triple 召回，如果 `graph_triple_inject_seeds=True` 则合并 triple 的 subject/object 到种子列表
- [x] 4.4 `backend/app/rag/graph_retrieval.py`: 合并去重时保留召回 score（entity 召回 score 和 triple 召回 score 取 max）
- [x] 4.5 `backend/app/rag/graph_retrieval.py`: `_ppr_search()` 改为 `_ppr_search_weighted()`，接受 `seeds: list[SeedInfo]` 参数（`SeedInfo` 包含 `name`、`entity_type`、`recall_score`）
- [x] 4.6 `backend/app/rag/graph_retrieval.py`: 构建 `seed_weights` = `type_weight * recall_score`（如果 `weight_by_type=False` 则只用 `recall_score`；如果 `weight_by_score=False` 则只用 `type_weight`）

## 5. HybridStore 接入

- [x] 5.1 `backend/app/infra/hybrid.py`: `_fetch_kg()` 调用 `GraphRetrieval.search()` 时透传 `retrieval_config`
- [x] 5.2 `backend/app/infra/hybrid.py`: `_search_hybrid()` 中 graph 路径使用 `retrieval_config` 的种子权重参数

## 6. 验证

- [x] 6.1 `ruff check .` 通过
- [x] 6.2 `pytest` 通过
- [x] 6.3 手动测试：GDS 可用时 PPR 使用 `sourceNodeWeights`，种子按类型 × score 加权
- [x] 6.4 手动测试：GDS 不可用时 APOC 降级路径也按类型加权 scoring
- [x] 6.5 手动测试：triple 召回结果注入 PPR 种子后，检索结果覆盖更全
- [x] 6.6 手动测试：`graph_seed_weight_by_type=False` 时种子等权重（与改动前行为一致）
- [x] 6.7 手动测试：`graph_triple_inject_seeds=False` 时只用 entity 召回做种子（与改动前行为一致）
