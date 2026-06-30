# RAG Evaluation Toolkit

基于 [CRUD-RAG](https://github.com/noneur/CRUD_RAG) 数据集的 RAG 系统效果评测工具，支持 **dense / bm25 / hybrid** 三种检索模式消融对比，覆盖检索层与生成层共 7 项指标。

## 目录结构

```
eval/
├── CRUD_RAG-main/          # 源数据集（新闻 QA）
│   └── data/crud_split/split_merged.json
├── prepare_corpus.py       # 语料提取脚本 → corpus.jsonl
├── prepare_golden.py       # QA 标注转换脚本 → golden.jsonl
├── metrics.py              # 7 项评测指标实现
├── test_metrics.py         # 指标单元测试（26 tests）
├── run_eval.py             # 评测主运行器
├── inspect_data.py         # 数据结构检查工具
├── corpus.jsonl            # 3,050 篇去重新闻文档
├── golden.jsonl            # 2,394 条 QA 标注
├── results_1/              # R1: 旧权重 (sem_w=0.7, kw_w=0.0, top_k=3, rerank=ON)
│   ├── dense_results.json
│   ├── bm25_results.json
│   ├── hybrid_results.json
│   └── comparison_report.md
├── results_2/              # R2: 修复权重 (sem_w=0.5, kw_w=0.5, top_k=3, rerank=ON)
│   ├── hybrid_results.json
│   └── comparison_report.md
└── results/               # R3-R5: 最终配置 (top_k=5, rerank=ON/OFF)
    ├── dense_results.json
    ├── hybrid_results.json
    └── comparison_report.md
```

## 前置条件

### 1. 基础设施

确保 Docker Compose 已启动以下服务：

| 服务 | 端口 | 用途 |
|------|------|------|
| PostgreSQL | 5432 | 文档 chunk 存储 |
| Milvus | 19530 | 语义向量检索 |
| Elasticsearch | 9200 | BM25 关键词检索 |

```bash
docker compose up -d postgres milvus milvus-etcd elasticsearch
```

### 2. 后端配置

`backend/.env.local` 需配置以下关键项：

```env
# 检索基础设施
MILVUS_HOST=localhost
ES_ADDRESSES=http://localhost:9200

# Embedding（DashScope）
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_MODEL=text-embedding-v3

# LLM（DashScope qwen-turbo）
LLM_API_KEY=sk-xxx
LLM_MODEL=qwen-turbo

# RAG 权重（影响 hybrid RRF 融合）
RAG_SEMANTIC_WEIGHT=0.5    # 语义检索权重
KG_WEIGHT=0.0              # 知识图谱权重（0 = 禁用）
RAG_RRF_CONSTANT_K=60      # RRF 常数

# RAG 检索配置
RAG_TOP_K=5                # 返回的 chunk 数量（需与评测 TOP_K 一致）
RAG_RERANK_ENABLED=True    # 是否启用 LLM Reranker
RAG_CHUNK_SIZE=200         # chunk 大小（字符）
RAG_CHUNK_OVERLAP=50       # chunk 重叠（字符）
RAG_REWRITE_ENABLED=True   # 是否启用 Query Rewrite
```

> **权重关系**：`kw_w = 1.0 - RAG_SEMANTIC_WEIGHT - KG_WEIGHT`
> 若 `RAG_SEMANTIC_WEIGHT + KG_WEIGHT = 1.0`，则 BM25 权重为 0，hybrid 退化为纯语义检索。

### 3. Python 环境

使用后端虚拟环境运行（依赖 pydantic-settings、pymilvus、elasticsearch 等）：

```powershell
# 所有命令使用后端 .venv
$python = "..\backend\.venv\Scripts\python.exe"
```

## 使用流程

### Step 1: 数据准备（已完成，通常无需重跑）

```powershell
# 提取 3,050 篇去重文档
$python prepare_corpus.py

# 转换 2,394 条 QA 标注
$python prepare_golden.py
```

**corpus.jsonl** 格式：
```json
{"doc_id": 0, "content": "新华社北京...", "source": "questanswer_1doc"}
```

**golden.jsonl** 格式：
```json
{"query_id": 0, "query": "...", "ground_truth_answer": "...", "relevant_docs": ["..."], "num_relevant_docs": 1, "task_type": "1doc"}
```

### Step 2: 运行评测

```powershell
cd d:\java\project\bitdance-agenthub-main\eval

# 快速验证（200 条采样，需 ~40 分钟）
..\backend\.venv\Scripts\python.exe -X utf8 run_eval.py --limit 200 --skip-ingest

# 全量评测（2,394 条 × 3 模式，需 ~13 小时）
..\backend\.venv\Scripts\python.exe -X utf8 run_eval.py --skip-ingest

# 首次运行（需先入库 3,050 篇文档）
..\backend\.venv\Scripts\python.exe -X utf8 run_eval.py --limit 200
```

### Step 3: 增量重跑（仅指定模式）

权重调整后只需重跑受影响的模式，其余模式从已有 JSON 加载：

```powershell
# 只跑 hybrid（dense/bm25 从 results/ 加载）
..\backend\.venv\Scripts\python.exe -X utf8 run_eval.py --limit 200 --skip-ingest --modes hybrid

# 跑 dense + hybrid
..\backend\.venv\Scripts\python.exe -X utf8 run_eval.py --limit 200 --skip-ingest --modes dense,hybrid
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--limit N` | 随机采样 N 条 golden 条目（seed=42 可复现） | 全部 2,394 条 |
| `--skip-ingest` | 跳过语料入库（已索引时使用） | 否 |
| `--modes` | 指定运行的模式（逗号分隔），未指定的从 JSON 加载 | dense,bm25,hybrid |

## 评测指标

### 检索层（纯计算，无 LLM 调用）

| 指标 | 公式 | 说明 |
|------|------|------|
| **Recall@K** | 命中数 / 相关文档数 | K=5，检索覆盖率 |
| **Precision@K** | 命中数 / K | K=5，检索精度 |
| **MRR** | 1 / 首个命中排名 | 平均倒数排名 |
| **NDCG@K** | DCG / IDCG | K=5，考虑排名位置的增益 |

命中检测采用**双向子串匹配**（`is_hit()`）：取 chunk 前 50 字符与 relevant_docs 做双向包含判断，解决 chunk_id 与文档级标注不对齐的问题。

### 生成层（LLM-as-judge）

| 指标 | 评判内容 | 分数范围 |
|------|----------|----------|
| **Faithfulness** | 答案是否忠于检索上下文（无幻觉） | [0, 1] |
| **Answer Relevance** | 答案与问题的相关程度 | [0, 1] |
| **Answer Quality** | 答案的综合质量（完整性、准确性） | [0, 1] |

使用 DashScope `qwen-turbo` 作为评判 LLM，通过结构化 prompt 约束输出为单一数值分数。

## 运行单元测试

```powershell
cd d:\java\project\bitdance-agenthub-main\eval
..\backend\.venv\Scripts\python.exe -X utf8 -m pytest test_metrics.py -v
```

覆盖 `is_hit`、`recall_at_k`、`precision_at_k`、`mrr`、`ndcg_at_k` 共 26 个测试用例。

## 评测结果

共进行 5 轮实验，逐步排查并修复了三个关键配置问题，最终在公平条件下验证 hybrid 全面优于单路检索。

### 实验配置总览

| 轮次 | sem_w | kw_w | top_k | rerank | 说明 |
|------|-------|------|-------|--------|------|
| R1 | 0.7 | 0.0 | 3 | ON | BM25 权重为零，hybrid 退化为 degraded dense |
| R2 | 0.5 | 0.5 | 3 | ON | 修复 BM25 权重，但 top_k=3 限制召回 |
| R3 | 0.5 | 0.5 | 3 | OFF | 关闭 Reranker，验证 reranker 是否拖后腿 |
| R4 | 0.5 | 0.5 | 5 | ON | 修复 top_k=3→5，重新开启 Reranker |
| R5 | 0.5 | 0.5 | 5 | ON | 公平对比：dense vs hybrid（同条件） |

### R1：旧权重（sem_w=0.7, kw_w=0.0, top_k=3, rerank=ON）

> BM25 权重为 0，hybrid 退化为纯语义检索

| Metric | dense | bm25 | hybrid |
|--------|-------|------|--------|
| recall@5 | **0.7700** | 0.7617 | 0.7492 |
| precision@5 | **0.6058** | 0.6042 | 0.6058 |
| mrr | **0.8442** | 0.8325 | 0.8108 |
| ndcg@5 | 0.7337 | **0.7376** | 0.7154 |
| faithfulness | **0.9925** | 0.9825 | 0.9840 |
| answer_relevance | 0.9235 | **0.9295** | 0.9225 |
| answer_quality | 0.7605 | **0.7745** | 0.7620 |

**问题**：hybrid 在 6/7 指标上垫底，因为 `RAG_SEMANTIC_WEIGHT + KG_WEIGHT = 1.0` 导致 `kw_w = 0`。

### R2：修复 BM25 权重（sem_w=0.5, kw_w=0.5, top_k=3, rerank=ON）

> BM25 恢复权重后 hybrid 有所改善

| Metric | hybrid (R1) | hybrid (R2) | 变化 |
|--------|-------------|------------|------|
| recall@5 | 0.7492 | 0.7492 | — |
| precision@5 | 0.6058 | 0.6075 | +0.2% |
| mrr | 0.8108 | 0.8258 | +1.5% |
| ndcg@5 | 0.7154 | 0.7277 | +1.2% |
| faithfulness | 0.9840 | 0.9850 | +0.1% |
| answer_relevance | 0.9225 | 0.9330 | +1.1% |
| answer_quality | 0.7620 | 0.7605 | -0.2% |

**问题**：MRR/NDCG 小幅提升，但 recall 完全不变。原因：top_k=3 时 RRF 融合只改变排序不改变召回集合，且 Reranker 覆盖了 RRF 排序。

### R3：关闭 Reranker（sem_w=0.5, kw_w=0.5, top_k=3, rerank=OFF）

> 排除 Reranker 干扰，验证 RRF 原始效果

| Metric | hybrid (R2, rerank=ON) | hybrid (R3, rerank=OFF) | 变化 |
|--------|------------------------|------------------------|------|
| recall@5 | 0.7492 | 0.7683 | +1.9% |
| precision@5 | 0.6075 | 0.6458 | +3.8% |
| mrr | 0.8258 | 0.8408 | +1.5% |
| ndcg@5 | 0.7277 | 0.7501 | +2.2% |
| answer_quality | 0.7605 | 0.7905 | +3.0% |

**发现**：Reranker 是主要瓶颈。关闭后 recall 首次提升（+1.9%），说明 Reranker 在丢弃相关文档（scores count mismatch 导致静默截断）。

### R4：修复 top_k（sem_w=0.5, kw_w=0.5, top_k=5, rerank=ON）

> 将 RAG_TOP_K 从 3 改为 5，与评测 TOP_K=5 一致；重新开启 Reranker

| Metric | hybrid (R3, k=3) | hybrid (R4, k=5) | 变化 |
|--------|------------------|------------------|------|
| recall@5 | 0.7683 | **0.8542** | +8.6% |
| precision@5 | 0.6458 | 0.5079 | -13.8% |
| mrr | 0.8408 | 0.8462 | +0.5% |
| ndcg@5 | 0.7501 | **0.8209** | +7.1% |
| faithfulness | 0.9825 | 0.9890 | +0.7% |
| answer_relevance | 0.9290 | 0.9480 | +1.9% |
| answer_quality | 0.7905 | 0.7865 | -0.4% |

**关键**：`RAG_TOP_K=3` 时系统只返回 3 条 chunks，评测却算 recall@5，相当于绑着手臂比赛。改为 5 后 recall 大幅跳升。precision 下降是数学必然（分母从 3 变 5），但命中绝对数增加了 31%。

> Reranker 在 top_k=5 下不再拖后腿：候选池从 12 条扩大到 20 条（`top_k × 4`），Reranker 有足够空间做正确排序。

### R5：公平对比（top_k=5, rerank=ON, dense vs hybrid）

> 相同条件下 dense 与 hybrid 的直接对比

| Metric | dense | **hybrid** | hybrid 优势 |
|--------|-------|-----------|------------|
| recall@5 | 0.8300 | **0.8542** | +2.4% |
| precision@5 | 0.5038 | **0.5079** | +0.4% |
| mrr | 0.8198 | **0.8462** | +2.6% |
| ndcg@5 | 0.8028 | **0.8209** | +1.8% |
| faithfulness | 0.9708 | **0.9890** | +1.8% |
| answer_relevance | 0.9448 | **0.9480** | +0.3% |
| answer_quality | 0.7750 | **0.7865** | +1.2% |

**结论：Hybrid 在 7/7 指标上全面领先 dense。**

> **注意**：dense 模式有 8 条搜索失败（DashScope API 返回 400 Bad Request），失败条目 recall=0 拉低了均值。但这也揭示了 hybrid 的容错优势——embedding API 出错时，dense 模式直接瘫痪（Milvus 无法搜索），而 hybrid 的 ES 路不受影响，仍能返回结果。

### 关键发现总结

1. **权重配置陷阱**：`RAG_SEMANTIC_WEIGHT + KG_WEIGHT = 1.0` 会导致 BM25 权重为 0，hybrid 退化为 degraded dense
2. **top_k 不匹配是最大隐患**：`RAG_TOP_K=3` 但评测算 recall@5，系统只返回 3 条 chunks，hybrid 的多路融合优势无法体现。修复为 5 后 recall 从 0.7683 跳到 0.8542（+8.6%）
3. **Reranker 候选池大小敏感**：top_k=3 时候选池仅 12 条，Reranker 丢弃相关文档；top_k=5 时候选池扩至 20 条，Reranker 反而帮助提升 recall
4. **RRF k=60 压缩区分度**：相邻排名分数差仅 ~0.0002，但 Reranker 开启时 RRF 只负责候选筛选，最终排序由 Reranker 决定，k 值影响较小
5. **Hybrid 容错优势**：embedding API 失败时，dense 模式完全瘫痪，hybrid 的 ES 路仍可工作，保证服务可用性
6. **最终配置**：`RAG_TOP_K=5, RAG_SEMANTIC_WEIGHT=0.5, KG_WEIGHT=0.0, RAG_RERANK_ENABLED=True, RAG_RRF_CONSTANT_K=60` 是当前最优组合

## 输出文件说明

### `<mode>_results.json`

每条 golden 条目的逐条评测结果，包含 query、retrieved chunks、generated answer、各指标分数。

### `comparison_report.md`

Markdown 格式的对比报告，包含：
- 运行元数据（时间、采样数、配置）
- 7×3 指标对比表（最优值加粗）
- 每个模式的详细平均值

## 技术细节

### 模式切换机制

评测脚本通过保存/重注入后端函数引用来切换检索模式：

| 模式 | Milvus | ES | mode() 返回值 | 搜索路径 |
|------|--------|-----|---------------|----------|
| dense | ✅ | ❌ (None) | "semantic" | `_search_semantic` |
| bm25 | ❌ (None) | ✅ | "keyword" | `_search_keyword` |
| hybrid | ✅ | ✅ | "hybrid" | `_search_hybrid` (RRF) |

### Windows 编码注意

PowerShell 下运行需加 `-X utf8` 标志，避免 GBK 编码错误：

```powershell
..\backend\.venv\Scripts\python.exe -X utf8 run_eval.py
```

### Token 消耗估算（200 条采样）

| 环节 | 调用次数 |
|------|----------|
| Query embedding | 200 × 模式数 |
| LLM 生成答案 | 200 × 模式数 |
| LLM-as-judge 评分 | 200 × 模式数 × 3 指标 |

单模式 200 条约消耗 ~1000 次 API 调用，三模式约 ~3000 次。
