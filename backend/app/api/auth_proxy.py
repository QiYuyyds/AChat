"""桌面认证代理：把 /api/auth/* 透明转发到云端部署（design D6）。

仅桌面模式挂载（web 模式挂载真实 auth router，行为不变）。前端认证流程
（AuthGate / auth-store / HttpOnly cookie 语义）不因桌面形态改变：

- httpx 转发保留 method / body / cookie；Set-Cookie 去掉 Domain 属性后原样
  落在本地 origin（浏览器拒绝为其他域设置的 Domain）
- 登录 / 注册 / VIP 登录 2xx → 写 cloud_session.json 缓存标记（强制登录准入）
- 登出：云端可达则转发；云端不可达也保证清除本地标记与 cookie（离线登出可用）
- 云端不可达（未配置 / 网络失败）：503 明确报错，前端登录页呈现重试入口，
  MUST NOT 静默失败或绕过登录
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.auth.desktop import clear_cloud_session, write_cloud_session
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter()

# hop-by-hop / 由响应对象自行管理的头，不透传
_DROP_REQUEST_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
_DROP_RESPONSE_HEADERS = {"content-length", "content-encoding", "transfer-encoding", "connection"}

# 登录成功（写缓存标记）的端点
_SESSION_ESTABLISHING_PATHS = {"login", "register", "vip-login"}
_DOMAIN_ATTR_RE = re.compile(r";\s*Domain=[^;]*", re.IGNORECASE)

_test_transport: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """测试注入点：替换 httpx transport（MockTransport 模拟云端部署）。"""
    global _test_transport
    _test_transport = transport


def _cloud_base_url() -> str | None:
    base = get_settings().cloud_api_url
    return base.rstrip("/") if base else None


async def _get_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(15.0, connect=5.0),
        follow_redirects=False,
        transport=_test_transport,
    )


@router.api_route(
    "/auth/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy_auth(request: Request, path: str) -> Response:
    body = await request.body()
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _DROP_REQUEST_HEADERS
    }

    base = _cloud_base_url()
    if not base:
        if path == "logout":
            # 云端未配置也保证登出语义：本地状态必须清理成功
            clear_cloud_session()
            return _cleared_cookie_response()
        return _cloud_unreachable("云端服务未配置")

    try:
        async with await _get_client() as client:
            resp = await client.request(
                request.method,
                f"{base}/api/auth/{path}",
                params=dict(request.query_params),
                content=body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        logger.warning("cloud auth proxy request failed: %s %s (%s)", request.method, path, exc)
        if path == "logout":
            # 离线登出：本地状态必须清理成功
            clear_cloud_session()
            return _cleared_cookie_response()
        return _cloud_unreachable("云端不可达，请检查网络后重试")

    response = _passthrough_response(resp)

    if path in _SESSION_ESTABLISHING_PATHS and 200 <= resp.status_code < 300:
        try:
            payload: Any = resp.json()
            user = payload.get("user")
            if isinstance(user, dict):
                write_cloud_session(user)
        except ValueError:
            logger.warning("cloud auth response was not JSON; session marker skipped")

    if path == "logout" and resp.status_code < 500:
        clear_cloud_session()

    return response


def _passthrough_response(resp: httpx.Response) -> Response:
    raw_headers: list[tuple[bytes, bytes]] = []
    for key, value in resp.headers.multi_items():
        if key.lower() in _DROP_RESPONSE_HEADERS:
            continue
        if key.lower() == "set-cookie":
            value = _DOMAIN_ATTR_RE.sub("", value)
        raw_headers.append((key.encode("latin-1"), value.encode("latin-1")))
    # 直接写 raw_headers：保留多个 Set-Cookie（dict 形式会互相覆盖）
    response = Response(content=resp.content, status_code=resp.status_code)
    response.raw_headers = raw_headers
    return response


def _cleared_cookie_response() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(key="agenthub_token", path="/")
    return response


def _cloud_unreachable(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=503)
