"""Port of src/server/agent-runner.ts (orchestrator prompt + XML builders).

The plan / aggregate / sub-agent prompts and their XML helpers. Wording and XML
shape are kept EXACT — they are an LLM contract, not free text. The DB-reading
``build_sub_agent_prompt`` / ``build_aggregate_prompt`` live here too (they read
artifacts / messages / summaries to assemble the prompt strings).

Port mappings: ``JSON.stringify`` -> json.dumps (no spaces, matching JS default);
``db.query.x`` -> sqlalchemy select(...) via get_db; ``Date.now()`` -> now_ms().
"""

from __future__ import annotations

import json

from sqlalchemy import and_, desc, select

from app.db.engine import get_db
from app.db.models import Agent, Artifact, Conversation, Message, Workspace
from app.schemas.dispatch import (
    DispatchExpectedOutput,
    DispatchPlanItem,
    DispatchTaskInput,
)
from app.services.context_compaction_service import (
    get_latest_context_summary,
    render_conversation_summary_block,
)
from app.services.dispatch_plan import collect_dependency_closure
from app.utils.workspace_utils import get_effective_cwd

SUB_AGENT_CONTEXT_RECENT_LIMIT = 5


# ─── resolved-input view (mirror the TS ResolvedTaskInput interface) ──────────
class ResolvedTaskInput:
    """A task input paired with its resolved upstream artifact (if any)."""

    def __init__(
        self,
        input: DispatchTaskInput,  # noqa: A002 - matches the TS field name
        type: str | None,  # noqa: A002 - matches the TS field name
        artifact_id: str | None,
        missing: bool,
    ) -> None:
        self.input = input
        self.type = type
        self.artifact_id = artifact_id
        self.missing = missing


# ─── JSON-stringify parity ────────────────────────────────────────────────────
def _json(value: object) -> str:
    """Match JS JSON.stringify default output (compact separators, non-ascii)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ─── plan / aggregate system prompts ──────────────────────────────────────────
def build_orchestrator_plan_prompt(
    base_system_prompt: str,
    other_agents: list[Agent],
    workspace: Workspace,
) -> str:
    agent_list = "\n".join(
        f"- {{ id: {_json(a.id)}, name: {_json(a.name)}, capabilities: "
        f"{_json(a.capabilities_list)}, tools: {_json(a.tool_names_list)}, "
        f"description: {_json(a.description)} }}"
        for a in other_agents
    )
    local_workspace_rules = (
        [
            "",
            "## 本地 workspace 规划规则",
            "- 用户要求在当前文件夹创建 / 修改 / 初始化 / 调试前后端项目或源码文件时，优先派给具备 fs_read / fs_write / bash 或 SDK 本地工具的 agent。",
            "- 这类本地代码任务不要声明 expectedOutputs；用 acceptanceCriteria 描述应落盘的目录、文件、命令和验证结果。",
            "- 子任务文本必须明确写出“直接修改当前本地 workspace 文件，不要用 write_artifact 代替源码落盘”。",
            "- 只有需要聊天内交付的独立文档、设计稿、可预览原型或 artifact handoff，才声明 expectedOutputs。",
        ]
        if workspace.mode == "local"
        else []
    )

    return "\n".join(
        [
            base_system_prompt,
            "",
            "## 你的工作流",
            "1. 阅读用户最新请求与上下文。",
            "2. 如果存在会阻塞正确规划的关键歧义，且能归纳为 2-4 个清晰选项，先调用 ask_user 让用户选择；拿到答案后继续。",
            "3. 调用 plan_tasks 工具，输出结构化 plan。",
            "4. 系统会自动执行 plan 并把子任务结果回传给你，由你做最终总结。",
            "",
            "## 可用 Agent",
            agent_list if len(agent_list) > 0 else "（无）",
            "",
            "## 拆解原则",
            "- 充分利用每个 Agent 的 capabilities，不要把任务派给不合适的人。",
            "- 每个子任务必须独立可执行（被分派的 Agent 看不到完整群聊上下文，必要上下文要写进 task）。",
            "- 计划阶段只能调用 ask_user、plan_tasks 和只读侦察工具（fs_list/fs_read/read_artifact/read_attachment）；不要写文件或执行命令。",
            "- 若用户需求已足够明确，不要为了形式感提问，直接 plan_tasks。",
            "",
            "## 依赖关系（执行顺序的唯一来源，务必读完）",
            "- 系统【只】按每个任务的 dependsOn 决定顺序：dependsOn 为空的任务会【同时并发】启动。",
            "- 若任务 B 需要任务 A 的产物 / 结论 / 输出，你【必须】在 B 的 dependsOn 里写上 A 的 id。",
            "- 在 task 文本里写「先做 A」「基于上一步」之类【没有任何效果】——执行顺序只认 dependsOn 字段。",
            "- 只有彼此真正无关、可同时进行的任务才留空 dependsOn；拿不准时倾向加依赖（串行更安全）。",
            '- Code implementation tasks MUST set taskKind="code", declare expectedOutputs:[{ id:"project", type:"project", required:true }], and include an acceptanceCriteria item requiring build/compile/test/typecheck to pass.',
            "- project expectedOutputs are system-created from workspace file writes; do not ask the child agent to call write_artifact for project.",
            "- Only declare non-project expectedOutputs when the assigned agent must create a real artifact via write_artifact for downstream handoff or user inspection.",
            "- Do NOT declare expectedOutputs for text-only tasks such as review, validation, diagnosis, status check, explanation, or summary; put their completion checks in acceptanceCriteria.",
            "- If a task needs an upstream artifact, declare inputs with fromTaskId and outputId; the system will compile these into dependencies.",
            "- For tasks with quality requirements, add concise acceptanceCriteria that the assigned agent can verify.",
            *local_workspace_rules,
            "- For code or test tasks, set taskKind and declare targetPaths, expectedWorkspaceChanges, requiredCommands, and requiredEvidence whenever possible.",
            "- Frontend and backend implementation tasks usually both depend on PRD/API contracts, not on each other; plan them as parallel siblings unless one truly consumes the other output.",
            '- Prefer requiredCommands with cwd, for example { command: "pnpm build", cwd: "frontend", timeoutMs: 300000 }; avoid encoding directory changes as "cd frontend && ...".',
            "- A retry/remediation plan must preserve the original user goal. Do not replace implementation work with a narrower review-only task unless the user explicitly approved that scope change.",
            *local_workspace_rules,
            "",
            "示例（设计 → 前端 → 审查，逐级依赖；agentId 用上面可用列表里的真实 id）：",
            "tasks: [",
            '  { "id": "t1", "agentId": "<设计师 id>", "task": "产出 UI 设计稿" },',
            '  { "id": "t2", "agentId": "<前端 id>", "task": "按设计稿实现页面", "dependsOn": ["t1"] },',
            '  { "id": "t3", "agentId": "<Reviewer id>", "task": "审查 t2 的实现", "dependsOn": ["t2"] }',
            "]",
        ]
    )


def build_orchestrator_aggregate_prompt(base_system_prompt: str) -> str:
    return "\n".join(
        [
            base_system_prompt,
            "",
            "## 当前阶段",
            "你处于「聚合阶段」。所有子任务已执行完成（含成功与失败），结果在 user 消息中以 XML 给出。",
            "请直接给用户输出一条总结消息：",
            "- 简明列出完成 / 失败的任务",
            "- 如果存在 failed / skipped / aborted 任务，必须明确说明整体未完成，不要把局部成功说成全部完成",
            "- 用 <artifact_ref id=\"art_xxx\"/> 形式引用关键产物（如果有）",
            "- 给出明确的下一步建议",
            "不要再调用 plan_tasks，不要把任务再次分派。",
        ]
    )


# ─── sub-agent prompt (reads upstream / existing artifacts + recent chat) ──────
async def build_sub_agent_prompt(
    task: DispatchPlanItem,
    upstream: dict[str, object],  # task_id -> DispatchTaskResult (see orchestrator)
    conversation_id: str,
    plan: list[DispatchPlanItem],
    resolved_inputs: list[ResolvedTaskInput],
    workspace: Workspace,
) -> str:
    # transitive dependency closure: a reviewer must see the PRD/UI, not just the
    # direct upstream implementation.
    upstream_artifact_ids: set[str] = set()
    for dep in collect_dependency_closure(plan, task.id):
        r = upstream.get(dep)
        if r is not None:
            for artifact_id in r.artifact_ids:  # type: ignore[attr-defined]
                upstream_artifact_ids.add(artifact_id)

    upstream_artifacts_xml = ""
    if upstream_artifact_ids:
        async with get_db() as db:
            artifacts = (
                await db.execute(
                    select(Artifact).where(Artifact.id.in_(list(upstream_artifact_ids)))
                )
            ).scalars().all()
        upstream_artifacts_xml = "\n".join(_render_artifact_summary_xml(a) for a in artifacts)

    async with get_db() as db:
        existing = (
            await db.execute(
                select(Artifact)
                .where(Artifact.conversation_id == conversation_id)
                .order_by(desc(Artifact.created_at))
            )
        ).scalars().all()
    existing_xml = "\n".join(
        _render_artifact_summary_xml(a)
        for a in [a for a in existing if a.id not in upstream_artifact_ids][
            :SUB_AGENT_CONTEXT_RECENT_LIMIT
        ]
    )

    # spec 06: inject the most recent N chat turns + every pinned message.
    latest_summary = await get_latest_context_summary(conversation_id)
    async with get_db() as db:
        if latest_summary is not None:
            recent_where = and_(
                Message.conversation_id == conversation_id,
                Message.status == "complete",
                Message.created_at > latest_summary.covered_until_created_at,
            )
        else:
            recent_where = and_(
                Message.conversation_id == conversation_id,
                Message.status == "complete",
            )
        recent = list(
            (
                await db.execute(
                    select(Message)
                    .where(recent_where)
                    .order_by(desc(Message.created_at))
                    .limit(SUB_AGENT_CONTEXT_RECENT_LIMIT)
                )
            ).scalars().all()
        )
        recent.reverse()

        conv = (
            await db.execute(select(Conversation).where(Conversation.id == conversation_id))
        ).scalar_one_or_none()
        pin_ids = conv.pinned_message_ids_list if conv else []
        pinned = (
            (
                await db.execute(
                    select(Message).where(Message.id.in_(pin_ids)).order_by(Message.created_at)
                )
            ).scalars().all()
            if pin_ids
            else []
        )

        # one batch query for agent names (avoid N+1 in the render loop)
        agent_ids = {m.agent_id for m in [*recent, *pinned] if m.agent_id}
        agents = (
            (
                await db.execute(select(Agent).where(Agent.id.in_(list(agent_ids))))
            ).scalars().all()
            if agent_ids
            else []
        )
    agent_name_by_id = {a.id: a.name for a in agents}

    def render_message(m: Message) -> str:
        from_ = (
            "user"
            if m.role == "user"
            else (m.agent_id and agent_name_by_id.get(m.agent_id)) or m.role
        )
        text = extract_text_from_parts(m.parts_list).strip()
        if not text:
            return ""
        return f"    <message from={_json(from_)}>{escape_xml(text)}</message>"

    recent_xml = "\n".join(filter(None, (render_message(m) for m in recent)))
    pinned_xml = "\n".join(filter(None, (render_message(m) for m in pinned)))
    summary_xml = (
        "\n".join(
            f"  {line}" for line in render_conversation_summary_block(latest_summary).split("\n")
        )
        if latest_summary
        else ""
    )
    task_inputs_xml = render_task_inputs_xml(resolved_inputs)
    expected_outputs_xml = render_expected_outputs_xml(task.expected_outputs or [])
    acceptance_criteria_xml = render_acceptance_criteria_xml(task.acceptance_criteria or [])
    evidence_contract_xml = render_task_evidence_contract_xml(task)

    lines = [
        "<context>",
        summary_xml,
        recent_xml and f"  <recent_conversation>\n{recent_xml}\n  </recent_conversation>",
        pinned_xml and f"  <pinned_messages>\n{pinned_xml}\n  </pinned_messages>",
        task_inputs_xml and f"  <required_inputs>\n{task_inputs_xml}\n  </required_inputs>",
        expected_outputs_xml
        and f"  <expected_outputs>\n{expected_outputs_xml}\n  </expected_outputs>",
        acceptance_criteria_xml
        and f"  <acceptance_criteria>\n{acceptance_criteria_xml}\n  </acceptance_criteria>",
        evidence_contract_xml
        and f"  <evidence_contract>\n{evidence_contract_xml}\n  </evidence_contract>",
        upstream_artifacts_xml
        and f"  <upstream_artifacts>\n{upstream_artifacts_xml}\n  </upstream_artifacts>",
        f"  <existing_artifacts>\n{existing_xml or '    （无）'}\n  </existing_artifacts>",
        "</context>",
        "",
        "<your_task>",
        task.task,
        "</your_task>",
        "",
        "Before working, read every required input artifact with read_artifact(artifactId).",
        workspace.mode == "local"
        and "If this task is about local project source files, directly modify the current local workspace with file/command tools. Do not use write_artifact to store source files that should be written to disk.",
        'For expected_outputs with type="project", write the project files into the workspace with fs_write or bash; AgentHub will create and bind the project artifact automatically. Do not call write_artifact for project.',
        "For non-project expected_outputs, create the artifact with write_artifact and pass outputKey equal to that output id.",
        "If no expected_outputs are declared, complete the task with a normal message; do not create an artifact just to satisfy status tracking.",
        "Satisfy every acceptance_criteria item when present.",
        "If evidence_contract is present, include matching filesChanged, commandsRun, tests, and/or acceptanceResults evidence in report_task_result.",
        "For required_commands, you may run the command yourself, and AgentHub will also run it as a completion gate after your attempt. Use bash cwd instead of cd when running commands in subdirectories.",
        "If dependencies are missing, install them inside the workspace and continue; dependency installation is preparation, not completion evidence.",
        "If a required command fails, fix the issue and continue; do not report complete until the command can pass.",
        "For target_paths, list every changed or verified path in report_task_result.filesChanged.",
        "At the end, call report_task_result exactly once. A normal text response alone does not complete this dispatched task.",
        'Use report_task_result.status="complete" only when you have FULLY accomplished the assigned task.',
        "Never report complete if tests are failing, implementation is partial, unresolved errors remain, or you could not find necessary files/dependencies.",
        "If acceptance_criteria are present, include acceptanceResults and copy each criterion string exactly with passed/evidence.",
        'Use status="failed" when the task was attempted but did not satisfy the assignment; use status="blocked" when external input or unavailable prerequisites prevent progress.',
        "",
        "执行任务，必要时通过 read_artifact 获取上游产物详情。",
    ]
    return "\n".join(line for line in lines if line)


def render_task_inputs_xml(inputs: list[ResolvedTaskInput]) -> str:
    out: list[str] = []
    for entry in inputs:
        inp = entry.input
        attrs = " ".join(
            filter(
                None,
                [
                    f"fromTaskId={xml_attr(inp.from_task_id)}",
                    f"outputId={xml_attr(inp.output_id)}",
                    f"required={xml_attr('false' if inp.required is False else 'true')}",
                    f"type={xml_attr(entry.type)}" if entry.type else "",
                    f"artifactId={xml_attr(entry.artifact_id)}" if entry.artifact_id else "",
                    'missing="true"' if entry.missing else "",
                ],
            )
        )
        description = escape_xml(inp.description) if inp.description else ""
        out.append(
            f"    <input {attrs}>{description}</input>"
            if description
            else f"    <input {attrs} />"
        )
    return "\n".join(out)


def render_expected_outputs_xml(outputs: list[DispatchExpectedOutput]) -> str:
    out: list[str] = []
    for output in outputs:
        attrs = " ".join(
            [
                f"id={xml_attr(output.id)}",
                f"type={xml_attr(output.type)}",
                f"required={xml_attr('false' if output.required is False else 'true')}",
            ]
        )
        description = escape_xml(output.description) if output.description else ""
        out.append(
            f"    <output {attrs}>{description}</output>"
            if description
            else f"    <output {attrs} />"
        )
    return "\n".join(out)


def render_acceptance_criteria_xml(criteria: list[str]) -> str:
    return "\n".join(f"    <item>{escape_xml(c)}</item>" for c in criteria)


def render_task_evidence_contract_xml(task: DispatchPlanItem) -> str:
    lines: list[str] = []
    if task.task_kind:
        lines.append(f"    <task_kind>{escape_xml(task.task_kind)}</task_kind>")
    for target_path in task.target_paths or []:
        lines.append(f"    <target_path>{escape_xml(target_path)}</target_path>")
    for change in task.expected_workspace_changes or []:
        lines.append(f"    <expected_workspace_change>{escape_xml(change)}</expected_workspace_change>")
    for required_command in task.required_commands or []:
        description = (
            escape_xml(required_command.description) if required_command.description else ""
        )
        attrs = " ".join(
            filter(
                None,
                [
                    f"command={xml_attr(required_command.command)}",
                    f"cwd={xml_attr(required_command.cwd)}" if required_command.cwd else "",
                    f"timeoutMs={xml_attr(str(required_command.timeout_ms))}"
                    if required_command.timeout_ms
                    else "",
                ],
            )
        )
        lines.append(
            f"    <required_command {attrs}>{description}</required_command>"
            if description
            else f"    <required_command {attrs} />"
        )
    for evidence in task.required_evidence or []:
        lines.append(f"    <required_evidence>{escape_xml(evidence)}</required_evidence>")
    return "\n".join(lines)


def _render_artifact_summary_xml(artifact: Artifact) -> str:
    return (
        f'  <artifact id="{artifact.id}" type="{artifact.type}" '
        f"title={_json(artifact.title)} />"
    )


def xml_attr(s: str) -> str:
    return '"' + escape_xml(s).replace('"', "&quot;") + '"'


def escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ─── aggregate prompt (reads result artifacts) ────────────────────────────────
async def build_aggregate_prompt(
    original_user_prompt: str,
    plan: list[DispatchPlanItem],
    task_results: dict[str, object],  # task_id -> DispatchTaskResult
    conflicts: list[object],  # list[FileWriteConflict]
    workspace: Workspace,
) -> str:
    all_artifact_ids: list[str] = []
    for r in task_results.values():
        all_artifact_ids.extend(r.artifact_ids)  # type: ignore[attr-defined]
    if all_artifact_ids:
        async with get_db() as db:
            artifacts = (
                await db.execute(select(Artifact).where(Artifact.id.in_(all_artifact_ids)))
            ).scalars().all()
    else:
        artifacts = []
    artifact_by_id = {a.id: a for a in artifacts}

    result_lines: list[str] = []
    for t in plan:
        r = task_results.get(t.id)
        if r is None:
            continue
        output_key_by_artifact_id = {
            artifact_id: output_key
            for output_key, artifact_id in r.output_artifacts.items()  # type: ignore[attr-defined]
        }
        arts = "\n".join(
            (
                f'    <artifact id="{a.id}" type="{a.type}"'
                + (
                    f" outputKey={_json(output_key_by_artifact_id[a.id])}"
                    if a.id in output_key_by_artifact_id
                    else ""
                )
                + f" title={_json(a.title)} />"
            )
            for a in (artifact_by_id.get(aid) for aid in r.artifact_ids)  # type: ignore[attr-defined]
            if a is not None
        )
        report = render_task_result_report_xml(r.task_report) if r.task_report else ""  # type: ignore[attr-defined]
        inner_content = "\n".join(filter(None, [report, arts]))
        inner = f"\n{inner_content}\n  " if inner_content else ""
        err_attr = f" error={_json(r.error)}" if r.error else ""  # type: ignore[attr-defined]
        result_lines.append(
            f'  <result task="{t.id}" agent="{t.agent_id}" status="{r.status}"{err_attr}>{inner}</result>'  # type: ignore[attr-defined]
        )
    results_xml = "\n".join(filter(None, result_lines))

    lines = [
        f"<user_request>{original_user_prompt}</user_request>",
        "<task_results>",
        results_xml,
        "</task_results>",
    ]

    if conflicts:
        cwd = get_effective_cwd(workspace)

        def to_rel(abs_path: str) -> str:
            if abs_path.startswith(cwd):
                return abs_path[len(cwd) :].lstrip("\\/")
            return abs_path

        lines.append("<file_conflicts>")
        lines.append(
            "  <!-- 多个并行子任务写了同一文件，后写已覆盖先写。请在总结里明确告知用户：哪个文件、涉及哪些任务、当前保留的是最后写入的版本，并建议如何处理（例如改为串行重做或人工合并）。 -->"
        )
        for c in conflicts:
            tasks = ", ".join(
                f"{w['taskId']}({w['agentId']})" for w in c.contributors  # type: ignore[attr-defined]
            )
            lines.append(f"  <conflict path={_json(to_rel(c.path))} tasks={_json(tasks)} />")  # type: ignore[attr-defined]
        lines.append("</file_conflicts>")

    lines.extend(["", "请基于以上结果给用户输出最终总结消息。"])
    return "\n".join(lines)


def render_task_result_report_xml(report: dict) -> str:
    children = [f"      <summary>{escape_xml(report['summary'])}</summary>"]
    for result in report.get("acceptanceResults") or []:
        children.append(
            f"      <acceptance criterion={xml_attr(result['criterion'])} "
            f"passed={xml_attr(str(result['passed']).lower())}>"
            f"{escape_xml(result['evidence'])}</acceptance>"
        )
    for file in report.get("filesChanged") or []:
        action_attr = f" action={xml_attr(file['action'])}" if file.get("action") else ""
        children.append(f"      <file path={xml_attr(file['path'])}{action_attr} />")
    for command in report.get("commandsRun") or []:
        summary = escape_xml(command["summary"]) if command.get("summary") else ""
        attrs = " ".join(
            filter(
                None,
                [
                    f"command={xml_attr(command['command'])}",
                    f"exitCode={xml_attr(str(command['exitCode']))}",
                    f"cwd={xml_attr(command['cwd'])}" if command.get("cwd") else "",
                    f"timedOut={xml_attr(str(command['timedOut']).lower())}"
                    if command.get("timedOut") is not None
                    else "",
                ],
            )
        )
        children.append(
            f"      <command {attrs}>{summary}</command>"
            if summary
            else f"      <command {attrs} />"
        )
    for test in report.get("tests") or []:
        summary = escape_xml(test["summary"]) if test.get("summary") else ""
        children.append(
            f"      <test command={xml_attr(test['command'])} "
            f"passed={xml_attr(str(test['passed']).lower())}>{summary}</test>"
            if summary
            else f"      <test command={xml_attr(test['command'])} "
            f"passed={xml_attr(str(test['passed']).lower())} />"
        )
    for blocker in report.get("blockers") or []:
        children.append(f"      <blocker>{escape_xml(blocker)}</blocker>")
    return "\n".join(
        [
            f"    <task_report status={xml_attr(report['status'])}>",
            *children,
            "    </task_report>",
        ]
    )


# ─── misc ─────────────────────────────────────────────────────────────────────
def extract_text_from_parts(parts: list[dict]) -> str:
    out: list[str] = []
    for p in parts:
        ptype = p.get("type")
        if ptype in ("text", "thinking"):
            out.append(p.get("content", ""))
        elif ptype == "code":
            out.append("```" + p.get("language", "") + "\n" + p.get("content", "") + "\n```")
        elif ptype == "image_attachment":
            out.append(
                f"[图片附件: {p['fileName']} ({format_size(p['size'])}, "
                f"{p['mimeType']}) · id={p['attachmentId']}]"
            )
        elif ptype == "file_attachment":
            out.append(
                f"[文件附件: {p['fileName']} ({format_size(p['size'])}, "
                f"{p['mimeType']}) · id={p['attachmentId']}]"
            )
    return "\n\n".join(s for s in out if s)


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes / 1024 / 1024:.1f}MB"


def ensure_includes(arr: list[str], v: str) -> list[str]:
    return arr if v in arr else [*arr, v]
