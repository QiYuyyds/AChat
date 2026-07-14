"""Observability layer: OTel tracing + Arize Phoenix evaluation.

Public API:
    init_observability(settings) — call at startup
    shutdown_observability()      — call at shutdown
    get_tracer(name)              — get OTel Tracer
    traced(span_key, **attrs)     — decorator for sync/async functions
    traced_async(span_key, ...)   — explicit async decorator
    start_span(span_key, ...)     — manual span context manager
    SPAN_NAMES                    — bilingual span name mapping
    Attribute key constants       — AGENTHUB_* prefixed keys
"""

from .instrumentation import (
    start_span,
    traced,
    traced_async,
)
from .span_names import SPAN_NAMES
from .tracer import (
    get_tracer,
    init_observability,
    is_trace_enabled,
    shutdown_observability,
)

__all__ = [
    "init_observability",
    "shutdown_observability",
    "get_tracer",
    "is_trace_enabled",
    "traced",
    "traced_async",
    "start_span",
    "SPAN_NAMES",
]
