"""
Standalone API deployment for Aeval.

Exposes the full eval route set under the `/v1` prefix as a self-contained
FastAPI application, with a version header on every response and a `/v1/meta`
capabilities endpoint. The existing `create_app()` is reused unchanged —
host-app mounts (e.g. AChat's `/api/eval`) keep their behaviour.

Usage:
    # In-process (tests / mounting)
    app = create_standalone_app(runner=my_runner)

    # Service
    eval-suite serve --port 8900
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _metadata_version

from fastapi import FastAPI

from agent_eval.api.app import create_app
from agent_eval.core.runner import EvalRunner
from agent_eval.graders import get_grader_catalog

PACKAGE_NAME = "agent-eval"

# /v1 下暴露的路由组 (与 create_app 内的挂载一致; /health 为探活)
CAPABILITY_ENDPOINTS = [
    "/suites",
    "/tasks",
    "/runs",
    "/compare",
    "/graders",
    "/datasets",
    "/metrics",
    "/health",
]


def package_version() -> str:
    """包版本: 优先取已安装元数据, 未安装 (源码直跑) 时回退模块常量。"""
    try:
        return _metadata_version(PACKAGE_NAME)
    except PackageNotFoundError:
        from agent_eval import __version__

        return __version__


def _meta_payload() -> dict:
    """GET /v1/meta 响应体: 版本 + 能力清单。"""
    return {
        "name": "Aeval",
        "package": PACKAGE_NAME,
        "version": package_version(),
        "api_prefix": "/v1",
        "endpoints": CAPABILITY_ENDPOINTS,
        "capabilities": {
            "graders": [g["name"] for g in get_grader_catalog()],
            "storage": ["memory", "sqlite"],
            "trace_providers": ["phoenix (optional, lazily imported)"],
            "sse": True,
            "datasets": True,
            "metrics": True,
        },
    }


def create_standalone_app(runner: EvalRunner | None = None) -> FastAPI:
    """
    Create the standalone Aeval API app (all routes under /v1).

    Args:
        runner: EvalRunner instance. If None, run routes return 503 while
            registry/storage-backed endpoints stay usable.

    Returns:
        FastAPI application serving /v1/* with X-Aeval-Version headers.
    """
    version = package_version()
    app = FastAPI(
        title="Aeval API",
        version=version,
        description="Standalone Agent Evaluation Framework API (/v1)",
    )

    @app.get("/v1/meta")
    async def meta() -> dict:
        # 注册在 mount 之前: Starlette 按注册顺序匹配, /v1/meta 优先于子应用
        return _meta_payload()

    @app.middleware("http")
    async def add_version_header(request, call_next):
        response = await call_next(request)
        response.headers["X-Aeval-Version"] = version
        return response

    # 复用既有 create_app 的全部路由集合, 整体挂 /v1 (既有 create_app 零改动,
    # 寄宿部署的 /api/eval 挂载不受影响 — 设计 D5)
    app.mount("/v1", create_app(runner=runner))

    return app
