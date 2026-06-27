"""ToolRegistry — global tool registry.

Port of src/server/tools/registry.ts. Agents reference tools by name
(``agent.tool_names``); AgentRunner resolves ToolDefs here when assembling the
adapter input.

Building the registry also wires the deploy slash-command handlers
(``deploy_command_service.set_deploy_handlers``) to the concrete deploy tools —
the integration point 阶段 2 left open.
"""

from __future__ import annotations

from typing import Any

from app.services import deploy_command_service
from app.tools.ask_user import ask_user_tool
from app.tools.base import ToolContext, ToolDef, ToolResult, err
from app.tools.bash import bash_tool
from app.tools.deploy_artifact import deploy_artifact_for_conversation, deploy_artifact_tool
from app.tools.deploy_workspace import (
    deploy_workspace_for_conversation,
    deploy_workspace_tool,
)
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
from app.tools.plan_tasks import plan_tasks_tool
from app.tools.read_artifact import read_artifact_tool
from app.tools.read_attachment import read_attachment_tool
from app.tools.report_task_result import report_task_result_tool
from app.tools.web_search import web_search_tool
from app.tools.write_artifact import write_artifact_tool


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
                raise ValueError(f"Unknown tool: {name}")
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


def _build_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(write_artifact_tool)
    reg.register(read_artifact_tool)
    reg.register(deploy_artifact_tool)
    reg.register(deploy_workspace_tool)
    reg.register(read_attachment_tool)
    reg.register(plan_tasks_tool)
    reg.register(report_task_result_tool)
    reg.register(fs_list_tool)
    reg.register(fs_read_tool)
    reg.register(fs_write_tool)
    reg.register(bash_tool)
    reg.register(ask_user_tool)
    reg.register(rag_search_tool)
    reg.register(rag_ingest_tool)
    reg.register(rag_list_documents_tool)
    reg.register(rag_delete_document_tool)
    reg.register(memory_recall_tool)
    reg.register(web_search_tool)
    return reg


# Tools are static (no held connections/state); rebuild once per import.
tool_registry = _build_registry()

# Wire the deploy slash-command handlers 阶段 2 left as a registry.
deploy_command_service.set_deploy_handlers(
    artifact_fn=deploy_artifact_for_conversation,
    workspace_fn=deploy_workspace_for_conversation,
)
