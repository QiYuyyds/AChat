## 1. DB 模型

- [x] 1.1 `backend/app/db/models.py`: 新增 `EvalDataset` 模型（id, kb_id, name, description, item_count, has_gold_chunks, has_gold_answers, build_metadata, created_by, created_at, updated_at, user_id）
- [x] 1.2 `backend/app/db/models.py`: 新增 `EvalDatasetItem` 模型（id, dataset_id FK, item_index, query_text, gold_chunk_ids JSONB, gold_answer）
- [x] 1.3 `backend/app/db/models.py`: 新增 `EvalRun` 模型（id, name, dataset_id FK, status, retrieval_config JSONB, metrics JSONB, overall_score, total_items, completed_items, started_at, completed_at, created_by, user_id）
- [x] 1.4 `backend/app/db/models.py`: 新增 `EvalRunItem` 模型（id, run_id FK, item_index, dataset_item_id, query_text, gold_chunk_ids JSONB, gold_answer, generated_answer, retrieved_chunks JSONB, metrics JSONB）
- [x] 1.5 `backend/app/db/table_routing.py`: 将 4 张 eval 表加入 `REMOTE_TABLES` 列表

## 2. 评估指标模块

- [x] 2.1 创建 `backend/app/rag/eval/__init__.py`
- [x] 2.2 创建 `backend/app/rag/eval/metrics.py`: 实现 `recall_at_k(retrieved_ids, gold_ids, k)` 函数
- [x] 2.3 实现 `precision_at_k(retrieved_ids, gold_ids, k)` 函数
- [x] 2.4 实现 `f1_at_k(retrieved_ids, gold_ids, k)` 函数
- [x] 2.5 实现 `llm_judge(question, generated_answer, gold_answer, judge_fn) -> float` 函数（LLM-as-judge 评分）

## 3. 评估运行器

- [x] 3.1 创建 `backend/app/rag/eval/evaluator.py`: 实现 `async def evaluate_question(query, gold_chunk_ids, gold_answer, rag_service, retrieval_config, judge_fn) -> dict`：调用 RAGService.search → 计算检索指标 → 计算 LLM judge → 返回 per-item 结果
- [x] 3.2 实现 `aggregate_metrics(item_results: list[dict]) -> dict`：从 per-item 结果聚合出整体指标

## 4. 数据集自动生成

- [x] 4.1 创建 `backend/app/rag/eval/benchmark_generation.py`: 实现 `async def generate_dataset(chunk_count, gen_fn, user_id) -> EvalDataset`：从 rag_chunks 采样 chunks → 用 dataset 生成 LLM 为每个 chunk 生成问题 → 存入 eval_datasets + eval_dataset_items

## 5. 评估服务

- [x] 5.1 创建 `backend/app/rag/eval/service.py`: `RAGEvalService` 类，注入 `settings`、`rag_service`、独立 judge LLM fn、独立 dataset gen LLM fn
- [x] 5.2 实现 `async def upload_dataset(self, items, name, description, user_id) -> EvalDataset`
- [x] 5.3 实现 `async def generate_dataset(self, chunk_count, name, description, user_id) -> EvalDataset`
- [x] 5.4 实现 `async def list_datasets(self, user_id) -> list[dict]`
- [x] 5.5 实现 `async def get_dataset(self, dataset_id, user_id, page, page_size) -> dict`
- [x] 5.6 实现 `async def delete_dataset(self, dataset_id, user_id) -> bool`
- [x] 5.7 实现 `async def run_evaluation(self, dataset_id, retrieval_config, user_id) -> str`：异步启动评估运行，返回 run_id
- [x] 5.8 实现 `async def list_runs(self, user_id) -> list[dict]`
- [x] 5.9 实现 `async def get_run_results(self, run_id, user_id, page, page_size) -> dict`
- [x] 5.10 实现 `async def delete_run(self, run_id, user_id) -> bool`
- [x] 5.11 实现 `_make_eval_llm_fn(settings) -> Callable` 和 `_make_dataset_llm_fn(settings) -> Callable`：从独立配置创建 LLM 调用函数

## 6. REST API

- [x] 6.1 创建 `backend/app/api/rag_eval.py`: 注册以下端点
- [x] 6.2 `POST /api/rag/eval/datasets` — 上传数据集 (JSONL)
- [x] 6.3 `POST /api/rag/eval/datasets/generate` — 自动生成数据集
- [x] 6.4 `GET /api/rag/eval/datasets` — 列出数据集
- [x] 6.5 `GET /api/rag/eval/datasets/{id}` — 数据集详情 (分页)
- [x] 6.6 `DELETE /api/rag/eval/datasets/{id}` — 删除数据集
- [x] 6.7 `POST /api/rag/eval/runs` — 启动评估运行
- [x] 6.8 `GET /api/rag/eval/runs` — 列出运行
- [x] 6.9 `GET /api/rag/eval/runs/{id}/results` — 运行结果 (分页)
- [x] 6.10 `DELETE /api/rag/eval/runs/{id}` — 删除运行

## 7. 装配

- [x] 7.1 `backend/app/main.py`: 在 RAGService 初始化后创建 `RAGEvalService(settings, _rag_service)`
- [x] 7.2 `backend/app/main.py`: 注册 `rag_eval` API router
- [x] 7.3 `backend/app/main.py`: 启动时确认 eval LLM 配置可用性（不可用只 warn 不阻断）

## 8. 验证

- [x] 8.1 `ruff check .` 通过
- [x] 8.2 `pytest` 通过
- [x] 8.3 手动测试：上传一个 JSONL 数据集，确认数据集被正确创建（需要 PG + 运行环境）
- [x] 8.4 手动测试：启动一个评估运行（limit=5），确认运行完成、指标被正确计算（需要 PG + LLM 环境）
- [x] 8.5 手动测试：不配置 eval LLM 时确认返回明确错误信息（需要 PG 环境）
- [x] 8.6 手动测试：自动生成数据集，确认从 chunks 生成了合理的问题（需要 PG + LLM 环境）
