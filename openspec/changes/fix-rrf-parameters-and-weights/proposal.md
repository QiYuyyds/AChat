## Why

RAG 混合检索的 RRF 融合参数存在三个问题：默认配置下 BM25 权重为零（`semantic_weight=0.7 + kg_weight=0.3 = 1.0`，导致 keyword_weight=0）、RRF k 值（60）过于保守与评测最优值（10-30）不符、`kgstore.py` 中 `score *= kg_weight` 对 RRF 排序无实际作用且当 kg_weight=0 时有害。评测数据已证明 `semantic_weight=0.5, kg_weight=0.0, k=10` 配置显著优于默认值（recall@5 从 0.7492 提升到 0.8667），但结论未回流到代码和 spec。

## What Changes

- **修复 `config.py` 默认值**：`rag_semantic_weight` 从 0.7 改为 0.5；`kg_weight` 从 0.3 改为 0.0；`rag_rrf_constant_k` 从 60 改为 30
- **新增显式 `rag_keyword_weight` 配置参数**：替代当前隐式推导 `1.0 - sem - kg`，消除权重和不为 1 时出错的可能
- **移除 `kgstore.py` 中 `score *= kg_weight` 冗余**：RRF 只用 rank 不用 score，乘以同一常数不改变排序；kg_weight=0 时反而使所有分数打平导致任意排序
- **更新 `.env.example`**：同步修正后的默认值和新增 `RAG_KEYWORD_WEIGHT` 参数
- **更新 spec 中的错误权重**：`migration-plan-path-b` delta spec 中记录的 `semantic_weight=0.7, keyword_weight=0.3, kg_weight=0.3` 与实际代码行为不符，修正为正确值
- **更新 `eval/README.md`**：同步评测推荐配置（k=30 替代 k=60 的注释）

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `rag-system`：RRF 融合权重默认值修正（semantic 0.7→0.5, kg 0.3→0.0, k 60→30）；keyword weight 从隐式推导改为显式配置参数；KGStore 打分公式移除冗余的 `*= kg_weight`

## Impact

- **修改文件**：
  - `backend/app/config.py`：3 个默认值修改 + 1 个新增字段
  - `backend/app/infra/hybrid.py`：权重读取逻辑从隐式推导改为显式三参数
  - `backend/app/graph/kgstore.py`：移除第 212 行 `score *= self.kg_weight` 和第 249 行 `"score": self.kg_weight`
  - `backend/.env.example`：3 个值修改 + 1 个新增行
  - `openspec/changes/migration-plan-path-b/specs/rag-system/spec.md`：修正第 42 行权重值
  - `eval/README.md`：更新推荐配置注释
- **兼容性**：无 breaking change。已有 `.env.local` 中显式配置了权重的用户不受影响（配置文件优先级高于默认值）。未显式配置权重的用户将获得更优的默认参数。
- **测试**：`backend/tests/test_rag_hybrid.py` 中 mock 的默认值需同步更新
- **基础设施依赖**：无变化
