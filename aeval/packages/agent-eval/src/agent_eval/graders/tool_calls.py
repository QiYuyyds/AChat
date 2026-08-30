"""
Tool-calls grader — tool call validation.

Validates that the agent:
- Called required tools
- Did not use forbidden tools
- (Optional) Called tools in a specific order

Config schema:
    {
        "required_tools": ["fs_read", "fs_write"],
        "forbidden_tools": ["dangerous_tool"],
        "threshold": 1.0
    }
"""

from __future__ import annotations

from typing import Any

from agent_eval.core.contract import EvalContext, Grader
from agent_eval.core.types import GraderResult, GraderType, EvalTask, TrialResult


class ToolCallsGrader:
    """工具调用验证评分器"""

    name = "tool_calls"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        required_tools = config.get("required_tools", [])
        forbidden_tools = config.get("forbidden_tools", [])
        threshold = config.get("threshold", 1.0)

        # 从 spans 提取工具调用
        tool_calls = self._extract_tool_calls(spans)
        used_tools = [tc["name"] for tc in tool_calls]

        # 检查必须使用的工具
        missing = [t for t in required_tools if t not in used_tools]
        # 检查禁止使用的工具
        violated = [t for t in forbidden_tools if t in used_tools]

        # 计算分数
        if required_tools:
            found = len(required_tools) - len(missing)
            score = found / len(required_tools)
        else:
            score = 1.0

        # 违反禁止工具则直接 0 分
        if violated:
            score = 0.0

        passed = score >= threshold

        # 构建解释
        parts = []
        if used_tools:
            parts.append(f"Used: {used_tools}")
        if missing:
            parts.append(f"Missing required: {missing}")
        if violated:
            parts.append(f"Violated forbidden: {violated}")
        explanation = "; ".join(parts) if parts else "No tool calls checked"

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.TOOL_CALLS,
            score=score,
            passed=passed,
            explanation=explanation,
            details={
                "tool_calls": tool_calls,
                "used_tools": used_tools,
                "missing": missing,
                "violated": violated,
            },
        )

    def _extract_tool_calls(self, spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """从 spans 中提取工具调用"""
        tool_calls = []
        for span in spans:
            name = span.get("name", "")
            attrs = span.get("attributes", {})

            if "tool.call" in name or "tool_call" in name:
                tool_calls.append({
                    "name": attrs.get("agenthub.tool_name")
                        or attrs.get("tool.name", ""),
                    "success": attrs.get("agenthub.success", True),
                })

        return tool_calls
