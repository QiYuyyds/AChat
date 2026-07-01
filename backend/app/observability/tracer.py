"""OpenTelemetry + Arize Phoenix tracer setup.

Provides:
    - init_observability(): one-time OTel SDK + Phoenix bootstrap
    - get_tracer(name): get an OTel tracer instance
    - traced(name, **attrs): decorator for auto-span wrapping
    - trace_context(name, **attrs): context manager for inline spans

Span kinds follow OpenInference semantic conventions:
    - SpanKind.INTERNAL + "openinference.span.kind" = "CHAIN" / "RETRIEVER" / "RERANKER"
    - SpanKind.CLIENT + "openinference.span.kind" = "LLM" / "TOOL" / "EMBEDDING"
    - SpanKind.SERVER → auto-set by FastAPI instrumentor
"""

from __future__ import annotations

import asyncio
import functools
import logging
import subprocess
import sys as _sys
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Any, Callable

import opentelemetry.trace as _otel_trace

logger = logging.getLogger(__name__)

# Module-level state
_tracer_provider: Any = None
_tracer: Any = None
_phoenix_process: subprocess.Popen | None = None
_phoenix_port: int = 6006
_initialized: bool = False


def init_observability(app: Any, settings: Any) -> None:
    """Initialize OpenTelemetry SDK + Arize Phoenix.

    Call once during application startup (in lifespan).
    Starts Phoenix as a subprocess (avoids 5s startup timeout on Windows).
    Sets up FastAPI + httpx auto-instrumentation and global tracer.
    """
    global _tracer_provider, _tracer, _phoenix_process, _phoenix_port, _initialized

    if _initialized:
        return

    try:
        # ── Windows: fix GBK encoding ──
        if _sys.platform == "win32":
            try:
                _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        # ── Start Phoenix subprocess ──
        _phoenix_port = getattr(settings, "trace_phoenix_port", 6006) or 6006
        exporter_host = "127.0.0.1"

        logger.info("Starting Arize Phoenix subprocess on port %s ...", _phoenix_port)
        _phoenix_process = subprocess.Popen(
            [
                _sys.executable, "-m", "phoenix.server.main", "serve",
                "--port", str(_phoenix_port),
                "--host", exporter_host,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Phoenix spawned (pid=%s), waiting for startup ...", _phoenix_process.pid)

        # ── Set up OTel TracerProvider with OTLP exporter ──
        project_name = getattr(settings, "trace_project_name", "achat") or "achat"
        resource = Resource.create({"service.name": project_name})

        otlp_endpoint = f"http://{exporter_host}:{_phoenix_port}/v1/traces"
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        trace.set_tracer_provider(provider)
        _tracer_provider = provider

        # ── Auto-instrumentation ──
        FastAPIInstrumentor.instrument_app(app)
        HTTPXClientInstrumentor().instrument()
        logger.info("OTel: FastAPI + httpx auto-instrumented, OTLP endpoint=%s", otlp_endpoint)

        # ── Global tracer ──
        _tracer = trace.get_tracer("achat.backend")

        _initialized = True
        logger.info(
            "Observability initialized: Phoenix port=%s, project=%s, exporter=%s",
            _phoenix_port, project_name, otlp_endpoint,
        )

    except Exception as e:
        logger.warning(
            "Observability init failed (traces will not be collected): %s", e
        )
        _tracer = _make_noop_tracer()
        _initialized = True


def get_tracer(name: str = "achat.backend") -> Any:
    """Get an OpenTelemetry tracer instance."""
    global _tracer
    if _tracer is not None:
        return _tracer
    # Fallback: noop tracer
    return _make_noop_tracer()


def traced(span_name: str, **attrs: Any):
    """Decorator: wrap a function in an OTel span.

    Works with both sync and async functions.
    Automatically records exceptions as span events.

    Usage:
        @traced("rag.search", kind="RETRIEVER")
        async def search(self, query: str) -> List[...]:
            ...

        @traced("agent.run", agent_id=run.agent_id)
        async def execute_run(self, run_id: str) -> None:
            ...
    """
    def decorator(fn: Callable):
        tracer = get_tracer()

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                with _start_span(tracer, span_name, dict(attrs)):
                    return await fn(*args, **kwargs)
            return async_wrapper
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                with _start_span(tracer, span_name, dict(attrs)):
                    return fn(*args, **kwargs)
            return sync_wrapper
    return decorator


@asynccontextmanager
async def trace_context(span_name: str, **attrs: Any):
    """Async context manager for inline span creation.

    Usage:
        async with trace_context("milvus.search", hits=len(results)):
            results = milvus_client.search(...)
    """
    tracer = get_tracer()
    with _start_span(tracer, span_name, attrs) as span:
        yield span


# ── Internal helpers ──────────────────────────────────────────────────────

@contextmanager
def _start_span(tracer, span_name: str, attrs: dict):
    """Create a span with OpenInference semantic conventions."""
    from opentelemetry.trace import SpanKind, Status, StatusCode

    # Extract known attributes, pass the rest as generic
    kind_str = attrs.pop("kind", None)
    status_str = attrs.pop("status", "ok")

    # Map span kind string to OTel SpanKind
    kind = SpanKind.INTERNAL
    if kind_str:
        kind = _SPAN_KIND_MAP.get(kind_str, SpanKind.INTERNAL)
        attrs["openinference.span.kind"] = kind_str

    span = tracer.start_span(span_name, kind=kind, attributes=_clean_attrs(attrs))
    try:
        yield span
    except Exception as exc:
        span.set_status(Status(StatusCode.ERROR, str(exc)[:256]))
        span.record_exception(exc)
        raise
    else:
        if status_str == "error":
            span.set_status(Status(StatusCode.ERROR))
        else:
            span.set_status(Status(StatusCode.OK))
    finally:
        span.end()


def _clean_attrs(attrs: dict) -> dict:
    """Convert attribute values to OTel-compatible types."""
    cleaned = {}
    for key, value in attrs.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        elif isinstance(value, (list, tuple)):
            # OTel supports arrays of uniform type
            try:
                cleaned[key] = list(value)
            except (TypeError, ValueError):
                cleaned[key] = str(value)
        else:
            cleaned[key] = str(value)
    return cleaned


# Map OpenInference kind to OTel SpanKind
_SPAN_KIND_MAP: dict[str, Any] = {
    "CHAIN": _otel_trace.SpanKind.INTERNAL,
    "RETRIEVER": _otel_trace.SpanKind.INTERNAL,
    "RERANKER": _otel_trace.SpanKind.INTERNAL,
    "LLM": _otel_trace.SpanKind.CLIENT,
    "TOOL": _otel_trace.SpanKind.CLIENT,
    "EMBEDDING": _otel_trace.SpanKind.CLIENT,
    "AGENT": _otel_trace.SpanKind.INTERNAL,
    "GUARDRAIL": _otel_trace.SpanKind.INTERNAL,
    "EVALUATOR": _otel_trace.SpanKind.INTERNAL,
}


def _make_noop_tracer() -> Any:
    """Return a no-op tracer when OTel is not initialized."""
    from opentelemetry import trace as _trace
    return _trace.get_tracer("achat.noop")
