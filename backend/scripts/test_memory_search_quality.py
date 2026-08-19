#!/usr/bin/env python3
"""Manual quality probe for file-native memory search.

Runs against the real local memory workspace (not pytest fixtures).

Usage (from backend/):
  .venv/Scripts/python.exe scripts/test_memory_search_quality.py
  .venv/Scripts/python.exe scripts/test_memory_search_quality.py 任务 python powershell
  .venv/Scripts/python.exe scripts/test_memory_search_quality.py --reindex --top-k 10 任务
  .venv/Scripts/python.exe scripts/test_memory_search_quality.py --bucket procedure 部署

Exit code:
  0 = no hard failures
  1 = one or more hard-irrelevant hits, or empty expected hits
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow `python scripts/...` from backend/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.memory.memory_service import MemoryService  # noqa: E402
from app.memory.search.bm25_index import _tokenize  # noqa: E402


DEFAULT_QUERIES = [
    "python",
    "typescript",
    "powershell",
    "xss",
    "todo",
    "任务",
    "小说",
    "部署",
    "agent",
    "escape",
    "task_complete",
    "doudou",
    "textContent",
    "AI Agent",
    "引号",
    "zzzznotexist123",
]

# Queries that MUST return zero hits
EMPTY_EXPECTED = {"zzzznotexist123"}

# Very common tokens that should not alone prove relevance
STOP = {
    "from", "this", "that", "with", "when", "daily", "session", "agent",
    "memory", "task", "file", "code", "true", "false", "name", "path",
    "steps", "content", "list", "user", "default", "complete", "success",
}


def _setup_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def _norm(p: str) -> str:
    return str(p).replace("\\", "/")


def _tokens(q: str) -> list[str]:
    q = q.strip().lower()
    if not q:
        return []
    # Keep CJK chars as individual tokens + ascii words length>=2
    out: list[str] = []
    for m in re.finditer(r"[a-z0-9_]{2,}|[一-鿿]", q):
        t = m.group(0)
        if t not in STOP:
            out.append(t)
    # Also keep original multi-char CJK substrings of len>=2 for soft match
    cjk = re.findall(r"[一-鿿]{2,}", q)
    out.extend(cjk)
    # de-dupe preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


@dataclass
class HitJudge:
    path: str
    name: str
    source: str
    score: float
    verdict: str  # HARD_OK | SOFT_OK | IRRELEVANT | EMPTY
    reason: str
    snippet: str


def judge_hit(query: str, path: str, name: str, content: str, source: str, score: float) -> HitJudge:
    path_n = _norm(path)
    hay = f"{name}\n{path_n}\n{content[:800]}".lower()
    toks = _tokens(query)
    if not toks:
        return HitJudge(path_n, name, source, score, "SOFT_OK", "empty query tokens", "")

    # Hard: full query string in hay, or any ascii token length>=3, or CJK bigram
    full = query.strip().lower()
    hard_hits = []
    soft_hits = []
    if full and full in hay:
        hard_hits.append(f"full:{full}")
    for t in toks:
        if t in hay:
            if len(t) >= 3 or re.fullmatch(r"[一-鿿]{2,}", t):
                hard_hits.append(t)
            else:
                soft_hits.append(t)

    # Find a short evidence line
    snippet = ""
    for line in (content or "").splitlines():
        low = line.lower()
        if any(t in low for t in toks if len(t) >= 2) or (full and full in low):
            snippet = line.strip()[:160]
            break
    if not snippet:
        # path/name only
        if any(t in path_n.lower() or t in name.lower() for t in toks):
            snippet = f"[path/name] {name}"

    if hard_hits:
        return HitJudge(path_n, name, source, score, "HARD_OK", f"matched {hard_hits[:5]}", snippet)
    if soft_hits:
        return HitJudge(path_n, name, source, score, "SOFT_OK", f"weak tokens {soft_hits[:5]}", snippet)
    return HitJudge(
        path_n, name, source, score, "IRRELEVANT",
        "no query token in name/path/body preview",
        snippet or content[:120].replace("\n", " "),
    )


def fts_snippet(db_path: Path, query: str, path: str) -> str:
    if not db_path.exists():
        return ""
    try:
        conn = sqlite3.connect(str(db_path))
        tok = _tokenize(query)
        if not tok.strip():
            return ""
        # path may be posix or windows style in older indexes
        candidates = {path, path.replace("/", "\\"), path.replace("\\", "/")}
        for cand in candidates:
            row = conn.execute(
                "SELECT snippet(memory_fts, 2, '>>>', '<<<', '…', 18) "
                "FROM memory_fts WHERE path = ? AND memory_fts MATCH ? LIMIT 1",
                (cand, tok),
            ).fetchone()
            if row and row[0]:
                return str(row[0]).replace("\n", " ")
    except Exception as e:
        return f"(snippet err: {e})"
    return ""


async def run(args: argparse.Namespace) -> int:
    settings = Settings()
    root = Path(settings.memory_workspace_path)
    print(f"memory workspace: {root}")
    print(f"exists: {root.exists()}")
    if not root.exists():
        print("ERROR: memory workspace missing", file=sys.stderr)
        return 1

    svc = MemoryService(settings)
    await svc.initialize()
    if args.reindex:
        n = svc.auto_index.full_reindex()
        print(f"reindex: {n} files")

    bm_db = root / "metadata" / "bm25.db"
    wl_db = root / "metadata" / "wikilinks.db"
    if bm_db.exists():
        conn = sqlite3.connect(str(bm_db))
        total = conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
        buckets = conn.execute(
            "SELECT bucket, COUNT(*) FROM memory_fts GROUP BY bucket ORDER BY bucket"
        ).fetchall()
        print(f"bm25 docs: {total}  buckets: {dict(buckets)}")
    if wl_db.exists():
        wconn = sqlite3.connect(str(wl_db))
        preds = wconn.execute(
            "SELECT COALESCE(predicate,'(null)'), COUNT(*) FROM wikilinks GROUP BY predicate"
        ).fetchall()
        print(f"wikilink edges: {dict(preds)}")

    queries = args.queries or DEFAULT_QUERIES
    top_k = args.top_k
    bucket = args.bucket

    hard_fail = 0
    soft_warn = 0
    total_hits = 0

    print("\n" + "=" * 72)
    print("SEARCH QUALITY REPORT")
    print("=" * 72)

    for q in queries:
        print(f"\n--- query: {q!r}  bucket={bucket!r} top_k={top_k} ---")
        results = await svc.recall(q, top_k=top_k, bucket=bucket)
        total_hits += len(results)

        if q in EMPTY_EXPECTED:
            if results:
                hard_fail += 1
                print(f"  HARD FAIL: expected 0 hits, got {len(results)}")
            else:
                print("  OK empty")
            continue

        if not results:
            print("  (no hits)")
            # not always a hard fail — unknown corpus
            continue

        for i, r in enumerate(results, 1):
            j = judge_hit(q, r.path, r.name, r.content or "", r.source, r.score)
            snip = fts_snippet(bm_db, q, j.path) or j.snippet
            mark = {
                "HARD_OK": "OK ",
                "SOFT_OK": "~~ ",
                "IRRELEVANT": "BAD",
            }.get(j.verdict, "?? ")
            if j.verdict == "IRRELEVANT":
                hard_fail += 1
            elif j.verdict == "SOFT_OK":
                soft_warn += 1
            print(
                f"  [{mark}] #{i} score={r.score:.5f} source={r.source:8} "
                f"bm25={r.scores.get('bm25', 0):.5f} wl={r.scores.get('wikilink', 0):.5f}"
            )
            print(f"        path: {j.path}")
            print(f"        name: {j.name}")
            print(f"        why : {j.reason}")
            if snip:
                print(f"        snip: {snip}")

        # rank-1 must be hard-ok for non-trivial ascii queries length>=4
        first = judge_hit(q, results[0].path, results[0].name, results[0].content or "", results[0].source, results[0].score)
        if len(q.strip()) >= 4 and re.search(r"[A-Za-z]{4,}", q) and first.verdict == "IRRELEVANT":
            hard_fail += 1
            print("  HARD FAIL: rank-1 is IRRELEVANT for long ascii query")

    # Bucket smoke (only when user didn't pin a bucket)
    if bucket is None and not args.queries:
        print("\n" + "=" * 72)
        print("BUCKET FILTER SMOKE (query='python')")
        print("=" * 72)
        for b in ("procedure", "wiki", "daily"):
            res = await svc.recall("python", top_k=10, bucket=b)
            paths = [_norm(r.path) for r in res]
            print(f"  bucket={b}: {len(res)} hits")
            for p in paths:
                print(f"    - {p}")
            if b == "daily":
                bad = [p for p in paths if not p.startswith("daily/")]
            elif b in ("procedure", "wiki"):
                # indexed bucket field should match; path heuristic for digest
                bad = []
                for r, p in zip(res, paths):
                    fb = (r.frontmatter or {}).get("bucket")
                    # daily cards forced to bucket=daily in index; frontmatter may still say procedure
                    if b == "procedure" and not (
                        p.startswith("digest/procedure/") or fb == "procedure"
                    ):
                        # allow only procedure digest / shared
                        if "procedure" not in p and fb != "procedure":
                            bad.append(p)
                    if b == "wiki" and not (p.startswith("digest/wiki/") or fb == "wiki"):
                        bad.append(p)
            else:
                bad = []
            if bad:
                hard_fail += 1
                print(f"    HARD FAIL bucket leakage: {bad}")

    print("\n" + "=" * 72)
    print(
        f"SUMMARY: queries={len(queries)} hits={total_hits} "
        f"hard_fail={hard_fail} soft_warn={soft_warn}"
    )
    if hard_fail:
        print("RESULT: FAIL — search still returns hard-irrelevant hits")
        print("Tip: pass the exact UI query, e.g.")
        print("  .venv/Scripts/python.exe scripts/test_memory_search_quality.py --reindex 任务")
        return 1
    if soft_warn:
        print("RESULT: PASS WITH WARNINGS — weak token matches only")
        return 0
    print("RESULT: PASS")
    return 0


def main() -> None:
    _setup_stdio()
    parser = argparse.ArgumentParser(description="Probe memory search quality on real data")
    parser.add_argument("queries", nargs="*", help="queries to run (default: built-in suite)")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--bucket", default=None, help="optional bucket filter")
    parser.add_argument("--reindex", action="store_true", help="force full reindex before search")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
