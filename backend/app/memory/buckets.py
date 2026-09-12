"""Canonical memory bucket constants and unit validators.

Single source of truth for digest content buckets vs UI lifecycle filters.
"""

from __future__ import annotations

import re
from typing import Any

DIGEST_BUCKETS: tuple[str, ...] = ("procedure", "personal", "wiki")
UI_FILTERS: tuple[str, ...] = ("all", "procedure", "personal", "wiki", "daily")

# personal cards must stay short rule-of-engagement notes
_PERSONAL_MAX_CHARS = 900

# Delivery / one-off project status — never promote to digest personal
_DELIVERY_RE = re.compile(
    r"(in_review|/deployments/|部署预览|功能清单|改动文件|交付记录|task.?complete|localStorage)",
    re.I,
)

# Encyclopedic bulk that does not belong in personal
_ENCYCLOPEDIA_RE = re.compile(
    r"(万字|章节|收藏|总推荐|代表作|K\s*歌|必听|单曲循环|评分|完结|"
    r"\d+\s*章|\d+\.?\d*\s*万字)",
    re.I,
)

# Runbook shape → procedure
_RUNBOOK_RE = re.compile(r"(##\s*Trigger|##\s*Steps|按以下步骤|操作步骤)", re.I)

_NON_ALNUM_RE = re.compile(r"[^\w一-鿿]+", re.UNICODE)


def normalize_bucket(bucket: str | None, default: str = "wiki") -> str:
    b = (bucket or "").strip().lower()
    if b in DIGEST_BUCKETS:
        return b
    return default if default in DIGEST_BUCKETS else "wiki"


def slugify_key_part(text: str, max_len: int = 48) -> str:
    s = (text or "").strip().lower()
    s = _NON_ALNUM_RE.sub(".", s)
    s = re.sub(r"\.+", ".", s).strip(".")
    return s[:max_len] or "unknown"


def make_stable_key(bucket: str, name: str, explicit: str | None = None) -> str:
    """Build a stable merge key for digest units/files.

    For personal bucket, the key MUST be topic-level (e.g. user.interest.music),
    never fact-level (e.g. user.interest.music.doudou). This is a prerequisite
    for SUPERSEDE to correctly hit the same card when preferences change.
    """
    if explicit and str(explicit).strip():
        key = slugify_key_part(str(explicit).strip(), max_len=80)
        # For personal bucket, strip object-level suffixes to enforce topic-level
        if normalize_bucket(bucket) == "personal":
            key = _normalize_personal_key(key)
        return key

    bucket = normalize_bucket(bucket)
    name = (name or "").strip()
    low = name.lower()

    if bucket == "personal":
        if any(k in name for k in ("身份", "职业", "背景", "负责人", "identity", "job")):
            return "user.identity.profile"
        if any(k in low for k in ("doudou", "福禄寿", "音乐", "乐队")):
            return "user.interest.music"  # topic-level, not .doudou
        if any(k in name for k in ("洛小阳", "匠人宇宙", "阅读", "小说")):
            return "user.interest.reading"  # topic-level, not .jiangren
        if any(k in name for k in ("偏好", "喜欢", "约定", "禁忌", "不要")):
            return f"user.rule.{slugify_key_part(name)}"
        return f"user.misc.{slugify_key_part(name)}"

    if bucket == "procedure":
        return f"proc.{slugify_key_part(name)}"
    return f"wiki.{slugify_key_part(name)}"


# Object-level suffixes that should be stripped from personal keys
_PERSONAL_OBJECT_SUFFIXES = (
    "doudou", "福禄寿", "洛小阳", "匠人宇宙",
)


def _normalize_personal_key(key: str) -> str:
    """Strip object-level suffix from a personal key to enforce topic-level.

    E.g. "user.interest.music.doudou" → "user.interest.music"
    """
    parts = key.split(".")
    # Remove trailing object-level suffixes
    while len(parts) > 2 and parts[-1].lower() in _PERSONAL_OBJECT_SUFFIXES:
        parts.pop()
    return ".".join(parts) if parts else key


def _shorten_personal_content(content: str, summary: str) -> str:
    """Keep only a short rule card for personal."""
    text = (content or "").strip()
    rule = summary.strip() if summary.strip() else ""
    if not rule:
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("derived_from"):
                continue
            s = re.sub(r"^[-*]\s+", "", s)
            if s:
                rule = s
                break
    if not rule:
        rule = text[:200].strip() or "用户相关稳定事实"
    # Drop encyclopedic bullets; keep a minimal how-to if present
    how: list[str] = []
    in_how = False
    for line in text.splitlines():
        if re.match(r"^##\s*How to apply", line, re.I) or re.match(r"^##\s*如何应用", line):
            in_how = True
            continue
        if line.startswith("## "):
            in_how = False
            continue
        if in_how:
            s = line.strip()
            if s.startswith("-") or s.startswith("*"):
                item = s.lstrip("-* ").strip()
                if item and not _ENCYCLOPEDIA_RE.search(item) and len(how) < 5:
                    how.append(f"- {item}")
    body = f"## Rule / fact\n{rule}\n"
    if how:
        body += "\n## How to apply\n" + "\n".join(how) + "\n"
    return body.strip()


def validate_and_normalize_unit(unit: dict[str, Any]) -> dict[str, Any] | None:
    """Validate one extract unit. Returns normalized unit or None to drop.

    Programmatic gate after LLM extract — must not rely on prompt alone.
    """
    if not isinstance(unit, dict):
        return None

    name = str(unit.get("name", "")).strip()
    if not name:
        return None

    summary = str(unit.get("summary", "")).strip()
    content = str(unit.get("content", "")).strip() or summary
    if not content:
        return None

    bucket = normalize_bucket(str(unit.get("bucket", "wiki")))
    blob = f"{name}\n{summary}\n{content}"

    # Delivery / one-off project status: never long-term personal
    if bucket == "personal" and _DELIVERY_RE.search(blob):
        # If it is pure delivery noise, drop; if it also has procedure signal, rebucket
        if _RUNBOOK_RE.search(blob):
            bucket = "procedure"
        else:
            return None

    # Runbook shape mislabeled as personal → procedure
    if bucket == "personal" and _RUNBOOK_RE.search(blob):
        bucket = "procedure"

    # Encyclopedic personal → keep short personal interest only when interest-like
    if bucket == "personal" and _ENCYCLOPEDIA_RE.search(blob):
        content = _shorten_personal_content(content, summary)
        # If after shorten still no user-subject signal, send to wiki
        if not re.search(r"(用户|本人|我|偏好|关注|喜欢)", f"{name}{summary}{content}"):
            bucket = "wiki"
        elif len(content) > _PERSONAL_MAX_CHARS:
            content = content[:_PERSONAL_MAX_CHARS].rstrip() + "…"

    if bucket == "personal" and len(content) > _PERSONAL_MAX_CHARS:
        content = _shorten_personal_content(content, summary)

    tags = [str(t) for t in (unit.get("tags") or []) if t][:10]
    try:
        importance = float(unit.get("importance", 0.5) or 0.5)
    except (TypeError, ValueError):
        importance = 0.5
    importance = max(0.0, min(1.0, importance))

    source_paths = unit.get("source_paths", [])
    if not isinstance(source_paths, list):
        source_paths = []

    stable_key = make_stable_key(bucket, name, unit.get("stable_key"))

    out = dict(unit)
    out.update(
        {
            "name": name,
            "bucket": bucket,
            "summary": summary or name,
            "content": content,
            "tags": tags,
            "importance": importance,
            "source_paths": source_paths,
            "stable_key": stable_key,
        }
    )
    return out


def to_relative_memory_path(path: str, workspace_root: str | None = None) -> str:
    """Normalize absolute/relative memory paths to repo-relative style.

    Prefer daily/... or digest/... form for derived_from wikilinks.
    """
    p = (path or "").replace("\\", "/").strip()
    if not p:
        return p
    # Strip workspace root prefix when provided
    if workspace_root:
        root = workspace_root.replace("\\", "/").rstrip("/")
        if p.startswith(root + "/"):
            p = p[len(root) + 1 :]
    # Pull out daily/ or digest/ tail if buried in absolute path
    for marker in ("/daily/", "/digest/"):
        idx = p.find(marker)
        if idx >= 0:
            return p[idx + 1 :]  # drop leading slash → daily/...
    if p.startswith("daily/") or p.startswith("digest/"):
        return p
    return p.lstrip("./")
