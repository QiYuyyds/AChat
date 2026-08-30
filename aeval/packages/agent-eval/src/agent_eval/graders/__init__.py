"""
Built-in graders for the Aeval evaluation framework.

Provides 9 built-in graders covering agent eval scenarios:
- code_based: Deterministic checks (string/regex matching)
- model_based: LLM-as-Judge
- state_check: Environment state verification
- tool_calls: Tool call validation
- transcript: Transcript analysis (turns/tokens/redundancy)
- artifact_check: Artifact verification
- human: Human expert scoring (pending semantics, async score submission)
- step_level: Step-level evaluation (expected_trace comparison)
- metric: LLM 输出质量指标分发 (按 config.metric_name 路由到 metrics 注册表)

Usage:
    from agent_eval.graders import DEFAULT_GRADERS, get_grader_catalog

    runner = EvalRunner(
        agent_runner=my_runner,
        graders=DEFAULT_GRADERS,  # Use all built-in graders
    )

    # API listing (name/type/description)
    catalog = get_grader_catalog()
"""

from typing import Any

from agent_eval.core.types import GraderType
from agent_eval.graders.artifact_check import ArtifactCheckGrader
from agent_eval.graders.code_based import CodeBasedGrader
from agent_eval.graders.human import HumanGrader
from agent_eval.graders.metric import MetricGrader
from agent_eval.graders.model_based import ModelBasedGrader
from agent_eval.graders.state_check import StateCheckGrader
from agent_eval.graders.step_level import StepLevelGrader
from agent_eval.graders.tool_calls import ToolCallsGrader
from agent_eval.graders.transcript import TranscriptGrader

# 注册表: name → {grader 实例, 类型, 描述} (供 API 列举与默认装配)
GRADER_REGISTRY: dict[str, dict[str, Any]] = {}


def _register(grader: Any, grader_type: GraderType, description: str) -> None:
    GRADER_REGISTRY[grader.name] = {
        "grader": grader,
        "type": grader_type,
        "description": description,
    }


_register(CodeBasedGrader(), GraderType.CODE, "确定性评分：字符串/正则/精确匹配检查")
_register(ModelBasedGrader(), GraderType.MODEL, "LLM-as-Judge 评分")
_register(StateCheckGrader(), GraderType.STATE, "环境状态检查")
_register(ToolCallsGrader(), GraderType.TOOL_CALLS, "工具调用验证 (必须/禁止调用)")
_register(TranscriptGrader(), GraderType.TRANSCRIPT, "转录记录分析 (轮次/Token 冗余)")
_register(ArtifactCheckGrader(), GraderType.ARTIFACT, "产物检查 (类型/内容正则)")
_register(HumanGrader(), GraderType.CUSTOM, "人工评分 (pending 语义, 异步回传)")
_register(StepLevelGrader(), GraderType.CUSTOM, "步骤级评估 (expected_trace 对照)")
_register(MetricGrader(), GraderType.METRIC, "LLM 输出质量指标 (按 metric_name 从注册表分发)")

# 默认内置 grader 实例列表
DEFAULT_GRADERS = [entry["grader"] for entry in GRADER_REGISTRY.values()]


def get_grader_catalog() -> list[dict[str, str]]:
    """列出可用 grader (name/type/description), 供 GET /graders 使用"""
    return [
        {
            "name": name,
            "type": entry["type"].value,
            "description": entry["description"],
        }
        for name, entry in GRADER_REGISTRY.items()
    ]


__all__ = [
    "DEFAULT_GRADERS",
    "GRADER_REGISTRY",
    "get_grader_catalog",
    "CodeBasedGrader",
    "ModelBasedGrader",
    "StateCheckGrader",
    "ToolCallsGrader",
    "TranscriptGrader",
    "ArtifactCheckGrader",
    "HumanGrader",
    "StepLevelGrader",
    "MetricGrader",
]
