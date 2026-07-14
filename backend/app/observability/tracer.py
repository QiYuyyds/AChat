"""OpenTelemetry tracer provider and lifecycle management.

Initialises a ``TracerProvider`` with a ``BatchSpanProcessor`` +
``OTLPSpanExporter`` that sends spans to Phoenix via OTLP gRPC.
When ``trace_enabled`` is False, all initialisation is skipped and the
``@traced`` decorator becomes a no-op.
"""

import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = logging.getLogger(__name__)

_provider: TracerProvider | None = None
_trace_enabled: bool = True


def init_observability(settings) -> None:
    """Initialise the OTel tracer provider and register the OTLP exporter.

    Skipped entirely when ``settings.trace_enabled`` is False.
    """
    global _provider, _trace_enabled
    _trace_enabled = settings.trace_enabled

    if not _trace_enabled:
        logger.info("Observability: trace_enabled=False, skipping OTel init")
        return

    resource = Resource.create({
        "service.name": "achat-backend",
        "service.version": "0.1.0",
    })
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=settings.phoenix_endpoint, insecure=True)
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)
    _provider = provider
    logger.info("Observability: OTel tracer provider initialised (endpoint=%s)", settings.phoenix_endpoint)


def get_tracer(name: str = "achat"):
    """Return a standard OTel Tracer instance."""
    return trace.get_tracer(name)


def is_trace_enabled() -> bool:
    """Whether tracing is enabled (``trace_enabled`` flag)."""
    return _trace_enabled


def shutdown_observability() -> None:
    """Flush buffered spans and shut down the provider."""
    global _provider
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception as e:
            logger.warning("Observability: provider shutdown error: %s", e)
        _provider = None
