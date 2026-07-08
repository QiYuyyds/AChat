"""Built-in lifecycle hooks.

Each hook module exports a `register(registry)` function. `register_all`
registers all built-in hooks at startup. Agents enable specific hook groups
via their `hook_names` configuration.
"""

from __future__ import annotations

from app.services.hook_registry import HookRegistry


def register_all(registry: HookRegistry) -> None:
    """Register all built-in hooks into the registry."""
    from app.services.hooks.audit_log import register as register_audit
    from app.services.hooks.auto_compact import register as register_auto_compact
    from app.services.hooks.checkpoint import register as register_checkpoint
    from app.services.hooks.memory_persist import register as register_memory
    from app.services.hooks.skill_auto_activator import register as register_skill_activator
    from app.services.hooks.summary_generate import register as register_summary
    from app.services.hooks.tool_approval import register as register_approval

    register_audit(registry)
    register_memory(registry)
    register_auto_compact(registry)
    register_checkpoint(registry)
    register_summary(registry)
    register_approval(registry)
    register_skill_activator(registry)


__all__ = ["register_all"]
