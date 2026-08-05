#!/usr/bin/env python3
"""Full-corpus memory search audit.

Scans every daily/digest markdown in the real workspace, builds representative
queries from titles/tags, runs HybridSearch, and prints Markdown tables.

Usage (from backend/):
  .venv/Scripts/python.exe scripts/audit_memory_search_corpus.py
  .venv/Scripts/python.exe scripts/audit_memory_search_corpus.py --reindex
  .venv/Scripts/python.exe scripts/audit_memory_search_corpus.py --md report.md
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.memory.file_store.markdown_io import read_markdown  # noqa: E402
from app.memory.memory_service import MemoryService  # noqa: E402

STOP = {
    "from", "this", "that", "with", "when", "daily", "session", "agent",
    "memory", "task", "file", "code", "true", "false", "name", "path",
    "list", "user", "default", "complete", "success", "shared", "digest",
    "procedure", "wiki", "personal", "the", "and", "for", "md", "conv",
    "session_conv", "how", "what", "when", "where", "vs", "to", "of", "in",
    "a", "an", "or", "on", "is", "as", "by", "at",
}


def _setup_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def norm(p: str) -> str:
    return str(p).replace("\\", "/")


def tokens_from_text(text: str) -> list[str]:
    text = (text or "").lower()
    out: list[str] = []
    for m in re.finditer(r"[a-z][a-z0-9_+-]{2,}|[一-鿿]{2,}", text):
        t = m.group(0).strip("-_")
        if t and t not in STOP and len(t) >= 2:
            out.append(t)
    # de-dupe
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


@dataclass
class Doc:
    path: str
    name: str
    bucket: str
    tags: list[str]
    body: str
    stage: str  # daily | digest


@dataclass
class QueryResult:
    query: str
    kind: str  # title | tag | keyword | fixed
    expected_paths: set[str] = field(default_factory=set)
    hits: list[tuple[str, str, float, str]] = field(default_factory=list)
    # path, name, score, source
    rank1_ok: bool = False
    expected_in_top: bool = False
    bad_hits: list[str] = field(default_factory=list)
    soft_hits: list[str] = field(default_factory=list)
    note: str = ""


def judge_hit(query: str, path: str, name: str, content: str) -> str:
    """Return HARD_OK | SOFT_OK | IRRELEVANT."""
    q = query.strip().lower()
    hay = f"{name}\n{path}\n{(content or '')[:900]}".lower()
    if q and q in hay:
        return "HARD_OK"
    toks = tokens_from_text(query)
    hard = []
    soft = []
    for t in toks:
        if t in hay:
            if len(t) >= 3 or re.fullmatch(r"[一-鿿]{2,}", t):
                hard.append(t)
            else:
                soft.append(t)
    if hard:
        return "HARD_OK"
    if soft:
        return "SOFT_OK"
    # Chinese single-char fallback already covered by bigrams in tokens_from_text
    return "IRRELEVANT"


def build_docs(root: Path) -> list[Doc]:
    docs: list[Doc] = []
    for f in sorted(root.rglob("*.md")):
        if "metadata" in f.parts:
            continue
        mem = read_markdown(f)
        if mem is None:
            continue
        rel = f.relative_to(root).as_posix()
        stage = "daily" if rel.startswith("daily/") else "digest"
        # path-based bucket for daily
        bucket = "daily" if stage == "daily" else (mem.frontmatter.bucket or "wiki")
        docs.append(
            Doc(
                path=rel,
                name=mem.frontmatter.name or f.stem,
                bucket=bucket,
                tags=list(mem.frontmatter.tags or []),
                body=mem.body or "",
                stage=stage,
            )
        )
    return docs


def build_queries(docs: list[Doc]) -> list[tuple[str, str, set[str]]]:
    """Return list of (query, kind, expected_paths)."""
    queries: list[tuple[str, str, set[str]]] = []
    seen_q: set[str] = set()

    def add(q: str, kind: str, expected: set[str]) -> None:
        q = q.strip()
        if not q or len(q) < 2:
            return
        key = q.lower()
        if key in seen_q:
            # merge expected
            for i, (qq, kk, exp) in enumerate(queries):
                if qq.lower() == key:
                    queries[i] = (qq, kk, exp | expected)
                    return
            return
        seen_q.add(key)
        queries.append((q, kind, set(expected)))

    # Fixed regression suite covering known themes
    theme_map = {
        "python": {"python", "typescript", "agent"},
        "typescript": {"python", "typescript", "agent"},
        "powershell": {"powershell", "引号", "ps1", "escape"},
        "xss": {"xss", "escapehtml", "textcontent"},
        "todo": {"todo", "todolist", "localstorage"},
        "task_complete": {"task_complete", "版本冲突", "任务"},
        "doudou": {"doudou", "窦靖童", "歌单"},
        "小说": {"小说", "洛小阳", "匠人", "三尸语"},
        "部署": {"部署", "deployment", "task_complete"},
        "escape": {"escape", "powershell", "quote"},
        "textContent": {"textcontent", "xss", "escapehtml"},
        "AI Agent": {"agent", "python", "typescript"},
        "引号": {"引号", "powershell"},
        "西藏": {"西藏", "南迦巴瓦", "加拉白垒", "美食"},
        "南迦巴瓦": {"南迦巴瓦", "西藏", "加拉白垒"},
        "洛小阳": {"洛小阳", "匠人", "小说"},
        "DOUDOU": {"doudou", "窦靖童"},
        "zzzz_no_such_memory_xyz": set(),  # must be empty
    }

    # Title queries — each doc's distinctive name tokens
    for d in docs:
        # full name if short enough
        if 2 <= len(d.name) <= 40 and not d.name.startswith("session_conv"):
            add(d.name, "title", {d.path})
        # top tags
        for tag in d.tags[:4]:
            if tag and len(tag) >= 2:
                # expected = docs sharing this tag token
                expected = {
                    x.path
                    for x in docs
                    if tag.lower() in (x.name + " " + " ".join(x.tags) + " " + x.body[:400]).lower()
                }
                add(tag, "tag", expected or {d.path})

    # Keyword queries from distinctive tokens in names
    for d in docs:
        for t in tokens_from_text(d.name)[:3]:
            expected = {
                x.path
                for x in docs
                if t in (x.name + " " + " ".join(x.tags) + " " + x.body[:500]).lower()
                or t in x.path.lower()
            }
            add(t, "keyword", expected)

    # Fixed suite last (merge expected by theme tokens)
    for q, needles in theme_map.items():
        if not needles:
            add(q, "fixed", set())
            continue
        expected = set()
        for d in docs:
            blob = f"{d.path} {d.name} {' '.join(d.tags)} {d.body[:800]}".lower()
            if any(n.lower() in blob for n in needles):
                expected.add(d.path)
        add(q, "fixed", expected)

    return queries


async def run_audit(reindex: bool, top_k: int) -> tuple[list[Doc], list[QueryResult], dict]:
    settings = Settings()
    root = Path(settings.memory_workspace_path)
    svc = MemoryService(settings)
    await svc.initialize()
    if reindex:
        svc.auto_index.full_reindex()

    docs = build_docs(root)
    qspecs = build_queries(docs)
    results: list[QueryResult] = []

    # path -> body cache for judging
    body_by_path = {d.path: d.body for d in docs}
    name_by_path = {d.path: d.name for d in docs}

    for query, kind, expected in qspecs:
        hits = await svc.recall(query, top_k=top_k)
        qr = QueryResult(query=query, kind=kind, expected_paths=set(expected))
        hit_paths: list[str] = []
        for h in hits:
            p = norm(h.path)
            hit_paths.append(p)
            body = body_by_path.get(p, h.content or "")
            name = name_by_path.get(p, h.name)
            verdict = judge_hit(query, p, name, body)
            qr.hits.append((p, name, float(h.score), h.source))
            if verdict == "IRRELEVANT":
                # if expected empty and query is nonsense, any hit is bad
                qr.bad_hits.append(p)
            elif verdict == "SOFT_OK":
                qr.soft_hits.append(p)

        if not hits:
            if expected:
                qr.note = "NO_HITS_BUT_EXPECTED"
            else:
                qr.note = "EMPTY_OK"
                qr.rank1_ok = True
                qr.expected_in_top = True
        else:
            # rank1
            r1_path, r1_name, _, _ = qr.hits[0]
            r1_body = body_by_path.get(r1_path, "")
            r1_verdict = judge_hit(query, r1_path, r1_name, r1_body)
            qr.rank1_ok = r1_verdict != "IRRELEVANT"
            if expected:
                qr.expected_in_top = bool(set(hit_paths) & expected)
            else:
                # nonsense query should have zero hits; if hits, fail
                qr.expected_in_top = len(hits) == 0
                if hits:
                    qr.note = "UNEXPECTED_HITS"
            if not qr.note:
                if not qr.rank1_ok:
                    qr.note = "RANK1_BAD"
                elif qr.bad_hits:
                    qr.note = "HAS_BAD_HITS"
                elif not qr.expected_in_top and expected:
                    qr.note = "EXPECTED_MISS"
                elif qr.soft_hits and not qr.bad_hits:
                    qr.note = "SOFT_ONLY"
                else:
                    qr.note = "OK"

        results.append(qr)

    # Doc coverage: can each doc be found by a title/tag query?
    coverage = {"found": 0, "missing": 0, "missing_paths": []}
    for d in docs:
        # try name if meaningful else first tag
        probes = []
        if d.name and not d.name.startswith("session_conv"):
            probes.append(d.name)
        probes.extend(d.tags[:2])
        probes.extend(tokens_from_text(d.name)[:2])
        found = False
        for probe in probes:
            if not probe or len(probe) < 2:
                continue
            hits = await svc.recall(probe, top_k=top_k)
            if any(norm(h.path) == d.path for h in hits):
                found = True
                break
        if found:
            coverage["found"] += 1
        else:
            coverage["missing"] += 1
            coverage["missing_paths"].append(d.path)

    stats = {
        "doc_count": len(docs),
        "query_count": len(results),
        "coverage": coverage,
        "by_note": defaultdict(int),
        "root": str(root),
    }
    for r in results:
        stats["by_note"][r.note] += 1
    return docs, results, stats


def md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def render_tables(docs: list[Doc], results: list[QueryResult], stats: dict) -> str:
    lines: list[str] = []
    lines.append("# Memory Search Corpus Audit")
    lines.append("")
    lines.append(f"- workspace: `{stats['root']}`")
    lines.append(f"- documents: **{stats['doc_count']}**")
    lines.append(f"- queries: **{stats['query_count']}**")
    cov = stats["coverage"]
    lines.append(
        f"- doc findability: **{cov['found']}/{stats['doc_count']}** "
        f"(missing {cov['missing']})"
    )
    lines.append("")
    lines.append("## Summary by status")
    lines.append("")
    lines.append("| status | count | meaning |")
    lines.append("|---|---:|---|")
    meaning = {
        "OK": "rank1 合理，且无硬性无关命中",
        "SOFT_ONLY": "仅有弱 token 命中（中文单字/短词），大体可接受",
        "HAS_BAD_HITS": "结果列表里出现硬性无关项",
        "RANK1_BAD": "第 1 名就不相关",
        "EXPECTED_MISS": "期望文档未进入 top-k",
        "NO_HITS_BUT_EXPECTED": "库内应有相关内容却 0 命中",
        "EMPTY_OK": "无意义查询正确返回空",
        "UNEXPECTED_HITS": "应为空的查询却有结果",
    }
    for k, v in sorted(stats["by_note"].items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"| `{k}` | {v} | {meaning.get(k, '')} |")
    lines.append("")

    # Main query table — prioritize fixed + title, then others with issues
    lines.append("## Query results (all)")
    lines.append("")
    lines.append(
        "| query | kind | #hits | rank1 | status | bad | "
        "rank1 path | top hits (path) |"
    )
    lines.append("|---|---|---:|---|---|---:|---|---|")

    # sort: bad first
    order = {
        "RANK1_BAD": 0,
        "HAS_BAD_HITS": 1,
        "UNEXPECTED_HITS": 2,
        "NO_HITS_BUT_EXPECTED": 3,
        "EXPECTED_MISS": 4,
        "SOFT_ONLY": 5,
        "OK": 6,
        "EMPTY_OK": 7,
    }
    for r in sorted(results, key=lambda x: (order.get(x.note, 9), x.kind, x.query)):
        r1 = r.hits[0][0] if r.hits else "-"
        r1_name = r.hits[0][1] if r.hits else "-"
        top = "<br>".join(
            f"{i+1}. `{md_escape(p)}`" for i, (p, n, s, src) in enumerate(r.hits[:5])
        ) or "-"
        lines.append(
            f"| `{md_escape(r.query)[:40]}` | {r.kind} | {len(r.hits)} | "
            f"{'Y' if r.rank1_ok else 'N'} | `{r.note}` | {len(r.bad_hits)} | "
            f"`{md_escape(r1)[:60]}` | {top} |"
        )

    lines.append("")
    lines.append("## Corpus inventory")
    lines.append("")
    lines.append("| path | name | bucket | tags |")
    lines.append("|---|---|---|---|")
    for d in docs:
        tags = ", ".join(d.tags[:6])
        lines.append(
            f"| `{md_escape(d.path)}` | {md_escape(d.name)[:40]} | {d.bucket} | "
            f"{md_escape(tags)[:50]} |"
        )

    if cov["missing_paths"]:
        lines.append("")
        lines.append("## Documents hard to find via title/tag probes")
        lines.append("")
        for p in cov["missing_paths"]:
            lines.append(f"- `{p}`")

    # Problem detail
    problems = [r for r in results if r.note in {
        "RANK1_BAD", "HAS_BAD_HITS", "UNEXPECTED_HITS",
        "NO_HITS_BUT_EXPECTED", "EXPECTED_MISS",
    }]
    lines.append("")
    lines.append("## Problem details")
    lines.append("")
    if not problems:
        lines.append("_No hard problems._")
    else:
        for r in problems:
            lines.append(f"### `{md_escape(r.query)}` — `{r.note}`")
            lines.append("")
            if r.bad_hits:
                lines.append("Bad hits:")
                for p in r.bad_hits:
                    lines.append(f"- `{p}`")
            if r.hits:
                lines.append("")
                lines.append("All hits:")
                for i, (p, n, s, src) in enumerate(r.hits, 1):
                    lines.append(f"{i}. `{p}` | {md_escape(n)[:50]} | score={s:.5f} | {src}")
            lines.append("")

    return "\n".join(lines) + "\n"


async def main() -> int:
    _setup_stdio()
    parser = argparse.ArgumentParser()
    parser.add_argument("--reindex", action="store_true")
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--md", default="", help="write markdown report to this path")
    args = parser.parse_args()

    docs, results, stats = await run_audit(args.reindex, args.top_k)
    report = render_tables(docs, results, stats)

    out_path = Path(args.md) if args.md else Path("scripts/_memory_search_audit_report.md")
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n[report written] {out_path.resolve()}")

    hard = sum(
        1
        for r in results
        if r.note in {
            "RANK1_BAD", "HAS_BAD_HITS", "UNEXPECTED_HITS",
            "NO_HITS_BUT_EXPECTED",
        }
    )
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
