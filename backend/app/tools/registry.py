"""ToolRegistry — global tool registry.

Port of src/server/tools/registry.ts. Agents reference tools by name
(``agent.tool_names``); AgentRunner resolves ToolDefs here when assembling the
adapter input.

Building the registry also wires the deploy slash-command handlers
(``deploy_command_service.set_deploy_handlers``) to the concrete deploy tools —
the integration point 阶段 2 left open.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.services import deploy_command_service
from app.tools.ask_user import ask_user_tool
from app.tools.base import ToolContext, ToolDef, ToolResult, err
from app.tools.bash import bash_tool
from app.tools.deploy_artifact import deploy_artifact_for_conversation, deploy_artifact_tool
from app.tools.deploy_workspace import (
    deploy_workspace_for_conversation,
    deploy_workspace_tool,
)
from app.tools.fs_edit import fs_edit_tool
from app.tools.fs_glob import fs_glob_tool
from app.tools.fs_grep import fs_grep_tool
from app.tools.fs_list import fs_list_tool
from app.tools.fs_read import fs_read_tool
from app.tools.fs_write import fs_write_tool
from app.tools.memory_rag import (
    memory_recall_tool,
    rag_delete_document_tool,
    rag_ingest_tool,
    rag_list_documents_tool,
    rag_search_tool,
)
from app.tools.read_artifact import read_artifact_tool
from app.tools.read_attachment import read_attachment_tool
from app.tools.skills import load_skill_tool, write_skill_tool
from app.tools.task_dispatch import task_dispatch_tool
from app.tools.web_search import web_search_tool
from app.tools.write_artifact import write_artifact_tool

if TYPE_CHECKING:
    from app.services.hook_registry import HookRegistry

logger = logging.getLogger(__name__)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def register(self, tool: ToolDef) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDef | None:
        return self._tools.get(name)

    def resolve(self, names: list[str]) -> list[ToolDef]:
        resolved: list[ToolDef] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                logger.warning("Skipping unknown tool '%s' (not in registry)", name)
                continue
            resolved.append(tool)
        return resolved

    async def execute(self, tool_name: str, args: Any, ctx: ToolContext) -> ToolResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            return err(f"Unknown tool: {tool_name}")
        try:
            return await tool.handler(args, ctx)
        except Exception as e:  # noqa: BLE001 - tool failures surface to the LLM
            return err(str(e))

    async def execute_with_hooks(
        self,
        name: str,
        args: Any,
        ctx: ToolContext,
        hook_registry: HookRegistry | None = None,
    ) -> ToolResult:
        """Execute a tool with pre/post hooks.

        1. Dispatch pre_tool_use hook → deny/modify/allow
        2. Execute tool (if not denied)
        3. Dispatch post_tool_use hook → modify result
        4. Return final result
        """
        if hook_registry is None:
            return await self.execute(name, args, ctx)

        from app.services.hook_registry import HookContext, HookEvent

        # ── pre_tool_use ──
        pre_ctx = HookContext(
            event=HookEvent.PRE_TOOL_USE,
            run_id=ctx.run_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            tool_name=name,
            args=args,
            call_id=None,
        )
        pre_result = await hook_registry.dispatch(pre_ctx)

        if pre_result.action == "deny":
            return err(str(pre_result.data) if pre_result.data else "blocked by hook")

        effective_args = args
        if pre_result.action == "modify" and isinstance(pre_result.data, dict):
            effective_args = pre_result.data.get("args", args)

        # ── execute ──
        result = await self.execute(name, effective_args, ctx)

        # ── post_tool_use ──
        post_ctx = HookContext(
            event=HookEvent.POST_TOOL_USE,
            run_id=ctx.run_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            tool_name=name,
            args=effective_args,
            result=result.value if result.ok else result.error,
            is_error=not result.ok,
            tool_names=ctx.tool_names,
        )
        post_result = await hook_registry.dispatch(post_ctx)

        # O8: store post_tool_use HookResult on ctx so _run_react_loop can
        # check for inject actions (skill_auto_activator) without re-dispatching.
        ctx.last_post_hook_result = post_result

        if post_result.action == "modify" and isinstance(post_result.data, dict):
            modified = post_result.data.get("result")
            if modified is not None:
                return ToolResult(ok=result.ok, value=modified, error=result.error)

        return result


def _build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(write_artifact_tool)
    reg.register(read_artifact_tool)
    reg.register(deploy_artifact_tool)
    reg.register(deploy_workspace_tool)
    reg.register(read_attachment_tool)
    reg.register(task_dispatch_tool)
    reg.register(fs_list_tool)
    reg.register(fs_read_tool)
    reg.register(fs_write_tool)
    reg.register(fs_edit_tool)
    reg.register(fs_grep_tool)
    reg.register(fs_glob_tool)
    reg.register(bash_tool)
    reg.register(ask_user_tool)
    reg.register(rag_search_tool)
    reg.register(rag_ingest_tool)
    reg.register(rag_list_documents_tool)
    reg.register(rag_delete_document_tool)
    reg.register(memory_recall_tool)
    reg.register(web_search_tool)
    # load_skill is auto-injected per equipped skill; write_skill is opt-in (tool_names).
    reg.register(load_skill_tool)
    reg.register(write_skill_tool)
    return reg


# Tools are static (no held connections/state); rebuild once per import.
tool_registry = _build_registry()

# Wire the deploy slash-command handlers 阶段 2 left as a registry.
deploy_command_service.set_deploy_handlers(
    artifact_fn=deploy_artifact_for_conversation,
    workspace_fn=deploy_workspace_for_conversation,
)
