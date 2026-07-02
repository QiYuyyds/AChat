"""Agents API routes.

Port of:
- src/app/api/agents/route.ts        (GET list, POST create)
- src/app/api/agents/[id]/route.ts   (PATCH update, DELETE)
- src/app/api/agents/draft/route.ts  (POST heuristic agent-config draft)

There is no standalone ``agent_service`` on the Python side yet; the TS CRUD
lived in ``src/server/agent-service.ts`` and is ported inline here (own
``get_db`` session, following the conversation_service style). The agent-draft
heuristic (``src/server/agent-draft-service.ts`` + ``agent-builder-config.ts``)
is likewise ported inline — it is purely deterministic (no LLM call). Errors are
translated to the same HTTP status codes the TS routes return.

Wire contract (byte-for-byte with the unchanged React frontend, which types
agent responses as Drizzle ``AgentRow`` — the FULL row, **including** ``apiKey``):
- ``GET    /api/agents``        → 200 ``{ "agents": [<full row>...] }``
- ``POST   /api/agents``        → 201 ``{ "agent": <full row> }``;
                                  400 ``{ "error": "Invalid body", "issues": [...] }``
                                  400 ``{ "error": <message> }``
- ``PATCH  /api/agents/{id}``   → 200 ``{ "agent": <full row> }``;
                                  400 invalid body / service error (same shapes)
- ``DELETE /api/agents/{id}``   → 200 ``{ "ok": true }``; 400 ``{ "error": <message> }``
- ``POST   /api/agents/draft``  → 200 ``{ "draft": <AgentConfigDraft> }``;
                                  400 invalid body / service error (same shapes)
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select

from app.adapters.custom_provider_client import (
    validate_openai_compatible_api_key,
    validate_openai_compatible_base_url,
)
from app.db.engine import get_db
from app.db.models import Agent
from app.schemas import CreateAgentRequest, UpdateAgentRequest
from app.utils.clock import now_ms
from app.utils.ids import new_agent_id

router = APIRouter()


# ─── Serialization ──────────────────────────────────────────────────
def _serialize(row: Agent) -> dict[str, Any]:
    """Full AgentRow wire shape (camelCase), matching the Drizzle select row.

    Includes ``apiKey`` — the frontend types this as ``AgentRow`` and the TS
    routes return the row verbatim (no redaction).
    """
    return {
        "id": row.id,
        "name": row.name,
        "avatar": row.avatar,
        "description": row.description,
        "capabilities": row.capabilities_list,
        "systemPrompt": row.system_prompt,
        "adapterName": row.adapter_name,
        "modelProvider": row.model_provider,
        "modelId": row.model_id,
        "apiKey": row.api_key,
        "apiBaseUrl": row.api_base_url,
        "toolNames": row.tool_names_list,
        "skillNames": row.skill_names_list,
        "isBuiltin": row.is_builtin,
        "isOrchestrator": row.is_orchestrator,
        "supportsVision": row.supports_vision,
        "createdAt": row.created_at,
        # CLI fields
        "executablePath": row.executable_path,
        "protocolFamily": row.protocol_family,
        "customArgs": row.custom_args_list,
    }


def _invalid_body(exc: ValidationError) -> JSONResponse:
    return JSONResponse(
        {"error": "Invalid body", "issues": exc.errors()},
        status_code=400,
    )


# ─── GET /api/agents ────────────────────────────────────────────────
@router.get("/agents")
async def list_agents() -> JSONResponse:
    """List agents: builtin first, then newest first (matches listAgentsOrdered)."""
    async with get_db() as db:
        result = await db.execute(
            select(Agent).order_by(
                Agent.is_builtin.desc(),
                Agent.created_at.desc(),
            )
        )
        rows = result.scalars().all()
        return JSONResponse({"agents": [_serialize(r) for r in rows]})


# ─── POST /api/agents ───────────────────────────────────────────────
@router.post("/agents")
async def create_agent(request: Request) -> JSONResponse:
    """Create a user custom agent (ports createCustomAgent)."""
    try:
        raw = await request.json()
    except Exception:
        raw = None

    if not isinstance(raw, dict):
        return JSONResponse({"error": "Invalid body", "issues": []}, status_code=400)

    # adapterName defaults to 'custom' in the TS zod schema; the Python schema
    # makes it required, so apply the default before validating.
    raw = dict(raw)
    raw.setdefault("adapterName", "custom")

    try:
        body = CreateAgentRequest.model_validate(raw)
    except ValidationError as exc:
        return _invalid_body(exc)

    # zod .refine: custom adapter requires modelProvider + modelId.
    if body.adapter_name == "custom" and not (body.model_provider and body.model_id):
        return JSONResponse(
            {"error": "Custom adapter requires modelProvider and modelId"},
            status_code=400,
        )

    try:
        row = await _create_custom_agent(body)
    except ValueError as err:
        return JSONResponse({"error": str(err)}, status_code=400)

    return JSONResponse({"agent": row}, status_code=201)


async def _create_custom_agent(body: CreateAgentRequest) -> dict[str, Any]:
    adapter_name = body.adapter_name

    if adapter_name == "custom":
        if not body.model_provider or not body.model_id:
            raise ValueError("Custom adapter requires modelProvider and modelId")
        base_url_error = validate_openai_compatible_base_url(
            body.model_provider, body.api_base_url
        )
        if base_url_error:
            raise ValueError(base_url_error)
        api_key_error = validate_openai_compatible_api_key(
            body.model_provider, body.api_key
        )
        if api_key_error:
            raise ValueError(api_key_error)

    avatar = (body.avatar or "").strip() or "🤖"
    api_key = (body.api_key.strip() if body.api_key else "") or None
    api_base_url = (body.api_base_url.strip() if body.api_base_url else "") or None

    agent = Agent(
        id=new_agent_id(),
        name=body.name.strip(),
        avatar=avatar,
        description=body.description.strip(),
        system_prompt=body.system_prompt,
        adapter_name=adapter_name,
        model_provider=(body.model_provider if adapter_name == "custom" else None),
        model_id=body.model_id,
        api_key=api_key,
        api_base_url=api_base_url,
        is_builtin=False,
        is_orchestrator=body.is_orchestrator or False,
        supports_vision=body.supports_vision or False,
        created_at=now_ms(),
    )
    agent.capabilities_list = body.capabilities or []
    # Non-custom (CLI) adapters use their own built-in tool set;
    # force empty toolNames/skillNames.
    tool_names = (body.tool_names or []) if adapter_name == "custom" else []
    # Orchestrator agents require plan_tasks + ask_user tools.
    if body.is_orchestrator and adapter_name == "custom":
        for required_tool in ("plan_tasks", "ask_user"):
            if required_tool not in tool_names:
                tool_names.append(required_tool)
    agent.tool_names_list = tool_names
    agent.skill_names_list = (body.skill_names or []) if adapter_name == "custom" else []

    # CLI fields: only set for CLI-based adapters
    agent.executable_path = (
        _trim_or_none(body.executable_path) if adapter_name in ("claude-code", "codex") else None
    )
    agent.protocol_family = (
        adapter_name if adapter_name in ("claude-code", "codex") else None
    )
    agent.custom_args_list = (
        body.custom_args if adapter_name in ("claude-code", "codex") and body.custom_args else []
    )

    async with get_db() as db:
        db.add(agent)
        await db.flush()
        return _serialize(agent)


# ─── PATCH /api/agents/{id} ─────────────────────────────────────────
_PATCH_ALIASES: set[str] = {
    "name",
    "description",
    "capabilities",
    "systemPrompt",
    "adapterName",
    "modelProvider",
    "modelId",
    "toolNames",
    "skillNames",
    "supportsVision",
    "isOrchestrator",
    "apiKey",
    "apiBaseUrl",
    # CLI fields
    "executablePath",
    "protocolFamily",
    "customArgs",
}


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, request: Request) -> JSONResponse:
    """Update an agent (ports updateCustomAgent)."""
    try:
        raw = await request.json()
    except Exception:
        raw = None

    if not isinstance(raw, dict):
        return JSONResponse({"error": "Invalid body", "issues": []}, status_code=400)

    # TS uses .strict(): reject unknown keys (camelCase wire names).
    unknown = [k for k in raw if k not in _PATCH_ALIASES]
    if unknown:
        return JSONResponse(
            {
                "error": "Invalid body",
                "issues": [
                    {
                        "code": "unrecognized_keys",
                        "keys": unknown,
                        "path": [],
                        "message": (
                            f"Unrecognized key(s) in object: {', '.join(unknown)}"
                        ),
                    }
                ],
            },
            status_code=400,
        )

    try:
        body = UpdateAgentRequest.model_validate(raw)
    except ValidationError as exc:
        return _invalid_body(exc)

    # adapterName is part of the TS PATCH schema but absent from the Python
    # UpdateAgentRequest model; read it straight off the raw body.
    has_adapter_name = "adapterName" in raw
    adapter_name_patch = raw.get("adapterName") if has_adapter_name else None

    try:
        row = await _update_custom_agent(
            agent_id, body, has_adapter_name, adapter_name_patch
        )
    except ValueError as err:
        return JSONResponse({"error": str(err)}, status_code=400)

    return JSONResponse({"agent": row})


def _trim_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


async def _update_custom_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    has_adapter_name: bool,
    adapter_name_patch: str | None,
) -> dict[str, Any]:
    provided = body.model_fields_set
    has_api_key = "api_key" in provided
    has_api_base_url = "api_base_url" in provided
    has_model_id = "model_id" in provided
    has_model_provider = "model_provider" in provided
    has_tool_names = "tool_names" in provided
    has_skill_names = "skill_names" in provided
    has_is_orchestrator = "is_orchestrator" in provided
    has_executable_path = "executable_path" in provided
    has_protocol_family = "protocol_family" in provided
    has_custom_args = "custom_args" in provided

    async with get_db() as db:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        # Builtin agents may be reconfigured; only deletion is protected.

        next_adapter_name = (
            adapter_name_patch if has_adapter_name else agent.adapter_name
        )
        next_model_provider = (
            body.model_provider if has_model_provider else agent.model_provider
        )
        next_model_id = body.model_id if has_model_id else agent.model_id
        next_api_base_url = (
            _trim_or_none(body.api_base_url) if has_api_base_url else agent.api_base_url
        )
        next_api_key = _trim_or_none(body.api_key) if has_api_key else agent.api_key

        if next_adapter_name == "custom" and not (next_model_provider and next_model_id):
            raise ValueError("Custom adapter requires modelProvider and modelId")
        if next_adapter_name == "custom":
            base_url_error = validate_openai_compatible_base_url(
                next_model_provider, next_api_base_url
            )
            if base_url_error:
                raise ValueError(base_url_error)
            api_key_error = validate_openai_compatible_api_key(
                next_model_provider, next_api_key
            )
            if api_key_error:
                raise ValueError(api_key_error)

        updated = False

        if "name" in provided and body.name is not None:
            agent.name = body.name.strip()
            updated = True
        if "description" in provided and body.description is not None:
            agent.description = body.description.strip()
            updated = True
        if "capabilities" in provided and body.capabilities is not None:
            agent.capabilities_list = body.capabilities
            updated = True
        if "system_prompt" in provided and body.system_prompt is not None:
            agent.system_prompt = body.system_prompt
            updated = True
        if has_adapter_name:
            agent.adapter_name = adapter_name_patch  # type: ignore[assignment]
            updated = True
        if has_model_id:
            agent.model_id = _trim_or_none(body.model_id)
            updated = True
        if "supports_vision" in provided and body.supports_vision is not None:
            agent.supports_vision = body.supports_vision
            updated = True
        if has_is_orchestrator and body.is_orchestrator is not None:
            agent.is_orchestrator = body.is_orchestrator
            updated = True
        if has_api_key:
            agent.api_key = _trim_or_none(body.api_key)
            updated = True
        if has_api_base_url:
            agent.api_base_url = _trim_or_none(body.api_base_url)
            updated = True
        # CLI fields
        is_cli = next_adapter_name in ("claude-code", "codex")
        if has_executable_path:
            agent.executable_path = _trim_or_none(body.executable_path) if is_cli else None
            updated = True
        if has_protocol_family:
            agent.protocol_family = body.protocol_family if is_cli else None
            updated = True
        if has_custom_args and body.custom_args is not None:
            agent.custom_args_list = body.custom_args if is_cli else []
            updated = True

        if next_adapter_name == "custom":
            if has_model_provider:
                agent.model_provider = body.model_provider
                updated = True
            if has_tool_names and body.tool_names is not None:
                agent.tool_names_list = body.tool_names
                updated = True
            if has_skill_names and body.skill_names is not None:
                agent.skill_names_list = body.skill_names
                updated = True
        else:
            # Non-custom (CLI) adapter: drop modelProvider/toolNames/skillNames.
            # modelId is still relevant (CLI agents pass --model <id>).
            if has_adapter_name or has_model_provider or has_tool_names or has_skill_names:
                agent.model_provider = None
                agent.tool_names_list = []
                agent.skill_names_list = []
                updated = True

        if not updated:
            return _serialize(agent)

        await db.flush()
        await db.refresh(agent)
        return _serialize(agent)


# ─── DELETE /api/agents/{id} ────────────────────────────────────────
@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str) -> JSONResponse:
    """Delete a non-builtin agent (ports deleteCustomAgent)."""
    try:
        await _delete_custom_agent(agent_id)
    except ValueError as err:
        return JSONResponse({"error": str(err)}, status_code=400)
    return JSONResponse({"ok": True})


async def _delete_custom_agent(agent_id: str) -> None:
    async with get_db() as db:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        if agent.is_builtin:
            raise ValueError("Built-in agents cannot be deleted")
        await db.delete(agent)
        await db.flush()


# ─── POST /api/agents/draft ─────────────────────────────────────────
# Ports src/server/agent-draft-service.ts + the heuristics in
# src/shared/agent-builder-config.ts. Deterministic — no LLM call.

_DEFAULT_PROVIDER = "deepseek"

_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "deepseek": {"label": "DeepSeek", "defaultModel": "deepseek-v4-flash"},
    "anthropic": {"label": "Anthropic", "defaultModel": "claude-opus-4-7"},
    "openai": {"label": "OpenAI", "defaultModel": "gpt-4o"},
    "volcano-ark": {"label": "火山方舟 (豆包)", "defaultModel": "doubao-seed-2-0-lite-260428"},
    "openai-compatible": {"label": "OpenAI-compatible", "defaultModel": ""},
}

_AVAILABLE_AGENT_TOOLS: tuple[str, ...] = (
    "write_artifact",
    "deploy_artifact",
    "deploy_workspace",
    "read_artifact",
    "read_attachment",
    "ask_user",
    "plan_tasks",
    "fs_list",
    "fs_read",
    "fs_write",
    "fs_edit",
    "fs_grep",
    "fs_glob",
    "bash",
    "web_search",
)

# Tools included by the "全栈通用" preset. web_search is intentionally excluded so it
# stays opt-in (avoids silently spending Tavily credits on every all-purpose agent).
_ALL_PURPOSE_TOOLS: list[str] = [
    "write_artifact",
    "deploy_artifact",
    "deploy_workspace",
    "read_artifact",
    "read_attachment",
    "ask_user",
    "fs_list",
    "fs_read",
    "fs_write",
    "fs_edit",
    "fs_grep",
    "fs_glob",
    "bash",
]

# ─── System prompt templates per role (shared 6-principle scaffold) ─
_PROMPT_ALL_PURPOSE = """你是一个 AChat custom agent。你的任务是理解用户目标，使用已启用的工具完成工作，并把结果清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；只有在用户提到附件、已有产物或工作区文件时，才调用对应读取工具。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 产出代码、网页、文档或设计稿时，优先用 write_artifact 创建结构化产物；网页产物完成后再调用 deploy_artifact。
5. 探索项目目录时优先用 fs_list，再用 fs_read 读取具体文件；使用 fs_write 或 bash 前确认确有必要，并只在当前 workspace 范围内操作。
6. 最终回复保持简洁，说明完成了什么、产物在哪里、还剩什么需要用户决策。"""

_PROMPT_LOCAL_CODE = """你是一名本地代码开发与调试工程师。你的任务是理解用户在当前 workspace 的代码目标，使用已启用的工具直接修改源码、运行命令，并把可验证的结果交付给用户。

工作原则：
1. 先判断需要什么上下文；用 fs_list 探索项目结构，用 fs_read 读取相关源码，用 fs_grep 搜索符号与引用，用 fs_glob 定位文件；用户提到附件或已有产物时才调用对应读取工具。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 修改源码时优先用 fs_edit 做精确局部替换（old_string 必须唯一），大段新建或全量重写才用 fs_write；不要用 write_artifact 代替源码落盘。
5. 改动前先读目标文件确认当前内容；执行 bash 命令前确认确有必要且只在当前 workspace 范围内操作；改完用 bash 跑测试或构建验证。
6. 最终回复保持简洁，说明改了哪些文件、命令结果如何、还剩什么需要用户决策。"""

_PROMPT_ARTIFACT = """你是一名产物交付工程师。你的任务是理解用户想交付的产物目标，使用已启用的工具创建可预览的网页、文档或原型，并把结构化产物清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 read_artifact 查看已有产物以便在其基础上迭代，用户提到附件时用 read_attachment；本角色一般不直接读 workspace 源码。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 产出网页、文档、原型或设计稿时，优先用 write_artifact 创建结构化产物；网页产物完成后再调用 deploy_artifact 生成预览链接；支持多版本迭代。
5. 本角色不直接修改 workspace 源码文件；如需读取工作区静态目录可用 deploy_workspace 生成预览。
6. 最终回复保持简洁，说明产出了什么、预览链接在哪里、还剩什么需要用户决策。"""

_PROMPT_REVIEW = """你是一名代码与产物审查员。你的任务是理解审查范围，使用已启用的只读工具检查代码或产物，并把发现的风险与建议清晰交付给用户。

工作原则：
1. 先判断需要审查什么；用 read_artifact 查看产物，用 fs_list/fs_read 查看源码，用 fs_grep 搜索可疑模式；用户给附件时用 read_attachment。
2. 多步骤审查先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 审查结论优先用 write_artifact 产出结构化报告；本角色不创建业务代码或产物，只产出审查意见。
5. 本角色只读不写：不使用 fs_write/fs_edit 修改任何文件；bash 仅用于运行只读检查命令（lint/typecheck/test），不得有副作用。
6. 最终回复保持简洁，说明发现了什么风险、严重程度、建议如何处理。"""

_PROMPT_TECH_WRITING = """你是一名技术文档工程师。你的任务是理解用户想交付的文档目标，使用已启用的工具采集准确信息，并把结构化文档清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用户提到源码、API 或已有产物时，用 fs_list/fs_glob 定位文件，fs_read 读取实现，fs_grep 搜索特定符号或注释；用户给附件时用 read_attachment。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 产出文档时优先用 write_artifact 创建结构化产物；面向读者组织结构，所有 API、路径、行为描述必须来自源码实测，不得臆造。
5. 引用源码时写明文件路径与行号范围；探索项目目录时优先用 fs_list，再用 fs_read 读取具体文件；本角色不修改源码。
6. 最终回复保持简洁，说明文档覆盖了什么、产物在哪里、还剩什么需要用户确认。"""

_PROMPT_TESTING_QA = """你是一名测试工程师。你的任务是理解待测目标，使用已启用的工具编写测试、运行验证、定位回归，并把测试结果与覆盖情况清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 fs_grep 搜索现有测试覆盖与断言，用 fs_read 读取待测实现，用 fs_list/fs_glob 定位测试目录；用户提到已有产物时用 read_artifact。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 编写测试用例用 fs_write 创建测试文件，测试报告用 write_artifact 产出结构化产物；优先覆盖边界、异常与回归路径。
5. 用 bash 运行测试/lint 命令验证；fs_write 仅限创建测试文件，不修改业务源码；所有操作只在当前 workspace 范围内。
6. 最终回复保持简洁，说明覆盖了什么、哪些用例失败、建议如何修复。"""

_PROMPT_FRONTEND_DESIGN = """你是一名前端工程师与设计师。你的任务是理解用户的前端交付目标，使用已启用的工具创建 UI 产物、修改前端源码，并把可预览的结果清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 fs_list/fs_glob 定位组件与样式文件，用 fs_read 读取现有实现，用 fs_grep 搜索样式或组件引用；用户提到已有产物时用 read_artifact。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 创建可预览的网页/原型用 write_artifact，完成后调用 deploy_artifact 生成预览；修改前端源码用 fs_edit 做精确替换或 fs_write 新建组件。
5. 改动前先读目标文件确认当前内容；遵循组件化、响应式与可访问性（a11y）原则；所有操作只在当前 workspace 范围内。
6. 最终回复保持简洁，说明改了哪些文件或产出了什么、预览链接在哪里、还剩什么需要用户决策。"""

_PROMPT_RESEARCHER = """你是一名调研分析师。你的任务是理解用户的调研目标，使用已启用的工具联网搜索、交叉验证，并把结构化调研报告清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 web_search 搜索公网获取实时信息，用户给参考资料时用 read_attachment；本角色不直接读 workspace 源码。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 调研结论优先用 write_artifact 产出结构化报告；多源交叉验证，标注来源与时效性，区分事实与推测。
5. 本角色不使用 fs_*/bash 等本地代码工具；所有信息来自 web_search 与用户提供的附件。
6. 最终回复保持简洁，说明调研了什么、关键结论、信息来源与时效、还剩什么需要用户确认。"""

_PROMPT_DATA_ANALYSIS = """你是一名数据分析师。你的任务是理解用户的数据分析目标，使用已启用的工具清洗数据、运行处理脚本、生成图表，并把分析结论清晰交付给用户。

工作原则：
1. 先判断需要什么上下文；用 read_attachment 读取用户上传的 csv/json 数据，用 fs_list/fs_glob 定位工作区数据文件，用 fs_read 读取已有脚本。
2. 多步骤任务先给自己形成简短计划，但不要把固定流程强加给简单问题。
3. 工具调用要少而准确；每次调用都应服务于当前目标。
4. 数据清洗与处理脚本用 fs_write 创建，处理结果与图表用 write_artifact 产出结构化产物；所有结论必须基于实际数据，不得臆造。
5. 用 bash 运行处理脚本验证结果；数据清洗优先于分析；标注样本量与局限性；所有操作只在当前 workspace 范围内。
6. 最终回复保持简洁，说明分析了什么、关键结论、数据来源与局限、还剩什么需要用户决策。"""

_AGENT_TOOL_PRESETS: dict[str, dict[str, Any]] = {
    "all-purpose": {
        "label": "全栈通用",
        # _ALL_PURPOSE_TOOLS already excludes plan_tasks (Orchestrator-only) and
        # web_search (opt-in, consumes Tavily credits).
        "tools": list(_ALL_PURPOSE_TOOLS),
        "systemPromptTemplate": _PROMPT_ALL_PURPOSE,
    },
    "local-code": {
        "label": "本地代码",
        "tools": [
            "deploy_workspace",
            "read_artifact",
            "read_attachment",
            "ask_user",
            "fs_list",
            "fs_read",
            "fs_write",
            "fs_edit",
            "fs_grep",
            "fs_glob",
            "bash",
        ],
        "systemPromptTemplate": _PROMPT_LOCAL_CODE,
    },
    "artifact": {
        "label": "产物交付",
        "tools": [
            "write_artifact",
            "deploy_artifact",
            "deploy_workspace",
            "read_artifact",
            "read_attachment",
            "ask_user",
        ],
        "systemPromptTemplate": _PROMPT_ARTIFACT,
    },
    "review": {
        "label": "审查验证",
        "tools": ["read_artifact", "read_attachment", "ask_user", "fs_list", "fs_read", "bash"],
        "systemPromptTemplate": _PROMPT_REVIEW,
    },
    "tech-writing": {
        "label": "技术写作",
        "tools": [
            "write_artifact",
            "read_artifact",
            "read_attachment",
            "ask_user",
            "fs_read",
            "fs_list",
            "fs_glob",
            "fs_grep",
        ],
        "systemPromptTemplate": _PROMPT_TECH_WRITING,
    },
    "testing-qa": {
        "label": "测试 QA",
        "tools": [
            "bash",
            "fs_read",
            "fs_list",
            "fs_glob",
            "fs_grep",
            "fs_write",
            "read_artifact",
            "ask_user",
            "write_artifact",
        ],
        "systemPromptTemplate": _PROMPT_TESTING_QA,
    },
    "frontend-design": {
        "label": "前端/设计",
        "tools": [
            "write_artifact",
            "deploy_artifact",
            "read_artifact",
            "ask_user",
            "fs_read",
            "fs_list",
            "fs_glob",
            "fs_grep",
            "fs_write",
            "fs_edit",
        ],
        "systemPromptTemplate": _PROMPT_FRONTEND_DESIGN,
    },
    "researcher": {
        "label": "调研员",
        "tools": [
            "web_search",
            "ask_user",
            "read_attachment",
            "write_artifact",
            "read_artifact",
        ],
        "systemPromptTemplate": _PROMPT_RESEARCHER,
    },
    "data-analysis": {
        "label": "数据分析",
        "tools": [
            "bash",
            "fs_read",
            "fs_write",
            "fs_list",
            "fs_glob",
            "read_attachment",
            "write_artifact",
            "ask_user",
        ],
        "systemPromptTemplate": _PROMPT_DATA_ANALYSIS,
    },
}

_AGENT_TOOL_META: dict[str, dict[str, str]] = {
    "write_artifact": {
        "label": "创建产物",
        "desc": "生成可预览的代码 / 网页 / 文档 / PPT，支持多版本迭代",
    },
    "deploy_artifact": {
        "label": "部署网页",
        "desc": "把网页产物发布为本地静态站点，生成预览链接与下载包",
    },
    "deploy_workspace": {
        "label": "部署目录",
        "desc": "把工作区内 dist/build/out 等静态目录生成预览链接与下载包",
    },
    "read_artifact": {
        "label": "读取产物",
        "desc": "查看会话中已有产物的完整内容，便于在其基础上继续改",
    },
    "read_attachment": {"label": "读取附件", "desc": "读取用户上传的文本 / 文件附件内容"},
    "ask_user": {
        "label": "结构化提问",
        "desc": "让用户在明确选项中选择，用于范围、风格、平台等关键澄清",
    },
    "plan_tasks": {
        "label": "任务规划",
        "desc": "Orchestrator 专用：拆解用户目标为子任务并分派给其他 Agent",
    },
    "fs_list": {"label": "列出文件", "desc": "列出工作区内的目录和文件，用于安全探索项目结构"},
    "fs_read": {"label": "读取文件", "desc": "读取工作区内的文件（源码 / 配置等），仅限沙箱目录"},
    "fs_write": {"label": "写入文件", "desc": "在工作区内新建 / 修改文件；review 模式下需用户批准"},
    "fs_edit": {"label": "编辑文件", "desc": "精确替换文件中的唯一文本片段；review 模式下 diff 只高亮改的行"},
    "fs_grep": {"label": "搜索文本", "desc": "用正则在 workspace 文件中搜索，返回结构化匹配结果；跳过二进制和依赖目录"},
    "fs_glob": {"label": "查找文件", "desc": "用 glob 模式递归查找文件（如 **/*.tsx），返回路径和大小"},
    "bash": {"label": "执行命令", "desc": "在工作区内运行命令行；受命令黑名单与沙箱目录约束"},
    "web_search": {
        "label": "联网搜索",
        "desc": "用 Tavily 搜索公网获取实时信息；调用会消耗 Tavily 额度",
    },
}


class AgentDraftRequest(BaseModel):
    """Body for POST /api/agents/draft (mirrors AgentDraftRequestSchema).

    zod applies ``.trim()`` BEFORE the length checks, so trim first then bound.
    """

    intent: str = Field(min_length=6, max_length=4000)
    follow_up: str | None = Field(default=None, max_length=2000, alias="followUp")

    model_config = {"populate_by_name": True}

    @field_validator("intent", "follow_up", mode="before")
    @classmethod
    def _trim(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, max_chars: int) -> str:
    chars = list(text)
    if len(chars) <= max_chars:
        return text
    return "".join(chars[: max_chars - 1]) + "…"


def _clean_name(text: str) -> str:
    return re.sub(r"[「」“”\"']", "", text).strip()


def _normalize_agent_tool_names(tool_names: list[str]) -> list[str]:
    allowed = set(_AVAILABLE_AGENT_TOOLS)
    seen: set[str] = set()
    out: list[str] = []
    for name in tool_names:
        if name not in allowed or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _build_tool_permission_summaries(tool_names: list[str]) -> list[dict[str, str]]:
    return [
        {"toolName": name, **_AGENT_TOOL_META[name]}
        for name in _normalize_agent_tool_names(tool_names)
    ]


def _infer_agent_tool_preset(intent: str, follow_up: str) -> str:
    text = f"{intent}\n{follow_up}".lower()
    wants_to_write = bool(
        re.search(r"写|实现|开发|生成|创建|搭建|部署|build|implement|create|write|ship", text)
        or re.search(r"修改(?!建议)", text)
    )
    wants_review = bool(
        re.search(r"审查|评审|检查|验证|验收|风险|review|audit|inspect|validate|verify", text)
    )
    if wants_review and not wants_to_write:
        return "review"
    # Specific roles — checked before general roles to avoid overlap
    # (e.g. "测试" should match testing-qa, not local-code).
    if re.search(r"调研|联网搜索|搜索公网|market.?research|竞品|research|文献综述", text):
        return "researcher"
    if re.search(r"数据分析|数据清洗|数据可视化|统计|csv|excel|data.?analy|数据处理", text):
        return "data-analysis"
    if re.search(r"技术文档|api文档|写文档|tech.?writ|documentation|文档工程师", text):
        return "tech-writing"
    if re.search(r"测试|qa|用例|断言|test|回归|覆盖率", text):
        return "testing-qa"
    if re.search(r"前端|ui设计|界面|样式|css|react|vue|组件|frontend|web.?design|交互设计", text):
        return "frontend-design"
    if re.search(
        r"代码|源码|仓库|本地|文件|命令|终端|测试|修复|重构|调试|"
        r"workspace|repo|repository|code|cli|bash|test|lint|debug|refactor",
        text,
    ):
        return "local-code"
    if re.search(
        r"产物|网页|页面|原型|文档|报告|幻灯片|演示|图示|图表|设计稿|"
        r"ppt|slides|presentation|website|document|diagram|mermaid|prototype",
        text,
    ):
        return "artifact"
    return "all-purpose"


def _infer_agent_name(text: str, preset_id: str) -> str:
    match = re.search(
        r"(?:叫|命名为|名字叫|名称(?:是|为)?|name(?:d)?\s*)"
        r"(?:「|“|\"|')?([^，,。.\n\"”』']{2,24})",
        text,
    )
    if match:
        return _truncate(_clean_name(match.group(1)), 64)

    lower = text.lower()
    if re.search(r"ppt|幻灯片|演示|presentation|slides", lower):
        return "PPT 设计师"
    if re.search(r"图示|图表|流程图|mermaid|diagram", lower):
        return "图示架构师"
    if re.search(r"文档|报告|document|report", lower):
        return "文档写作助手"
    if re.search(r"网页|页面|原型|website|prototype|landing", lower):
        return "网页原型助手"

    return {
        "local-code": "代码工程师",
        "artifact": "产物设计师",
        "review": "审查验证助手",
        "all-purpose": "专属助手",
        "tech-writing": "技术文档工程师",
        "testing-qa": "测试工程师",
        "frontend-design": "前端工程师",
        "researcher": "调研分析师",
        "data-analysis": "数据分析师",
    }[preset_id]


def _infer_description(text: str, preset_id: str) -> str:
    target = _truncate(text, 72)
    prefix = {
        "local-code": "围绕本地代码与命令行任务提供实现、修改和验证支持",
        "artifact": "围绕网页、文档、PPT 等产物提供规划、生成和迭代支持",
        "review": "围绕已有产物或代码提供审查、验证和风险发现",
        "tech-writing": "围绕技术文档采集与结构化文档交付提供支持",
        "testing-qa": "围绕测试用例编写、运行验证与回归定位提供支持",
        "frontend-design": "围绕前端 UI 产物与源码修改提供设计和实现支持",
        "researcher": "围绕联网搜索与交叉验证提供结构化调研报告",
        "data-analysis": "围绕数据清洗、处理脚本与分析结论提供支持",
    }.get(preset_id, "围绕用户目标提供规划、执行和交付支持")
    return _truncate(f"{prefix}：{target}", 280)


def _infer_capabilities(text: str, preset_id: str) -> list[str]:
    lower = text.lower()
    capabilities = {
        "local-code": ["代码实现", "本地验证", "命令行"],
        "artifact": ["产物交付", "内容生成", "原型设计"],
        "review": ["审查验证", "风险发现", "改进建议"],
        "tech-writing": ["文档交付", "源码采集", "结构化写作"],
        "testing-qa": ["测试编写", "运行验证", "回归定位"],
        "frontend-design": ["UI 设计", "前端实现", "产物交付"],
        "researcher": ["联网搜索", "交叉验证", "调研报告"],
        "data-analysis": ["数据清洗", "统计分析", "图表生成"],
    }.get(preset_id, ["需求澄清", "任务执行", "交付自检"])
    capabilities = list(capabilities)

    if re.search(r"ppt|幻灯片|演示|presentation|slides", lower):
        capabilities.append("PPT")
    if re.search(r"图示|图表|mermaid|diagram", lower):
        capabilities.append("图示")
    if re.search(r"网页|页面|website|prototype|landing", lower):
        capabilities.append("网页")
    if re.search(r"图片|截图|视觉|image|screenshot|visual", lower):
        capabilities.append("视觉理解")

    deduped: list[str] = []
    for cap in capabilities:
        if cap not in deduped:
            deduped.append(cap)
    return deduped[:8]


def _build_system_prompt(
    name: str,
    intent: str,
    follow_up: str,
    preset_label: str,
    permission_summaries: list[dict[str, str]],
) -> str:
    permission_line = "、".join(
        f"{s['label']}({s['toolName']})" for s in permission_summaries
    )
    lines = [
        f"你是 {name}。",
        "",
        f"用户创建你的目标：{intent}",
        f"补充偏好：{follow_up}" if follow_up else "",
        "",
        "工作方式：",
        "- 先判断用户真正想完成的交付物、约束和验收标准。",
        "- 信息不足时，优先使用结构化提问澄清关键选择；不要假装已经知道用户偏好。",
        "- 执行前简要说明计划，执行中保持结果可检查，交付前做自检。",
        "- 涉及文件写入、命令执行或部署时，明确说明影响范围和结果。",
        "",
        f"默认工具策略：{preset_label}。可用权限包括：{permission_line or 'SDK 内置工具集'}。",
        "不要尝试使用未授权工具；普通自建 Agent 不承担 Orchestrator 的任务拆分职责。",
    ]
    return "\n".join(line for line in lines if line != "")  # noqa: PLC1901


def build_heuristic_agent_config_draft(
    intent_raw: str, follow_up_raw: str | None
) -> dict[str, Any]:
    intent = _normalize_text(intent_raw)
    follow_up = _normalize_text(follow_up_raw or "")
    combined = "\n".join(x for x in (intent, follow_up) if x)
    preset_id = _infer_agent_tool_preset(intent, follow_up)
    preset = _AGENT_TOOL_PRESETS[preset_id]
    name = _infer_agent_name(combined, preset_id)
    capabilities = _infer_capabilities(combined, preset_id)
    permission_summaries = _build_tool_permission_summaries(preset["tools"])

    provider_label = _PROVIDER_DEFAULTS[_DEFAULT_PROVIDER]["label"]
    provider_model = _PROVIDER_DEFAULTS[_DEFAULT_PROVIDER]["defaultModel"]

    return {
        "name": name,
        "avatar": "🤖",
        "description": _infer_description(combined, preset_id),
        "capabilities": capabilities,
        "systemPrompt": preset["systemPromptTemplate"],
        "adapterName": "custom",
        "modelProvider": _DEFAULT_PROVIDER,
        "modelId": provider_model,
        "toolNames": [s["toolName"] for s in permission_summaries],
        "supportsVision": True,
        "rationale": [
            f"根据描述匹配到「{preset['label']}」工具预设。",
            "按普通自建 Agent 生成，不包含 Orchestrator 专用工具。",
            "最终保存仍会走现有 Agent 创建接口，保存前可切到详细配置继续调整。",
        ],
        "assumptions": [
            {
                "label": "模型",
                "detail": (
                    f"默认使用 {provider_label} / {provider_model}，"
                    "可在详细配置中改成其他 provider。"
                ),
            },
            {
                "label": "视觉",
                "detail": (
                    "默认开启视觉能力，方便处理截图、设计稿、图示和图片附件；"
                    "如果模型不支持可在详细配置中关闭。"
                ),
            },
            {
                "label": "权限",
                "detail": (
                    f"工具权限来自「{preset['label']}」预设，"
                    "保存前会逐项展示，可切到详细配置增减。"
                ),
            },
        ],
        "toolPermissionSummaries": permission_summaries,
    }


@router.post("/agents/draft")
async def draft_agent(request: Request) -> JSONResponse:
    """Build a heuristic agent-config draft (ports createAgentConfigDraft)."""
    try:
        raw = await request.json()
    except Exception:
        raw = None

    if not isinstance(raw, dict):
        return JSONResponse({"error": "Invalid body", "issues": []}, status_code=400)

    try:
        body = AgentDraftRequest.model_validate(raw)
    except ValidationError as exc:
        return _invalid_body(exc)

    draft = build_heuristic_agent_config_draft(body.intent, body.follow_up)
    return JSONResponse({"draft": draft})
