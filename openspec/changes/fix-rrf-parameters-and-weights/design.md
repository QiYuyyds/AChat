## Context

AChat 的 RAG 混合检索使用加权 RRF（Reciprocal Rank Fusion）融合三路结果：Milvus 语义、ES BM25、Neo4j KG。当前实现存在三类问题：

1. **权重推导 bug**：`config.py` 默认 `rag_semantic_weight=0.7` + `kg_weight=0.3` = 1.0，导致 keyword weight = `1.0 - 0.7 - 0.3 = 0.0`，BM25 路径在默认配置下完全无效
2. **k 值偏保守**：默认 `rag_rrf_constant_k=60`（来自 Cormack 2009 原论文），但 CRUD-RAG 评测数据显示 k=10 效果更好（recall@5 0.8667 vs 0.7492）
3. **KGStore 冗余加权**：`kgstore.py` 在打分时 `score *= kg_weight`，但 RRF 只使用 rank 不使用 score 值，乘以同一常数不改变排序顺序；当 kg_weight=0 时反而使所有分数打平导致任意排序

评测数据（`eval/results/comparison_report.md`）已证明最优配置为 `semantic_weight=0.5, kg_weight=0.0, k=10`，但结论未回流到代码默认值和 spec。

## Goals / Non-Goals

**Goals:**
- 修复 config 默认值，使 BM25 在默认配置下有非零权重
- 新增显式 `rag_keyword_weight` 配置参数，消除隐式推导出错的可能
- 移除 kgstore.py 中对 RRF 无实际作用的 `*= kg_weight` 冗余
- 修正 spec 中记录的错误权重值
- 选择 k=30 作为折中默认值（介于学术默认 60 和评测最优 10 之间）

**Non-Goals:**
- 不实现场景级参数动态调优（查询分类 + 参数 profile 注入）——这是后续增强
- 不改变 RRF 公式本身（加权 RRF `Σ w_i / (k + rank_i + 1)` 不变）
- 不改变多查询 RRF 融合逻辑（`search_multi` 中等权 RRF 不变）
- 不改变降级逻辑（路径不可用时归一化剩余权重不变）
- 不新增数据库表或 API 端点
- 不修改 `migration-plan-kgstore` 的端到端验证任务（5.1-5.6）

## Decisions

### 决策 1：k 默认值选 30 而非评测最优的 10

**选择**：`rag_rrf_constant_k = 30`

**理由**：
- CRUD-RAG 数据集是中文新闻 QA，不能代表所有场景。k=10 在该数据集上最优，但代码检索、技术文档等场景可能需要更平滑的排名衰减
- k=30 是保守折中：比 k=60 更有头部区分度，又比 k=10 更稳定
- 用户可通过 `.env` 覆盖为任意值

**替代方案**：k=10（评测最优但过拟合单一数据集）；k=60（原论文值但过于保守）

### 决策 2：新增 `rag_keyword_weight` 显式参数

**选择**：在 `config.py` 新增 `rag_keyword_weight: float = 0.5`，`hybrid.py` 直接读取三个显式参数，不再用 `1.0 - sem - kg` 隐式推导

**理由**：
- 隐式推导 `raw_kw = 1.0 - raw_sem - kg_weight` 在 `sem + kg > 1.0` 时产生 0 或被 `max(0.0, ...)` 截断为 0，用户无法直观看出 BM25 被禁用
- 显式三参数让配置自解释：`RAG_SEMANTIC_WEIGHT=0.5, RAG_KEYWORD_WEIGHT=0.5, KG_WEIGHT=0.0` 一目了然
- 归一化逻辑不变：仍按可用路径的权重和归一化到 1.0

**替代方案**：保持隐式推导但修复默认值——不够直观，且未来仍可能因配置不当导致权重为 0

### 决策 3：移除 kgstore.py 中的 `*= kg_weight`

**选择**：删除 `kgstore.py` 第 212 行 `score *= self.kg_weight` 和第 249 行 `"score": self.kg_weight`

**理由**：
- RRF 融合公式 `kg_w / (k + rank + 1)` 只使用 KGStore 返回结果的排列顺序（rank），不读取 score 值
- 乘以同一正常数不改变排序（A > B ⟹ cA > cB 当 c > 0），对 RRF 结果无影响
- 当 kg_weight=0 时，所有 score 变为 0，`sort()` 退化为任意排序，反而有害
- 权重控制应单一职责：只在 RRF 归一化中生效，不泄漏到打分逻辑

**替代方案**：保留但加注释说明无实际作用——不够干净

### 决策 4：kg_weight 默认设为 0.0

**选择**：`kg_weight = 0.0`

**理由**：
- `migration-plan-kgstore` 的端到端验证（tasks 5.1-5.6）从未执行，KG 路径在实际运行中从未被验证
- KG 路径需要 Neo4j 运行 + APOC 插件 + LLM API Key，默认环境下不可用
- kg_weight=0 时即使 KG 路径不可用也不会影响归一化（raw_kg=0 不参与 available 计算）
- 用户验证 KG 路径可用后可手动设为 0.2-0.3

## Risks / Trade-offs

- **[k=30 非评测最优值]** → 评测最优为 k=10，k=30 可能不如 k=10 在新闻 QA 场景好。缓解：用户可通过 `.env` 调整；后续场景级参数调优可自动选值
- **[显式三参数可能和不为 1]** → 用户可能配置 `sem=0.5, kw=0.5, kg=0.3`（和为 1.3）。缓解：归一化逻辑会将可用路径权重归一到 1.0，不影响正确性；但语义上不直观。可后续增加启动时校验日志
- **[kg_weight=0 使 KG 路径默认不参与]** → 已写完 KG 代码但默认不启用。缓解：这与 `migration-plan-kgstore` 的验证状态一致（端到端验证未完成）；用户显式配置后可启用
- **[spec 修正可能影响其他引用]** → `migration-plan-path-b` 的 delta spec 被其他文档引用。缓解：只改权重数值，不改 spec 结构
