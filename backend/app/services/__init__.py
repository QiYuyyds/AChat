"""Services module.

Phase 2 — core service layer:
  - event_bus:              in-process StreamEvent fan-out (SSE backbone)
  - conversation_service:   conversation CRUD + message lifecycle
  - pending_dispatch_plans: in-memory store for plans awaiting user review
  - deploy_command_service: /deploy slash-command handling
  - runner_registry:        late-bound AgentRunner (filled in phase 5)
"""

from app.services.event_bus import EventBus, event_bus
from app.services.pending_dispatch_plans import (
    PendingDispatchPlansStore,
    PlanReviewOutcome,
    pending_dispatch_plans,
)
from app.services.runner_registry import (
    AgentRunner,
    RunHandle,
    get_agent_runner,
    set_agent_runner,
)

__all__ = [
    # Event bus
    "EventBus",
    "event_bus",
    # Pending dispatch plans
    "PendingDispatchPlansStore",
    "PlanReviewOutcome",
    "pending_dispatch_plans",
    # Runner registry
    "AgentRunner",
    "RunHandle",
    "get_agent_runner",
    "set_agent_runner",
]
