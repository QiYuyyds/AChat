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
        "executablePath": row.executable_path,
        "toolNames": row.tool_names_list,
        "skillNames": row.skill_names_list,
        "isBuiltin": row.is_builtin,
        "isOrchestrator": row.is_orchestrator,
        "supportsVision": row.supports_vision,
        "createdAt": row.created_at,
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

    executable_path = (body.executable_path.strip() if body.executable_path else "") or None

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
        executable_path=executable_path if adapter_name == "codex" else None,
        is_builtin=False,
        is_orchestrator=body.is_orchestrator or False,
        supports_vision=body.supports_vision or False,
        created_at=now_ms(),
    )
    agent.capabilities_list = body.capabilities or []
    # SDK adapters use their own builtin tool set; force empty toolNames/skillNames.
    tool_names = (body.tool_names or []) if adapter_name == "custom" else []
    # Orchestrator agents require plan_tasks + ask_user tools.
    if body.is_orchestrator and adapter_name == "custom":
        for required_tool in ("plan_tasks", "ask_user"):
            if required_tool not in tool_names:
                tool_names.append(required_tool)
    agent.tool_names_list = tool_names
    agent.skill_names_list = (body.skill_names or []) if adapter_name == "custom" else []

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
    "executablePath",
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
        if "executable_path" in provided:
            agent.executable_path = _trim_or_none(body.executable_path)
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
            # SDK adapter: drop modelProvider/toolNames/skillNames; clear modelId on switch.
            if has_adapter_name and not has_model_id:
                agent.model_id = None
                updated = True
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
    "bash",
]

_AGENT_TOOL_PRESETS: dict[str, dict[str, Any]] = {
    "all-purpose": {
        "label": "全栈通用",
        # _ALL_PURPOSE_TOOLS already excludes plan_tasks (Orchestrator-only) and
        # web_search (opt-in, consumes Tavily credits).
        "tools": list(_ALL_PURPOSE_TOOLS),
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
            "bash",
        ],
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
    },
    "review": {
        "label": "审查验证",
        "tools": ["read_artifact", "read_attachment", "ask_user", "fs_list", "fs_read", "bash"],
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
    }[preset_id]


def _infer_description(text: str, preset_id: str) -> str:
    target = _truncate(text, 72)
    prefix = {
        "local-code": "围绕本地代码与命令行任务提供实现、修改和验证支持",
        "artifact": "围绕网页、文档、PPT 等产物提供规划、生成和迭代支持",
        "review": "围绕已有产物或代码提供审查、验证和风险发现",
    }.get(preset_id, "围绕用户目标提供规划、执行和交付支持")
    return _truncate(f"{prefix}：{target}", 280)


def _infer_capabilities(text: str, preset_id: str) -> list[str]:
    lower = text.lower()
    capabilities = {
        "local-code": ["代码实现", "本地验证", "命令行"],
        "artifact": ["产物交付", "内容生成", "原型设计"],
        "review": ["审查验证", "风险发现", "改进建议"],
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
        "systemPrompt": _build_system_prompt(
            name, intent, follow_up, preset["label"], permission_summaries
        ),
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
