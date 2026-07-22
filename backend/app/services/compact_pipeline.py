"""Five-stage in-memory compaction pipeline for the SDK ReAct loop.

Replaces the single-point ``_mid_run_compact`` with escalating stages:
  - stage 1 (ratio ≥ 0.70): semantic summarization of old tool results
  - stage 2 (ratio ≥ 0.80): re-prune stage-1 summaries more aggressively
  - stage 3 (ratio ≥ 0.88): fold older turns into a single marker
  - stage 4 (ratio ≥ 0.93): soft wrap-up inject (unchanged, in react_loop_termination)
  - stage 5 (ratio ≥ 0.95): forced final (unchanged, in react_loop_termination)

Stages 1/2/3 are pure regex + structural pruning (no LLM). Token estimation
counts only ``content`` + ``tool_calls.function.name/arguments`` +
``reasoning_content`` — not JSON structural fields like ``role`` /
``tool_call_id`` / ``type``. This is independent from the cross-run
``conversation-context`` compaction (Tier 1) and the LLM-backed full compaction
(Tier 2/3).
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any

from app.services.compact_markers import (
    CompactMarkerBuilder,
    CompactSuccessJudge,
)
from app.services.fs_service import detect_language, extract_outline

logger = logging.getLogger(__name__)

# ─── Stage thresholds (kept here so tuning is one-file) ─────────────────────
STAGE1_SUMMARIZE_RATIO = 0.70
STAGE2_PRUNE_RATIO = 0.80
STAGE3_FOLD_RATIO = 0.88

# Keep the most recent N complete turns intact during fold/prune.
KEEP_RECENT_TURNS = 2
# Only fold when there are at least this many complete turns; otherwise the
# savings are too small to justify losing the turns.
FOLD_TURN_THRESHOLD = 4

# Fallback message-count keep when no turn boundary is found.
LEGACY_RECENT_KEEP = 6

__all__ = [
    "STAGE1_SUMMARIZE_RATIO",
    "STAGE2_PRUNE_RATIO",
    "STAGE3_FOLD_RATIO",
    "KEEP_RECENT_TURNS",
    "FOLD_TURN_THRESHOLD",
    "LEGACY_RECENT_KEEP",
    "estimate_messages_tokens",
    "find_turn_boundaries",
    "keep_recent_turns",
    "summarize_tool_result",
    "summarize_tool_result_with_summary",
    "summarize_tool_result_full",
    "run_compact_pipeline",
]


# ─── Token estimation (delegates to transcript_renderer) ────────────────────


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate tokens from content-only fields, excluding JSON metadata.

    Counts ``content`` + ``tool_calls.function.name/arguments`` +
    ``reasoning_content`` + 4 tokens/message overhead. Does NOT count
    ``role``, ``tool_call_id``, ``type``, or other structural fields.

    Delegates to ``transcript_renderer.estimate_dict_message_tokens`` with
    ``include_reasoning=True`` (in-memory ReAct loop messages carry
    ``reasoning_content``). Uses lazy import to avoid a circular dependency
    (``transcript_renderer`` imports ``compact_pipeline.summarize_tool_result_with_summary``).
    """
    from app.services.transcript_renderer import estimate_dict_message_tokens
    return sum(
        estimate_dict_message_tokens(m, include_reasoning=True) for m in messages
    )


# ─── TurnBoundaryFinder ─────────────────────────────────────────────────────


def find_turn_boundaries(messages: list[dict]) -> list[tuple[int, int]]:
    """Identify complete ReAct turns in a messages list.

    A turn = one ``role=="assistant"`` message containing ``tool_calls`` + all
    immediately following ``role=="tool"`` messages. Returns a list of
    ``(start_index, end_index)`` tuples (inclusive). Messages without a
    tool-calling assistant are skipped — they're not ReAct turns. Returns an
    empty list when no complete turn is found.
    """
    boundaries: list[tuple[int, int]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if (
            isinstance(msg, dict)
            and msg.get("role") == "assistant"
            and msg.get("tool_calls")
        ):
            start = i
            j = i + 1
            while j < n and isinstance(messages[j], dict) and messages[j].get("role") == "tool":
                j += 1
            boundaries.append((start, j - 1))
            i = j
        else:
            i += 1
    return boundaries


def keep_recent_turns(
    messages: list[dict],
    k: int = KEEP_RECENT_TURNS,
) -> tuple[list[dict], list[dict]]:
    """Split messages into (recent, old) on turn boundaries.

    ``recent`` contains the last ``k`` complete turns verbatim (including the
    assistant message and all trailing tool messages for each turn). ``old``
    contains everything else (system prompt, user messages, older turns). When
    there are ``<= k`` complete turns, returns ``(messages, [])``.
    """
    boundaries = find_turn_boundaries(messages)
    if len(boundaries) <= k:
        return list(messages), []

    # Index of the first message in the k-th-from-last turn.
    keep_from = boundaries[-k][0]
    return list(messages[keep_from:]), list(messages[:keep_from])


# ─── ToolResultSummarizer ────────────────────────────────────────────────────


def _parse_tool_content(content: Any) -> str:
    """Normalize a tool message's content field to a plain string."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _parse_args(raw: Any) -> dict:
    """Best-effort parse of tool_call arguments into a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass
    return {}


def _looks_like_json_array_of_objects(text: str) -> bool:
    return text.lstrip().startswith("[")


def _summarize_fs_list(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Return (new_content, summary, recover_hint) for fs_list."""
    path = args.get("path", "")
    depth = args.get("depth", 1)
    recover = f"fs_list(path={path!r}, depth={depth}) 重新获取结构"

    # Parse entries from JSON content.
    entries: list[dict] = []
    if _looks_like_json_array_of_objects(content):
        try:
            data = json.loads(content)
            if isinstance(data, list):
                entries = [e for e in data if isinstance(e, dict)]
        except (TypeError, ValueError):
            entries = []

    dirs = [e for e in entries if e.get("isDirectory")]
    files = [e for e in entries if not e.get("isDirectory")]

    if stage == 1:
        # Keep name + relativePath, drop size/depth/isDirectory.
        kept = [
            {"name": e.get("name", ""), "relativePath": e.get("relativePath", e.get("name", ""))}
            for e in entries
        ]
        new_content = json.dumps(kept, ensure_ascii=False)
        summary = f"{path or '目录'} 含 {len(files)} 文件、{len(dirs)} 子目录"
        return new_content, summary, recover

    if stage == 2:
        # Only directory tree.
        tree = [e.get("name", "") for e in dirs]
        new_content = json.dumps({"directories": tree}, ensure_ascii=False)
        summary = f"{path or '目录'} 子目录 {len(dirs)} 个"
        return new_content, summary, recover

    # stage 3: only a count summary.
    new_content = f"目录 {path or ''} 含 {len(files)} 文件、{len(dirs)} 子目录"
    summary = new_content
    return new_content, summary, recover


def _summarize_fs_read(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Return (new_content, summary, recover_hint) for fs_read."""
    path = args.get("path", "")
    mode = args.get("mode", "full")
    recover = f"fs_read(path={path!r}, mode={mode!r}) 重新获取内容"

    # outline / head modes are short by construction — keep verbatim.
    if mode in ("outline", "head"):
        return content, f"{path} ({mode})", recover

    # mode == "full"
    if stage == 1:
        language = detect_language(path)
        outline = extract_outline(content, language)
        total_lines = content.count("\n") + 1
        payload = {
            "outline": outline,
            "language": language,
            "totalLines": total_lines,
            "fullSize": len(content),
        }
        if not outline:
            payload["note"] = "outline 提取为空，建议重新 fs_read(mode='full') 获取完整内容"
        new_content = json.dumps(payload, ensure_ascii=False)
        summary = f"{path} 有 {total_lines} 行，提取 {len(outline)} 条定义"
        return new_content, summary, recover

    if stage == 2:
        language = detect_language(path)
        outline = extract_outline(content, language)
        first_lines = content.splitlines()[:3]
        # Cap head so single-line / very long content doesn't survive verbatim.
        head_text = "\n".join(first_lines)[:500]
        head_outline = outline[:5]
        payload = {
            "head": head_text,
            "outline": head_outline,
            "language": language,
        }
        new_content = json.dumps(payload, ensure_ascii=False)
        summary = f"{path} 首 3 行 + outline 头部 {len(head_outline)} 条"
        return new_content, summary, recover

    # stage 3: just the file size + outline head keywords.
    language = detect_language(path)
    outline = extract_outline(content, language)
    total_lines = content.count("\n") + 1
    head_names = "、".join(o.get("content", "")[:40] for o in outline[:3])
    new_content = f"文件 {path} 有 {total_lines} 行，主要定义了 {head_names}" if head_names else f"文件 {path} 有 {total_lines} 行"
    summary = new_content
    return new_content, summary, recover


def _summarize_bash(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Return (new_content, summary, recover_hint) for bash."""
    command = args.get("command", "")
    recover = f"bash(command={command!r}) 重新执行"

    lines = content.splitlines()
    # Try to find an exit_code marker in the content (commonly the last line).
    exit_code: str | None = None
    if lines and lines[-1].lower().startswith("exit"):
        exit_code = lines[-1]

    if stage == 1:
        tail = "\n".join(lines[-20:])
        prefix = f"{exit_code}\n" if exit_code else ""
        new_content = prefix + tail
        summary = f"bash 末 20 行（{exit_code or 'no exit'}）"
        return new_content, summary, recover

    if stage == 2:
        tail = "\n".join(lines[-5:])
        prefix = f"{exit_code}\n" if exit_code else ""
        new_content = prefix + tail
        summary = f"bash 末 5 行（{exit_code or 'no exit'}）"
        return new_content, summary, recover

    # stage 3
    last = lines[-1] if lines else ""
    new_content = f"{exit_code}\n{last}".strip()
    summary = f"bash exit={exit_code or 'no exit'}"
    return new_content, summary, recover


def _summarize_fs_grep(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Return (new_content, summary, recover_hint) for fs_grep."""
    pattern = args.get("pattern", "")
    path = args.get("path", "")
    recover = f"fs_grep(pattern={pattern!r}, path={path!r}) 重新搜索"

    # Content is usually lines of "file:line:match".
    lines = content.splitlines() if content else []
    matches = [ln for ln in lines if ln.strip()]

    if stage == 1:
        kept = matches[:10]
        new_content = "\n".join(kept)
        summary = f"找到 {len(matches)} 处匹配，保留前 10"
        return new_content, summary, recover

    if stage == 2:
        kept = matches[:5]
        new_content = "\n".join(kept)
        summary = f"找到 {len(matches)} 处匹配，保留前 5"
        return new_content, summary, recover

    # stage 3
    new_content = f"找到 {len(matches)} 处匹配"
    summary = new_content
    return new_content, summary, recover


def _summarize_code_explore(args: dict, content: str, stage: int) -> tuple[str, str, str]:  # noqa: ARG001
    """code_explore is already a high-density summary — preserve verbatim."""
    recover = "code_explore 结果密度高，不可恢复，已完整保留"
    summary = f"code_explore 完整保留（{len(content)} 字符）"
    return content, summary, recover


def _try_parse_json(content: str) -> dict | None:
    """Try to parse content as a JSON object; return dict or None."""
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError):
        return None


def _summarize_load_skill(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize load_skill: keep skill name + description preview."""
    slug = args.get("slug", args.get("name", ""))
    recover = f"load_skill(slug={slug!r}) 重新加载"
    data = _try_parse_json(content)
    name = (data or {}).get("slug", slug) if data else slug

    if stage == 1:
        body = (data or {}).get("body", "") if data else content
        new_content = json.dumps(
            {"slug": name, "description": body[:200]}, ensure_ascii=False
        )
        summary = f"load_skill: {name}"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps({"slug": name}, ensure_ascii=False)
        summary = f"load_skill: {name}"
        return new_content, summary, recover

    new_content = f"load_skill: {name}"
    summary = new_content
    return new_content, summary, recover


def _summarize_write_artifact(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize write_artifact: keep artifactId + title + type."""
    data = _try_parse_json(content)
    artifact_id = (data or {}).get("artifactId", args.get("artifactId", ""))
    title = (data or {}).get("title", args.get("title", ""))
    art_type = (data or {}).get("type", args.get("type", ""))
    recover = f"read_artifact(artifactId={artifact_id!r}) 重新获取内容"

    if stage == 1:
        new_content = json.dumps(
            {"artifactId": artifact_id, "title": title, "type": art_type},
            ensure_ascii=False,
        )
        summary = f"write_artifact: {title} ({art_type})"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps(
            {"artifactId": artifact_id, "type": art_type}, ensure_ascii=False
        )
        summary = f"write_artifact: {art_type}"
        return new_content, summary, recover

    new_content = f"write_artifact: {title} ({art_type})"
    summary = new_content
    return new_content, summary, recover


def _summarize_read_artifact(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize read_artifact: keep artifactId + title + content preview."""
    data = _try_parse_json(content)
    artifact_id = (data or {}).get("id", args.get("artifactId", ""))
    title = (data or {}).get("title", "")
    raw_content = (data or {}).get("content", "") if data else content
    content_str = json.dumps(raw_content, ensure_ascii=False) if not isinstance(raw_content, str) else raw_content
    recover = f"read_artifact(artifactId={artifact_id!r}) 重新获取内容"

    if stage == 1:
        new_content = json.dumps(
            {"id": artifact_id, "title": title, "content": content_str[:500]},
            ensure_ascii=False,
        )
        summary = f"read_artifact: {title} ({len(content_str)} chars)"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps(
            {"id": artifact_id, "title": title}, ensure_ascii=False
        )
        summary = f"read_artifact: {title}"
        return new_content, summary, recover

    new_content = f"read_artifact: {title}"
    summary = new_content
    return new_content, summary, recover


def _summarize_update_artifact(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize update_artifact: keep artifactId + updated files count."""
    data = _try_parse_json(content)
    artifact_id = (data or {}).get("artifactId", args.get("artifactId", ""))
    updated = (data or {}).get("updatedFiles", [])
    updated_count = len(updated) if isinstance(updated, list) else 0
    recover = f"read_artifact(artifactId={artifact_id!r}) 重新获取内容"

    if stage == 1:
        new_content = json.dumps(
            {"artifactId": artifact_id, "updatedFilesCount": updated_count},
            ensure_ascii=False,
        )
        summary = f"update_artifact: {artifact_id}, {updated_count} files"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps({"artifactId": artifact_id}, ensure_ascii=False)
        summary = f"update_artifact: {artifact_id}"
        return new_content, summary, recover

    new_content = f"update_artifact: {artifact_id}"
    summary = new_content
    return new_content, summary, recover


def _summarize_fs_write(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize fs_write: keep path + bytes written."""
    data = _try_parse_json(content)
    path = (data or {}).get("path", args.get("path", ""))
    bytes_written = (data or {}).get("bytes", 0)
    applied = (data or {}).get("applied", "")
    recover = f"fs_read(path={path!r}) 重新读取"

    if stage == 1:
        new_content = json.dumps(
            {"path": path, "bytes": bytes_written, "applied": applied},
            ensure_ascii=False,
        )
        summary = f"fs_write: {path} ({bytes_written} bytes, {applied})"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps({"path": path, "bytes": bytes_written}, ensure_ascii=False)
        summary = f"fs_write: {path} ({bytes_written} bytes)"
        return new_content, summary, recover

    new_content = f"fs_write: {path} ({bytes_written} bytes)"
    summary = new_content
    return new_content, summary, recover


def _summarize_fs_edit(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize fs_edit: keep path + lines changed summary."""
    data = _try_parse_json(content)
    path = (data or {}).get("path", args.get("path", ""))
    bytes_written = (data or {}).get("bytes", 0)
    applied = (data or {}).get("applied", "")
    recover = f"fs_read(path={path!r}) 重新读取"

    if stage == 1:
        new_content = json.dumps(
            {"path": path, "bytes": bytes_written, "applied": applied},
            ensure_ascii=False,
        )
        summary = f"fs_edit: {path} ({bytes_written} bytes, {applied})"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps({"path": path, "bytes": bytes_written}, ensure_ascii=False)
        summary = f"fs_edit: {path} ({bytes_written} bytes)"
        return new_content, summary, recover

    new_content = f"fs_edit: {path} ({bytes_written} bytes)"
    summary = new_content
    return new_content, summary, recover


def _summarize_fs_glob(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize fs_glob: keep pattern + first 10 matches."""
    pattern = args.get("pattern", "")
    data = _try_parse_json(content)
    files = (data or {}).get("files", []) if data else []
    truncated = (data or {}).get("truncated", False) if data else False
    recover = f"fs_glob(pattern={pattern!r}) 重新搜索"

    if stage == 1:
        kept = files[:10]
        new_content = json.dumps(
            {"pattern": pattern, "matches": kept, "total": len(files), "truncated": truncated},
            ensure_ascii=False,
        )
        summary = f"fs_glob: {pattern}, {len(files)} matches"
        return new_content, summary, recover

    if stage == 2:
        kept = files[:5]
        new_content = json.dumps(
            {"pattern": pattern, "matches": kept, "total": len(files)},
            ensure_ascii=False,
        )
        summary = f"fs_glob: {pattern}, {len(files)} matches"
        return new_content, summary, recover

    new_content = f"fs_glob: {pattern}, {len(files)} matches"
    summary = new_content
    return new_content, summary, recover


def _summarize_web_search(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize web_search: keep query + first 5 result titles + URLs."""
    query = args.get("query", "")
    data = _try_parse_json(content)
    answer = (data or {}).get("answer", "") if data else ""
    results = (data or {}).get("results", []) if data else []
    recover = f"web_search(query={query!r}) 重新搜索"

    if stage == 1:
        top = [
            {"title": r.get("title", ""), "url": r.get("url", "")}
            for r in results[:5]
            if isinstance(r, dict)
        ]
        new_content = json.dumps(
            {"query": query, "answer": answer, "results": top},
            ensure_ascii=False,
        )
        summary = f"web_search: {query}, {len(results)} results"
        return new_content, summary, recover

    if stage == 2:
        titles = [r.get("title", "") for r in results[:3] if isinstance(r, dict)]
        new_content = json.dumps(
            {"query": query, "titles": titles}, ensure_ascii=False
        )
        summary = f"web_search: {query}, {len(results)} results"
        return new_content, summary, recover

    new_content = f"web_search: {query}, {len(results)} results"
    summary = new_content
    return new_content, summary, recover


def _summarize_read_attachment(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize read_attachment: keep fileName + content preview."""
    data = _try_parse_json(content)
    file_name = (data or {}).get("fileName", args.get("fileName", ""))
    raw_content = (data or {}).get("content", "") if data else content
    recover = f"read_attachment(fileName={file_name!r}) 重新读取"

    if stage == 1:
        new_content = json.dumps(
            {"fileName": file_name, "content": raw_content[:500]},
            ensure_ascii=False,
        )
        summary = f"read_attachment: {file_name}"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps({"fileName": file_name}, ensure_ascii=False)
        summary = f"read_attachment: {file_name}"
        return new_content, summary, recover

    new_content = f"read_attachment: {file_name}"
    summary = new_content
    return new_content, summary, recover


def _summarize_deploy(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize deploy_artifact / deploy_workspace: keep status + preview URL."""
    data = _try_parse_json(content)
    dep_id = (data or {}).get("id", "")
    title = (data or {}).get("title", "")
    preview_path = (data or {}).get("previewPath", "")
    status = (data or {}).get("status", "")
    source_type = (data or {}).get("sourceType", "")
    recover = f"deploy preview: {preview_path}"

    if stage == 1:
        new_content = json.dumps(
            {"id": dep_id, "title": title, "previewPath": preview_path, "status": status},
            ensure_ascii=False,
        )
        summary = f"deploy ({source_type}): {title} — {status}"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps(
            {"id": dep_id, "status": status, "previewPath": preview_path},
            ensure_ascii=False,
        )
        summary = f"deploy: {status}"
        return new_content, summary, recover

    new_content = f"deploy: {title} — {status}"
    summary = new_content
    return new_content, summary, recover


def _summarize_task_dispatch(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize task_dispatch: keep agentId + status + result head."""
    agent_id = args.get("agentId", "")
    task_desc = args.get("task", "")
    data = _try_parse_json(content)
    status = (data or {}).get("status", "")
    summary_text = (data or {}).get("summary", "") if data else content
    recover = f"task_dispatch(task={task_desc[:50]!r}) 重新执行"

    if stage == 1:
        new_content = json.dumps(
            {
                "agentId": agent_id,
                "status": status,
                "summary": summary_text[:200],
            },
            ensure_ascii=False,
        )
        summary = f"task_dispatch: {status}"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps(
            {"agentId": agent_id, "status": status}, ensure_ascii=False
        )
        summary = f"task_dispatch: {status}"
        return new_content, summary, recover

    new_content = f"task_dispatch: {status}"
    summary = new_content
    return new_content, summary, recover


def _summarize_dispatch_plan(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize dispatch_plan: keep task count + statuses summary."""
    data = _try_parse_json(content)
    tasks = (data or {}).get("tasks", {}) if data else {}
    task_count = len(tasks) if isinstance(tasks, dict) else 0
    statuses: Counter[str] = Counter()
    if isinstance(tasks, dict):
        for t in tasks.values():
            if isinstance(t, dict):
                statuses[t.get("status", "unknown")] += 1
    status_summary = ", ".join(f"{k}={v}" for k, v in statuses.items()) or "unknown"
    recover = "dispatch_plan 不可恢复，结果已摘要"

    if stage == 1:
        new_content = json.dumps(
            {"taskCount": task_count, "statuses": dict(statuses)},
            ensure_ascii=False,
        )
        summary = f"dispatch_plan: {task_count} tasks ({status_summary})"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps({"taskCount": task_count, "statusSummary": status_summary}, ensure_ascii=False)
        summary = f"dispatch_plan: {task_count} tasks"
        return new_content, summary, recover

    new_content = f"dispatch_plan: {task_count} tasks ({status_summary})"
    summary = new_content
    return new_content, summary, recover


def _summarize_plan_tools(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize create_plan / plan_step / add_plan_steps: keep planId + step count."""
    data = _try_parse_json(content)
    plan_id = (data or {}).get("planId", args.get("planId", ""))
    step_count = (data or {}).get("stepCount", 0) or (data or {}).get("totalSteps", 0)
    added_count = (data or {}).get("addedCount", 0)
    status = (data or {}).get("status", "")
    recover = f"查看计划 planId={plan_id}"

    if stage == 1:
        info = {"planId": plan_id}
        if step_count:
            info["stepCount"] = step_count
        if added_count:
            info["addedCount"] = added_count
        if status:
            info["status"] = status
        new_content = json.dumps(info, ensure_ascii=False)
        summary = f"plan: {plan_id}, {step_count or added_count} steps"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps({"planId": plan_id, "stepCount": step_count or added_count}, ensure_ascii=False)
        summary = f"plan: {plan_id}"
        return new_content, summary, recover

    new_content = f"plan: {plan_id}, {step_count or added_count} steps"
    summary = new_content
    return new_content, summary, recover


def _summarize_manage_tools(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize manage_* tools: keep action + result status."""
    action = args.get("action", "")
    data = _try_parse_json(content)
    total = (data or {}).get("total", 0)
    deleted = (data or {}).get("deleted", 0)
    message = (data or {}).get("message", "")
    recover = "manage 操作不可恢复，结果已摘要"

    if stage == 1:
        info: dict[str, Any] = {"action": action}
        if total:
            info["total"] = total
        if deleted:
            info["deleted"] = deleted
        if message:
            info["message"] = message[:200]
        new_content = json.dumps(info, ensure_ascii=False)
        summary = f"manage: action={action}"
        return new_content, summary, recover

    if stage == 2:
        new_content = json.dumps({"action": action, "total": total or deleted}, ensure_ascii=False)
        summary = f"manage: action={action}"
        return new_content, summary, recover

    new_content = f"manage: action={action}"
    summary = new_content
    return new_content, summary, recover


def _summarize_ask_user(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Summarize ask_user: keep question + answer head."""
    questions = args.get("questions", [])
    first_q = ""
    if isinstance(questions, list) and questions:
        q0 = questions[0]
        first_q = q0.get("question", "") if isinstance(q0, dict) else str(q0)
    data = _try_parse_json(content)
    answers = (data or {}).get("answers", {}) if data else {}
    recover = "ask_user 不可恢复，用户回答已摘要"

    if stage == 1:
        truncated_answers = {
            k[:80]: v[:200] if isinstance(v, str) else str(v)[:200]
            for k, v in answers.items()
        }
        new_content = json.dumps(
            {"q": first_q[:50], "answers": truncated_answers},
            ensure_ascii=False,
        )
        summary = f"ask_user: {first_q[:50]}"
        return new_content, summary, recover

    if stage == 2:
        first_answer = next(iter(answers.values()), "") if answers else ""
        new_content = json.dumps(
            {"question": first_q[:50], "answer": first_answer[:100] if isinstance(first_answer, str) else str(first_answer)[:100]},
            ensure_ascii=False,
        )
        summary = f"ask_user: {first_q[:50]}"
        return new_content, summary, recover

    new_content = f"ask_user: {first_q[:50]}"
    summary = new_content
    return new_content, summary, recover


def _warn_unknown_tool(tool_name: str) -> None:
    """Log a warning when a tool without a dedicated summarizer is encountered."""
    logger.warning(
        "[compact-pipeline] unknown tool %r encountered in summarizer; "
        "falling back to generic truncation",
        tool_name,
    )


def _summarize_unknown(args: dict, content: str, stage: int) -> tuple[str, str, str]:
    """Fallback for tools not in the strategy table."""
    if stage == 1:
        new_content = content[:1000]
        summary = "未知工具结果，保留前 1000 字符"
        return new_content, summary, "重新调用原工具以获取完整结果"

    if stage == 2:
        return "[tool_result 已摘要（stage 2）]", "未知工具结果已折叠", "重新调用原工具以获取完整结果"

    # stage 3 — the fold marker is produced by the caller; here we just empty.
    return "[tool_result 已折叠（stage 3）]", "未知工具结果已折叠", "重新调用原工具以获取完整结果"


_SUMMARIZERS = {
    "fs_list": _summarize_fs_list,
    "fs_read": _summarize_fs_read,
    "bash": _summarize_bash,
    "fs_grep": _summarize_fs_grep,
    "code_explore": _summarize_code_explore,
    "load_skill": _summarize_load_skill,
    "write_artifact": _summarize_write_artifact,
    "read_artifact": _summarize_read_artifact,
    "update_artifact": _summarize_update_artifact,
    "fs_write": _summarize_fs_write,
    "fs_edit": _summarize_fs_edit,
    "fs_glob": _summarize_fs_glob,
    "web_search": _summarize_web_search,
    "read_attachment": _summarize_read_attachment,
    "deploy_artifact": _summarize_deploy,
    "deploy_workspace": _summarize_deploy,
    "task_dispatch": _summarize_task_dispatch,
    "dispatch_plan": _summarize_dispatch_plan,
    "create_plan": _summarize_plan_tools,
    "plan_step": _summarize_plan_tools,
    "add_plan_steps": _summarize_plan_tools,
    "manage_agents": _summarize_manage_tools,
    "manage_skills": _summarize_manage_tools,
    "manage_mcp": _summarize_manage_tools,
    "manage_documents": _summarize_manage_tools,
    "manage_memory": _summarize_manage_tools,
    "manage_profile": _summarize_manage_tools,
    "manage_conversations": _summarize_manage_tools,
    "ask_user": _summarize_ask_user,
}


def summarize_tool_result(
    tool_name: str,
    args: dict,
    content: str,
    stage: int,
) -> str:
    """Produce a pruned version of a tool_result's content.

    Dispatches to a per-tool summarizer; unknown tools fall back to the generic
    "first 1k chars → marker → folded" strategy. For stages 1/2 the result is a
    new content string. ``stage`` is 1 (light), 2 (moderate), or 3 (heavy).
    """
    new_content, _summary = summarize_tool_result_with_summary(
        tool_name, args, content, stage
    )
    return new_content


def summarize_tool_result_with_summary(
    tool_name: str,
    args: dict,
    content: str,
    stage: int,
) -> tuple[str, str]:
    """Like ``summarize_tool_result`` but also returns the human-readable summary.

    Returns ``(new_content, summary)``. The summary is a short one-liner
    describing what was kept (e.g. "src/ 含 5 文件、3 子目录"). Used by the
    transcript renderer to give the LLM a quick scan line before the
    compressed content.
    """
    summarizer = _SUMMARIZERS.get(tool_name)
    if summarizer is None:
        _warn_unknown_tool(tool_name)
        new_content, summary, _recover = _summarize_unknown(args, content, stage)
        return new_content, summary
    new_content, summary, _recover = summarizer(args, content, stage)
    return new_content, summary


def summarize_tool_result_full(
    tool_name: str,
    args: dict,
    content: str,
    stage: int,
) -> tuple[str, str, str]:
    """Like ``summarize_tool_result_with_summary`` but also returns the recover hint.

    Returns ``(new_content, summary, recover_hint)``. The recover hint tells the
    model how to re-fetch the original content (e.g. ``fs_list(path='src', depth=3)
    重新获取结构``). Used by cross-run compaction (Tier 4) to build structured
    markers via ``CompactMarkerBuilder.build_tool_result_marker``.
    """
    summarizer = _SUMMARIZERS.get(tool_name)
    if summarizer is None:
        _warn_unknown_tool(tool_name)
        return _summarize_unknown(args, content, stage)
    return summarizer(args, content, stage)


# ─── Five-stage pipeline ─────────────────────────────────────────────────────


def _find_tool_call_for_tool_message(
    messages: list[dict],
    tool_msg_index: int,
) -> tuple[str, dict] | None:
    """Find the tool name + args for a ``role=="tool"`` message.

    Scans backwards from ``tool_msg_index`` for the assistant message whose
    ``tool_calls`` contains an entry with a matching ``id`` (== tool_msg's
    ``tool_call_id``). Returns ``(tool_name, args_dict)`` or None.
    """
    tool_msg = messages[tool_msg_index]
    tool_call_id = tool_msg.get("tool_call_id") if isinstance(tool_msg, dict) else None
    if not tool_call_id:
        return None

    for j in range(tool_msg_index - 1, -1, -1):
        prev = messages[j]
        if not isinstance(prev, dict) or prev.get("role") != "assistant":
            continue
        for tc in prev.get("tool_calls") or []:
            if isinstance(tc, dict) and tc.get("id") == tool_call_id:
                fn = tc.get("function") or {}
                return fn.get("name", ""), _parse_args(fn.get("arguments"))
    return None


def _stage1_summarize(messages: list[dict]) -> list[dict]:
    """Stage 1: summarize old tool_result content, keep recent turns intact."""
    recent, old = keep_recent_turns(messages, k=KEEP_RECENT_TURNS)
    if not old:
        return list(messages)

    # old indices start at 0 in the old slice. We summarize tool messages there.
    for idx, msg in enumerate(old):
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        # Recover the tool name + args from the paired assistant tool_call.
        # ``old`` is a prefix of ``messages`` so indices within old map 1:1 to
        # indices within the full messages list for positions < len(old).
        pair = _find_tool_call_for_tool_message(messages, idx)
        if pair is None:
            continue
        tool_name, args = pair
        content = _parse_tool_content(msg.get("content"))
        # code_explore / outline / head are always preserved — skip entirely.
        if tool_name == "code_explore":
            continue
        mode = args.get("mode")
        if tool_name == "fs_read" and mode in ("outline", "head"):
            continue
        new_content = summarize_tool_result(tool_name, args, content, stage=1)
        # Only replace if the summary is actually shorter (safeguard against
        # dense-code outlines that can be larger than the original).
        if new_content != content and len(new_content) < len(content):
            msg["content"] = new_content

    # Reassemble: old (now summarized) + recent.
    return old + recent


def _stage2_prune(messages: list[dict]) -> list[dict]:
    """Stage 2: re-prune stage-1 summaries more aggressively."""
    recent, old = keep_recent_turns(messages, k=KEEP_RECENT_TURNS)
    if not old:
        return list(messages)

    for idx, msg in enumerate(old):
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        pair = _find_tool_call_for_tool_message(messages, idx)
        if pair is None:
            continue
        tool_name, args = pair
        if tool_name == "code_explore":
            continue
        mode = args.get("mode")
        if tool_name == "fs_read" and mode in ("outline", "head"):
            continue
        content = _parse_tool_content(msg.get("content"))
        new_content = summarize_tool_result(tool_name, args, content, stage=2)
        if new_content != content and len(new_content) < len(content):
            msg["content"] = new_content

    return old + recent


def _collect_tool_names_in_span(messages: list[dict], start: int, end: int) -> Counter[str]:
    """Count tool names invoked by assistant messages in [start, end]."""
    counts: Counter[str] = Counter()
    for idx in range(start, end + 1):
        msg = messages[idx]
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                name = fn.get("name")
                if name:
                    counts[name] += 1
    return counts


def _first_user_head(messages: list[dict], start: int, end: int) -> str | None:
    for idx in range(start, end + 1):
        msg = messages[idx]
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()[:80]
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text", "")
                        if text.strip():
                            return text.strip()[:80]
    return None


def _last_assistant_text_head(messages: list[dict], start: int, end: int) -> str | None:
    for idx in range(end, start - 1, -1):
        msg = messages[idx]
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:80]
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text.strip():
                        return text.strip()[:80]
    return None


def _stage3_fold(messages: list[dict]) -> list[dict]:
    """Stage 3: fold older turns into a single fold marker.

    The first ``role=="system"`` message (the system prompt / agent identity)
    is always preserved verbatim — folding it would break the agent.
    """
    # Preserve the system prompt if it's the first message.
    system_prompt: dict | None = None
    body_start = 0
    if messages and isinstance(messages[0], dict) and messages[0].get("role") == "system":
        system_prompt = dict(messages[0])
        body_start = 1
    body = messages[body_start:]

    boundaries = find_turn_boundaries(body)
    if not boundaries or len(boundaries) < FOLD_TURN_THRESHOLD:
        # Fallback to legacy count-based keep.
        if len(body) > LEGACY_RECENT_KEEP:
            logger.warning(
                "[compact-pipeline] stage 3: no/few turn boundaries (%d turns), "
                "falling back to recent_keep=%d",
                len(boundaries),
                LEGACY_RECENT_KEEP,
            )
            recent = list(body[-LEGACY_RECENT_KEEP:])
            old = list(body[:-LEGACY_RECENT_KEEP])
        else:
            return list(messages)
    else:
        recent, old = keep_recent_turns(body, k=KEEP_RECENT_TURNS)
        if not old:
            return list(messages)

    # Build fold marker from the old span.
    tools_used = _collect_tool_names_in_span(old, 0, len(old) - 1)
    first_user = _first_user_head(old, 0, len(old) - 1)
    last_reply = _last_assistant_text_head(old, 0, len(old) - 1)

    # Count how many complete turns were folded (within old).
    folded_turns = len(find_turn_boundaries(old))

    summary = f"已折叠 {len(old)} 条消息（{folded_turns} 个工具轮次）"
    fold_marker_text = CompactMarkerBuilder.build_fold_marker(
        stage=3,
        turns_folded=folded_turns,
        tools_used_counts=tools_used,
        summary=summary,
        first_user_msg_head=first_user,
        last_assistant_text_head=last_reply,
    )
    fold_marker = {"role": "system", "content": fold_marker_text}
    result: list[dict] = []
    if system_prompt is not None:
        result.append(system_prompt)
    result.append(fold_marker)
    result.extend(recent)
    return result


def run_compact_pipeline(messages: list[dict], stage: int) -> list[dict]:
    """Dispatch to the right stage function.

    ``stage`` must be 1, 2, or 3. Stages 4/5 (soft_inject / force_final) are
    handled in ``react_loop_termination`` and are not dispatched here.
    """
    if stage == 1:
        return _stage1_summarize(messages)
    if stage == 2:
        return _stage2_prune(messages)
    if stage == 3:
        return _stage3_fold(messages)
    raise ValueError(f"unknown compact stage: {stage}")


# Re-export the success judge for convenience.
judge_compact_success = CompactSuccessJudge.judge
