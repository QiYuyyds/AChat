"""Manual dataset import — YAML / JSON files with strict validation.

Validation failure messages follow the change-① Suite loader convention
(pydantic ValidationError wrapped with the source file path). Items missing
a prompt or grader config are rejected with the specific item and field.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from agent_eval.dataset.models import (
    DatasetError,
    EvalDataset,
    SourceType,
    now_ms,
)
from pydantic import ValidationError


class DatasetImportError(DatasetError):
    """数据集导入失败 (解析/校验), 错误信息含来源文件上下文。"""


def _validate_items_or_raise(items: list[dict[str, Any]], source: str) -> None:
    """条目级必填校验: prompt 与 graders 必填, 缺失时报具体条目与字段。"""
    problems: list[str] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"items[{idx}]: must be a mapping (got {type(item).__name__})")
            continue
        item_label = f"items[{idx}]" + (f" (id={item.get('id')!r})" if item.get("id") else "")
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            problems.append(f"{item_label}: missing required field 'prompt'")
        if not item.get("graders"):
            problems.append(f"{item_label}: missing required field 'graders'")
    if problems:
        raise DatasetImportError(
            f"Dataset import failed for '{source}' — {len(problems)} item problem(s):\n"
            + "\n".join(f"- {p}" for p in problems)
        )


def _fill_item_defaults(items: list[dict[str, Any]], source_type: SourceType) -> list[dict[str, Any]]:
    """补齐条目溯源默认值 (source_type / created_at), 保留显式指定值。"""
    filled = []
    for item in items:
        item = dict(item)
        item.setdefault("source_type", source_type)
        item.setdefault("created_at", now_ms())
        filled.append(item)
    return filled


def parse_dataset_payload(
    data: dict[str, Any],
    source: str = "<payload>",
    source_type: SourceType = SourceType.MANUAL,
    source_ref: str = "",
) -> EvalDataset:
    """
    将 dict 解析为 EvalDataset (与文件导入同一校验路径, API 复用)。

    Args:
        data: 数据集定义 (name/items[...]/tags/...)
        source: 报错时展示的来源描述 (文件路径或 API)
        source_type: 条目默认来源类型
        source_ref: 条目默认来源引用

    Raises:
        DatasetImportError: 顶层不是 mapping / 条目缺 prompt 或 graders / 模型校验失败
    """
    if not isinstance(data, dict):
        raise DatasetImportError(
            f"Dataset payload '{source}' must contain a mapping "
            f"(got {type(data).__name__})"
        )

    raw_items = data.get("items", [])
    if not isinstance(raw_items, list):
        raise DatasetImportError(f"Dataset payload '{source}': 'items' must be a list")

    _validate_items_or_raise(raw_items, source)

    payload = dict(data)
    payload["items"] = _fill_item_defaults(raw_items, source_type)
    if source_ref:
        for item in payload["items"]:
            item.setdefault("source_ref", source_ref)

    try:
        return EvalDataset(**payload)
    except ValidationError as e:
        raise DatasetImportError(
            f"Dataset validation failed for '{source}':\n{e}"
        ) from e


def import_from_yaml(path: str | Path, source_type: SourceType = SourceType.MANUAL) -> EvalDataset:
    """从 YAML 文件导入数据集 (对抗样本等手工构造场景传 adversarial)。"""
    dataset_path = Path(path)

    if not dataset_path.exists():
        raise DatasetImportError(f"Dataset file not found: {dataset_path}")

    try:
        with open(dataset_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as e:
        raise DatasetImportError(f"Cannot read dataset file '{dataset_path}': {e}") from e
    except yaml.YAMLError as e:
        raise DatasetImportError(f"Invalid YAML in dataset file '{dataset_path}': {e}") from e

    return parse_dataset_payload(data, source=str(dataset_path), source_type=source_type)


def import_from_json(path: str | Path, source_type: SourceType = SourceType.MANUAL) -> EvalDataset:
    """从 JSON 文件导入数据集。"""
    dataset_path = Path(path)

    if not dataset_path.exists():
        raise DatasetImportError(f"Dataset file not found: {dataset_path}")

    try:
        with open(dataset_path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise DatasetImportError(f"Cannot read dataset file '{dataset_path}': {e}") from e
    except json.JSONDecodeError as e:
        raise DatasetImportError(f"Invalid JSON in dataset file '{dataset_path}': {e}") from e

    return parse_dataset_payload(data, source=str(dataset_path), source_type=source_type)


def import_from_content(
    content: str,
    format: str = "yaml",
    source: str = "<inline>",
    source_type: SourceType = SourceType.MANUAL,
    source_ref: str = "",
) -> EvalDataset:
    """
    从文本内容导入 (API 用): format 为 "yaml" 或 "json"。

    Raises:
        DatasetImportError: 格式不支持 / 语法错误 / 校验失败
    """
    fmt = (format or "yaml").lower()
    if fmt not in ("yaml", "json"):
        raise DatasetImportError(
            f"Unsupported import format '{format}' (valid: yaml, json)"
        )

    if fmt == "json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise DatasetImportError(f"Invalid JSON in dataset content '{source}': {e}") from e
    else:
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise DatasetImportError(f"Invalid YAML in dataset content '{source}': {e}") from e

    return parse_dataset_payload(
        data, source=source, source_type=source_type, source_ref=source_ref
    )
