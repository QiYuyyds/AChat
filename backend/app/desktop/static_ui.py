"""Serve packaged static frontend from the local engine (D26).

Looks for UI assets in (first hit wins):
  1. ``ACHAT_UI_DIR`` env
  2. ``DesktopRuntime.ui_dir`` / config ``uiDir``
  3. ``{data_dir}/ui``
  4. sibling ``resources/ui`` next to the engine binary / package
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

logger = logging.getLogger(__name__)

_INDEX_NAMES = ("index.html",)


def resolve_ui_dir(explicit: str | Path | None = None, data_dir: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env = os.environ.get("ACHAT_UI_DIR", "").strip()
    if env:
        candidates.append(Path(env))
    if data_dir is not None:
        candidates.append(data_dir / "ui")
    # Dev / package layout relative to this file and CWD
    here = Path(__file__).resolve()
    candidates.extend(
        [
            here.parents[3] / "apps" / "desktop" / "resources" / "ui",
            here.parents[3] / "apps" / "desktop" / "ui",
            Path.cwd() / "resources" / "ui",
            Path.cwd() / "ui",
        ]
    )
    for c in candidates:
        try:
            p = c.expanduser().resolve()
        except Exception:
            continue
        if p.is_dir() and any((p / name).is_file() for name in _INDEX_NAMES):
            return p
    return None


def mount_static_ui(app: FastAPI, ui_dir: Path) -> None:
    """Mount static assets + SPA fallback for non-API routes."""
    ui_dir = ui_dir.resolve()
    index = ui_dir / "index.html"
    if not index.is_file():
        logger.warning("static UI dir has no index.html: %s", ui_dir)
        return

    # Immutable hashed assets (Next export / standalone client)
    next_static = ui_dir / "_next"
    if next_static.is_dir():
        app.mount("/_next", StaticFiles(directory=str(next_static)), name="desktop-next-static")

    assets = ui_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="desktop-assets")

    @app.get("/")
    async def desktop_index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> Response:
        # Never shadow API / health / OpenAPI
        if (
            full_path.startswith("api/")
            or full_path.startswith("health")
            or full_path.startswith("docs")
            or full_path.startswith("openapi")
            or full_path.startswith("deployments/")
            or full_path.startswith("redoc")
        ):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = (ui_dir / full_path).resolve()
        try:
            candidate.relative_to(ui_dir)
        except ValueError as e:
            raise HTTPException(status_code=404, detail="Not Found") from e

        if candidate.is_file():
            return FileResponse(candidate)

        # SPA / App Router client navigation
        return FileResponse(index)

    logger.info("Desktop static UI mounted from %s", ui_dir)
