# RAGAS 评测使用指南

> 使用 [RAGAS](https://github.com/explodinggradients/ragas) 框架对 AChat RAG 系统进行标准化评测。

## 与现有评测的区别

| 对比项 | `run_eval.py`（自定义评测） | `run_ragas_eval.py`（RAGAS 评测） |
|--------|---------------------------|-------------------------------------|
| 评测框架 | 自定义指标实现 | RAGAS 开源框架（业界标准） |
| 检索指标 | Recall@K, Precision@K, MRR, NDCG@K（基于子串匹配） | Context Precision, Context Recall（LLM-as-judge） |
| 生成指标 | Faithfulness, Answer Relevance, Answer Quality（自定义 prompt） | Faithfulness, Answer Relevancy, Answer Correctness（RAGAS 内置 prompt） |
| 评测 LLM | 项目自建 generate_fn | LangChain ChatOpenAI 包装器 |
| Embedding | 不需要 | answer_relevancy 指标需要 |
| 可比性 | 仅项目内对比 | 可与其他 RAGAS 评测结果横向对比 |

两套评测使用同一数据集（CRUD-RAG）和同一 RAGService，可交叉验证。

## RAGAS 指标说明

### 生成层指标

| 指标 | 含义 | 计算方式 | 需要 ground_truth |
|------|------|----------|:-:|
| **faithfulness** | 答案是否忠于检索上下文，无幻觉 | 将答案拆分为陈述句，逐一验证是否可从 context 推导 | ❌ |
| **answer_relevancy** | 答案与问题的相关程度 | 从答案反生成问题，计算与原问题的余弦相似度 | ❌ |

### 检索层指标

| 指标 | 含义 | 计算方式 | 需要 ground_truth |
|------|------|----------|:-:|
| **context_precision** | 检索上下文的信噪比 | 逐条判断 context 是否对回答问题有贡献，考虑排名位置加权 | ✅ |
| **context_recall** | 检索是否覆盖了回答所需信息 | 逐句分析 ground_truth 是否可归因于检索到的 context | ✅ |

### 端到端指标

| 指标 | 含义 | 计算方式 | 需要 ground_truth |
|------|------|----------|:-:|
| **answer_correctness** | 答案与标准答案的语义一致性 | 提取关键事实声明，比对生成答案与 ground_truth 的事实重合度 | ✅ |

所有指标范围均为 `[0, 1]`，越高越好。

## 前置条件

### 1. 基础设施

```bash
docker compose up -d postgres milvus milvus-etcd elasticsearch
```

### 2. 后端配置

`backend/.env.local` 需配置：

```env
# 检索基础设施
MILVUS_HOST=localhost
ES_ADDRESSES=http://localhost:9200

# Embedding（DashScope 或其他 OpenAI 兼容 API）
EMBEDDING_API_KEY=sk-xxx
EMBEDDING_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v3

# LLM（用于 RAG 生成 + RAGAS 评判）
LLM_API_KEY=sk-xxx
LLM_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-turbo

# RAG 配置
RAG_TOP_K=5
RAG_SEMANTIC_WEIGHT=0.5
KG_WEIGHT=0.0
RAG_RERANK_ENABLED=True
RAG_REWRITE_ENABLED=True
```

### 3. 安装 RAGAS 依赖

```powershell
cd d:\java\project\bitdance-agenthub-main
.\backend\.venv\Scripts\pip.exe install -r eval\requirements-ragas.txt
```

> **注意**：RAGAS 0.2.x 需要 `langchain-community<0.4`（0.4+ 移除了 `vertexai` 模块路径）。
> 如果遇到 `ModuleNotFoundError: No module named 'langchain_community.chat_models.vertexai'`，请按上面的 requirements 文件安装指定版本。

### 4. 数据准备

确保以下文件已生成（通常已存在，无需重跑）：

- `eval/corpus.jsonl` — 3,050 篇去重新闻文档
- `eval/golden.jsonl` — 2,394 条 QA 标注

```powershell
# 如需重新生成
cd d:\java\project\bitdance-agenthub-main\eval
..\backend\.venv\Scripts\python.exe -X utf8 prepare_corpus.py
..\backend\.venv\Scripts\python.exe -X utf8 prepare_golden.py
```

## 使用流程

### 快速验证（50 条采样）

```powershell
cd d:\java\project\bitdance-agenthub-main\eval

# 跳过入库（已索引过）
..\backend\.venv\Scripts\python.exe -X utf8 run_ragas_eval.py --limit 50 --skip-ingest

# 首次运行（需先入库）
..\backend\.venv\Scripts\python.exe -X utf8 run_ragas_eval.py --limit 50
```

### 全量评测（2,394 条 × 3 模式）

```powershell
..\backend\.venv\Scripts\python.exe -X utf8 run_ragas_eval.py --skip-ingest
```

### 仅评测指定模式

```powershell
# 只跑 hybrid
..\backend\.venv\Scripts\python.exe -X utf8 run_ragas_eval.py --limit 100 --skip-ingest --modes hybrid

# 跑 dense + hybrid
..\backend\.venv\Scripts\python.exe -X utf8 run_ragas_eval.py --limit 100 --skip-ingest --modes dense,hybrid
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--limit N` | 随机采样 N 条 golden 条目（seed=42） | 全部 2,394 条 |
| `--skip-ingest` | 跳过语料入库 | 否 |
| `--modes` | 指定运行模式（逗号分隔） | dense,bm25,hybrid |

## 评测流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    run_ragas_eval.py 流程                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. 加载数据                                                     │
│     corpus.jsonl (3050 docs) + golden.jsonl (2394 QA)           │
│                                                                 │
│  2. 初始化 RAGService                                           │
│     PG + Milvus + ES + Embedding + LLM                         │
│                                                                 │
│  3. 入库（可选）                                                  │
│     3050 篇文档 → split → embed → index                         │
│                                                                 │
│  4. 配置 RAGAS LLM & Embeddings                                 │
│     LangChain ChatOpenAI (DashScope Qwen)                      │
│     LangChain OpenAIEmbeddings (DashScope text-embedding-v3)    │
│                                                                 │
│  5. 对每个模式 (dense/bm25/hybrid):                              │
│     ┌──────────────────────────────────────────────┐            │
│     │ a. 切换检索后端                                │            │
│     │ b. 逐条搜索 → 收集 (question, answer,          │            │
│     │    contexts, ground_truth)                     │            │
│     │ c. 构建 HuggingFace Dataset                    │            │
│     │ d. RAGAS evaluate() → 5 项指标                 │            │
│     │ e. 保存 JSON 结果                               │            │
│     └──────────────────────────────────────────────┘            │
│                                                                 │
│  6. 生成对比报告 (ragas_comparison_report.md)                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 输出文件

```
eval/ragas_results/
├── dense_ragas_results.json      # dense 模式逐条结果
├── bm25_ragas_results.json       # bm25 模式逐条结果
├── hybrid_ragas_results.json     # hybrid 模式逐条结果
└── ragas_comparison_report.md    # 三模式对比报告
```

### `<mode>_ragas_results.json`

每条评测条目的 RAGAS 指标分数 + 模式级平均值。

### `ragas_comparison_report.md`

Markdown 格式的对比报告，包含：
- 运行元数据（时间、采样数、RAG 配置）
- RAGAS 指标说明表
- 5×3 指标对比表（最优值加粗）
- 每个模式的详细平均值

## Token 消耗估算

RAGAS 的 LLM-as-judge 会产生额外 LLM 调用：

| 环节 | 调用次数（N = 评测条目数） |
|------|--------------------------|
| RAG 搜索（生成答案） | N × 模式数 |
| faithfulness 评判 | N × 模式数（可能多轮拆分陈述句） |
| answer_relevancy 评判 | N × 模式数（生成反问题 + embedding） |
| context_precision 评判 | N × 模式数 |
| context_recall 评判 | N × 模式数 |
| answer_correctness 评判 | N × 模式数 |

单模式 50 条约消耗 ~500 次 LLM 调用，三模式约 ~1500 次。

## 与自定义评测交叉验证

建议同时运行两套评测，对比结果：

```powershell
# 自定义评测
..\backend\.venv\Scripts\python.exe -X utf8 run_eval.py --limit 200 --skip-ingest --modes hybrid

# RAGAS 评测
..\backend\.venv\Scripts\python.exe -X utf8 run_ragas_eval.py --limit 200 --skip-ingest --modes hybrid
```

两套评测的 faithfulness 指标角度不同（自定义用单次 prompt 评分，RAGAS 用陈述句拆分验证），如果两者趋势一致，说明评测结果可信。

## 常见问题

### Q: `ModuleNotFoundError: No module named 'langchain_community.chat_models.vertexai'`

A: langchain-community 版本过高。安装 `langchain-community==0.3.20`。

### Q: RAGAS evaluate 报 `NaN` 错误

A: 某些条目 LLM 返回格式异常导致指标计算失败。脚本已设置 `raise_exceptions=False`，失败条目指标记为 0.0。

### Q: answer_relevancy 指标为 0

A: 该指标需要 embedding 模型。确保 `EMBEDDING_API_KEY` 和 `EMBEDDING_MODEL` 已配置。

### Q: Windows 编码错误

A: PowerShell 下运行需加 `-X utf8` 标志：
```powershell
..\backend\.venv\Scripts\python.exe -X utf8 run_ragas_eval.py
```
