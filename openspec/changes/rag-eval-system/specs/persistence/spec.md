## Modified Requirements

### Requirement: Eval system tables SHALL persist datasets and run results

The system MUST persist 4 new tables for the evaluation system: `eval_datasets` (dataset metadata + user isolation), `eval_dataset_items` (individual QA entries with optional gold_chunk_ids and gold_answer), `eval_runs` (run metadata + retrieval_config + aggregated metrics + progress tracking), `eval_run_items` (per-item results: retrieved_chunks, generated_answer, per-item metrics). All tables MUST include `user_id` for multi-user isolation.

#### Scenario: User queries their eval datasets
- **WHEN** an authenticated user calls `GET /api/rag/eval/datasets`
- **THEN** only datasets with matching `user_id` are returned

#### Scenario: User queries eval run results
- **WHEN** an authenticated user calls `GET /api/rag/eval/runs/{id}/results`
- **THEN** only results from runs with matching `user_id` are returned
