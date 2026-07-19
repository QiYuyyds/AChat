"""Authenticated HTTPS client from local engine → official cloud API.

Desktop online persistence must not open direct PG/Milvus connections.
The user access token is provided via session handoff (task 8.x), never logged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class CloudSession:
    """In-memory user session for cloud API calls (not persisted to logs)."""

    access_token: str | None = None
    user_id: str | None = None

    def clear(self) -> None:
        self.access_token = None
        self.user_id = None

    @property
    def is_authenticated(self) -> bool:
        return bool(self.access_token)


_session = CloudSession()


def get_cloud_session() -> CloudSession:
    return _session


def set_cloud_access_token(token: str | None, user_id: str | None = None) -> None:
    """Handoff from desktop frontend after login. Token is not logged."""
    _session.access_token = token
    if user_id is not None:
        _session.user_id = user_id
    if token:
        logger.info("cloud session token set user_id=%s", user_id or "?")
    else:
        logger.info("cloud session token cleared")


def clear_cloud_session() -> None:
    _session.clear()
    logger.info("cloud session cleared")


def official_api_base() -> str:
    return (
        os.environ.get("ACHAT_OFFICIAL_API_URL")
        or os.environ.get("OFFICIAL_API_URL")
        or ""
    ).rstrip("/")


@dataclass
class CloudApiClient:
    base_url: str = field(default_factory=official_api_base)
    timeout: float = 30.0

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        token = _session.access_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        if not self.base_url:
            raise RuntimeError("official API URL not configured for desktop engine")
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.request(
                method,
                url,
                headers=self._headers(),
                json=json,
                params=params,
            )
            return resp

    async def get_json(self, path: str, **kwargs: Any) -> Any:
        resp = await self.request("GET", path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def post_json(self, path: str, body: Any, **kwargs: Any) -> Any:
        resp = await self.request("POST", path, json=body, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def fetch_user_settings(self) -> dict[str, Any]:
        """Pull account settings including provider keys over HTTPS."""
        return await self.get_json("/api/settings")

    async def post_message(self, conversation_id: str, body: dict[str, Any]) -> Any:
        """Durable message write that must NOT start a cloud Agent run.

        Prefer /api/sync/messages (UPSERT). Falls back to conversation message
        POST only if sync endpoint is missing (older cloud deployments).
        """
        payload = dict(body)
        payload.setdefault("conversationId", conversation_id)
        try:
            return await self.post_json("/api/sync/messages", {"messages": [payload]})
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                # Legacy fallback — may start a cloud run; only for old servers.
                return await self.post_json(
                    f"/api/conversations/{conversation_id}/messages",
                    body,
                )
            raise


def get_cloud_client() -> CloudApiClient:
    return CloudApiClient()
