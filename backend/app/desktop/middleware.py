"""Engine-token + Origin middleware for desktop local engine."""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Callable
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

ENGINE_TOKEN_HEADER = "x-engine-token"
PUBLIC_PATHS = {"/healthz", "/health"}


def _configured_token() -> str:
    return os.environ.get("ACHAT_ENGINE_TOKEN", "").strip()


def _allowed_origins() -> set[str]:
    raw = os.environ.get("ACHAT_ALLOWED_ORIGINS") or os.environ.get("CORS_ORIGINS") or ""
    return {o.strip() for o in raw.split(",") if o.strip()}


def _origin_of(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin:
        return origin
    referer = request.headers.get("referer")
    if not referer:
        return None
    try:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None
    return None


def _is_static_or_public(request: Request) -> bool:
    """Static UI GETs and health probes do not require engine token."""
    path = request.url.path
    if path in PUBLIC_PATHS:
        return True
    # SPA assets / index / _next / favicon etc.
    return request.method in ("GET", "HEAD") and not path.startswith("/api")


class EngineAuthMiddleware(BaseHTTPMiddleware):
    """Reject API requests missing engine token or using non-allowlisted Origin."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if request.method == "OPTIONS":
            return await call_next(request)

        token = _configured_token()
        origins = _allowed_origins()

        origin = _origin_of(request)
        # Same-origin local UI may omit Origin; when present must match allowlist
        # or be loopback (port list may lag shell handshake).
        if (
            origin
            and origins
            and origin not in origins
            and not _is_loopback_origin(origin)
        ):
            logger.warning("desktop engine rejected origin=%s path=%s", origin, path)
            return JSONResponse(status_code=403, content={"detail": "Origin not allowed"})

        if _is_static_or_public(request):
            return await call_next(request)

        if not token:
            return JSONResponse(
                status_code=503,
                content={"detail": "Engine token not configured"},
            )
        provided = request.headers.get(ENGINE_TOKEN_HEADER, "")
        if not provided:
            provided = request.query_params.get("engineToken", "") or request.query_params.get(
                "engine_token", ""
            )
        if not provided or not hmac.compare_digest(provided, token):
            return JSONResponse(status_code=401, content={"detail": "Invalid engine token"})

        return await call_next(request)


def _is_loopback_origin(origin: str) -> bool:
    try:
        p = urlparse(origin)
        host = (p.hostname or "").lower()
        return host in ("127.0.0.1", "localhost", "::1")
    except Exception:
        return False
