"""PPT block normalisation.

Port of the normalisation half of src/shared/ppt-normalize.ts (the deck/slide
rendering helpers live with the PPT export route in a later phase). The
artifact-content builder uses :func:`normalize_blocks` to clean slide ``blocks``
before storing a ppt artifact.

Blocks are returned as plain camelCase dicts so they stay byte-compatible with
the existing frontend and the TS-written rows.
"""

from __future__ import annotations

from typing import Any

_TONES = {"neutral", "positive", "negative", "info", "warning"}


def normalize_blocks(raw_blocks: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_blocks, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_blocks:
        block = _normalize_block(raw)
        if block:
            out.append(block)
    return out


def _normalize_block(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    obj = raw
    block_type = _clean_text(obj.get("type"))

    if block_type == "heading":
        text = _read_block_text(obj)
        if not text:
            return None
        level = obj.get("level") if obj.get("level") in (1, 2) else None
        block: dict[str, Any] = {"type": "heading", "text": text}
        if level:
            block["level"] = level
        return block

    if block_type == "paragraph":
        text = _read_block_text(obj)
        return {"type": "paragraph", "text": text} if text else None

    if block_type == "bullets":
        items = _normalize_string_list(
            obj.get("items") if obj.get("items") is not None
            else obj.get("bullets") if obj.get("bullets") is not None
            else obj.get("points")
        )
        return (
            {"type": "bullets", "items": items, "ordered": obj.get("ordered") is True}
            if items
            else None
        )

    if block_type == "metric":
        label = _clean_text(obj.get("label"))
        value = _clean_text(obj.get("value"))
        if not label or not value:
            return None
        block = {"type": "metric", "label": label, "value": value}
        change = _clean_text(obj.get("change"))
        if change:
            block["change"] = change
        tone = _normalize_tone(obj.get("tone"))
        if tone:
            block["tone"] = tone
        return block

    if block_type == "quote":
        text = _read_block_text(obj)
        if not text:
            return None
        block = {"type": "quote", "text": text}
        attribution = _clean_text(
            obj.get("attribution")
            if obj.get("attribution") is not None
            else obj.get("author") if obj.get("author") is not None
            else obj.get("source")
        )
        if attribution:
            block["attribution"] = attribution
        return block

    if block_type == "timeline":
        items = _normalize_timeline_items(obj.get("items"))
        return {"type": "timeline", "items": items} if items else None

    if block_type == "columns":
        columns = _normalize_columns(obj.get("columns"))
        return {"type": "columns", "columns": columns} if columns else None

    if block_type == "callout":
        text = _read_block_text(obj)
        if not text:
            return None
        block = {"type": "callout"}
        title = _clean_text(obj.get("title"))
        if title:
            block["title"] = title
        block["text"] = text
        tone = _normalize_tone(obj.get("tone"))
        if tone:
            block["tone"] = tone
        return block

    if block_type == "divider":
        return {"type": "divider"}

    if block_type == "spacer":
        size = obj.get("size") if obj.get("size") in ("sm", "md", "lg") else None
        block = {"type": "spacer"}
        if size:
            block["size"] = size
        return block

    return None


def _normalize_columns(raw_columns: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_columns, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_columns[:3]:
        if not isinstance(raw, dict):
            continue
        title = _clean_text(raw.get("title"))
        blocks = _normalize_column_blocks(raw.get("blocks"))
        bullets = _normalize_string_list(
            raw.get("bullets") if raw.get("bullets") is not None
            else raw.get("items") if raw.get("items") is not None
            else raw.get("points")
        )
        if bullets:
            blocks.append({"type": "bullets", "items": bullets})
        if not title and not blocks:
            continue
        column: dict[str, Any] = {}
        if title:
            column["title"] = title
        column["blocks"] = blocks
        out.append(column)
    return out


def _normalize_column_blocks(raw_blocks: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_blocks, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_blocks:
        block = _normalize_block(raw)
        if block and block.get("type") in ("paragraph", "bullets", "metric", "callout"):
            out.append(block)
    return out


def _normalize_timeline_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    out: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        label = _clean_text(
            raw.get("label") if raw.get("label") is not None
            else raw.get("date") if raw.get("date") is not None
            else raw.get("phase")
        )
        if not label:
            continue
        item: dict[str, Any] = {"label": label}
        title = _clean_text(raw.get("title"))
        if title:
            item["title"] = title
        text = _clean_text(
            raw.get("text") if raw.get("text") is not None else raw.get("description")
        )
        if text:
            item["text"] = text
        out.append(item)
    return out


def _normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str):
                out.extend(_split_lines(item))
        return out
    if isinstance(value, str):
        return _split_lines(value)
    return []


def _split_lines(value: str) -> list[str]:
    return [item.strip() for item in value.split("\n") if item.strip()]


def _read_block_text(obj: dict[str, Any]) -> str | None:
    return _clean_text(
        obj.get("text") if obj.get("text") is not None
        else obj.get("content") if obj.get("content") is not None
        else obj.get("body")
    )


def _clean_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _normalize_tone(value: Any) -> str | None:
    return value if value in _TONES else None
