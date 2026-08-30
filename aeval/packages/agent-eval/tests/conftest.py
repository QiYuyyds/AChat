"""Aeval test-suite configuration.

Register the built-in metric pytest plugin (fixtures answer_relevancy /
faithfulness / context_recall / context_precision / eval_metrics /
eval_runner and the --eval-suite / --eval-threshold gate options). pytest 9
requires `pytest_plugins` in a top-level conftest, so registration lives here.
"""

pytest_plugins = ["agent_eval.metrics.pytest_plugin"]
