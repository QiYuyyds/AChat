"""@traced decorator and span attribute key constants.

``@traced(span_key, **attrs)`` wraps sync/async functions in an OTel span.
The *span_key* is looked up in ``span_names.SPAN_NAMES`` to produce the
bilingual span name.  When tracing is disabled the decorator is a no-op.

Attribute keys use the ``agenthub.`` prefix for business-specific attributes
and align with OTel semantic conventions for standard ones.
"""

import asyncio
import contextlib
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from opentelemetry.trace import Status, StatusCode

from .span_names import resolve_span_name
from .tracer import get_tracer, is_trace_enabled

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")

# ── Attribute key constants (business-specific, agenthub. prefix) ──
AGENTHUB_HITS = "agenthub.hits"
AGENTHUB_EMPTY = "agenthub.empty"
AGENTHUB_SKIPPED = "agenthub.skipped"
AGENTHUB_MODEL = "agenthub.model"
AGENTHUB_ADAPTER_NAME = "agenthub.adapter_name"
AGENTHUB_TOOL_NAME = "agenthub.tool_name"
AGENTHUB_SUCCESS = "agenthub.success"
AGENTHUB_TURN = "agenthub.turn"
AGENTHUB_FINISH_REASON = "agenthub.finish_reason"
AGENTHUB_INPUT_TOKENS = "agenthub.input_tokens"
AGENTHUB_OUTPUT_TOKENS = "agenthub.output_tokens"
AGENTHUB_CACHE_READ_TOKENS = "agenthub.cache_read_tokens"
AGENTHUB_DISPATCH_DEPTH = "agenthub.dispatch_depth"
AGENTHUB_DISPATCH_VISIBILITY = "agenthub.dispatch_visibility"
AGENTHUB_AGENT_ID = "agenthub.agent_id"
AGENTHUB_RUN_ID = "agenthub.run_id"
AGENTHUB_CONVERSATION_ID = "agenthub.conversation_id"
AGENTHUB_DISPATCH_MODE = "agenthub.dispatch_mode"
AGENTHUB_PARENT_RUN_ID = "agenthub.parent_run_id"
AGENTHUB_SYSTEM_PROMPT_HASH = "agenthub.system_prompt_hash"
AGENTHUB_RAG_CHUNKS_INJECTED = "agenthub.rag_chunks_injected"
AGENTHUB_MEMORY_ITEMS_INJECTED = "agenthub.memory_items_injected"
AGENTHUB_SCHEMA_MODE = "agenthub.schema_mode"
AGENTHUB_FINAL_TOKEN_COUNT = "agenthub.final_token_count"
AGENTHUB_HISTORY_MSG_COUNT = "agenthub.history_msg_count"
AGENTHUB_RAG_ENABLED = "agenthub.rag_enabled"
AGENTHUB_MEMORY_ENABLED = "agenthub.memory_enabled"
AGENTHUB_SOURCE = "agenthub.source"
AGENTHUB_TOP_K = "agenthub.top_k"
AGENTHUB_MIN_SCORE = "agenthub.min_score"
AGENTHUB_WINDOW_SIZE = "agenthub.window_size"
AGENTHUB_QUERY = "agenthub.query"
AGENTHUB_MODE = "agenthub.mode"
AGENTHUB_REWRITE_ENABLED = "agenthub.rewrite_enabled"
AGENTHUB_ORIGINAL = "agenthub.original"
AGENTHUB_REWRITTEN = "agenthub.rewritten"
AGENTHUB_FINAL_COUNT = "agenthub.final_count"
AGENTHUB_FUSION_METHOD = "agenthub.fusion_method"
AGENTHUB_ARGS_SUMMARY = "agenthub.args_summary"
AGENTHUB_TASK_ID = "agenthub.task_id"
AGENTHUB_CHILD_AGENT_ID = "agenthub.child_agent_id"
AGENTHUB_TOTAL_TURNS = "agenthub.total_turns"
AGENTHUB_TOTAL_TOKENS = "agenthub.total_tokens"
AGENTHUB_DURATION_MS = "agenthub.duration_ms"
AGENTHUB_EVAL_TYPE = "agenthub.eval_type"
AGENTHUB_EVAL_MODE = "agenthub.eval_mode"
AGENTHUB_ERROR = "agenthub.error"
AGENTHUB_RESULT_SUMMARY = "agenthub.result_summary"


def _set_attrs(span, attrs: dict[str, Any]) -> None:
    """Set attributes on a span, filtering out None values."""
    for key, value in attrs.items():
        if value is not None:
            span.set_attribute(key, value)


def traced(
    span_key: str,
    *,
    suffix: str | None = None,
    **attrs: Any,
) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Decorator that wraps a sync or async function in an OTel span.

    *span_key* is looked up in ``SPAN_NAMES`` to produce the bilingual
    name.  Extra keyword attributes are set on the span (None values
    are skipped).  When tracing is disabled the decorator is a no-op.

    The wrapped function's exception is recorded on the span as ERROR
    and re-raised unchanged.
    """
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        if not is_trace_enabled():
            return func

        span_name = resolve_span_name(span_key, suffix)
        tracer = get_tracer("achat")

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
                with tracer.start_as_current_span(span_name) as span:
                    _set_attrs(span, attrs)
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        span.record_exception(e)
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        raise
            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            with tracer.start_as_current_span(span_name) as span:
                _set_attrs(span, attrs)
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        return sync_wrapper  # type: ignore[return-value]

    return decorator


def traced_async(
    span_key: str,
    *,
    suffix: str | None = None,
    **attrs: Any,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Explicitly async variant of :func:`traced` for edge cases."""
    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        if not is_trace_enabled():
            return func

        span_name = resolve_span_name(span_key, suffix)
        tracer = get_tracer("achat")

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            with tracer.start_as_current_span(span_name) as span:
                _set_attrs(span, attrs)
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise
        return wrapper

    return decorator


class _NoOpSpan:
    """Drop-in span stub used when tracing is disabled.

    Mirrors the OTel ``Span`` interface so callers can use
    ``span.is_recording()`` / ``span.set_attribute(...)`` without NoneType
    crashes.
    """

    __slots__ = ()

    def is_recording(self) -> bool:
        return False

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: ARG002
        pass

    def record_exception(self, exception: BaseException) -> None:  # noqa: ARG002
        pass

    def set_status(self, status: Any) -> None:  # noqa: ARG002
        pass

    def add_event(self, name: str, attributes: Any = None) -> None:  # noqa: ARG002
        pass


_NOOP_SPAN = _NoOpSpan()


def start_span(span_key: str, *, suffix: str | None = None, **attrs: Any):
    """Context manager that starts a span with dynamic attributes.

    Usage::

        with start_span("agent.run", agent_id="x", run_id="y") as span:
            ...

    When tracing is disabled, returns a no-op context manager that yields a
    ``_NoOpSpan`` so callers can safely call ``span.is_recording()`` etc.
    """
    if not is_trace_enabled():
        return contextlib.nullcontext(_NOOP_SPAN)

    span_name = resolve_span_name(span_key, suffix)
    tracer = get_tracer("achat")

    @contextlib.contextmanager
    def _wrapper():
        with tracer.start_as_current_span(span_name) as span:
            _set_attrs(span, attrs)
            yield span

    return _wrapper()
