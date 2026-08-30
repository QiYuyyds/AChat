"""AChat HTTP API 客户端。

覆盖 AChatAgentRunner 所需的真实端点 (对照表见设计文档 §14.1, 已核对):

    POST   /api/conversations                          创建会话 (sandbox workspace 服务端默认)
    POST   /api/conversations/{id}/messages            发消息 → {messageId, runIds[]}
    GET    /api/conversations/{id}/messages            全量消息 (transcript)
    GET    /api/conversations/{id}/fs/listdir?path=    目录清单
    GET    /api/conversations/{id}/fs/read?path=       读文件
    POST   /api/conversations/{id}/fs/write            写种子文件
    GET    /api/artifacts?conversation_id=             产物清单 (change ② 补的过滤参数)
    DELETE /api/conversations/{id}                     清理 trial 会话

认证: Bearer JWT。token 由注入的 ``token_provider`` 异步解析 (可缓存)。

错误映射: httpx 传输层错误 (超时/网络) → TransientError (框架指数退避重试);
非 2xx 响应 → AChatApiError (不重试)。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from agent_eval.core.contract import TransientError

from app.eval_integration.errors import AChatApiError

logger = logging.getLogger(__name__)

# 传输层错误 → 瞬态 (可重试)。read 超时不在此列 — 完成等待由 runner 自管理。
# httpx.TimeoutException 继承 TransportError, 但连接超时才是典型瞬态;
# 为简化语义: 所有 TransportError 都按瞬态处理 (评测语境下重试无副作用,
# 每次 trial 都是新 conversation)。
_TERMINAL_MESSAGE_STATUSES = {"complete", "error", "aborted", "interrupted"}


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, httpx.TransportError)


class AChatApiClient:
    """AChat HTTP API 薄封装。

    Args:
        base_url: AChat 后端地址 (如 ``http://127.0.0.1:8000``)
        token_provider: 返回 Bearer JWT 的异步可调用 (每次请求前调用,
            实现方可内部缓存)
        client: 注入预构建的 httpx.AsyncClient (测试用 MockTransport);
            缺省时内部创建
    """

    def __init__(
        self,
        base_url: str,
        token_provider: Callable[[], Awaitable[str]],
        client: httpx.AsyncClient | None = None,
        request_timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        self._request_timeout = request_timeout
        self._client = client
        self._owns_client = client is None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._request_timeout, connect=10.0)
            )
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Request core ─────────────────────────────────────────────────────

    async def _headers(self) -> dict[str, str]:
        token = await self._token_provider()
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        client = await self._ensure_client()
        headers = await self._headers()
        url = f"{self.base_url}{path}"
        try:
            resp = await client.request(
                method, url, params=params, json=json, headers=headers
            )
        except httpx.TransportError as e:
            raise TransientError(f"AChat API transport error ({method} {path}): {e}") from e

        if resp.status_code >= 400:
            body = (resp.text or "")[:500]
            raise AChatApiError(
                f"AChat API {method} {path} -> {resp.status_code}: {body}",
                status_code=resp.status_code,
            )
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ── Conversations ────────────────────────────────────────────────────

    async def create_conversation(
        self,
        *,
        title: str,
        agent_id: str,
        fs_write_approval_mode: str = "auto",
        mode: str = "single",
        agent_ids: list[str] | None = None,
        dispatch_mode: str | None = None,
    ) -> str:
        """创建 sandbox 会话 (不传 boundPath → 服务端默认全新 sandbox workspace)。

        fs_write_approval_mode 默认 auto: 评测会话无人值守, review 审批会让
        fs_write 永久挂起 run (等待人工决定); 仅作用于 eval 会话。

        mode / agent_ids / dispatch_mode 对应 CreateConversationRequest 原生
        字段 (single|group / ≥1 个 agent / solo|orchestrated); ``agent_ids``
        提供时覆盖 ``agentIds`` 字段 (group 会话传多个 agent 用), 缺省时回退
        单 agent 快捷形态 ``[agent_id]``。dispatch_mode 仅在提供时下发。
        """
        payload: dict[str, Any] = {
            "title": title,
            "mode": mode,
            "agentIds": agent_ids if agent_ids is not None else [agent_id],
        }
        if dispatch_mode is not None:
            payload["dispatchMode"] = dispatch_mode
        data = await self._request("POST", "/api/conversations", json=payload)
        conv = (data or {}).get("conversation") or {}
        conv_id = conv.get("id")
        if not conv_id:
            raise AChatApiError("create_conversation: response missing conversation.id")
        if fs_write_approval_mode != "review":
            await self._request(
                "PATCH",
                f"/api/conversations/{conv_id}",
                json={"fsWriteApprovalMode": fs_write_approval_mode},
            )
        return conv_id

    async def delete_conversation(self, conversation_id: str) -> None:
        """删除 trial 会话 (workspace/artifacts 级联清理; 幂等容错)。"""
        try:
            await self._request("DELETE", f"/api/conversations/{conversation_id}")
        except AChatApiError as e:
            if e.status_code == 404:
                return
            raise

    async def send_message(self, conversation_id: str, content: str) -> dict[str, Any]:
        """发送任务 prompt, 返回 {message_id, run_ids}。"""
        data = await self._request(
            "POST",
            f"/api/conversations/{conversation_id}/messages",
            json={"content": content},
        )
        data = data or {}
        run_ids = data.get("runIds") or []
        if not run_ids:
            raise AChatApiError(
                f"send_message: no runIds returned for conversation {conversation_id}"
            )
        return {"message_id": data.get("messageId"), "run_ids": [str(r) for r in run_ids]}

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        """全量消息 (camelCase wire 格式, 按 runId 过滤由调用方决定)。"""
        data = await self._request(
            "GET", f"/api/conversations/{conversation_id}/messages"
        )
        return (data or {}).get("messages") or []

    # ── Workspace fs ─────────────────────────────────────────────────────

    async def fs_write(self, conversation_id: str, path: str, content: str) -> None:
        await self._request(
            "POST",
            f"/api/conversations/{conversation_id}/fs/write",
            json={"path": path, "content": content},
        )

    async def fs_read(self, conversation_id: str, path: str) -> dict[str, Any]:
        return await self._request(
            "GET",
            f"/api/conversations/{conversation_id}/fs/read",
            params={"path": path},
        )

    async def fs_listdir(self, conversation_id: str, path: str = "") -> list[dict[str, Any]]:
        """目录清单 → [{name, isDirectory, size?}] (fs 服务已做路径沙箱)。"""
        data = await self._request(
            "GET",
            f"/api/conversations/{conversation_id}/fs/listdir",
            params={"path": path} if path else None,
        )
        return (data or {}).get("entries") or []

    # ── Artifacts ────────────────────────────────────────────────────────

    async def list_artifacts(self, conversation_id: str) -> list[dict[str, Any]]:
        """按会话过滤产物清单 (GET /api/artifacts?conversation_id=...)。"""
        data = await self._request(
            "GET", "/api/artifacts", params={"conversation_id": conversation_id}
        )
        return (data or {}).get("artifacts") or []

    # ── Transcript / completion helpers ──────────────────────────────────

    async def run_message_statuses(
        self, conversation_id: str, run_ids: list[str]
    ) -> dict[str, str]:
        """从消息列表推导各 run 的当前状态 (HTTP 降级完成检测)。

        Returns:
            {run_id: message_status}; run 尚未产生任何消息时不出现在结果中。
        """
        wanted = set(run_ids)
        statuses: dict[str, str] = {}
        for msg in await self.list_messages(conversation_id):
            rid = msg.get("runId")
            if rid in wanted:
                status = msg.get("status")
                if status:
                    statuses[rid] = status
        return statuses

    @staticmethod
    def is_terminal_message_status(status: str | None) -> bool:
        return status in _TERMINAL_MESSAGE_STATUSES
