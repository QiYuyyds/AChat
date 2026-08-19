## Context

AChat 已有 `add-rag-evaluation` 变更提案——一个脚本式评测系统（`eval/run_eval.py`），使用 CRUD-RAG 数据集跑离线评测，输出 JSON + Markdown 报告。新提案是服务化评测系统，有 DB 持久化、REST API、独立 LLM 配置。两者完全独立，不互相依赖。

方案决策点 4 要求评估系统 LLM 独立配置，不与 RAG 的 `llm_api_key` 混用。

## Goals / Non-Goals

**Goals:**
- 实现服务化评测系统（数据集 CRUD + 评估运行 + 指标计算 + REST API）
- 评估 LLM 独立配置（judge LLM + dataset 生成 LLM）
- 4 张 DB 表持久化数据集和评估结果
- 检索指标（Recall@K / Precision@K / F1@K）和生成指标（LLM Judge）

**Non-Goals:**
- 不替代现有 `add-rag-evaluation` 提案（两者独立共存）
- 不实现评测系统的前端 UI（除非 API 签名要求）
- 不实现 CRUD-RAG 数据集导入（旧提案已有）
- 不实现三模式消融对比（dense/bm25/hybrid），用户可通过不同 RetrievalConfig 跑多次评估实现

## Decisions

### Decision 1: 独立于现有 add-rag-evaluation 提案

**Choice**: 新评测系统完全独立，不修改旧提案的 `eval/run_eval.py` 脚本。两者可以共存——旧提案的评测结果可以导入新系统的数据集。

**Rationale**: 旧提案是离线脚本，新提案是服务化系统，架构完全不同。合并会造成混乱。

### Decision 2: 评估 LLM 使用独立环境变量

**Choice**: judge LLM 使用 `eval_llm_api_key` / `eval_llm_api_url` / `eval_llm_model`，dataset 生成 LLM 使用 `eval_dataset_llm_*`，两者都不与 RAG 的 `llm_api_key` 混用。

**Rationale**: 方案决策点 4。评估系统的 LLM 调用不应消耗 RAG 的 API 配额。独立配置也方便用户用不同 provider 做评测。

### Decision 3: 4 张 DB 表路由到远端 PostgreSQL

**Choice**: `eval_datasets`、`eval_dataset_items`、`eval_runs`、`eval_run_items` 路由到远端 PostgreSQL（`REMOTE_TABLES`），与 `documents`、`rag_chunks` 同库。

**Rationale**: 评测数据需要和 RAG 数据在同一库中做 join 查询（如从 rag_chunks 采样生成数据集）。

### Decision 4: 评估运行是异步的

**Choice**: `POST /api/rag/eval/runs` 启动评估运行后立即返回 run_id，评估在后台异步执行。前端通过 `GET /api/rag/eval/runs/{id}` 轮询进度。

**Rationale**: 评估运行可能涉及数百条 QA，每条需要检索 + LLM 生成 + judge，耗时可能数分钟。同步等待会超时。

## Risks / Trade-offs

- **[Risk] 评估 LLM 调用成本高** → 独立配置让用户可以选择便宜的模型做评测
- **[Risk] 大数据集评估运行时间长** → 异步执行 + 进度追踪
- **[Risk] 与旧 add-rag-evaluation 提案混淆** → 提案文档明确说明两者独立

## Open Questions

无。
