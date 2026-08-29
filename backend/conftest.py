"""Root pytest configuration — framework-level pytest plugin registration.

eval_harness.metrics.pytest_plugin provides the Aeval metric fixtures
(answer_relevancy / faithfulness / context_recall / context_precision /
eval_metrics / eval_runner) and the --eval-suite / --eval-threshold gate
options. pytest 9 requires `pytest_plugins` in the top-level conftest, so
registration lives here rather than in tests/conftest.py (design D3: dev-time
import registration; entry-point packaging deferred to the standalone repo).

The sys.path append must happen in this module body — the plugin import
resolves right after it and needs backend/app importable.
"""

import sys
from pathlib import Path

# eval_harness uses top-level `from eval_harness...` imports (spec: no app.*
# reverse dependency), so backend/app must be a sys.path root. APPENDED (not
# prepended) so installed packages that collide with app/* subpackage names
# keep priority (same rationale as tests/conftest.py).
_APP_DIR = str(Path(__file__).resolve().parent / "app")
if _APP_DIR not in sys.path:
    sys.path.append(_APP_DIR)

pytest_plugins = ["eval_harness.metrics.pytest_plugin"]
