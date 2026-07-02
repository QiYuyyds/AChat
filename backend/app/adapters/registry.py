"""AgentRegistry — route to an adapter by Agent.adapter_name.

Port of src/server/adapters/registry.ts. See specs/05-adapter-interface.md.

CLI-based adapters (claude-code, codex) use vendor CLI subprocesses.
SDK-based adapters (custom) call LLM APIs directly.
"""

from __future__ import annotations

from app.adapters.base import AdapterName, AgentPlatformAdapter
from app.adapters.claude_adapter import ClaudeCLIAdapter
from app.adapters.codex_adapter import CodexCLIAdapter
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
    # CLI-based adapters — spawn vendor CLI subprocesses.
    reg.register(ClaudeCLIAdapter(
        executable_path=agent_executable_path_fallback("claude-code", "claude"),
    ))
    reg.register(CodexCLIAdapter(
        executable_path=agent_executable_path_fallback("codex", "codex"),
    ))
    return reg


def agent_executable_path_fallback(adapter_name: str, default_exe: str) -> str:
    """Return the default executable name for a CLI adapter.

    The per-agent ``executable_path`` in ``Agent`` / ``AdapterInput`` takes
    precedence at runtime.  This is only the module-level fallback.
    """
    return default_exe


# Adapters are stateless translators; rebuild on each module load.
agent_registry = _build_registry()
