#!/usr/bin/env python
"""RAG Evaluation Runner — three-mode (dense / bm25 / hybrid) ablation.

Executes the full evaluation pipeline:
  1. Load corpus.jsonl and golden.jsonl
  2. Ingest corpus into RAG (PG + Milvus + ES)
  3. For each mode (dense, bm25, hybrid):
     - Switch retrieval backends
     - For each golden entry: search → compute retrieval + generation metrics
     - Save per-mode JSON results
  4. Generate Markdown comparison report

Usage:
    cd eval && python run_eval.py --limit 200    # quick validation
    cd eval && python run_eval.py                 # full evaluation (2394 entries × 3 modes)

Requires: Docker Compose (PG + Milvus + ES) running + backend .env.local configured.
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

# Add backend to sys.path so we can import app modules
sys.path.insert(0, str(BACKEND_DIR))
# Add eval dir to sys.path so we can import metrics
sys.path.insert(0, str(SCRIPT_DIR))

# ─── File paths ────────────────────────────────────────────────────────────
CORPUS_PATH = SCRIPT_DIR / "corpus.jsonl"
GOLDEN_PATH = SCRIPT_DIR / "golden.jsonl"
RESULTS_DIR = SCRIPT_DIR / "results"

# ─── Evaluation config ─────────────────────────────────────────────────────
MODES = ["dense", "bm25", "hybrid"]
RANDOM_SEED = 42
TOP_K = 5  # K for retrieval metrics (recall@K, precision@K, ndcg@K)
PROGRESS_INTERVAL = 50  # Log progress every N entries

# ─── Logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    force=True,
)
# Suppress noisy logs
for _noisy in ("pymilvus", "elastic_transport", "sqlalchemy", "neo4j.notifications", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger("eval")


# ═══════════════════════════════════════════════════════════════════════════
#  Data loading
# ═══════════════════════════════════════════════════════════════════════════

def load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file into a list of dicts."""
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ═══════════════════════════════════════════════════════════════════════════
#  RAG service initialization
# ═══════════════════════════════════════════════════════════════════════════

async def init_rag_service():
    """Initialize RAGService with infrastructure backends.

    Mirrors the wiring in backend/app/main.py but without the FastAPI server.
    Changes CWD to backend/ so pydantic-settings can find .env.local.
    """
    # Change to backend dir so pydantic-settings finds .env.local
    original_cwd = os.getcwd()
    os.chdir(str(BACKEND_DIR))

    from app.config import apply_env_overrides, get_settings
    from app.db.engine import init_db
    from app.services.rag_service import RAGService

    apply_env_overrides()
    settings = get_settings()

    # Restore CWD (settings are cached by lru_cache, safe to switch back)
    os.chdir(original_cwd)

    await init_db()


    from app.infra.factory import build_infrastructure
    infra = build_infrastructure(settings)

    rag_service = RAGService(settings)

    # Wire Milvus
    if infra and infra.milvus_client:
        _wire_milvus(rag_service, infra.milvus_client, settings)
        logger.info("Milvus backend wired")
    else:
        logger.error("Milvus not available — cannot run evaluation")
        return None, None

    # Wire ES
    if infra and infra.es_client:
        _wire_es(rag_service, infra.es_client)
        logger.info("Elasticsearch backend wired")
    else:
        logger.error("Elasticsearch not available — cannot run evaluation")
        return None, None

    # Inject embed_fn
    embed_fn = _make_embed_fn(settings)
    if embed_fn:
        rag_service.set_embed_fn(embed_fn)
        logger.info("embed_fn injected (model=%s)", settings.embedding_model)
    else:
        logger.error("embed_fn not available — cannot run evaluation")
        return None, None

    # Inject generate_fn
    generate_fn = _make_generate_fn(settings)
    if generate_fn:
        rag_service.set_generate_fn(generate_fn)
        logger.info("generate_fn injected")
    else:
        logger.error("generate_fn not available — cannot run evaluation")
        return None, None

    await rag_service.initialize()
    logger.info("RAGService initialized: mode=%s", rag_service.hybrid.mode())
    return rag_service, settings, infra


def _make_embed_fn(settings):
    """Create embedding function using OpenAI-compatible API."""
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
    """Create LLM generate function using OpenAI-compatible API."""
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
    """Wire MilvusClient into RAGService's HybridStore."""
    from app.main import _wire_milvus_to_rag
    _wire_milvus_to_rag(rag_service, milvus_client, settings)


def _wire_es(rag_service, es_client):
    """Wire AsyncElasticsearch into RAGService's HybridStore."""
    from app.main import _wire_es_to_rag
    _wire_es_to_rag(rag_service, es_client)


# ═══════════════════════════════════════════════════════════════════════════
#  Corpus ingestion
# ═══════════════════════════════════════════════════════════════════════════

async def ingest_corpus(rag_service, corpus: list[dict]) -> int:
    """Ingest all corpus documents into RAG (PG + Milvus + ES).

    RAGEngine auto-deduplicates by sha256(doc), so duplicate documents are skipped.
    Returns the total number of chunks ingested.
    """
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
            logger.info("Ingest progress: %d/%d docs (%d chunks total)", i, total_docs, total_chunks)

    logger.info("Corpus ingestion complete: %d docs, %d chunks", total_docs, total_chunks)
    return total_chunks


# ═══════════════════════════════════════════════════════════════════════════
#  Mode switching
# ═══════════════════════════════════════════════════════════════════════════

def save_backends(rag_service):
    """Save original backend function references for later restoration."""
    hybrid = rag_service.hybrid
    return {
        "milvus_search_fn": hybrid._milvus_search_fn,
        "milvus_insert_fn": hybrid._milvus_insert_fn,
        "es_search_fn": hybrid._es_search_fn,
        "es_index_fn": hybrid._es_index_fn,
    }


def switch_mode(rag_service, mode: str, saved: dict) -> str:
    """Switch retrieval mode by re-injecting backend functions.

    Args:
        mode: One of "dense", "bm25", "hybrid".
        saved: Dict of saved backend function references.

    Returns:
        The hybrid.mode() string after switching.
    """
    milvus_search = saved["milvus_search_fn"]
    milvus_insert = saved["milvus_insert_fn"]
    es_search = saved["es_search_fn"]
    es_index = saved["es_index_fn"]

    if mode == "dense":
        # Milvus only, ES disabled
        rag_service.set_milvus_backend(milvus_search, milvus_insert)
        rag_service.set_es_backend(None)
    elif mode == "bm25":
        # ES only, Milvus disabled
        rag_service.set_milvus_backend(None)
        rag_service.set_es_backend(es_search, es_index)
    elif mode == "hybrid":
        # Both Milvus and ES enabled
        rag_service.set_milvus_backend(milvus_search, milvus_insert)
        rag_service.set_es_backend(es_search, es_index)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    actual_mode = rag_service.hybrid.mode()
    expected = {"dense": "semantic", "bm25": "keyword", "hybrid": "hybrid"}
    if actual_mode != expected[mode]:
        logger.warning(
            "Mode mismatch: expected %s → hybrid.mode()=%s, got %s",
            mode, expected[mode], actual_mode,
        )
    else:
        logger.info("Switched to %s mode → hybrid.mode()=%s", mode, actual_mode)

    return actual_mode


# ═══════════════════════════════════════════════════════════════════════════
#  Evaluation loop
# ═══════════════════════════════════════════════════════════════════════════

async def evaluate_mode(
    rag_service,
    generate_fn,
    golden_entries: list[dict],
    mode: str,
) -> dict:
    """Run evaluation for a single mode across all golden entries.

    Returns a dict with per-entry metrics and mode-level averages.
    """
    from metrics import (
        recall_at_k, precision_at_k, mrr, ndcg_at_k,
        faithfulness, answer_relevance, answer_quality,
    )

    per_entry_results: list[dict] = []
    metric_sums = {m: 0.0 for m in [
        "recall_at_k", "precision_at_k", "mrr", "ndcg_at_k",
        "faithfulness", "answer_relevance", "answer_quality",
    ]}
    count = 0
    gen_count = 0  # Entries where generation metrics were computed

    total = len(golden_entries)
    logger.info("=== %s mode: evaluating %d entries ===", mode, total)

    for i, entry in enumerate(golden_entries, 1):
        query = entry["query"]
        relevant_docs = entry["relevant_docs"]
        ground_truth = entry.get("ground_truth_answer", "")
        query_id = entry["query_id"]

        # Search
        try:
            answer, chunks = await rag_service.search(query)
        except Exception as e:
            logger.warning("Search failed for %s: %s", query_id, e)
            answer, chunks = "", []

        # Extract retrieved chunk contents
        retrieved = [c.get("content", "") for c in chunks if c.get("content")]

        # Retrieval metrics
        r_recall = recall_at_k(retrieved, relevant_docs, TOP_K)
        r_precision = precision_at_k(retrieved, relevant_docs, TOP_K)
        r_mrr = mrr(retrieved, relevant_docs)
        r_ndcg = ndcg_at_k(retrieved, relevant_docs, TOP_K)

        # Generation metrics (only if answer is non-empty)
        g_faith = 0.0
        g_relevance = 0.0
        g_quality = 0.0
        if answer and answer.strip() and not answer.startswith("No relevant content"):
            context = "\n\n".join(retrieved) if retrieved else ""
            if context:
                g_faith = faithfulness(query, answer, context, generate_fn)
            g_relevance = answer_relevance(query, answer, generate_fn)
            if ground_truth:
                g_quality = answer_quality(ground_truth, answer, generate_fn)
            gen_count += 1

        # Record per-entry result
        entry_result = {
            "query_id": query_id,
            "query": query[:100],  # Truncate for storage
            "task_type": entry.get("task_type", ""),
            "num_relevant_docs": entry.get("num_relevant_docs", 0),
            "num_retrieved": len(retrieved),
            "recall_at_k": round(r_recall, 4),
            "precision_at_k": round(r_precision, 4),
            "mrr": round(r_mrr, 4),
            "ndcg_at_k": round(r_ndcg, 4),
            "faithfulness": round(g_faith, 4),
            "answer_relevance": round(g_relevance, 4),
            "answer_quality": round(g_quality, 4),
        }
        per_entry_results.append(entry_result)

        # Accumulate sums
        metric_sums["recall_at_k"] += r_recall
        metric_sums["precision_at_k"] += r_precision
        metric_sums["mrr"] += r_mrr
        metric_sums["ndcg_at_k"] += r_ndcg
        metric_sums["faithfulness"] += g_faith
        metric_sums["answer_relevance"] += g_relevance
        metric_sums["answer_quality"] += g_quality
        count += 1

        # Progress logging every PROGRESS_INTERVAL entries
        if i % PROGRESS_INTERVAL == 0 or i == total:
            avg_recall = metric_sums["recall_at_k"] / count if count else 0
            avg_mrr = metric_sums["mrr"] / count if count else 0
            avg_ndcg = metric_sums["ndcg_at_k"] / count if count else 0
            logger.info(
                "  [%s] %d/%d — recall@%d=%.4f, mrr=%.4f, ndcg@%d=%.4f",
                mode, i, total, TOP_K, avg_recall, avg_mrr, TOP_K, avg_ndcg,
            )

    # Compute mode-level averages
    averages = {}
    for metric, total_val in metric_sums.items():
        if metric in ("faithfulness", "answer_relevance", "answer_quality"):
            # Generation metrics averaged over entries where they were computed
            averages[metric] = round(total_val / gen_count, 4) if gen_count else 0.0
        else:
            averages[metric] = round(total_val / count, 4) if count else 0.0

    return {
        "mode": mode,
        "total_entries": count,
        "generation_entries": gen_count,
        "averages": averages,
        "per_entry": per_entry_results,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Report generation
# ═══════════════════════════════════════════════════════════════════════════

def save_mode_json(mode_result: dict, results_dir: Path) -> Path:
    """Save per-mode results to JSON file."""
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{mode_result['mode']}_results.json"
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
    """Generate Markdown comparison report with 7×3 metrics table.

    Args:
        mode_results: List of per-mode result dicts.
        settings: Settings object for RAG config metadata.
        limit: --limit parameter value (None = full run).
        results_dir: Directory to save the report.

    Returns:
        Path to the generated report file.
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    report_path = results_dir / "comparison_report.md"

    metrics_list = [
        "recall_at_k", "precision_at_k", "mrr", "ndcg_at_k",
        "faithfulness", "answer_relevance", "answer_quality",
    ]
    mode_names = [r["mode"] for r in mode_results]

    # Build metric → best mode mapping (higher is better)
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
    lines.append("# RAG Evaluation Comparison Report")
    lines.append("")

    # Run metadata
    lines.append("## Run Metadata")
    lines.append("")
    lines.append(f"- **Timestamp**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    entry_count = mode_results[0]["total_entries"] if mode_results else 0
    lines.append(f"- **Total Entries**: {entry_count}")
    lines.append(f"- **Limit**: {limit if limit else 'None (full run)'}")
    lines.append(f"- **Top-K**: {TOP_K}")
    lines.append(f"- **Chunk Size**: {getattr(settings, 'rag_chunk_size', 'N/A')}")
    lines.append(f"- **Chunk Overlap**: {getattr(settings, 'rag_chunk_overlap', 'N/A')}")
    lines.append(f"- **Rewrite Enabled**: {getattr(settings, 'rag_rewrite_enabled', 'N/A')}")
    lines.append(f"- **Rerank Enabled**: {getattr(settings, 'rag_rerank_enabled', 'N/A')}")
    lines.append("")

    # Metrics table
    lines.append("## Metrics Comparison")
    lines.append("")
    # Header row
    header = "| Metric | " + " | ".join(f"{m}" for m in mode_names) + " |"
    separator = "|--------|" + "|".join(["--------" for _ in mode_names]) + "|"
    lines.append(header)
    lines.append(separator)

    for metric in metrics_list:
        row = f"| {metric} |"
        for mr in mode_results:
            val = mr["averages"].get(metric, 0.0)
            m = mr["mode"]
            # Bold the best mode
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
        lines.append(f"- Generation-evaluated entries: {mr['generation_entries']}")
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
        description="RAG Evaluation Runner — three-mode ablation (dense/bm25/hybrid)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Randomly sample N golden entries (seed=42 for reproducibility). "
             "Default: evaluate all entries.",
    )
    parser.add_argument(
        "--skip-ingest", action="store_true",
        help="Skip corpus ingestion (use if corpus is already indexed).",
    )
    parser.add_argument(
        "--modes", type=str, default=None,
        help="Comma-separated modes to run (e.g. 'hybrid' or 'dense,hybrid'). "
             "Modes not specified will be loaded from existing JSON results if available. "
             "Default: run all 3 modes (dense,bm25,hybrid).",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("RAG Evaluation Runner")
    logger.info("Mode: dense / bm25 / hybrid")
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

    # Ingest corpus (once, shared across all modes)
    if not args.skip_ingest:
        await ingest_corpus(rag_service, corpus)
    else:
        logger.info("Skipping corpus ingestion (--skip-ingest)")

    # Ensure engine.loaded is True (set by ingest or initialize)
    if not rag_service.engine.loaded:
        logger.warning("RAGEngine.loaded is False — search will return empty results")

    # Get generate_fn for LLM-as-judge
    generate_fn = rag_service._generate_fn
    if generate_fn is None:
        logger.warning("generate_fn not available — generation metrics will be 0.0")

    # Determine which modes to run vs. load from existing results
    run_modes = [m.strip() for m in args.modes.split(",")] if args.modes else list(MODES)
    for m in run_modes:
        if m not in MODES:
            logger.error("Unknown mode '%s'. Valid: %s", m, ", ".join(MODES))
            sys.exit(1)
    load_modes = [m for m in MODES if m not in run_modes]

    logger.info("Modes to run: %s", ", ".join(run_modes))
    if load_modes:
        logger.info("Modes to load from existing JSON: %s", ", ".join(load_modes))

    # Run evaluation for specified modes
    all_mode_results: list[dict] = []
    for mode in run_modes:
        switch_mode(rag_service, mode, saved_backends)
        mode_result = await evaluate_mode(rag_service, generate_fn, golden, mode)
        save_mode_json(mode_result, RESULTS_DIR)
        all_mode_results.append(mode_result)

    # Load existing results for skipped modes
    for mode in load_modes:
        json_path = RESULTS_DIR / f"{mode}_results.json"
        if json_path.exists():
            logger.info("Loading existing results for '%s' from %s", mode, json_path)
            with open(json_path, "r", encoding="utf-8") as f:
                all_mode_results.append(json.load(f))
        else:
            logger.warning("No existing results for '%s' at %s — skipping in report", mode, json_path)

    # Sort results by MODES order for consistent report layout
    mode_order = {m: i for i, m in enumerate(MODES)}
    all_mode_results.sort(key=lambda r: mode_order.get(r["mode"], 99))

    # Restore hybrid mode
    switch_mode(rag_service, "hybrid", saved_backends)

    # Generate comparison report
    generate_comparison_report(all_mode_results, settings, args.limit, RESULTS_DIR)

    # Print summary
    logger.info("=" * 60)
    logger.info("Evaluation Complete!")
    logger.info("=" * 60)
    for mr in all_mode_results:
        logger.info(
            "%s mode: recall@%d=%.4f, mrr=%.4f, ndcg@%d=%.4f, faith=%.4f",
            mr["mode"], TOP_K,
            mr["averages"]["recall_at_k"],
            mr["averages"]["mrr"],
            TOP_K, mr["averages"]["ndcg_at_k"],
            mr["averages"]["faithfulness"],
        )
    logger.info("Results saved to: %s", RESULTS_DIR)

    # ─── Cleanup async clients ────────────────────────────────────────────────
    try:
        from app.infra.factory import shutdown_infrastructure
        await shutdown_infrastructure(infra)
    except Exception as e:
        logger.warning("Failed to cleanup infrastructure: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
