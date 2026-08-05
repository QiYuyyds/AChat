#!/usr/bin/env python3
"""Theme-based memory search table for the real workspace."""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.memory.file_store.markdown_io import read_markdown  # noqa: E402
from app.memory.memory_service import MemoryService  # noqa: E402


def norm(p: str) -> str:
    return str(p).replace("\\", "/")


async def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    settings = Settings()
    root = Path(settings.memory_workspace_path)
    svc = MemoryService(settings)
    await svc.initialize()
    svc.auto_index.full_reindex()

    docs = []
    for f in sorted(root.rglob("*.md")):
        if "metadata" in f.parts:
            continue
        m = read_markdown(f)
        if not m:
            continue
        rel = f.relative_to(root).as_posix()
        docs.append(
            {
                "path": rel,
                "name": m.frontmatter.name or f.stem,
                "bucket": "daily" if rel.startswith("daily/") else (m.frontmatter.bucket or "wiki"),
                "tags": list(m.frontmatter.tags or []),
                "body": m.body or "",
            }
        )

    themes: list[tuple[str, list[str]]] = [
        ("python", ["python"]),
        ("typescript", ["typescript"]),
        ("powershell", ["powershell", "ps1"]),
        ("xss", ["xss", "escapehtml", "textcontent"]),
        ("todo", ["todo", "todolist", "localstorage"]),
        ("task_complete", ["task_complete", "版本冲突"]),
        ("任务", ["任务", "task_complete", "todo"]),
        ("doudou", ["doudou", "窦靖童", "福禄寿"]),
        ("小说", ["小说", "洛小阳", "匠人", "三尸语", "诡匠"]),
        ("洛小阳", ["洛小阳", "匠人", "三尸语"]),
        ("部署", ["部署", "deployment", "task_complete"]),
        ("escape", ["escape", "powershell", "quote"]),
        ("引号", ["引号", "powershell", "转义"]),
        ("textContent", ["textcontent", "xss"]),
        ("AI Agent", ["agent", "python", "typescript"]),
        ("西藏", ["西藏", "南迦巴瓦", "加拉白垒"]),
        ("南迦巴瓦", ["南迦巴瓦", "西藏", "加拉白垒", "雪山"]),
        ("甘肃", ["甘肃", "美食", "雪山", "玉龙"]),
        ("玉龙雪山", ["玉龙雪山", "甘肃", "雪山"]),
        ("美食", ["美食", "甘肃", "牛羊肉", "火锅"]),
        ("用户", ["用户", "重庆邮电", "实验室"]),
        ("zzzz_no_such", []),
    ]

    rows = []
    for q, needles in themes:
        hits = await svc.recall(q, top_k=8)
        good = []
        bad = []
        for h in hits:
            p = norm(h.path)
            name = h.name
            body = h.content or ""
            for d in docs:
                if d["path"] == p:
                    body = d["body"]
                    name = d["name"]
                    break
            hay = f"{name}\n{p}\n{body[:1500]}".lower()
            qlow = q.lower()
            if not needles:
                bad.append((p, name, h.score, h.source))
                continue
            ok = any(n.lower() in hay for n in needles) or qlow in hay
            if ok:
                good.append((p, name, h.score, h.source))
            else:
                bad.append((p, name, h.score, h.source))

        exp = 0
        if needles:
            for d in docs:
                hay = f"{d['name']}\n{d['path']}\n{d['body'][:1500]}".lower()
                if any(n.lower() in hay for n in needles):
                    exp += 1

        if not needles:
            status = "OK" if not hits else "UNEXPECTED"
        elif not hits:
            status = "MISS"
        elif bad and not good:
            status = "BAD"
        elif bad:
            status = "MIXED"
        else:
            status = "OK"

        rows.append(
            {
                "q": q,
                "status": status,
                "hits": len(hits),
                "good": len(good),
                "bad": len(bad),
                "exp": exp,
                "rank1": (hits[0].name if hits else "-"),
                "rank1_path": (norm(hits[0].path) if hits else "-"),
                "bad_names": [b[1] for b in bad[:3]],
                "good_names": [g[1] for g in good[:3]],
                "top": [(norm(h.path), h.name, h.score, h.source) for h in hits[:5]],
            }
        )

    c = Counter(r["status"] for r in rows)
    meaning = {
        "OK": "结果合理（或正确为空）",
        "MIXED": "前排相关，但夹杂无关",
        "BAD": "几乎全不相关",
        "MISS": "库内有相关却 0 命中",
        "UNEXPECTED": "应为空却有结果",
    }

    lines: list[str] = []
    lines.append("# 主题搜索体检表")
    lines.append("")
    lines.append(f"- workspace: `{root}`")
    lines.append(f"- 语料文档数: **{len(docs)}**")
    lines.append(f"- 主题查询数: **{len(rows)}**")
    lines.append("")
    lines.append("## 状态汇总")
    lines.append("")
    lines.append("| 状态 | 数量 | 含义 |")
    lines.append("|---|---:|---|")
    for k, v in c.most_common():
        lines.append(f"| {k} | {v} | {meaning.get(k, k)} |")
    lines.append("")
    lines.append("## 主题查询结果")
    lines.append("")
    lines.append("| 查询 | 状态 | 命中 | 相关 | 无关 | 库内应有 | 第1名 | 无关样例 |")
    lines.append("|---|---|---:|---:|---:|---:|---|---|")
    for r in rows:
        bad_s = "；".join(r["bad_names"]) if r["bad_names"] else "-"
        rank1 = (r["rank1"] or "-")[:28]
        lines.append(
            f"| `{r['q']}` | **{r['status']}** | {r['hits']} | {r['good']} | {r['bad']} | "
            f"{r['exp']} | {rank1} | {bad_s[:42]} |"
        )

    lines.append("")
    lines.append("## 问题查询明细")
    lines.append("")
    problems = [r for r in rows if r["status"] in {"MIXED", "BAD", "MISS", "UNEXPECTED"}]
    if not problems:
        lines.append("_无硬性问题。_")
    else:
        for r in problems:
            lines.append(f"### `{r['q']}` — {r['status']}")
            lines.append("")
            lines.append("| # | path | name | score | source |")
            lines.append("|---:|---|---|---:|---|")
            for i, (p, n, s, src) in enumerate(r["top"], 1):
                lines.append(f"| {i} | `{p}` | {n[:40]} | {s:.5f} | {src} |")
            lines.append("")

    lines.append("## 语料清单")
    lines.append("")
    lines.append("| path | name | bucket |")
    lines.append("|---|---|---|")
    for d in docs:
        lines.append(f"| `{d['path']}` | {d['name'][:40]} | {d['bucket']} |")
    lines.append("")

    report = "\n".join(lines) + "\n"
    out = Path("scripts/_memory_search_theme_report.md")
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"[written] {out.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
