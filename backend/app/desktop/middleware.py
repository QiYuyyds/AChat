"""Engine-token + official Origin middleware for desktop local engine."""

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
# Health can be probed with or without token; shell may send token either way.
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


class EngineAuthMiddleware(BaseHTTPMiddleware):
    """Reject requests missing engine token or using non-allowlisted Origin."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        # Always allow CORS preflight through; browser will still fail without token on real call.
        if request.method == "OPTIONS":
            return await call_next(request)

        token = _configured_token()
        origins = _allowed_origins()

        # Origin allowlist when Origin/Referer present
        origin = _origin_of(request)
        if origin and origins and origin not in origins:
            logger.warning("desktop engine rejected origin=%s path=%s", origin, path)
            return JSONResponse(status_code=403, content={"detail": "Origin not allowed"})

        # Token gate (health optional)
        if path not in PUBLIC_PATHS:
            if not token:
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Engine token not configured"},
                )
            provided = request.headers.get(ENGINE_TOKEN_HEADER, "")
            # EventSource cannot set headers; allow token via query for /api/stream.
            if not provided:
                provided = request.query_params.get("engineToken", "") or request.query_params.get(
                    "engine_token", ""
                )
            if not provided or not hmac.compare_digest(provided, token):
                return JSONResponse(status_code=401, content={"detail": "Invalid engine token"})

        return await call_next(request)
