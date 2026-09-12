"""Root pytest configuration — framework-level pytest plugin registration.

agent_eval.metrics.pytest_plugin provides the Aeval metric fixtures
(answer_relevancy / faithfulness / context_recall / context_precision /
eval_metrics / eval_runner) and the --eval-suite / --eval-threshold gate
options. pytest 9 requires `pytest_plugins` in the top-level conftest, so
registration lives here rather than in tests/conftest.py (design D3: dev-time
import registration; entry-point packaging deferred to the standalone repo).

agent_eval is consumed as an installed (editable) package:
    pip install -e ../aeval/packages/agent-eval[api,cli]
"""

pytest_plugins = ["agent_eval.metrics.pytest_plugin"]
