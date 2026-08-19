## rag-graph-v2

### PPR seed weighting

- Seeds are no longer equally weighted. Each seed's weight = `entity_type_weight(entity.type) × milvus_recall_score(entity)`.
- Entity type weight map (defaults, overridable via `RetrievalConfig.graph_entity_type_weights`):

| Entity Type | Weight | Rationale |
|---|---|---|
| Concept | 1.5 | Most domain-specific |
| Product | 1.4 | Domain-specific |
| Event | 1.3 | Moderately specific |
| Organization | 1.0 | Generic |
| Person | 0.8 | Often high-frequency noise |
| Location | 0.7 | Most generic |
| Unknown | 1.0 | Default |

- GDS `pageRank.stream` uses `sourceNodeWeights` parameter (parallel array to `sourceNodes`).
- APOC fallback scoring: `sum(seed_weight × type_weight) + degree × 0.01` (replaces `len(seeds) × 0.6 + degree × 0.01`).

### Triple recall injects seeds

- `GraphRetrieval.search()` now performs both entity and triple Milvus vector recall.
- Triple recall results are parsed for subject + object entity names.
- These are merged with entity recall results (deduplication, keeping max recall score).
- Controlled by `RetrievalConfig.graph_triple_inject_seeds` (default `True`).

### RetrievalConfig new parameters

| Parameter | Type | Default | Purpose |
|---|---|---|---|
| `graph_seed_weight_by_type` | `bool` | `True` | Weight seeds by entity type |
| `graph_seed_weight_by_score` | `bool` | `True` | Weight seeds by Milvus recall score |
| `graph_triple_inject_seeds` | `bool` | `True` | Inject triple recall into PPR seeds |
| `graph_entity_type_weights` | `dict[str, float] \| None` | `None` | Override default type weights |

### GraphBuildConfig — NOT implemented

User explicitly decided: graph build config is not persisted. No `GraphBuildConfig` dataclass, no `Document.graph_config` column. Task parameters for graph build live in `RagTask.payload` (from `rag-task-queue` proposal).
