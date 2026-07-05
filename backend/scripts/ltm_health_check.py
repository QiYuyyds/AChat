"""Read-only LTM dirty-data health check (fix-ltm-dirty-data task 6.1).

Queries long_term_memory and reports totals, importance distribution, category
mix, duplicate rate, and suspected raw-conversation ratio. Writes nothing.
"""
import asyncio
import re
from collections import Counter

from sqlalchemy import select

from app.config import apply_env_overrides, get_settings
from app.db.engine import get_db, init_db
from app.db.models import LongTermMemory

# A well-formed extracted fact looks like "用户<key>: <value>".
_STRUCTURED = re.compile(r"^用户.+?[:：]")
# Fillers / questions that should never have been stored as memories.
_FILLER = re.compile(r"^(继续|好的|好|嗯+|行|ok|OK|不对|重来|谢谢|请|帮我|麻烦)", re.I)


async def main() -> None:
    apply_env_overrides()
    get_settings()
    await init_db()
    async with get_db() as session:
        rows = (await session.execute(select(LongTermMemory))).scalars().all()

    n = len(rows)
    print(f"=== long_term_memory 体检 (共 {n} 条) ===")
    if n == 0:
        print("空表。")
        return

    # id vs count (exposes the old enumerate-index id collision surface)
    ids = [r.id for r in rows]
    print(f"id 范围: min={min(ids)} max={max(ids)} | 唯一 id 数={len(set(ids))}")

    # importance distribution
    imps = [float(r.importance) for r in rows]
    buckets = Counter()
    for x in imps:
        buckets[round(x, 1)] += 1
    print(f"importance: min={min(imps):.3f} max={max(imps):.3f} avg={sum(imps)/n:.3f}")
    print("  分布(按0.1桶): " + ", ".join(f"{k}:{v}" for k, v in sorted(buckets.items())))

    # category mix
    cats = Counter((r.category or "none") for r in rows)
    print("category 分布: " + ", ".join(f"{k}:{v}" for k, v in cats.most_common()))

    # duplicate content rate (exact)
    contents = [(r.content or "").strip() for r in rows]
    cc = Counter(contents)
    dup_groups = {c: k for c, k in cc.items() if k > 1}
    dup_rows = sum(k for k in dup_groups.values())
    print(f"完全重复: {len(dup_groups)} 组, 涉及 {dup_rows} 行 "
          f"({dup_rows/n*100:.1f}% 行是重复内容)")
    for c, k in Counter(dup_groups).most_common(5):
        print(f"    ×{k}  {c[:50]!r}")

    # suspected raw-conversation dumps (not a structured 用户X: Y fact)
    raw = [c for c in contents if not _STRUCTURED.match(c)]
    filler = [c for c in contents if _FILLER.match(c)]
    print(f"疑似原文流水(非「用户X: Y」结构): {len(raw)} 行 ({len(raw)/n*100:.1f}%)")
    print(f"其中疑似填充/提问句: {len(filler)} 行")
    for c in raw[:8]:
        print(f"    · {c[:60]!r}")

    # embedding coverage
    no_emb = sum(1 for r in rows if not r.embedding)
    print(f"无 embedding 的行: {no_emb} ({no_emb/n*100:.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
