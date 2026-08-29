"""
Artifact-check grader — artifact verification.

Validates artifacts produced by the agent:
- Artifact exists
- Artifact type matches expected
- Artifact content matches regex pattern

Config schema:
    {
        "expected_type": "code_file",
        "content_regex": "def \\w+\\(",
        "threshold": 1.0
    }
"""

from __future__ import annotations

import re
from typing import Any

from eval_harness.core.contract import EvalContext, Grader
from eval_harness.core.types import GraderResult, GraderType, EvalTask, TrialResult


class ArtifactCheckGrader:
    """产物检查评分器"""

    name = "artifact_check"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        expected_type = config.get("expected_type")
        content_regex = config.get("content_regex")
        threshold = config.get("threshold", 1.0)

        # 从 outcome 或 spans 提取产物
        artifacts = self._extract_artifacts(trial, spans)

        if not artifacts:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.ARTIFACT,
                score=0.0,
                passed=False,
                explanation="No artifacts produced",
            )

        # 检查类型
        if expected_type:
            types = [a.get("type", "") for a in artifacts]
            if expected_type not in types:
                return GraderResult(
                    grader_name=self.name,
                    grader_type=GraderType.ARTIFACT,
                    score=0.0,
                    passed=False,
                    explanation=(
                        f"Expected type '{expected_type}', "
                        f"got {types}"
                    ),
                    details={"artifacts": artifacts},
                )

        # 检查内容
        if content_regex:
            contents = [a.get("content", "") for a in artifacts]
            content_match = any(re.search(content_regex, c) for c in contents)
            if not content_match:
                return GraderResult(
                    grader_name=self.name,
                    grader_type=GraderType.ARTIFACT,
                    score=0.3,
                    passed=0.3 >= threshold,
                    explanation=f"Content does not match pattern: {content_regex}",
                    details={"artifacts": artifacts},
                )

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.ARTIFACT,
            score=1.0,
            passed=True,
            explanation=f"Artifact check passed: {len(artifacts)} artifact(s)",
            details={"artifacts": artifacts},
        )

    def _extract_artifacts(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """从 outcome 或 spans 中提取产物"""
        # 优先从 outcome 获取
        artifacts = trial.outcome.get("artifacts", [])
        if artifacts:
            return artifacts

        # 从 spans 提取
        return [
            {
                "type": span.get("attributes", {}).get("agenthub.artifact_type", ""),
                "id": span.get("attributes", {}).get("agenthub.artifact_id", ""),
                "content": span.get("attributes", {}).get("agenthub.content", ""),
            }
            for span in spans
            if "artifact.create" in span.get("name", "")
        ]
