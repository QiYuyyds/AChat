#!/usr/bin/env bash
# Coverage check for eval_harness core modules (task 6.4, design doc §17.11).
#
# Scope: core/metrics + core/suite + graders/* — the pure-unit-testable parts.
# Target: >= 90% line coverage from the dedicated unit test files.
#
# Usage (from backend/):
#   bash scripts/check_eval_coverage.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# Prefer the project venv interpreter when present
if [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python"
fi

"$PY" -m pytest \
  tests/test_eval_harness_metrics.py \
  tests/test_eval_harness_suite.py \
  tests/test_eval_harness_graders.py \
  tests/test_eval_harness_builtin_graders.py \
  --cov=eval_harness.core.metrics \
  --cov=eval_harness.core.suite \
  --cov=eval_harness.graders \
  --cov-report=term-missing \
  --cov-fail-under=90
