"""AgentRegistry — route to an adapter by Agent.adapter_name.

Port of src/server/adapters/registry.ts. See specs/05-adapter-interface.md.
"""

from __future__ import annotations

from app.adapters.base import AdapterName, AgentPlatformAdapter
from app.adapters.claude_adapter import ClaudeAdapter
from app.adapters.custom_adapter import CustomAdapter
from app.adapters.mock_adapter import MockAdapter
from app.db.models import Agent


class AgentRegistry:
    def __init__(self) -> None:
        self._adapters: dict[AdapterName, AgentPlatformAdapter] = {}

    def register(self, adapter: AgentPlatformAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get_adapter(self, agent: Agent) -> AgentPlatformAdapter:
        adapter = self._adapters.get(agent.adapter_name)
        if adapter is None:
            raise ValueError(
                f'No adapter registered for "{agent.adapter_name}" '
                f"(agent: {agent.name} / {agent.id})"
            )
        return adapter


def _build_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(MockAdapter())
    reg.register(CustomAdapter())
    reg.register(ClaudeAdapter())
    # Codex adapter deferred — @openai/codex-sdk port pending.
    return reg


# adapters are stateless translators (SDK client instantiation is cheap), so rebuild
# on each module load rather than keeping a cross-reload singleton alive.
agent_registry = _build_registry()
