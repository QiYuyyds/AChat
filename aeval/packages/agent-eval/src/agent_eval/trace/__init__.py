"""
Trace provider implementations for the Aeval evaluation framework.

Provides a Phoenix-based trace provider out of the box.
Other backends (Jaeger, Tempo, etc.) can be implemented by users.

Usage:
    from agent_eval.trace import PhoenixProvider

    provider = PhoenixProvider(endpoint="http://localhost:6006")
    spans = await provider.get_spans("trace_abc123")
"""

from agent_eval.trace.phoenix import PhoenixProvider

__all__ = ["PhoenixProvider"]
