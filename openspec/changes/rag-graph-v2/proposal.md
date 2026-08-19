## Why

AChat 图谱系统已实现 PPR（GDS + APOC 降级）、entity/triple Milvus 向量召回、graph RRF post-fusion。但 Fidi-Intelli 的图谱检索还有一个关键能力：**seed weighting**——PPR 种子节点不是等权重的，而是按实体类型、出现频率、与查询的相关度加权。当前 AChat 的 PPR 种子节点等权重（`sourceNodes: allSeeds`），导致高频通用实体（如"中国"、"系统"）与低频专业实体（如"React Fiber"）同等竞争，稀释了专业实体的 PPR 分数。

此外，Fidi-Intelli 的 triple 向量召回结果会注入 PPR 作为补充种子（当前 AChat 只用 entity 召回做种子，triple 召回仅作为独立路径）。

用户明确要求"图谱系统也需要完全一模一样"，但"图谱配置锁定不做"。

## What Changes

- **PPR seed weighting**：`KGStore.search_with_ppr` 中 GDS `pageRank.stream` 的 `sourceNodeWeights` 参数从等权重改为加权（按 entity 类型权重 × Milvus 向量召回 score）
- **Triple 召回注入 PPR 种子**：`GraphRetrieval.search` 中 triple 向量召回的结果（subject + object 实体名）合并到 PPR 种子列表，与 entity 召回结果一起去重 + 加权
- **`RetrievalConfig` 图谱参数透传**：`graph_seed_weight_by_type: bool = True`、`graph_seed_weight_by_score: bool = True` 控制种子加权策略
- **Entity 类型权重映射**：`Concept` / `Product` 权重高于 `Location` / `Person`（专业实体 > 通用实体）

## Capabilities

### New Capabilities

- `rag-graph-v2`: 图谱检索增强——PPR seed weighting + triple 召回注入种子

### Modified Capabilities

- 无 DB schema 变更（纯算法层改动）

## Impact

- **修改文件**: `backend/app/graph/kgstore.py`（`_ppr_via_gds` 使用 `sourceNodeWeights`）、`backend/app/rag/graph_retrieval.py`（triple 召回结果注入种子 + 种子加权）、`backend/app/infra/hybrid.py`（`RetrievalConfig` 新增图谱种子权重参数）
- **无 DB schema 变更**
- **无新依赖**
- **图谱配置锁定不做**：不新增 `GraphBuildConfig` 持久化，不新增图谱构建参数配置项（用户已明确）
