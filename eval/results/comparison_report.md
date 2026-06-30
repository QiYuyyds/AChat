# RAG Evaluation Comparison Report

## Run Metadata

- **Timestamp**: 2026-06-30 19:48:00
- **Total Entries**: 200
- **Limit**: 200
- **Top-K**: 5
- **Chunk Size**: 200
- **Chunk Overlap**: 50
- **Rewrite Enabled**: True
- **Rerank Enabled**: True

## Metrics Comparison

| Metric | dense | hybrid |
|--------|--------|--------|
| recall_at_k | 0.8300 | **0.8542** |
| precision_at_k | 0.5038 | **0.5079** |
| mrr | 0.8198 | **0.8462** |
| ndcg_at_k | 0.8028 | **0.8209** |
| faithfulness | 0.9708 | **0.9890** |
| answer_relevance | 0.9448 | **0.9480** |
| answer_quality | 0.7750 | **0.7865** |

## Best Mode Summary

- **recall_at_k**: hybrid
- **precision_at_k**: hybrid
- **mrr**: hybrid
- **ndcg_at_k**: hybrid
- **faithfulness**: hybrid
- **answer_relevance**: hybrid
- **answer_quality**: hybrid

## Per-Mode Details

### dense
- Total entries: 200
- Generation-evaluated entries: 192
- Averages:
  - recall_at_k: 0.8300
  - precision_at_k: 0.5038
  - mrr: 0.8198
  - ndcg_at_k: 0.8028
  - faithfulness: 0.9708
  - answer_relevance: 0.9448
  - answer_quality: 0.7750

### hybrid
- Total entries: 200
- Generation-evaluated entries: 200
- Averages:
  - recall_at_k: 0.8542
  - precision_at_k: 0.5079
  - mrr: 0.8462
  - ndcg_at_k: 0.8209
  - faithfulness: 0.9890
  - answer_relevance: 0.9480
  - answer_quality: 0.7865
