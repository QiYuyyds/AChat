"""LLM-assisted dataset generation — scenario description → batch items.

The LLM function is injected as a protocol (`async (system, user) -> str`);
the framework core binds to no LLM SDK. Generated items go through the SAME
validation as manual import: every item must carry a prompt and grader
config; invalid ones are rejected with explicit reasons.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from agent_eval.core.types import GraderConfig
from agent_eval.dataset.models import (
    DatasetError,
    EvalDatasetItem,
    SourceType,
    now_ms,
)

# LLM 函数协议: (system_prompt, user_message) → raw text (D2)
LLMFn = Callable[[str, str], Awaitable[str]]

_ALLOWED_GRADER_TYPES = ("code", "model", "metric", "tool_calls", "transcript", "artifact")

_GEN_SYSTEM_PROMPT = "You are an evaluation dataset designer. You output only valid JSON."

_GEN_PROMPT = """根据以下场景描述，生成 {count} 个评测任务。

场景: {scenario}
能力维度: {capabilities}

每个任务是一个 JSON 对象，包含:
- "id": 唯一标识 (kebab-case, 如 "summarize-quarterly-report")
- "description": 一句话描述
- "prompt": 给 Agent 的完整指令
- "capabilities": 该任务考察的能力维度标签列表 (从上面能力维度中选)
- "graders": 评分器配置列表，每项形如 {{"type": "<type>", "name": "<name>", "config": {{...}}}}
  - type 只能是: {grader_types}
  - name 必须用框架内置名: model→"model_based", code→"code_based", metric→"metric",
    tool_calls→"tool_calls", transcript→"transcript", artifact→"artifact_check"
  - metric 类型请在 config 中加 "metric_name" (如 faithfulness) 与 "threshold"

以 JSON 数组格式返回，不要输出数组以外的任何内容。"""


class DatasetGenerationError(DatasetError):
    """LLM 生成失败 (LLM 未配置 / 输出不可解析 / 无合法条目)。"""


@dataclass
class GenerationReport:
    """生成结果 — 合法条目 + 被拒绝条目的明细"""

    scenario: str = ""
    requested: int = 0
    items: list[EvalDatasetItem] = field(default_factory=list)
    invalid: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "requested": self.requested,
            "generated": len(self.items),
            "invalid_count": len(self.invalid),
            "invalid": self.invalid,
            "item_ids": [i.id for i in self.items],
        }


class LLMDatasetGenerator:
    """LLM 辅助生成评测数据集条目"""

    def __init__(self, llm_fn: LLMFn | None = None):
        self.llm_fn = llm_fn

    async def generate(
        self,
        scenario: str,
        capabilities: list[str] | None = None,
        count: int = 5,
        llm_fn: LLMFn | None = None,
    ) -> GenerationReport:
        """
        按场景批量生成评测条目。

        Args:
            scenario: 场景描述
            capabilities: 能力维度标签 (写入条目 metadata.capabilities)
            count: 请求生成的条目数
            llm_fn: 覆盖实例级 llm_fn

        Raises:
            DatasetGenerationError: 未注入 llm_fn / 输出不可解析 / 无一条合法
        """
        fn = llm_fn or self.llm_fn
        if fn is None:
            raise DatasetGenerationError(
                "LLM function not configured — pass llm_fn to "
                "LLMDatasetGenerator() or generate(). Metric-style judge "
                "assembly lives in eval_integration.config."
            )

        capabilities = capabilities or []
        report = GenerationReport(scenario=scenario, requested=count)
        caps = ", ".join(capabilities) if capabilities else "(无)"

        raw = await fn(
            _GEN_SYSTEM_PROMPT,
            _GEN_PROMPT.format(
                count=count,
                scenario=scenario,
                capabilities=caps,
                grader_types=", ".join(_ALLOWED_GRADER_TYPES),
            ),
        )

        parsed = _extract_json_array(raw)
        if parsed is None:
            raise DatasetGenerationError(
                f"LLM response is not a parsable JSON array (got: {raw[:200]!r}...)"
            )

        for idx, entry in enumerate(parsed):
            try:
                report.items.append(self._entry_to_item(entry, idx, capabilities, scenario))
            except DatasetError as e:
                report.invalid.append({"index": idx, "error": str(e)})

        if not report.items:
            detail = "; ".join(i["error"] for i in report.invalid) or "empty response"
            raise DatasetGenerationError(
                f"LLM generation produced no valid items — {detail}"
            )
        return report

    def _entry_to_item(
        self,
        entry: Any,
        idx: int,
        capabilities: list[str],
        scenario: str,
    ) -> EvalDatasetItem:
        if not isinstance(entry, dict):
            raise DatasetError(f"item #{idx}: must be a JSON object")

        prompt = entry.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise DatasetError(f"item #{idx}: missing required field 'prompt'")
        raw_graders = entry.get("graders")
        if not raw_graders:
            raise DatasetError(f"item #{idx}: missing required field 'graders'")

        graders = [self._parse_grader(g, idx) for g in raw_graders]

        item_id = entry.get("id")
        if not isinstance(item_id, str) or not item_id.strip():
            item_id = f"gen-{idx}"
        item_caps = entry.get("capabilities")
        if not isinstance(item_caps, list) or not item_caps:
            item_caps = list(capabilities)

        return EvalDatasetItem(
            id=item_id.strip(),
            prompt=prompt.strip(),
            description=str(entry.get("description", "")),
            graders=graders,
            metadata={
                "capabilities": [str(c) for c in item_caps],
                "scenario": scenario[:128],
            },
            source_type=SourceType.LLM_GENERATED,
            source_ref=scenario[:128],
            created_at=now_ms(),
        )

    @staticmethod
    def _parse_grader(raw: Any, idx: int) -> GraderConfig:
        from agent_eval.dataset.models import make_grader_config

        if isinstance(raw, str):
            return make_grader_config(raw)

        if isinstance(raw, dict):
            grader_type = raw.get("type")
            if not grader_type:
                raise DatasetError(f"item #{idx}: grader missing 'type': {raw!r}")
            config = raw.get("config") or {}
            if not isinstance(config, dict):
                raise DatasetError(f"item #{idx}: grader 'config' must be a mapping")
            name = raw.get("name")
            return make_grader_config(
                str(grader_type), name=str(name) if name else None, **config
            )

        raise DatasetError(f"item #{idx}: grader must be a string or object, got {raw!r}")


def _extract_json_array(raw: str) -> list[Any] | None:
    """从容错提取 LLM 输出中的 JSON 数组 (容忍代码围栏/前后缀文本)"""
    text = raw.strip()
    # 去掉 ```json ... ``` 围栏
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None
