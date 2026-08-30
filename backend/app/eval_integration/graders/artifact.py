"""AChatArtifactGrader — 产物存在/类型校验 (任务 2.3)。

信号来源: trial.outcome["artifacts"] (runner 经 GET /api/artifacts
?conversation_id= 收集) 优先, 退回 ``artifact.create`` spans 的
``agenthub.artifact_type`` 属性。
"""

from __future__ import annotations

from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import EvalTask, GraderResult, GraderType, TrialResult


class AChatArtifactGrader:
    """产物检查评分器 — artifact 存在且类型符合预期。"""

    name = "achat_artifact"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        expected_type = config.get("expected_type")

        artifacts = self._extract_artifacts(trial, spans)

        if not artifacts:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.ARTIFACT,
                score=0.0,
                passed=False,
                explanation="No artifact created",
            )

        types = [str(a.get("type", "")) for a in artifacts]
        if expected_type:
            expected = (
                [expected_type] if isinstance(expected_type, str) else list(expected_type)
            )
            if not any(t in expected for t in types):
                return GraderResult(
                    grader_name=self.name,
                    grader_type=GraderType.ARTIFACT,
                    score=0.0,
                    passed=False,
                    explanation=f"Expected type {expected}, got {types}",
                    details={"artifacts": artifacts},
                )

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.ARTIFACT,
            score=1.0,
            passed=True,
            explanation=f"Artifact check passed: {len(artifacts)} artifact(s)",
            details={"artifacts": artifacts, "types": types},
        )

    def _extract_artifacts(
        self, trial: TrialResult, spans: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """优先 outcome.artifacts (API 收集), 退回 artifact.create spans。"""
        artifacts = trial.outcome.get("artifacts") or []
        if artifacts:
            return [
                a if isinstance(a, dict) else {"id": str(a), "type": ""}
                for a in artifacts
            ]
        return [
            {
                "id": (s.get("attributes") or {}).get("agenthub.artifact_id", ""),
                "type": (s.get("attributes") or {}).get("agenthub.artifact_type", ""),
            }
            for s in spans
            if "artifact.create" in str(s.get("name", ""))
        ]
