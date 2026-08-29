"""
REST API for the Aeval evaluation framework.

Provides FastAPI routes for managing suites, runs, and viewing results.

Usage:
    from eval_harness.api import create_app

    app = create_app(runner=my_eval_runner)
"""

from eval_harness.api.app import create_app

__all__ = ["create_app"]
