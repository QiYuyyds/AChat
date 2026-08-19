## Context

AChat 图谱系统当前架构（已由 `rag-graph-build-task` 提案实现）：

```
Query → GraphRetrieval.search()
  ├── MilvusGraphVectorStore.search_entities(query) → entity names
  └── KGStore.search_with_ppr(entity_names)
        ├── GDS pageRank.stream(sourceNodes=allSeeds)  ← 等权重种子
        └── APOC subgraphNodes + manual scoring       ← 降级
```

### Fidi-Intelli 的图谱检索差异

1. **Seed weighting**：Fidi-Intelli 的 PPR 种子节点带权重（`sourceNodeWeights`），权重来源：
   - Milvus 向量召回 score（召回 score 越高，种子权重越大）
   - Entity 类型权重（`Concept` / `Product` > `Person` / `Location`，专业实体 > 通用实体）
   
2. **Triple 召回注入种子**：Fidi-Intelli 的 triple 向量召回结果会解析出 subject + object 实体名，合并到 PPR 种子列表。当前 AChat 只用 entity 向量召回做种子，triple 召回仅作为独立路径（如果有的话）。

3. **图谱配置锁定不做**：用户已明确不需要 `GraphBuildConfig` 持久化。

### 当前 AChat PPR 实现

```python
# kgstore.py _ppr_via_gds()
CALL gds.pageRank.stream('entityGraph', {
  maxIterations: $maxIter,
  dampingFactor: 0.85,
  sourceNodes: allSeeds  # ← 等权重，无 sourceNodeWeights
})
```

APOC 降级路径的手动 scoring：
```python
score = len(seeds) * 0.6 + degree * 0.01  # ← 种子数 × 固定权重，无类型/向量 score 差异
```

## Goals / Non-Goals

**Goals:**
- PPR 种子节点按类型 × 向量召回 score 加权
- Triple 向量召回结果注入 PPR 种子列表
- `RetrievalConfig` 新增种子加权控制参数
- APOC 降级路径的手动 scoring 也支持类型加权

**Non-Goals:**
- 不做 `GraphBuildConfig` 持久化（图谱配置锁定不做）
- 不修改图谱构建流程（`GraphBuildTask` 不变）
- 不修改 MilvusGraphVectorStore schema
- 不修改 entity / triple 的抽取逻辑
- 不修改前端 UI

## Decisions

### Decision 1: 种子权重 = 类型权重 × 向量召回 score

**Choice**: PPR 种子权重 = `entity_type_weight(entity.type) * milvus_recall_score(entity)`。

类型权重映射：
```python
_ENTITY_TYPE_WEIGHTS = {
    "Concept": 1.5,   # 概念/技术 → 专业性强
    "Product": 1.4,   # 产品/工具 → 专业性强
    "Event": 1.3,      # 事件 → 中等专业
    "Organization": 1.0,  # 组织 → 通用
    "Person": 0.8,     # 人物 → 通用
    "Location": 0.7,   # 地点 → 最通用
    "Unknown": 1.0,    # 默认
}
```

**Rationale**: Fidi-Intelli 的做法是专业实体（Concept/Product）权重高，通用实体（Location/Person）权重低，避免高频通用实体稀释 PPR。向量召回 score 越高说明与查询越相关，应给更高权重。

**Alternative considered**: 只用类型权重，不用向量 score。否决——同类型的不同实体与查询的相关度不同，需要 score 区分。

### Decision 2: GDS `sourceNodeWeights` 参数

**Choice**: GDS `pageRank.stream` 使用 `sourceNodeWeights` 而非 `sourceNodes`：

```cypher
CALL gds.pageRank.stream('entityGraph', {
  maxIterations: $maxIter,
  dampingFactor: 0.85,
  sourceNodes: $seedNodeIds,
  sourceNodeWeights: $seedWeights  # 新增
})
```

`seedNodeIds` 和 `seedWeights` 是平行数组，一一对应。

**Rationale**: GDS pageRank 支持 `sourceNodeWeights` 参数（GDS >= 2.0），用于给不同种子节点不同初始权重。

**Alternative considered**: 在 `scalerFactor` 中间接调权。否决——`sourceNodeWeights` 更直接。

### Decision 3: Triple 召回注入种子

**Choice**: `GraphRetrieval.search` 中，triple 向量召回结果解析出 `subject` + `object` 实体名，与 entity 召回结果合并去重后作为 PPR 种子。

```python
# 1. entity 召回
entity_names = await cls._milvus_entity_recall(query, top_k)
# 2. triple 召回（新增）
triple_hits = await cls._milvus_triple_recall(query, top_k)
triple_entity_names = _extract_subjects_objects(triple_hits)
# 3. 合并去重 + 加权
all_seeds = _merge_seeds(entity_names, triple_entity_names, entity_scores, triple_scores)
# 4. PPR with weighted seeds
return await cls._ppr_search_weighted(all_seeds, top_k, expand_depth)
```

**Rationale**: Fidi-Intelli 的 triple 召回结果不仅作为独立检索路径，还注入 PPR 作为种子补充。triple 的 subject/object 可能是 entity 召回遗漏的实体。

**Alternative considered**: triple 召回只作为独立路径不注入种子。否决——用户要求"完全一模一样"。

### Decision 4: APOC 降级路径也支持类型加权

**Choice**: APOC 手动 scoring 从 `len(seeds) * 0.6 + degree * 0.01` 改为 `sum(seed_weight * type_weight for each seed) + degree * 0.01`。

```python
# 旧：score = len(seeds) * 0.6 + degree * 0.01
# 新：
type_weighted = sum(
    _ENTITY_TYPE_WEIGHTS.get(seed.type, 1.0) * seed.recall_score
    for seed in seeds_info
)
score = type_weighted * 0.6 + degree * 0.01
```

**Rationale**: GDS 不可用时降级路径也要保持类型加权一致性，否则降级前后效果差异大。

### Decision 5: `RetrievalConfig` 控制参数

**Choice**: `RetrievalConfig` 新增：
- `graph_seed_weight_by_type: bool = True` — 是否按类型加权
- `graph_seed_weight_by_score: bool = True` — 是否按向量召回 score 加权
- `graph_triple_inject_seeds: bool = True` — 是否将 triple 召回注入种子

**Rationale**: 给调用方关闭加权的能力（调试 / A/B 测试）。默认全开。

**Alternative considered**: 只用全局配置项不用 RetrievalConfig 参数。否决——检索时动态控制比全局配置灵活。

## Risks / Trade-offs

- **[Risk] GDS 不支持 `sourceNodeWeights`** → 部分 GDS 版本可能不支持，降级为 `sourceNodes` 等权重（与当前行为一致）
- **[Risk] 种子列表过多导致 PPR 慢** → 合并去重后种子数通常 < 30，PPR 收敛快
- **[Risk] 类型权重不合理导致某些实体被低估** → 权重映射可配置（通过 RetrievalConfig 覆盖），默认值基于经验

## Migration Plan

无 DB schema 变更。纯算法层改动，不需要迁移脚本。

## Open Questions

无——所有决策点已在讨论中确认。图谱配置锁定不做（用户已明确）。
