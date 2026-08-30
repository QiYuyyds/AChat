"""
Code-based grader — deterministic scoring via string/regex matching.

Supports:
- contains: substring match
- not_contains: substring absence
- regex: regular expression match
- exact: exact string equality

Config schema:
    {
        "checks": [
            {"type": "contains", "value": "def hello", "target": "transcript"},
            {"type": "regex", "value": "class \\w+:", "target": "outcome"},
        ],
        "threshold": 1.0  # fraction of checks that must pass
    }

Target can be: "transcript" | "outcome" | "spans"
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_eval.core.contract import EvalContext, Grader
from agent_eval.core.types import GraderResult, GraderType, EvalTask, TrialResult


class CodeBasedGrader:
    """通用确定性评分器"""

    name = "code_based"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        checks = config.get("checks", [])
        threshold = config.get("threshold", 1.0)

        if not checks:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.CODE,
                score=1.0,
                passed=True,
                explanation="No checks configured, auto-pass",
            )

        passed_count = 0
        details: list[dict[str, Any]] = []

        for check in checks:
            check_type = check.get("type", "contains")
            target = check.get("target", "transcript")
            value = check.get("value", "")

            # 获取目标文本
            if target == "transcript":
                text = json.dumps(trial.transcript, ensure_ascii=False)
            elif target == "outcome":
                text = json.dumps(trial.outcome, ensure_ascii=False)
            elif target == "spans":
                text = json.dumps(spans, ensure_ascii=False)
            else:
                text = ""

            # 执行检查
            if check_type == "contains":
                ok = value in text
            elif check_type == "not_contains":
                ok = value not in text
            elif check_type == "regex":
                ok = bool(re.search(value, text))
            elif check_type == "exact":
                ok = value == text
            else:
                ok = False

            if ok:
                passed_count += 1
            details.append({"check": check, "passed": ok})

        total = len(checks)
        score = passed_count / total if total > 0 else 1.0

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CODE,
            score=score,
            passed=score >= threshold,
            explanation=f"{passed_count}/{total} checks passed",
            details={"checks": details},
        )
