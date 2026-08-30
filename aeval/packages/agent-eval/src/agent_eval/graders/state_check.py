"""
State-check grader — environment state verification.

Validates the environment state after a trial:
- file_exists: file is present
- file_contains: file content contains a substring
- db_record: database record matches criteria
- custom: custom check function

Config schema:
    {
        "expectations": [
            {"type": "file_exists", "path": "output.py"},
            {"type": "file_contains", "path": "output.py", "value": "def main"},
            {"type": "db_record", "table": "users", "match": {"id": 1}},
        ],
        "threshold": 1.0
    }
"""

from __future__ import annotations

import re
from typing import Any

from agent_eval.core.contract import EvalContext, Grader
from agent_eval.core.types import GraderResult, GraderType, EvalTask, TrialResult


class StateCheckGrader:
    """环境状态检查评分器"""

    name = "state_check"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        expectations = config.get("expectations", [])
        threshold = config.get("threshold", 1.0)

        if not expectations:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.STATE,
                score=1.0,
                passed=True,
                explanation="No expectations configured, auto-pass",
            )

        passed_count = 0
        details: list[dict[str, Any]] = []

        for exp in expectations:
            exp_type = exp.get("type", "file_exists")
            ok = False

            if exp_type == "file_exists":
                files = trial.outcome.get("files", {})
                ok = exp["path"] in files

            elif exp_type == "file_contains":
                files = trial.outcome.get("files", {})
                content = files.get(exp["path"], "")
                ok = exp["value"] in content

            elif exp_type == "file_regex":
                files = trial.outcome.get("files", {})
                content = files.get(exp["path"], "")
                ok = bool(re.search(exp["value"], content))

            elif exp_type == "db_record":
                records = trial.outcome.get("db_records", [])
                match_criteria = exp.get("match", {})
                ok = any(
                    all(r.get(k) == v for k, v in match_criteria.items())
                    for r in records
                )

            elif exp_type == "no_conflict_markers":
                files = trial.outcome.get("files", {})
                content = files.get(exp["path"], "")
                ok = "<<<<<<<" not in content and ">>>>>>>" not in content

            else:
                ok = False

            if ok:
                passed_count += 1
            details.append({"expectation": exp, "passed": ok})

        total = len(expectations)
        score = passed_count / total if total > 0 else 1.0

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.STATE,
            score=score,
            passed=score >= threshold,
            explanation=f"{passed_count}/{total} state checks passed",
            details={"expectations": details},
        )
