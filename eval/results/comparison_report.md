# RAG Evaluation Comparison Report

## Run Metadata

- **Timestamp**: 2026-07-01 00:11:10
- **Total Entries**: 200
- **Limit**: 200
- **Top-K**: 5
- **RAG Top-K (config)**: 5
- **Chunk Size**: 200
- **Chunk Overlap**: 50
- **Semantic Weight**: 0.5
- **KG Weight**: 0.0
- **RRF k**: 10
- **Rewrite Enabled**: True
- **Rerank Enabled**: True

## Metrics Comparison

| Metric | dense | bm25 | hybrid |
|--------|--------|--------|--------|
| recall_at_k | 0.8608 | 0.8300 | **0.8667** |
| precision_at_k | 0.5118 | 0.4862 | **0.5208** |
| mrr | 0.8417 | **0.8685** | 0.8638 |
| ndcg_at_k | 0.8267 | 0.8146 | **0.8442** |
| faithfulness | 0.9875 | 0.9740 | **0.9880** |
| answer_relevance | 0.9505 | **0.9530** | 0.9490 |
| answer_quality | 0.7755 | 0.7795 | **0.7810** |

## Best Mode Summary

- **recall_at_k**: hybrid
- **precision_at_k**: hybrid
- **mrr**: bm25
- **ndcg_at_k**: hybrid
- **faithfulness**: hybrid
- **answer_relevance**: bm25
- **answer_quality**: hybrid

## Per-Mode Details

## Source Distribution (Path Origin)

Chunk source breakdown per mode (pre-reranker path attribution):

| Mode | semantic | keyword | semantic+keyword | other | total |
|------|----------|---------|------------------|-------|-------|
| dense | 683 (100.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) | 683 |
| bm25 | 0 (0.0%) | 700 (100.0%) | 0 (0.0%) | 0 (0.0%) | 700 |
| hybrid | 25 (3.6%) | 16 (2.3%) | 648 (94.0%) | 0 (0.0%) | 689 |

> **semantic**: chunk appeared only in Milvus (dense) results  
> **keyword**: chunk appeared only in ES (BM25) results  
> **semantic+keyword**: chunk appeared in BOTH paths — boosted by RRF fusion  

### dense
- Total entries: 200
- Generation-evaluated entries: 200
- Chunks retrieved: 683 (semantic=683, keyword=0, both=0)
- Averages:
  - recall_at_k: 0.8608
  - precision_at_k: 0.5118
  - mrr: 0.8417
  - ndcg_at_k: 0.8267
  - faithfulness: 0.9875
  - answer_relevance: 0.9505
  - answer_quality: 0.7755

### bm25
- Total entries: 200
- Generation-evaluated entries: 200
- Chunks retrieved: 700 (semantic=0, keyword=700, both=0)
- Averages:
  - recall_at_k: 0.8300
  - precision_at_k: 0.4862
  - mrr: 0.8685
  - ndcg_at_k: 0.8146
  - faithfulness: 0.9740
  - answer_relevance: 0.9530
  - answer_quality: 0.7795

### hybrid
- Total entries: 200
- Generation-evaluated entries: 200
- Chunks retrieved: 689 (semantic=25, keyword=16, both=648)
- Averages:
  - recall_at_k: 0.8667
  - precision_at_k: 0.5208
  - mrr: 0.8638
  - ndcg_at_k: 0.8442
  - faithfulness: 0.9880
  - answer_relevance: 0.9490
  - answer_quality: 0.7810
