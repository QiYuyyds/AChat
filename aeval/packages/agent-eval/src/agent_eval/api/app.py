"""
FastAPI application factory for Aeval.

Creates a FastAPI app with all eval routes mounted.
Can be used standalone or mounted in an existing app.

Usage:
    # Standalone
    app = create_app(runner=my_runner)

    # Mounted in existing FastAPI app
    from fastapi import FastAPI
    app = FastAPI()
    eval_app = create_app(runner=my_runner)
    app.mount("/api/eval", eval_app)
"""

from __future__ import annotations

from agent_eval.api.routes import datasets, graders, metrics, runs, suites, tasks
from agent_eval.core.runner import EvalRunner
from fastapi import FastAPI

# Global runner reference (set by create_app)
_runner: EvalRunner | None = None


def _get_runner() -> EvalRunner | None:
    """Get the global EvalRunner instance"""
    return _runner


def set_runner(runner: EvalRunner | None) -> None:
    """Set the global EvalRunner instance.

    Used by the host app to inject the real runner during async startup
    (e.g. main.py lifespan → eval_integration.config.create_aeval_runner).
    """
    global _runner
    _runner = runner


def create_app(runner: EvalRunner | None = None) -> FastAPI:
    """
    Create a FastAPI app with Aeval routes.

    Args:
        runner: EvalRunner instance. If None, routes will return 503.

    Returns:
        FastAPI application
    """
    app = FastAPI(
        title="Aeval API",
        version="0.1.0",
        description="Agent Evaluation Framework API",
    )

    # Store runner in app state and global reference
    global _runner
    _runner = runner
    app.state.runner = runner

    # Include routers
    app.include_router(suites.router, prefix="/suites", tags=["suites"])
    app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
    app.include_router(runs.router, prefix="/runs", tags=["runs"])
    # compare 挂在 /compare (spec §REST API), 不带 /runs 前缀
    app.include_router(runs.compare_router, tags=["compare"])
    app.include_router(graders.router, prefix="/graders", tags=["graders"])
    # 数据集管理 (change ③: 数据集构建闭环 — CRUD/导入/挖掘/生成/质量/to-suite)
    app.include_router(datasets.router, prefix="/datasets", tags=["datasets"])
    # 批量评测 (change ④: 对已有输出直接批量打分 — POST /metrics/batch)
    app.include_router(metrics.router, prefix="/metrics", tags=["metrics"])

    @app.get("/health")
    async def health():
        # 动态读取全局 runner — host app 会在 startup 阶段注入真实 runner
        return {"status": "ok", "runner_configured": _get_runner() is not None}

    return app
