## Purpose

服务化 RAG 评估系统：定义数据集管理、评估运行、指标计算和独立 LLM 配置的行为契约，使 RAG 检索质量可以通过 REST API 持久化评测和量化对比。

## ADDED Requirements

### Requirement: EvalDataset SHALL support upload and auto-generation

The evaluation system MUST support two ways to create datasets: (1) upload JSONL file with `{query, gold_chunk_ids?, gold_answer?}` entries, (2) auto-generate from knowledge base chunks by sampling chunks and using a dedicated dataset-generation LLM to create questions. Datasets MUST be persisted in `eval_datasets` + `eval_dataset_items` tables with user isolation.

#### Scenario: Upload JSONL dataset
- **WHEN** a user uploads a JSONL file via `POST /api/rag/eval/datasets`
- **THEN`eval_datasets` row is created with the user's `user_id`
- **AND** each JSONL line becomes an `eval_dataset_items` row
- **AND** `item_count` is set to the number of entries

#### Scenario: Auto-generate dataset from knowledge base
- **WHEN** a user calls `POST /api/rag/eval/datasets/generate` with a target item count
- **THEN` the system samples chunks from the knowledge base
- **AND** the dataset-generation LLM creates questions for each sampled chunk
- **AND** the generated questions are stored as dataset items with `gold_chunk_ids`

### Requirement: EvalRun SHALL execute evaluation with configurable retrieval parameters

An evaluation run MUST accept a `RetrievalConfig` that parameterizes the search behavior (search_mode, weights, top_k, etc.). For each dataset item, the run MUST: (1) call `RAGService.search()` with the retrieval config, (2) compute retrieval metrics if `gold_chunk_ids` exist, (3) compute LLM judge score if `gold_answer` exists, (4) persist per-item results in `eval_run_items`.

#### Scenario: Evaluation run with hybrid mode
- **WHEN** a user starts an eval run with `RetrievalConfig(search_mode="hybrid")`
- **THEN** each dataset item is searched using hybrid retrieval
- **AND` Recall@K and Precision@K are computed for items with gold_chunk_ids
- **AND` LLM judge score is computed for items with gold_answer
- **AND` aggregated metrics are stored in `eval_runs.metrics`

#### Scenario: Evaluation run progress tracking
- **WHEN** an eval run is in progress
- **THEN` `eval_runs.status` is `"running"`
- **AND` `eval_runs.completed_items` is updated as items complete
- **AND` `eval_runs.status` transitions to `"completed"` when all items are done

### Requirement: Evaluation LLM SHALL use independent configuration

The evaluation system's judge LLM and dataset-generation LLM MUST use independent environment variables (`eval_llm_api_key`, `eval_llm_api_url`, `eval_llm_model`, `eval_dataset_llm_*`) that are NOT shared with the RAG system's `llm_api_key`. When eval LLM keys are not configured, the evaluation system MUST report a clear error and refuse to run.

#### Scenario: Judge LLM not configured
- **WHEN` a user starts an eval run but `eval_llm_api_key` is not set
- **THEN` the system returns an error: "Evaluation LLM not configured (EVAL_LLM_API_KEY required)"
- **AND` no evaluation run is started

#### Scenario: Judge LLM and dataset LLM use different providers
- **WHEN` `eval_llm_model` is set to `deepseek-chat` and `eval_dataset_llm_model` is set to `gpt-4o`
- **THEN` answer judging uses the deepseek model
- **AND` dataset generation uses the gpt-4o model
