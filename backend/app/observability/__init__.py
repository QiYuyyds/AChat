"""Observability module — OpenTelemetry + Arize Phoenix tracing.

Public API:
    - init_observability(app, settings): called once at startup
    - get_tracer(name) -> Tracer: get an OTel tracer
    - traced(name, **attrs): decorator for auto-span on functions
    - trace_context(name, **attrs): context manager for inline spans
"""

from app.observability.tracer import (
    get_tracer,
    init_observability,
    traced,
    trace_context,
)

__all__ = [
    "init_observability",
    "get_tracer",
    "traced",
    "trace_context",
]
