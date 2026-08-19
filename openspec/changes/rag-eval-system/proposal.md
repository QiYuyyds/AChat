## Why

AChat 已有一个脚本式 RAG 评测系统（`add-rag-evaluation` change，用 CRUD-RAG 数据集跑离线评测脚本），但它不是服务化的——没有 DB 持久化、没有 REST API、没有独立 LLM 配置。方案决策点 4 要求评估系统的 judge LLM 和 dataset 生成 LLM 使用独立环境变量。需要一个服务化评测系统，支持数据集上传/自动生成、评估运行管理、指标计算和结果持久化。

## What Changes

- 新增 `backend/app/rag/eval/` 包：`RAGEvalService` 服务 + 指标计算 + 评估运行器 + 数据集自动生成
- 新增 4 张 DB 表：`eval_datasets`、`eval_dataset_items`、`eval_runs`、`eval_run_items`（持久化数据集和评估结果）
- 评估系统 LLM 独立配置：`eval_llm_api_key` / `eval_llm_api_url` / `eval_llm_model`（judge LLM）和 `eval_dataset_llm_*`（dataset 生成 LLM），不与 RAG 的 `llm_api_key` 混用
- 新增 `backend/app/api/rag_eval.py`：评估系统 REST API（数据集 CRUD + 评估运行 + 结果查询）
- 指标：检索层 `Recall@K` / `Precision@K` / `F1@K`（有 gold_chunk_ids 时计算），生成层 `LLM Judge`（有 gold_answer 时计算）

## Capabilities

### New Capabilities

- `rag-eval-system`: 服务化 RAG 评估系统——数据集 CRUD + LLM 自动生成 + 评估运行管理 + 检索/生成指标计算 + REST API + 独立 LLM 配置

### Modified Capabilities

- `persistence`: 新增 `eval_datasets`、`eval_dataset_items`、`eval_runs`、`eval_run_items` 4 张表

## Impact

- **新增文件**: `backend/app/rag/eval/__init__.py`、`service.py`、`metrics.py`、`evaluator.py`、`benchmark_generation.py`、`backend/app/api/rag_eval.py`（共 6 个文件）
- **修改文件**: `backend/app/db/models.py`（4 张 eval 表）、`backend/app/main.py`（RAGEvalService 装配 + API router 注册）
- **新增 DB 表**: `eval_datasets`、`eval_dataset_items`、`eval_runs`、`eval_run_items`
- **与现有 `add-rag-evaluation` 的关系**: 完全独立，不替代旧提案。旧提案是脚本式离线评测（用 CRUD-RAG 数据集），新提案是服务化评测系统（DB 持久化 + REST API + 独立 LLM 配置）。两者可以共存——旧提案的评测结果可以导入新系统的数据集
- **依赖**: `rag-overhaul-foundation` 提案（`eval_llm_*` 配置项）、`rag-retrieval-enhancement` 提案（`RetrievalConfig` 用于评估运行参数化）
