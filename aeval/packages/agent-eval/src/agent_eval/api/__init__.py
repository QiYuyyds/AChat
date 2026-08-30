"""
REST API for the Aeval evaluation framework.

Provides FastAPI routes for managing suites, runs, and viewing results.

Usage:
    from agent_eval.api import create_app

    app = create_app(runner=my_eval_runner)
"""

from agent_eval.api.app import create_app

__all__ = ["create_app"]
