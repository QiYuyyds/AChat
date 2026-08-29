"""
Phoenix trace provider implementation.

Fetches trace spans from an Arize Phoenix instance.
Requires: pip install arize-phoenix

Usage:
    provider = PhoenixProvider(endpoint="http://localhost:6006")
    spans = await provider.get_spans("trace_abc123")
"""

from __future__ import annotations

from typing import Any


class PhoenixProvider:
    """
    Phoenix TraceProvider 实现。

    通过 Phoenix Python SDK 获取 trace span 数据。
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:6006",
        project: str = "default",
    ):
        """
        Args:
            endpoint: Phoenix UI endpoint (e.g., http://localhost:6006)
            project: Phoenix project name
        """
        self.endpoint = endpoint
        self.project = project
        self._client = None

    def _get_client(self):
        """Lazy-init Phoenix client"""
        if self._client is None:
            try:
                # phoenix >= 5 移除了顶层 px.Client, 统一走 phoenix.client;
                # base_url 在旧版叫 endpoint, 新 API 只认 base_url。
                from phoenix.client import Client as PhoenixClient

                self._client = PhoenixClient(base_url=self.endpoint)
            except ImportError:
                raise RuntimeError(
                    "phoenix package not installed. "
                    "Install with: pip install arize-phoenix"
                )
        return self._client

    async def get_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """
        获取一个 trace 的所有 span。

        Args:
            trace_id: OTel trace ID

        Returns:
            标准化的 span 列表
        """
        import asyncio

        # Phoenix SDK 是同步的, 在线程中运行
        return await asyncio.to_thread(self._get_spans_sync, trace_id)

    def _get_spans_sync(self, trace_id: str) -> list[dict[str, Any]]:
        """同步获取 spans"""
        client = self._get_client()

        try:
            df = client.spans.get_spans_dataframe(project_name=self.project)
        except Exception:
            return []

        if df is None or df.empty:
            return []

        # 过滤指定 trace
        trace_col = "context.trace_id"
        if trace_col not in df.columns:
            return []

        trace_spans = df[df[trace_col] == trace_id]
        if trace_spans.empty:
            return []

        # 转换为标准格式
        return self._normalize_spans(trace_spans.to_dict("records"))

    async def get_trace_ids(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[str]:
        """
        查询 trace ID 列表。

        Args:
            filters: 过滤条件 (暂未实现, 保留接口)
            limit: 返回数量限制

        Returns:
            trace ID 列表
        """
        import asyncio

        return await asyncio.to_thread(self._get_trace_ids_sync, limit)

    def _get_trace_ids_sync(self, limit: int) -> list[str]:
        """同步获取 trace IDs"""
        client = self._get_client()

        try:
            df = client.spans.get_spans_dataframe(project_name=self.project)
        except Exception:
            return []

        if df is None or df.empty:
            return []

        trace_col = "context.trace_id"
        if trace_col not in df.columns:
            return []

        trace_ids = df[trace_col].unique().tolist()
        return trace_ids[:limit]

    def _normalize_spans(self, raw_spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """将 Phoenix DataFrame 格式归一化为标准格式"""
        normalized = []
        for span in raw_spans:
            normalized.append({
                "name": span.get("name", ""),
                "attributes": span.get("attributes", {}),
                "start_time": str(span.get("start_time", "")),
                "end_time": str(span.get("end_time", "")),
                "status": span.get("status", {}),
                "trace_id": span.get("context.trace_id", ""),
                "span_id": span.get("context.span_id", ""),
            })
        return normalized
