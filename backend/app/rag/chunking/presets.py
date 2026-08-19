"""Chunking preset definitions and resolution helpers.

4 presets: general / qa / semantic / separator.
"""

import json
import logging

logger = logging.getLogger(__name__)

CHUNK_PRESETS: dict[str, dict[str, str]] = {
    "general": {
        "label": "General",
        "description": "通用分块：递归分隔符栈 + Markdown 保护（笔记 / 日记 / 论文）",
    },
    "qa": {
        "label": "QA",
        "description": "问答分块：优先抽取问题-回答结构（面经 / FAQ）",
    },
    "semantic": {
        "label": "Semantic",
        "description": "语义分块：嵌入聚类 + 自动增强标题上下文（论文 / 技术文档）",
    },
    "separator": {
        "label": "Separator",
        "description": "严格分隔：命中分隔符即切分（特定导出格式）",
    },
}

_DEFAULT_PRESET = "general"


def normalize_chunk_preset_id(preset_id: str | None) -> str:
    """Normalize a preset ID, falling back to 'general' for invalid values."""
    if not preset_id:
        return _DEFAULT_PRESET
    pid = preset_id.strip().lower()
    if pid not in CHUNK_PRESETS:
        logger.warning("Unknown chunk preset '%s', falling back to '%s'", preset_id, _DEFAULT_PRESET)
        return _DEFAULT_PRESET
    return pid


def resolve_chunk_processing_params(preset_id: str, config: dict | None) -> dict:
    """Resolve processing parameters for a preset from config.

    config keys (all optional):
      - chunk_size: int (default 200)
      - chunk_overlap: int (default 50)
      - separators: list[str] (general preset only)
      - separator: str (separator preset only, default "---")
      - semantic_threshold: float (semantic preset, default 0.5)
      - qa_patterns: list[str] (qa preset, uses built-in defaults if absent)

    Also merges settings from rag_chunk_parser_config JSON string if present.
    """
    params: dict = {
        "chunk_size": 200,
        "chunk_overlap": 50,
    }

    if config:
        # Merge chunk_size and chunk_overlap from config
        if "chunk_size" in config:
            params["chunk_size"] = int(config["chunk_size"])
        if "chunk_overlap" in config:
            params["chunk_overlap"] = int(config["chunk_overlap"])
        if "separators" in config:
            params["separators"] = config["separators"]
        if "separator" in config:
            params["separator"] = config["separator"]
        if "semantic_threshold" in config:
            params["semantic_threshold"] = float(config["semantic_threshold"])
        if "qa_patterns" in config:
            params["qa_patterns"] = config["qa_patterns"]

    # Merge rag_chunk_parser_config JSON overrides
    raw_json = config.get("_parser_config_json") if config else None
    if raw_json:
        try:
            overrides = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
            if isinstance(overrides, dict):
                preset_overrides = overrides.get(preset_id, {})
                params.update(preset_overrides)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Failed to parse rag_chunk_parser_config: %s", e)

    return params
