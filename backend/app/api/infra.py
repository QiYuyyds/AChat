"""Infra config API (rag-infra-config) — runtime access to RAG infrastructure.

- ``GET  /api/infra/config``  stored connection overrides (password masked)
- ``PUT  /api/infra/config``  write — desktop mode only (web 运维走 env / docker-compose)
- ``POST /api/infra/config/test``  connect with un-persisted params (desktop mode only —
  arbitrary-host probes from a multi-user web deployment would be an SSRF vector)
- ``GET  /api/infra/status``  per-service connected/degraded/disabled + config source

生效语义：基础设施在启动期装配，保存后重启生效（无热重连）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.auth.dependencies import get_current_user
from app.config import get_settings
from app.db.models import User
from app.schemas import InfraConfigTestRequest, InfraConfigUpdate
from app.services.global_settings_service import (
    get_global_settings,
    update_global_settings,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Secrets-form mask: GET echoes this when a password is stored; PUT/test treat
# the mask echo (or "") as "unchanged".
PASSWORD_MASK = "********"

_WEB_MODE_MESSAGE = (
    "基础设施连接配置仅桌面模式可修改。web 自托管部署请通过 env / "
    "docker-compose 运维（MILVUS_HOST / NEO4J_URI 等）。"
)


def _is_desktop_mode() -> bool:
    from app.auth.desktop import is_desktop_mode

    return is_desktop_mode()


def _mask_password(stored: str | None) -> str:
    return PASSWORD_MASK if stored else ""


def _serialize_stored(gs) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Stored (db) overrides — the form edits these; empty = 跟随 env."""
    return {
        "milvusHost": gs.milvus_host,
        "milvusPort": gs.milvus_port,
        "neo4jUri": gs.neo4j_uri,
        "neo4jUser": gs.neo4j_user,
        "neo4jPassword": _mask_password(gs.neo4j_password),
        "enableGraph": gs.enable_graph,
    }


async def _resolved_sources() -> dict[str, str]:
    from app.infra.factory import resolve_infra_config

    cfg = await resolve_infra_config(get_settings())
    return {
        "milvus": cfg.milvus_source,
        "neo4j": cfg.neo4j_source,
        "graph": cfg.graph_source,
    }


@router.get("/infra/config")
async def get_infra_config(user: User = Depends(get_current_user)) -> JSONResponse:
    """Stored connection overrides (masked) + resolved config sources."""
    gs = await get_global_settings()
    return JSONResponse({
        "config": _serialize_stored(gs),
        "sources": await _resolved_sources(),
        "desktopMode": _is_desktop_mode(),
    })


@router.put("/infra/config")
async def put_infra_config(
    request: dict[str, Any],
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Write connection overrides. Desktop mode only — web 模式 403 并附说明."""
    if not _is_desktop_mode():
        return JSONResponse({"error": _WEB_MODE_MESSAGE}, status_code=403)

    try:
        parsed = InfraConfigUpdate.model_validate(request)
    except ValidationError as exc:
        return JSONResponse({"error": "Invalid body", "issues": exc.errors()}, status_code=400)

    provided = parsed.model_fields_set
    sent = parsed.model_dump(by_alias=False)

    gs = await get_global_settings()
    patch: dict[str, Any] = {}
    for field in ("milvus_host", "milvus_port", "neo4j_uri", "neo4j_user", "enable_graph"):
        if field in provided:
            patch[field] = sent[field]
    # Secrets-form convention: mask echo or "" → 未修改，keep stored password.
    if "neo4j_password" in provided:
        pwd = sent["neo4j_password"]
        if pwd and pwd != PASSWORD_MASK:
            patch["neo4j_password"] = pwd

    if patch:
        gs = await update_global_settings(patch)

    return JSONResponse({
        "config": _serialize_stored(gs),
        "restartRequired": True,
        "message": "已保存，重启后生效",
    })


# ─── Connection test (un-persisted params) ──────────────────────────────


def _classify_error(exc: Exception, kind_hints: dict[type[Exception], str]) -> str:
    """Map an exception to network / auth / protocol (best-effort keyword match)."""
    for exc_type, kind in kind_hints.items():
        if isinstance(exc, exc_type):
            return kind
    text = str(exc).lower()
    if any(k in text for k in ("auth", "unauthenticated", "unauthorized", "forbidden",
                               "credential", "permission", "401", "403")):
        return "auth"
    if any(k in text for k in ("timeout", "timed out", "connection", "refused",
                               "unreachable", "resolve", "dns", "reset")):
        return "network"
    return "protocol"


async def _test_milvus(host: str, port: int | None) -> dict[str, Any]:
    from app.infra.factory import build_milvus_client

    uri = f"http://{host}:{port or 19530}"
    started = time.perf_counter()
    client = None
    try:
        client = build_milvus_client(uri, timeout=5)
        await asyncio.to_thread(client.list_collections)
        latency_ms = (time.perf_counter() - started) * 1000
        return {"tested": True, "ok": True, "latencyMs": round(latency_ms, 1)}
    except Exception as e:
        return {
            "tested": True,
            "ok": False,
            "errorKind": _classify_error(e, {}),
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


async def _test_neo4j(uri: str, usr: str, password: str) -> dict[str, Any]:
    from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired

    from app.infra.factory import build_neo4j_driver

    kind_hints: dict[type[Exception], str] = {
        AuthError: "auth",
        ServiceUnavailable: "network",
        SessionExpired: "network",
    }
    started = time.perf_counter()
    driver = None
    try:
        driver = build_neo4j_driver(uri, usr, password)
        await asyncio.wait_for(driver.verify_connectivity(), timeout=10)
        latency_ms = (time.perf_counter() - started) * 1000
        return {"tested": True, "ok": True, "latencyMs": round(latency_ms, 1)}
    except Exception as e:
        return {
            "tested": True,
            "ok": False,
            "errorKind": _classify_error(e, kind_hints),
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        if driver is not None:
            try:
                await driver.close()
            except Exception:
                pass


@router.post("/infra/config/test")
async def test_infra_config(
    body: dict[str, Any],
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Connect with the form's current values (un-persisted). MUST NOT 写库.

    Desktop mode only: the endpoint probes arbitrary hosts, which must not be
    reachable as an SSRF primitive from a multi-user web deployment.
    """
    if not _is_desktop_mode():
        return JSONResponse({"error": _WEB_MODE_MESSAGE}, status_code=403)

    try:
        parsed = InfraConfigTestRequest.model_validate(body)
    except ValidationError as exc:
        return JSONResponse({"error": "Invalid body", "issues": exc.errors()}, status_code=400)

    sent = parsed.model_dump(by_alias=False)

    milvus_result: dict[str, Any] = {"tested": False}
    if sent["milvus_host"]:
        milvus_result = await _test_milvus(sent["milvus_host"], sent["milvus_port"])

    neo4j_result: dict[str, Any] = {"tested": False}
    if sent["neo4j_uri"]:
        password = sent["neo4j_password"]
        if password == PASSWORD_MASK:
            # Mask echo → test with the stored credential
            gs = await get_global_settings()
            password = gs.neo4j_password or ""
        neo4j_result = await _test_neo4j(sent["neo4j_uri"], sent["neo4j_user"] or "", password or "")

    return JSONResponse({"milvus": milvus_result, "neo4j": neo4j_result})


# ─── Runtime status ─────────────────────────────────────────────────────


@router.get("/infra/status")
async def get_infra_status(user: User = Depends(get_current_user)) -> JSONResponse:
    """Per-service connected/degraded/disabled + config source + 失败原因.

    Reflects the startup-time factory snapshot (重启生效语义 — no hot recheck).
    """
    from app.infra.factory import get_infrastructure
    from app.infra.status import InfrastructureStatus

    infra = get_infrastructure()
    if infra is None:
        # Factory never ran (or failed): report the neutral snapshot honestly.
        states = InfrastructureStatus().service_states()
        states["postgres"]["detail"] = states["postgres"]["detail"] or "基础设施未装配"
        return JSONResponse({"services": states, "infraAvailable": False})

    return JSONResponse({
        "services": infra.status.service_states(),
        "infraAvailable": True,
    })
