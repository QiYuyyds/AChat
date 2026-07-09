"""Legacy orchestrator module — superseded by the Unified Agent Loop.

The original three-stage orchestrator (PLAN → EXECUTE → AGGREGATE) with
verification gates, retry harness, and LLM judge has been replaced by the
unified ``run_agent_loop`` abstraction in ``agent_loop.py``.

Orchestrated conversations now use ``run_agent_loop(mode='coordinated')``
which runs the orchestrator agent through the same while-loop as solo agents,
with a ``task_dispatch`` tool added for sub-agent dispatch.

See ``specs/19-unified-agent-loop.md`` for the full design.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
