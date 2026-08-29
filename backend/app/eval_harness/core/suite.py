"""
Suite loading from YAML with strict validation.

All validation rules (semver version, unique task ids, grader name format,
weight >= 0, sample_count 1-10, name <= 128 chars, at least one task) live on
the Pydantic models in core/types.py so both YAML loading and API JSON
creation validate identically. This module wraps load/validation failures
with file-path context.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from eval_harness.core.types import EvalSuite
from pydantic import ValidationError


class SuiteLoadError(Exception):
    """Suite YAML 加载或校验失败 (错误信息包含文件路径上下文)。"""


def load_suite(path: str | Path) -> EvalSuite:
    """
    从 YAML 文件加载评测套件。

    Args:
        path: YAML 文件路径

    Returns:
        校验通过的 EvalSuite

    Raises:
        SuiteLoadError: 文件不存在 / YAML 语法错误 / 校验失败 (均含文件路径)
    """
    suite_path = Path(path)

    if not suite_path.exists():
        raise SuiteLoadError(f"Suite file not found: {suite_path}")

    try:
        with open(suite_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as e:
        raise SuiteLoadError(f"Cannot read suite file '{suite_path}': {e}") from e
    except yaml.YAMLError as e:
        raise SuiteLoadError(f"Invalid YAML in suite file '{suite_path}': {e}") from e

    if not isinstance(data, dict):
        raise SuiteLoadError(
            f"Suite file '{suite_path}' must contain a YAML mapping "
            f"(got {type(data).__name__})"
        )

    try:
        return EvalSuite(**data)
    except ValidationError as e:
        raise SuiteLoadError(f"Suite validation failed for '{suite_path}':\n{e}") from e
