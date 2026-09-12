#!/usr/bin/env bash
# Coverage check for agent_eval core modules (task 6.4, design doc §17.11).
#
# Scope: core/metrics + core/suite + graders/* — the pure-unit-testable parts.
# Target: >= 90% line coverage from the dedicated unit test files.
#
# The framework tests now live in the agent-eval package (aeval/). This script
# runs them from the backend venv (agent_eval must be installed editable).
#
# Usage (from backend/):
#   bash scripts/check_eval_coverage.sh
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Prefer the backend venv interpreter when present (contains agent_eval)
if [ -x "$REPO_ROOT/backend/.venv/Scripts/python.exe" ]; then
  PY="$REPO_ROOT/backend/.venv/Scripts/python.exe"
elif [ -x "$REPO_ROOT/backend/.venv/bin/python" ]; then
  PY="$REPO_ROOT/backend/.venv/bin/python"
else
  PY="python"
fi

cd "$REPO_ROOT/aeval/packages/agent-eval"

"$PY" -m pytest \
  tests/test_metrics.py \
  tests/test_suite.py \
  tests/test_graders.py \
  tests/test_builtin_graders.py \
  tests/test_eval_metric_pipeline.py \
  tests/test_eval_metrics_module.py \
  --cov=agent_eval.core.metrics \
  --cov=agent_eval.core.suite \
  --cov=agent_eval.graders \
  --cov-report=term-missing \
  --cov-fail-under=90
