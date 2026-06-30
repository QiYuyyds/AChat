# RAG Evaluation Comparison Report

## Run Metadata

- **Timestamp**: 2026-06-30 22:29:16
- **Total Entries**: 200
- **Limit**: 200
- **Top-K**: 5
- **RAG Top-K (config)**: 5
- **Chunk Size**: 200
- **Chunk Overlap**: 50
- **Semantic Weight**: 0.5
- **KG Weight**: 0.0
- **RRF k**: 60
- **Rewrite Enabled**: True
- **Rerank Enabled**: True

## Metrics Comparison

| Metric | dense | bm25 | hybrid |
|--------|--------|--------|--------|
| recall_at_k | **0.8642** | 0.8308 | 0.8492 |
| precision_at_k | **0.5164** | 0.4753 | 0.5075 |
| mrr | 0.8427 | 0.8392 | **0.8583** |
| ndcg_at_k | 0.8253 | 0.8047 | **0.8268** |
| faithfulness | **0.9840** | 0.9790 | 0.9714 |
| answer_relevance | 0.9478 | 0.9485 | **0.9603** |
| answer_quality | 0.7630 | **0.7640** | 0.7603 |

## Best Mode Summary

- **recall_at_k**: dense
- **precision_at_k**: dense
- **mrr**: hybrid
- **ndcg_at_k**: hybrid
- **faithfulness**: dense
- **answer_relevance**: hybrid
- **answer_quality**: bm25

## Per-Mode Details

## Source Distribution (Path Origin)

Chunk source breakdown per mode (pre-reranker path attribution):

| Mode | semantic | keyword | semantic+keyword | other | total |
|------|----------|---------|------------------|-------|-------|
| dense | 678 (100.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 678 |
| bm25 | 0 (0.0%) | 714 (100.0%) | 0 (0.0%) | 0 (0.0%) | 714 |
| hybrid | 20 (2.9%) | 7 (1.0%) | 657 (96.1%) | 0 (0.0%) | 684 |

> **semantic**: chunk appeared only in Milvus (dense) results  
> **keyword**: chunk appeared only in ES (BM25) results  
> **semantic+keyword**: chunk appeared in BOTH paths — boosted by RRF fusion  

### dense
- Total entries: 200
- Generation-evaluated entries: 200
- Chunks retrieved: 678 (semantic=678, keyword=0, both=0)
- Averages:
  - recall_at_k: 0.8642
  - precision_at_k: 0.5164
  - mrr: 0.8427
  - ndcg_at_k: 0.8253
  - faithfulness: 0.9840
  - answer_relevance: 0.9478
  - answer_quality: 0.7630

### bm25
- Total entries: 200
- Generation-evaluated entries: 200
- Chunks retrieved: 714 (semantic=0, keyword=714, both=0)
- Averages:
  - recall_at_k: 0.8308
  - precision_at_k: 0.4753
  - mrr: 0.8392
  - ndcg_at_k: 0.8047
  - faithfulness: 0.9790
  - answer_relevance: 0.9485
  - answer_quality: 0.7640

### hybrid
- Total entries: 200
- Generation-evaluated entries: 199
- Chunks retrieved: 684 (semantic=20, keyword=7, both=657)
- Averages:
  - recall_at_k: 0.8492
  - precision_at_k: 0.5075
  - mrr: 0.8583
  - ndcg_at_k: 0.8268
  - faithfulness: 0.9714
  - answer_relevance: 0.9603
  - answer_quality: 0.7603
