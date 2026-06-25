"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import apply_env_overrides, get_settings
from app.db.engine import close_db, init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager."""
    # Startup
    # bridge backend/.env API keys into os.environ (env-fallback layer)
    apply_env_overrides()
    # import wires the real AgentRunner into runner_registry at module load
    import app.services.agent_runner  # noqa: F401

    await init_db()
    yield
    # Shutdown
    await close_db()


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="AgentHub Backend",
        description="Multi-Agent Collaboration Workspace API",
        version="0.1.0",
        lifespan=lifespan,
        debug=settings.debug,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    from app.api import (
        agents,
        artifacts,
        attachments,
        conversations,
        deployments,
        fs,
        messages,
        pending,
        runs_misc,
        stream,
    )
    from app.api import (
        settings as settings_router,
    )
    from app.api.mobile import routes as mobile_routes

    app.include_router(conversations.router, prefix="/api", tags=["conversations"])
    app.include_router(messages.router, prefix="/api", tags=["messages"])
    app.include_router(agents.router, prefix="/api", tags=["agents"])
    app.include_router(artifacts.router, prefix="/api", tags=["artifacts"])
    app.include_router(attachments.router, prefix="/api", tags=["attachments"])
    app.include_router(fs.router, prefix="/api", tags=["fs"])
    # pending router decorators already carry the /api prefix, so no prefix here
    app.include_router(pending.router, tags=["pending"])
    app.include_router(settings_router.router, prefix="/api", tags=["settings"])
    app.include_router(runs_misc.router, prefix="/api", tags=["runs-misc"])
    app.include_router(mobile_routes.router, prefix="/api", tags=["mobile"])
    app.include_router(stream.router, prefix="/api", tags=["stream"])
    # deployment preview assets served at root /deployments/{id}/... (no /api prefix);
    # the previewPath the agent emits is /deployments/{id}. Frontend proxies via rewrite.
    app.include_router(deployments.router, tags=["deployments"])

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
