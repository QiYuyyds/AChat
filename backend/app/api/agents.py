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

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import or_, select

from app.adapters.custom_provider_client import (
    validate_openai_compatible_api_key,
    validate_openai_compatible_base_url,
)
from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import Agent, User
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
        "mcpServerIds": row.mcp_server_ids_list,
        "isBuiltin": row.is_builtin,
        "isOrchestrator": row.is_orchestrator,
        "isGuide": row.is_guide,
        "supportsVision": row.supports_vision,
        "memoryEnabled": row.memory_enabled,
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
async def list_agents(user: User = Depends(get_current_user)) -> JSONResponse:
    """List agents: builtin first, then newest first (matches listAgentsOrdered)."""
    async with get_db() as db:
        result = await db.execute(
            select(Agent)
            .where(or_(Agent.user_id.is_(None), Agent.user_id == user.id))
            .order_by(
                Agent.is_builtin.desc(),
                Agent.created_at.desc(),
            )
        )
        rows = result.scalars().all()
        return JSONResponse({"agents": [_serialize(r) for r in rows]})


# ─── POST /api/agents ───────────────────────────────────────────────
@router.post("/agents")
async def create_agent(request: Request, user: User = Depends(get_current_user)) -> JSONResponse:
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
        row = await _create_custom_agent(body, user.id)
    except ValueError as err:
        return JSONResponse({"error": str(err)}, status_code=400)

    return JSONResponse({"agent": row}, status_code=201)


async def _create_custom_agent(body: CreateAgentRequest, user_id: str) -> dict[str, Any]:
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
        user_id=user_id,
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
        memory_enabled=body.memory_enabled or False,
        created_at=now_ms(),
    )
    agent.capabilities_list = body.capabilities or []
    # Non-custom (CLI) adapters use their own built-in tool set;
    # force empty toolNames/skillNames.
    tool_names = (body.tool_names or []) if adapter_name == "custom" else []
    # Orchestrator agents require ask_user tool; task_dispatch is auto-injected
    # at runtime by the coordinated agent loop.
    if body.is_orchestrator and adapter_name == "custom":
        if "ask_user" not in tool_names:
            tool_names.append("ask_user")
    agent.tool_names_list = tool_names
    agent.skill_names_list = (body.skill_names or []) if adapter_name == "custom" else []
    agent.mcp_server_ids_list = (body.mcp_server_ids or []) if adapter_name == "custom" else []

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
        result = _serialize(agent)

    from app.infra.cache_helpers import invalidate_agent_cache
    await invalidate_agent_cache(agent.id)
    return result


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
    "mcpServerIds",
    "supportsVision",
    "isOrchestrator",
    "memoryEnabled",
    "apiKey",
    "apiBaseUrl",
    # CLI fields
    "executablePath",
    "protocolFamily",
    "customArgs",
}


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, request: Request, user: User = Depends(get_current_user)) -> JSONResponse:
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
            agent_id, body, has_adapter_name, adapter_name_patch, user.id
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
    user_id: str,
) -> dict[str, Any]:
    provided = body.model_fields_set
    has_api_key = "api_key" in provided
    has_api_base_url = "api_base_url" in provided
    has_model_id = "model_id" in provided
    has_model_provider = "model_provider" in provided
    has_tool_names = "tool_names" in provided
    has_skill_names = "skill_names" in provided
    has_mcp_server_ids = "mcp_server_ids" in provided
    has_is_orchestrator = "is_orchestrator" in provided
    has_executable_path = "executable_path" in provided
    has_protocol_family = "protocol_family" in provided
    has_custom_args = "custom_args" in provided

    async with get_db() as db:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        if agent.user_id is not None and agent.user_id != user_id:
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
        if "memory_enabled" in provided and body.memory_enabled is not None:
            agent.memory_enabled = body.memory_enabled
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
            if has_mcp_server_ids and body.mcp_server_ids is not None:
                agent.mcp_server_ids_list = body.mcp_server_ids
                updated = True
        else:
            # Non-custom (CLI) adapter: drop modelProvider/toolNames/skillNames.
            # modelId is still relevant (CLI agents pass --model <id>).
            if has_adapter_name or has_model_provider or has_tool_names or has_skill_names or has_mcp_server_ids:
                agent.model_provider = None
                agent.tool_names_list = []
                agent.skill_names_list = []
                agent.mcp_server_ids_list = []
                updated = True

        if not updated:
            return _serialize(agent)

        await db.flush()
        await db.refresh(agent)
        result = _serialize(agent)

    from app.infra.cache_helpers import invalidate_agent_cache
    await invalidate_agent_cache(agent_id)
    return result


# ─── DELETE /api/agents/{id} ────────────────────────────────────────
@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Delete a non-builtin agent (ports deleteCustomAgent)."""
    try:
        await _delete_custom_agent(agent_id, user.id)
    except ValueError as err:
        return JSONResponse({"error": str(err)}, status_code=400)
    return JSONResponse({"ok": True})


async def _delete_custom_agent(agent_id: str, user_id: str) -> None:
    async with get_db() as db:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        if agent.user_id is not None and agent.user_id != user_id:
            raise ValueError(f"Agent not found: {agent_id}")
        if agent.is_builtin:
            raise ValueError("Built-in agents cannot be deleted")
        await db.delete(agent)
        await db.flush()

    from app.infra.cache_helpers import invalidate_agent_cache
    await invalidate_agent_cache(agent_id)


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

# Baseline tools always enabled for every Custom adapter agent at runtime.
# These are NOT shown as UI checkboxes — they are implicitly always-on.
# SDK agents (Claude Code / Codex) use their own CLI built-in tools and are unaffected.
_BASELINE_AGENT_TOOLS: tuple[str, ...] = (
    "read_attachment",
    "ask_user",
    "fs_list",
    "fs_read",
    "fs_write",
    "fs_edit",
    "fs_grep",
    "fs_glob",
    "bash",
)

# UI-selectable tools for Custom adapter agents. Only these 5 appear as
# checkboxes in the create/edit agent dialog. Baseline tools are merged at
# runtime by agent_runner.py and are not selectable.
_AVAILABLE_AGENT_TOOLS: tuple[str, ...] = (
    "write_artifact",
    "deploy_artifact",
    "deploy_workspace",
    "read_artifact",
    "web_search",
)

# ─── System prompt templates per role (4-role: coder/researcher/orchestrator/writer) ─
# Each template covers exactly 4 areas: role positioning, production strategy,
# behavior constraints, quality standards. Tool-specific usage instructions,
# multi-step plan guidance, and task dispatch guidance are handled by separate
# prompt layers (_build_agent_hub_tool_guidance, _PLAN_SUFFIX,
# _COORDINATED_PROMPT_SUFFIX) and MUST NOT appear here.
_PROMPT_CODER = """你是一名程序员。你的核心职责是在当前 workspace 内直接修改源码、运行命令、验证结果，把可工作的代码交付给用户。

产出策略：
- 代码改动直接落盘到 workspace 文件，不做成 artifact。
- 构建出 dist/build/out 等静态目录时，用 deploy_workspace 生成预览卡方便用户查看。
- 需要参考上游产物（PRD、设计稿、现有代码片段）时用 read_artifact 读取。

行为约束：
- 改动前先读目标文件确认当前内容，不要凭记忆盲改。
- 精确局部修改优先用 fs_edit；大段新建或全量重写才用 fs_write。
- 命令执行前确认确有必要，且只在当前 workspace 范围内操作。

质量标准：
- 改完跑必要的验证命令（typecheck / build / test），让结果说话。
- 最终回复说明改了哪些文件、验证结果如何、还剩什么需要用户决策。"""

_PROMPT_RESEARCHER = """你是一名调研员。你的核心职责是联网搜索、交叉验证、产出结构化调研报告，帮用户做决策。

产出策略：
- 用 web_search 获取公网实时信息，多源交叉验证，不要单源下结论。
- 调研结论用 write_artifact 产出结构化报告，方便用户保存与分享。
- 用户提到已有报告或参考资料时，用 read_artifact 读取后在其基础上迭代。

行为约束：
- 区分事实与推测：事实标注来源与时效，推测写明依据与不确定性。
- 信息不足时用 ask_user 澄清范围，不要臆造数据或引用。
- 联网搜索无结果时如实说明，不要编造来源。

质量标准：
- 报告结构清晰：背景 / 关键发现 / 对比分析 / 结论与建议。
- 所有引用可追溯：标注链接、发布时间、检索日期。
- 最终回复概括关键结论、信息来源与时效、还剩什么需要用户确认。"""

_PROMPT_ORCHESTRATOR = """你是一名协调者。你的核心职责是在群聊中拆解任务、派发给合适的 Agent、聚合结果，自己不直接执行业务工作。

产出策略：
- 收到用户目标后先判断哪些子任务可以并行、哪些有依赖，再派发。
- 子任务产物用 read_artifact 读取后聚合，最终结论用 write_artifact 产出汇总报告。
- 自己不写业务代码、不直接修改 workspace 文件；把执行交给子 Agent。

行为约束：
- 优先派发给群内已有对口 Agent；没有合适的再克隆自己处理。
- 子任务描述要清晰、可独立执行，包含目标、输入、验收标准。
- 子任务失败时聚合失败原因并给出下一步建议，不要静默重试。

质量标准：
- 聚合报告覆盖所有子任务的结论与产物引用，不要漏掉。
- 标注哪些子任务成功、哪些失败、哪些需要用户决策。
- 最终回复概括整体进度、关键产物位置、还剩什么需要用户介入。"""

_PROMPT_WRITER = """你是一名写作工程师。你的核心职责是采集信息、产出结构化文字产物，覆盖技术文档、内容文案、审查报告、网页原型四类场景。

产出策略：
- 技术文档：从源码实测 API、路径与行为，用 write_artifact 产出结构化文档。
- 内容文案：围绕目标读者组织结构，用 write_artifact 产出可分享的内容。
- 审查报告：用 read_artifact 读取被审查产物，用 write_artifact 产出审查意见；不修改被审查对象。
- 网页原型：用 write_artifact 创建 web_app，完成后用 deploy_artifact 生成预览链接。

行为约束：
- 引用源码或产物时写明文件路径、行号范围或 artifactId，不要凭记忆描述。
- 审查场景下 bash 仅用于运行只读检查命令（lint/typecheck/test），不修改被审查的代码或产物。
- 所有描述必须来自实测，不得臆造 API、路径或行为。

质量标准：
- 产物结构面向读者：目录 / 摘要 / 正文 / 附录清晰分层。
- 网页原型符合组件化、响应式与可访问性（a11y）原则。
- 最终回复说明产出了什么、预览链接在哪里、还剩什么需要用户决策。"""

_AGENT_TOOL_PRESETS: dict[str, dict[str, Any]] = {
    "coder": {
        "label": "程序员",
        "tools": ["deploy_workspace", "read_artifact"],
        "systemPromptTemplate": _PROMPT_CODER,
    },
    "researcher": {
        "label": "调研员",
        "tools": ["write_artifact", "read_artifact", "web_search"],
        "systemPromptTemplate": _PROMPT_RESEARCHER,
    },
    "orchestrator": {
        "label": "协调者",
        "tools": ["write_artifact", "read_artifact"],
        "systemPromptTemplate": _PROMPT_ORCHESTRATOR,
    },
    "writer": {
        "label": "写作",
        "tools": ["write_artifact", "deploy_artifact", "read_artifact"],
        "systemPromptTemplate": _PROMPT_WRITER,
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
    "web_search": {
        "label": "联网搜索",
        "desc": "用 Tavily 搜索公网获取实时信息；调用会消耗 Tavily 额度",
    },
}

_BASELINE_AGENT_TOOL_META: dict[str, dict[str, str]] = {
    "read_attachment": {"label": "读取附件", "desc": "读取用户上传的文本 / 文件附件内容"},
    "ask_user": {
        "label": "结构化提问",
        "desc": "让用户在明确选项中选择，用于范围、风格、平台等关键澄清",
    },
    "fs_list": {"label": "列出文件", "desc": "列出工作区内的目录和文件，用于安全探索项目结构"},
    "fs_read": {"label": "读取文件", "desc": "读取工作区内的文件（源码 / 配置等），仅限沙箱目录"},
    "fs_write": {"label": "写入文件", "desc": "在工作区内新建 / 修改文件；review 模式下需用户批准"},
    "fs_edit": {"label": "编辑文件", "desc": "精确替换文件中的唯一文本片段；review 模式下 diff 只高亮改的行"},
    "fs_grep": {"label": "搜索文本", "desc": "用正则在 workspace 文件中搜索，返回结构化匹配结果；跳过二进制和依赖目录"},
    "fs_glob": {"label": "查找文件", "desc": "用 glob 模式递归查找文件（如 **/*.tsx），返回路径和大小"},
    "bash": {"label": "执行命令", "desc": "在工作区内运行命令行；受命令黑名单与沙箱目录约束"},
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
    """Filter persisted toolNames to only the 5 UI-selectable tools.

    Baseline tools are not filtered here — they are merged at runtime by
    agent_runner.py.
    """
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
    # Specific roles — checked before coder to avoid overlap
    # (e.g. "调研" should match researcher, not coder).
    if re.search(r"调研|联网搜索|搜索公网|market.?research|竞品|research|文献综述|行业分析", text):
        return "researcher"
    if re.search(r"协调|派发|项目管理|拆分任务|orchestrat|coordinat|项目经理|群聊", text):
        return "orchestrator"
    if re.search(
        r"文档|文案|报告|审查|评审|原型|网页|ppt|幻灯片|演示|"
        r"tech.?writ|documentation|review|prototype|presentation|slides",
        text,
    ):
        return "writer"
    # coder 关键词覆盖最广，放最后
    if re.search(
        r"代码|实现|开发|bug|重构|测试|前端|后端|源码|仓库|本地|文件|命令|终端|"
        r"修复|调试|workspace|repo|code|implement|build|ship|cli|bash|"
        r"test|lint|debug|refactor|frontend|backend",
        text,
    ):
        return "coder"
    return "coder"


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
        return "PPT 写作助手"
    if re.search(r"图示|图表|流程图|mermaid|diagram", lower):
        return "图示写作助手"
    if re.search(r"文档|报告|document|report", lower):
        return "文档写作助手"
    if re.search(r"网页|页面|原型|website|prototype|landing", lower):
        return "网页写作助手"

    return {
        "coder": "代码工程师",
        "researcher": "调研分析师",
        "orchestrator": "任务协调者",
        "writer": "写作工程师",
    }[preset_id]


def _infer_description(text: str, preset_id: str) -> str:
    target = _truncate(text, 72)
    prefix = {
        "coder": "围绕本地代码修改、命令执行与验证结果提供实现支持",
        "researcher": "围绕联网搜索、交叉验证与调研报告提供决策支持",
        "orchestrator": "围绕任务拆解、子 Agent 派发与结果聚合提供协调支持",
        "writer": "围绕技术文档、内容文案、审查报告与网页原型提供写作支持",
    }.get(preset_id, "围绕用户目标提供规划、执行和交付支持")
    return _truncate(f"{prefix}：{target}", 280)


def _infer_capabilities(text: str, preset_id: str) -> list[str]:
    lower = text.lower()
    capabilities = {
        "coder": ["代码实现", "本地验证", "命令行"],
        "researcher": ["联网搜索", "交叉验证", "调研报告"],
        "orchestrator": ["任务拆解", "子 Agent 派发", "结果聚合"],
        "writer": ["文档交付", "内容创作", "产物交付"],
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
    permission_summaries: list[dict[str, str]] | None = None,
) -> str:
    """Build a generic system prompt aligned with the 4-role template style.

    NOTE: This function is currently unused — ``build_heuristic_agent_config_draft``
    uses the preset's ``systemPromptTemplate`` directly. Kept for future use
    and style alignment. Does NOT list tool permissions (baseline tools are
    always-on and don't need to be enumerated).
    """
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
        f"默认角色预设：{preset_label}。所有 custom agent 自带基础工具（fs_read / fs_write / fs_edit / bash 等），可选工具按预设配置。",
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
async def draft_agent(request: Request, user: User = Depends(get_current_user)) -> JSONResponse:
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
