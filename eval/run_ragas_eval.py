#!/usr/bin/env python
"""RAGAS Evaluation Runner — RAGAS framework integration for AChat RAG system.

Executes RAGAS evaluation pipeline:
  1. Load corpus.jsonl and golden.jsonl (same data as run_eval.py)
  2. Ingest corpus into RAG (PG + Milvus + ES) — optional
  3. For each mode (dense, bm25, hybrid):
     - Switch retrieval backends
     - For each golden entry: search → collect (question, answer, contexts, ground_truth)
     - Run RAGAS evaluate() with standard RAGAS metrics
     - Save per-mode JSON results
  4. Generate Markdown comparison report

RAGAS Metrics (all LLM-as-judge, no manual annotation needed except ground_truth):
  - faithfulness: answer grounded in retrieved context (no hallucination)
  - answer_relevancy: answer directly addresses the question
  - context_precision: retrieved context is relevant (signal-to-noise)
  - context_recall: all info needed to answer is retrieved
  - answer_correctness: answer matches ground truth semantically

Usage:
    cd eval && python run_ragas_eval.py --limit 50 --skip-ingest
    cd eval && python run_ragas_eval.py --skip-ingest --modes hybrid
    cd eval && python run_ragas_eval.py --limit 100

Requires: ragas==0.2.15, langchain-openai, Docker Compose (PG + Milvus + ES)
"""

import argparse
import asyncio
import json
import logging
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# ─── Path setup ────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "backend"

sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(SCRIPT_DIR))

# ─── File paths ────────────────────────────────────────────────────────────
CORPUS_PATH = SCRIPT_DIR / "corpus.jsonl"
GOLDEN_PATH = SCRIPT_DIR / "golden.jsonl"
RAGAS_RESULTS_DIR = SCRIPT_DIR / "ragas_results"

# ─── Evaluation config ─────────────────────────────────────────────────────
MODES = ["dense", "bm25", "hybrid"]
RANDOM_SEED = 42
PROGRESS_INTERVAL = 50

# ─── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,
)
for _noisy in ("pymilvus", "elastic_transport", "sqlalchemy",
               "neo4j.notifications", "httpx", "httpcore",
               "langchain_core", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("ragas_eval")


# ═══════════════════════════════════════════════════════════════════════════
#  Data loading
# ═══════════════════════════════════════════════════════════════════════════

def load_jsonl(path: Path) -> list[dict]:
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ═══════════════════════════════════════════════════════════════════════════
#  RAG service initialization (mirrors run_eval.py)
# ═══════════════════════════════════════════════════════════════════════════

async def init_rag_service():
    original_cwd = os.getcwd()
    os.chdir(str(BACKEND_DIR))

    from app.config import apply_env_overrides, get_settings
    from app.db.engine import init_db
    from app.services.rag_service import RAGService

    apply_env_overrides()
    settings = get_settings()
    os.chdir(original_cwd)

    await init_db()

    from app.infra.factory import build_infrastructure
    infra = build_infrastructure(settings)

    rag_service = RAGService(settings)

    if infra and infra.milvus_client:
        _wire_milvus(rag_service, infra.milvus_client, settings)
        logger.info("Milvus backend wired")
    else:
        logger.error("Milvus not available")
        return None, None, None

    if infra and infra.es_client:
        _wire_es(rag_service, infra.es_client)
        logger.info("Elasticsearch backend wired")
    else:
        logger.error("Elasticsearch not available")
        return None, None, None

    embed_fn = _make_embed_fn(settings)
    if embed_fn:
        rag_service.set_embed_fn(embed_fn)
        logger.info("embed_fn injected (model=%s)", settings.embedding_model)
    else:
        logger.error("embed_fn not available")
        return None, None, None

    generate_fn = _make_generate_fn(settings)
    if generate_fn:
        rag_service.set_generate_fn(generate_fn)
        logger.info("generate_fn injected")
    else:
        logger.error("generate_fn not available")
        return None, None, None

    await rag_service.initialize()
    logger.info("RAGService initialized: mode=%s", rag_service.hybrid.mode())
    return rag_service, settings, infra


def _make_embed_fn(settings):
    api_key = settings.embedding_api_key
    api_url = settings.embedding_api_url or "https://api.openai.com/v1"
    model = settings.embedding_model or "text-embedding-3-small"
    if not api_key:
        return None
    import httpx
    client = httpx.Client(timeout=30.0)

    def embed(text: str) -> list[float]:
        resp = client.post(
            f"{api_url}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"input": text, "model": model},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    return embed


def _make_generate_fn(settings):
    if settings.llm_api_key:
        api_key = settings.llm_api_key
        api_url = settings.llm_api_url or "https://api.openai.com/v1"
        model = settings.llm_model or "gpt-4o-mini"
    elif settings.openai_api_key:
        api_key = settings.openai_api_key
        api_url = "https://api.openai.com/v1"
        model = "gpt-4o-mini"
    elif settings.deepseek_api_key:
        api_key = settings.deepseek_api_key
        api_url = "https://api.deepseek.com/v1"
        model = "deepseek-chat"
    else:
        return None
    import httpx
    client = httpx.Client(timeout=60.0)

    def generate(system_prompt: str, user_msg: str) -> str:
        resp = client.post(
            f"{api_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    return generate


def _wire_milvus(rag_service, milvus_client, settings):
    from app.main import _wire_milvus_to_rag
    _wire_milvus_to_rag(rag_service, milvus_client, settings)


def _wire_es(rag_service, es_client):
    from app.main import _wire_es_to_rag
    _wire_es_to_rag(rag_service, es_client)


# ═══════════════════════════════════════════════════════════════════════════
#  Corpus ingestion
# ═══════════════════════════════════════════════════════════════════════════

async def ingest_corpus(rag_service, corpus: list[dict]) -> int:
    total_chunks = 0
    total_docs = len(corpus)
    logger.info("Starting corpus ingestion: %d documents", total_docs)

    for i, entry in enumerate(corpus, 1):
        content = entry.get("content", "")
        if not content.strip():
            continue
        try:
            chunks = await rag_service.ingest(content)
            total_chunks += chunks
        except Exception as e:
            logger.warning("Ingest failed for doc %s: %s", entry.get("doc_id", "?"), e)
        if i % 100 == 0 or i == total_docs:
            logger.info("Ingest progress: %d/%d docs (%d chunks)", i, total_docs, total_chunks)

    logger.info("Corpus ingestion complete: %d docs, %d chunks", total_docs, total_chunks)
    return total_chunks


# ═══════════════════════════════════════════════════════════════════════════
#  Mode switching (same as run_eval.py)
# ═══════════════════════════════════════════════════════════════════════════

def save_backends(rag_service):
    hybrid = rag_service.hybrid
    return {
        "milvus_search_fn": hybrid._milvus_search_fn,
        "milvus_insert_fn": hybrid._milvus_insert_fn,
        "es_search_fn": hybrid._es_search_fn,
        "es_index_fn": hybrid._es_index_fn,
    }


def switch_mode(rag_service, mode: str, saved: dict) -> str:
    milvus_search = saved["milvus_search_fn"]
    milvus_insert = saved["milvus_insert_fn"]
    es_search = saved["es_search_fn"]
    es_index = saved["es_index_fn"]

    if mode == "dense":
        rag_service.set_milvus_backend(milvus_search, milvus_insert)
        rag_service.set_es_backend(None)
    elif mode == "bm25":
        rag_service.set_milvus_backend(None)
        rag_service.set_es_backend(es_search, es_index)
    elif mode == "hybrid":
        rag_service.set_milvus_backend(milvus_search, milvus_insert)
        rag_service.set_es_backend(es_search, es_index)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    actual_mode = rag_service.hybrid.mode()
    expected = {"dense": "semantic", "bm25": "keyword", "hybrid": "hybrid"}
    if actual_mode != expected[mode]:
        logger.warning("Mode mismatch: expected %s -> got %s", expected[mode], actual_mode)
    else:
        logger.info("Switched to %s mode -> %s", mode, actual_mode)
    return actual_mode


# ═══════════════════════════════════════════════════════════════════════════
#  RAGAS LLM & Embeddings setup
# ═══════════════════════════════════════════════════════════════════════════

def make_ragas_llm(settings):
    """Create LangChain ChatOpenAI wrapper for RAGAS LLM-as-judge.

    Uses JUDGE_LLM_* env vars if configured (for decoupling judge model
    from RAG pipeline model). Falls back to LLM_* settings otherwise.
    """
    judge_key = os.environ.get("JUDGE_LLM_API_KEY", "")
    judge_url = os.environ.get("JUDGE_LLM_API_URL", "")
    judge_model = os.environ.get("JUDGE_LLM_MODEL", "")

    if judge_key:
        api_key = judge_key
        api_url = judge_url or "https://api.openai.com/v1"
        model = judge_model or "gpt-4o-mini"
        logger.info("RAGAS judge LLM: %s (from JUDGE_LLM_* env)", model)
    elif settings.llm_api_key:
        api_key = settings.llm_api_key
        api_url = settings.llm_api_url or "https://api.openai.com/v1"
        model = settings.llm_model or "gpt-4o-mini"
        logger.info("RAGAS judge LLM: %s (from LLM_* settings)", model)
    elif settings.openai_api_key:
        api_key = settings.openai_api_key
        api_url = "https://api.openai.com/v1"
        model = "gpt-4o-mini"
    elif settings.deepseek_api_key:
        api_key = settings.deepseek_api_key
        api_url = "https://api.deepseek.com/v1"
        model = "deepseek-chat"
    else:
        logger.error("No LLM API key configured for RAGAS judge")
        return None

    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=model,
        openai_api_key=api_key,
        openai_api_base=api_url,
        temperature=0,
        timeout=180,
        max_retries=3,
    )


def make_ragas_embeddings(settings):
    """Create LangChain OpenAIEmbeddings wrapper for RAGAS answer_relevancy metric."""
    api_key = settings.embedding_api_key
    api_url = settings.embedding_api_url or "https://api.openai.com/v1"
    model = settings.embedding_model or "text-embedding-3-small"
    if not api_key:
        logger.warning("No embedding API key — answer_relevancy may fail")
        return None

    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(
        model=model,
        openai_api_key=api_key,
        openai_api_base=api_url,
        timeout=30,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  RAG data collection (search → question/answer/contexts/ground_truth)
# ═══════════════════════════════════════════════════════════════════════════

async def collect_rag_data(
    rag_service,
    golden_entries: list[dict],
    mode: str,
) -> list[dict]:
    """Run RAG search for each golden entry and collect RAGAS-formatted data.

    Returns list of dicts with keys: question, answer, contexts, ground_truth
    """
    rag_data: list[dict] = []
    total = len(golden_entries)
    logger.info("=== %s mode: collecting RAG data for %d entries ===", mode, total)

    for i, entry in enumerate(golden_entries, 1):
        query = entry["query"]
        ground_truth = entry.get("ground_truth_answer", "")

        try:
            answer, chunks = await rag_service.search(query)
        except Exception as e:
            logger.warning("Search failed for %s: %s", entry.get("query_id", "?"), e)
            answer, chunks = "", []

        contexts = [c.get("content", "") for c in chunks if c.get("content")]

        rag_data.append({
            "question": query,
            "answer": answer,
            "contexts": contexts,
            "ground_truth": ground_truth,
        })

        if i % PROGRESS_INTERVAL == 0 or i == total:
            logger.info("  [%s] %d/%d — collected", mode, i, total)

    return rag_data


# ═══════════════════════════════════════════════════════════════════════════
#  RAGAS evaluation
# ═══════════════════════════════════════════════════════════════════════════

def run_ragas_evaluate(
    rag_data: list[dict],
    ragas_llm,
    ragas_embeddings,
    mode: str,
) -> dict:
    """Run RAGAS evaluate() on collected data.

    Returns a dict with per-entry results and mode-level averages.
    """
    from datasets import Dataset
    from ragas import evaluate
    from ragas.metrics import (
        faithfulness,
        answer_relevancy,
        context_precision,
        context_recall,
        answer_correctness,
    )

    # Filter out entries with empty answer or empty contexts (RAGAS will error)
    valid_data = [
        d for d in rag_data
        if d["answer"] and d["answer"].strip()
        and d["contexts"] and any(c.strip() for c in d["contexts"])
    ]
    skipped = len(rag_data) - len(valid_data)
    if skipped > 0:
        logger.warning("[%s] Skipped %d entries with empty answer/contexts", mode, skipped)

    if not valid_data:
        logger.error("[%s] No valid entries to evaluate", mode)
        return _empty_result(mode, len(rag_data))

    logger.info("[%s] Running RAGAS evaluate on %d entries...", mode, len(valid_data))

    dataset = Dataset.from_list(valid_data)

    metrics = [
        faithfulness,
        answer_relevancy,
        context_precision,
        context_recall,
        answer_correctness,
    ]

    try:
        result = evaluate(
            dataset=dataset,
            metrics=metrics,
            llm=ragas_llm,
            embeddings=ragas_embeddings,
            raise_exceptions=False,
            show_progress=True,
        )
    except Exception as e:
        logger.error("[%s] RAGAS evaluate failed: %s", mode, e)
        return _empty_result(mode, len(rag_data))

    # Extract results
    result_df = result.to_pandas()
    metric_names = [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
        "answer_correctness",
    ]

    per_entry: list[dict] = []
    metric_sums = {m: 0.0 for m in metric_names}
    count = 0

    for idx in range(len(result_df)):
        row = result_df.iloc[idx]
        entry_result: dict = {}
        for m in metric_names:
            val = row.get(m, 0.0)
            if val is None or (isinstance(val, float) and val != val):
                val = 0.0
            entry_result[m] = round(float(val), 4)
            metric_sums[m] += float(val)
        per_entry.append(entry_result)
        count += 1

    averages = {m: round(metric_sums[m] / count, 4) if count else 0.0 for m in metric_names}

    return {
        "mode": mode,
        "total_entries": len(rag_data),
        "evaluated_entries": count,
        "skipped_entries": skipped,
        "averages": averages,
        "per_entry": per_entry,
    }


def _empty_result(mode: str, total: int) -> dict:
    return {
        "mode": mode,
        "total_entries": total,
        "evaluated_entries": 0,
        "skipped_entries": total,
        "averages": {
            "faithfulness": 0.0,
            "answer_relevancy": 0.0,
            "context_precision": 0.0,
            "context_recall": 0.0,
            "answer_correctness": 0.0,
        },
        "per_entry": [],
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Report generation
# ═══════════════════════════════════════════════════════════════════════════

def save_mode_json(mode_result: dict, results_dir: Path) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{mode_result['mode']}_ragas_results.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mode_result, f, ensure_ascii=False, indent=2)
    logger.info("Saved: %s", path)
    return path


def generate_comparison_report(
    mode_results: list[dict],
    settings,
    limit: int | None,
    results_dir: Path,
) -> Path:
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "ragas_comparison_report.md"

    metrics_list = [
        "faithfulness", "answer_relevancy", "context_precision",
        "context_recall", "answer_correctness",
    ]
    mode_names = [r["mode"] for r in mode_results]

    best_mode: dict[str, str] = {}
    for metric in metrics_list:
        best_val = -1.0
        best_m = ""
        for mr in mode_results:
            val = mr["averages"].get(metric, 0.0)
            if val > best_val:
                best_val = val
                best_m = mr["mode"]
        best_mode[metric] = best_m

    lines: list[str] = []
    lines.append("# RAGAS Evaluation Comparison Report")
    lines.append("")
    lines.append("> 评测框架: [RAGAS](https://github.com/explodinggradients/ragas) v0.2.x")
    lines.append("> 数据集: CRUD-RAG (中文新闻 QA)")
    lines.append("")

    # Run metadata
    lines.append("## Run Metadata")
    lines.append("")
    lines.append(f"- **Timestamp**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    entry_count = mode_results[0]["total_entries"] if mode_results else 0
    lines.append(f"- **Total Entries**: {entry_count}")
    lines.append(f"- **Limit**: {limit if limit else 'None (full run)'}")
    lines.append(f"- **RAG Top-K (config)**: {getattr(settings, 'rag_top_k', 'N/A')}")
    lines.append(f"- **Chunk Size**: {getattr(settings, 'rag_chunk_size', 'N/A')}")
    lines.append(f"- **Chunk Overlap**: {getattr(settings, 'rag_chunk_overlap', 'N/A')}")
    lines.append(f"- **Semantic Weight**: {getattr(settings, 'rag_semantic_weight', 'N/A')}")
    lines.append(f"- **KG Weight**: {getattr(settings, 'kg_weight', 'N/A')}")
    lines.append(f"- **RRF k**: {getattr(settings, 'rag_rrf_constant_k', 'N/A')}")
    lines.append(f"- **Rewrite Enabled**: {getattr(settings, 'rag_rewrite_enabled', 'N/A')}")
    lines.append(f"- **Rerank Enabled**: {getattr(settings, 'rag_rerank_enabled', 'N/A')}")
    lines.append(f"- **RAG LLM Model**: {getattr(settings, 'llm_model', 'N/A')}")
    judge_model_name = os.environ.get("JUDGE_LLM_MODEL", getattr(settings, "llm_model", "N/A"))
    lines.append(f"- **Judge LLM Model**: {judge_model_name}")
    lines.append(f"- **Embedding Model**: {getattr(settings, 'embedding_model', 'N/A')}")
    lines.append("")

    # Metrics explanation
    lines.append("## RAGAS Metrics Explained")
    lines.append("")
    lines.append("| Metric | Layer | Description | Range |")
    lines.append("|--------|-------|-------------|-------|")
    lines.append("| **faithfulness** | Generation | 答案是否忠于检索上下文（无幻觉） | [0, 1] |")
    lines.append("| **answer_relevancy** | Generation | 答案与问题的相关程度 | [0, 1] |")
    lines.append("| **context_precision** | Retrieval | 检索上下文中的相关信息占比（信噪比） | [0, 1] |")
    lines.append("| **context_recall** | Retrieval | 检索是否覆盖了回答所需的全部信息 | [0, 1] |")
    lines.append("| **answer_correctness** | End-to-end | 答案与标准答案的语义一致性 | [0, 1] |")
    lines.append("")

    # Metrics table
    lines.append("## Metrics Comparison")
    lines.append("")
    header = "| Metric | " + " | ".join(f"{m}" for m in mode_names) + " |"
    separator = "|--------|" + "|".join(["--------" for _ in mode_names]) + "|"
    lines.append(header)
    lines.append(separator)

    for metric in metrics_list:
        row = f"| {metric} |"
        for mr in mode_results:
            val = mr["averages"].get(metric, 0.0)
            m = mr["mode"]
            if best_mode[metric] == m:
                row += f" **{val:.4f}** |"
            else:
                row += f" {val:.4f} |"
        lines.append(row)
    lines.append("")

    # Best mode summary
    lines.append("## Best Mode Summary")
    lines.append("")
    for metric in metrics_list:
        lines.append(f"- **{metric}**: {best_mode[metric]}")
    lines.append("")

    # Per-mode details
    lines.append("## Per-Mode Details")
    lines.append("")
    for mr in mode_results:
        lines.append(f"### {mr['mode']}")
        lines.append(f"- Total entries: {mr['total_entries']}")
        lines.append(f"- Evaluated entries: {mr['evaluated_entries']}")
        lines.append(f"- Skipped (empty answer/contexts): {mr['skipped_entries']}")
        lines.append(f"- Averages:")
        for metric in metrics_list:
            val = mr["averages"].get(metric, 0.0)
            lines.append(f"  - {metric}: {val:.4f}")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Saved: %s", report_path)
    return report_path


# ═══════════════════════════════════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    parser = argparse.ArgumentParser(
        description="RAGAS Evaluation Runner — RAGAS framework integration"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Randomly sample N golden entries (seed=42). Default: all.",
    )
    parser.add_argument(
        "--skip-ingest", action="store_true",
        help="Skip corpus ingestion (use if corpus is already indexed).",
    )
    parser.add_argument(
        "--modes", type=str, default=None,
        help="Comma-separated modes to run (e.g. 'hybrid' or 'dense,hybrid'). "
             "Default: all 3 modes (dense,bm25,hybrid).",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("RAGAS Evaluation Runner")
    logger.info("Framework: RAGAS v0.2.x")
    logger.info("Limit: %s", args.limit if args.limit else "None (full run)")
    logger.info("=" * 60)

    # Load data
    if not CORPUS_PATH.exists():
        logger.error("corpus.jsonl not found. Run prepare_corpus.py first.")
        sys.exit(1)
    if not GOLDEN_PATH.exists():
        logger.error("golden.jsonl not found. Run prepare_golden.py first.")
        sys.exit(1)

    corpus = load_jsonl(CORPUS_PATH)
    golden = load_jsonl(GOLDEN_PATH)
    logger.info("Loaded corpus: %d docs", len(corpus))
    logger.info("Loaded golden: %d entries", len(golden))

    # Apply --limit sampling
    if args.limit and args.limit < len(golden):
        random.seed(RANDOM_SEED)
        golden = random.sample(golden, args.limit)
        logger.info("Sampled %d golden entries (seed=%d)", len(golden), RANDOM_SEED)

    # Initialize RAG service
    rag_service, settings, infra = await init_rag_service()
    if rag_service is None:
        logger.error("RAGService initialization failed. Exiting.")
        sys.exit(1)

    # Save original backend references
    saved_backends = save_backends(rag_service)
    logger.info("Saved backend functions: %s", list(saved_backends.keys()))

    # Ingest corpus
    if not args.skip_ingest:
        await ingest_corpus(rag_service, corpus)
    else:
        logger.info("Skipping corpus ingestion (--skip-ingest)")

    if not rag_service.engine.loaded:
        logger.warning("RAGEngine.loaded is False — search will return empty results")

    # Setup RAGAS LLM & embeddings
    ragas_llm = make_ragas_llm(settings)
    if ragas_llm is None:
        logger.error("RAGAS LLM not available. Exiting.")
        sys.exit(1)
    logger.info("RAGAS LLM configured")

    ragas_embeddings = make_ragas_embeddings(settings)
    if ragas_embeddings:
        logger.info("RAGAS embeddings configured")
    else:
        logger.warning("RAGAS embeddings not available — answer_relevancy may be skipped")

    # Determine modes
    run_modes = [m.strip() for m in args.modes.split(",")] if args.modes else list(MODES)
    for m in run_modes:
        if m not in MODES:
            logger.error("Unknown mode '%s'. Valid: %s", m, ", ".join(MODES))
            sys.exit(1)

    logger.info("Modes to run: %s", ", ".join(run_modes))

    # Run evaluation
    all_mode_results: list[dict] = []
    for mode in run_modes:
        switch_mode(rag_service, mode, saved_backends)

        # Step 1: Collect RAG data (search)
        rag_data = await collect_rag_data(rag_service, golden, mode)

        # Step 2: Run RAGAS evaluate
        mode_result = run_ragas_evaluate(rag_data, ragas_llm, ragas_embeddings, mode)
        save_mode_json(mode_result, RAGAS_RESULTS_DIR)
        all_mode_results.append(mode_result)

    # Load existing results for skipped modes
    load_modes = [m for m in MODES if m not in run_modes]
    for mode in load_modes:
        json_path = RAGAS_RESULTS_DIR / f"{mode}_ragas_results.json"
        if json_path.exists():
            logger.info("Loading existing results for '%s' from %s", mode, json_path)
            with open(json_path, "r", encoding="utf-8") as f:
                all_mode_results.append(json.load(f))
        else:
            logger.warning("No existing results for '%s' — skipping", mode)

    # Sort results by MODES order
    mode_order = {m: i for i, m in enumerate(MODES)}
    all_mode_results.sort(key=lambda r: mode_order.get(r["mode"], 99))

    # Restore hybrid mode
    switch_mode(rag_service, "hybrid", saved_backends)

    # Generate comparison report
    generate_comparison_report(all_mode_results, settings, args.limit, RAGAS_RESULTS_DIR)

    # Print summary
    logger.info("=" * 60)
    logger.info("RAGAS Evaluation Complete!")
    logger.info("=" * 60)
    for mr in all_mode_results:
        avg = mr["averages"]
        logger.info(
            "%s mode: faith=%.4f, ans_rel=%.4f, ctx_prec=%.4f, ctx_rec=%.4f, ans_corr=%.4f",
            mr["mode"],
            avg.get("faithfulness", 0),
            avg.get("answer_relevancy", 0),
            avg.get("context_precision", 0),
            avg.get("context_recall", 0),
            avg.get("answer_correctness", 0),
        )
    logger.info("Results saved to: %s", RAGAS_RESULTS_DIR)

    # Cleanup
    try:
        from app.infra.factory import shutdown_infrastructure
        await shutdown_infrastructure(infra)
    except Exception as e:
        logger.warning("Failed to cleanup infrastructure: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
