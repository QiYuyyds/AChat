"""Pending approval API routes (bash commands, dispatch plans, questions, writes).

Auth: every endpoint requires authentication and verifies conversation ownership
before resolving any pending item.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_conversation_ownership
from app.db.models import User
from app.schemas.dispatch import AskUserAnswer
from app.services import conversation_service
from app.services.pending_bash_commands import pending_bash_commands
from app.services.pending_dispatch_plans import pending_dispatch_plans
from app.services.pending_mcp_calls import pending_mcp_calls
from app.services.pending_merge_conflicts import pending_merge_conflicts
from app.services.pending_questions import pending_questions
from app.services.pending_writes import pending_writes

router = APIRouter()


async def _read_json(req: Request) -> Any:
    try:
        return await req.json()
    except Exception:
        return None


def _invalid_body() -> JSONResponse:
    return JSONResponse(
        {"error": "Invalid body", "issues": []},
        status_code=400,
    )


# ─── pending-writes ──────────────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-writes")
async def list_pending_writes(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    writes = pending_writes.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingWrites": [w.model_dump(by_alias=True) for w in writes]}
    )


@router.post("/api/conversations/{conversation_id}/pending-writes/{pw_id}")
async def resolve_pending_write(
    conversation_id: str,
    pw_id: str,
    req: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    raw = await _read_json(req)
    if not isinstance(raw, dict) or raw.get("action") not in ("approve", "reject"):
        return _invalid_body()

    existing = pending_writes.get(pw_id)
    if existing is None:
        return JSONResponse({"error": "Pending write not found"}, status_code=404)

    ok = (
        pending_writes.approve(pw_id)
        if raw["action"] == "approve"
        else pending_writes.reject(pw_id)
    )
    if not ok:
        return JSONResponse(
            {"error": "Failed to process pending write"}, status_code=500
        )
    return JSONResponse({"ok": True})


# ─── pending-questions ───────────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-questions")
async def list_pending_questions(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    questions = pending_questions.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingQuestions": [q.model_dump(by_alias=True) for q in questions]}
    )


@router.post("/api/conversations/{conversation_id}/pending-questions/{qid}")
async def answer_pending_question(
    conversation_id: str,
    qid: str,
    req: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    raw = await _read_json(req)
    if not isinstance(raw, dict) or not isinstance(raw.get("answers"), dict):
        return _invalid_body()

    answers: dict[str, AskUserAnswer] = {}
    for key, value in raw["answers"].items():
        if not isinstance(value, dict) or not isinstance(
            value.get("selectedLabels"), list
        ):
            return _invalid_body()
        try:
            answers[key] = AskUserAnswer.model_validate(value)
        except Exception:
            return _invalid_body()

    existing = pending_questions.get(qid)
    if existing is None:
        return JSONResponse(
            {"error": "Pending question not found"}, status_code=404
        )

    ok = pending_questions.answer(qid, answers)
    if not ok:
        return JSONResponse({"error": "Failed to record answer"}, status_code=500)
    return JSONResponse({"ok": True})


# ─── pending-bash-commands ───────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-bash-commands")
async def list_pending_bash_commands(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    commands = pending_bash_commands.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingCommands": [c.model_dump(by_alias=True) for c in commands]}
    )


@router.post(
    "/api/conversations/{conversation_id}/pending-bash-commands/{command_id}"
)
async def resolve_pending_bash_command(
    conversation_id: str,
    command_id: str,
    req: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    raw = await _read_json(req)
    if not isinstance(raw, dict) or raw.get("action") not in ("approve", "reject"):
        return _invalid_body()

    existing = pending_bash_commands.get(command_id)
    if existing is None or existing.conversation_id != conversation_id:
        return JSONResponse(
            {"error": "Pending command not found"}, status_code=404
        )

    ok = (
        pending_bash_commands.approve(command_id)
        if raw["action"] == "approve"
        else pending_bash_commands.reject(command_id)
    )
    if not ok:
        return JSONResponse(
            {"error": "Failed to process pending command"}, status_code=500
        )
    return JSONResponse({"ok": True})


# ─── pending-dispatch-plans ──────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-dispatch-plans")
async def list_pending_dispatch_plans(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    plans = pending_dispatch_plans.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingDispatchPlans": [p.model_dump(by_alias=True) for p in plans]}
    )


@router.post(
    "/api/conversations/{conversation_id}/pending-dispatch-plans/{plan_id}"
)
async def resolve_pending_dispatch_plan(
    conversation_id: str,
    plan_id: str,
    req: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    raw = await _read_json(req)
    if not isinstance(raw, dict):
        return _invalid_body()
    action = raw.get("action")
    if action == "revise":
        feedback = raw.get("feedback")
        if not isinstance(feedback, str) or not (1 <= len(feedback) <= 4000):
            return _invalid_body()
    elif action not in ("approve", "reject"):
        return _invalid_body()

    existing = pending_dispatch_plans.get(plan_id)
    if existing is None or existing.conversation_id != conversation_id:
        return JSONResponse(
            {"error": "Pending dispatch plan not found"}, status_code=404
        )

    if action == "reject":
        ok = pending_dispatch_plans.reject(plan_id)
        if not ok:
            return JSONResponse(
                {"error": "Failed to reject pending dispatch plan"},
                status_code=500,
            )
        return JSONResponse({"ok": True})

    if action == "revise":
        result = await conversation_service.revise_dispatch_plan(
            conversation_id=conversation_id, plan_id=plan_id, feedback=raw["feedback"]
        )
        if not result.get("ok"):
            return JSONResponse({"error": result.get("error")}, status_code=400)
        return JSONResponse({"ok": True})

    result = pending_dispatch_plans.approve(plan_id)
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True})


# ─── pending-mcp-calls ────────────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-mcp-calls")
async def list_pending_mcp_calls(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    calls = pending_mcp_calls.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingMcpCalls": [c.model_dump(by_alias=True) for c in calls]}
    )


@router.post("/api/conversations/{conversation_id}/pending-mcp-calls/{call_id}")
async def resolve_pending_mcp_call(
    conversation_id: str,
    call_id: str,
    req: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    raw = await _read_json(req)
    if not isinstance(raw, dict) or raw.get("action") not in ("approve", "reject"):
        return _invalid_body()

    existing = pending_mcp_calls.get(call_id)
    if existing is None or existing.conversation_id != conversation_id:
        return JSONResponse(
            {"error": "Pending MCP call not found"}, status_code=404
        )

    ok = (
        pending_mcp_calls.approve(call_id)
        if raw["action"] == "approve"
        else pending_mcp_calls.reject(call_id)
    )
    if not ok:
        return JSONResponse(
            {"error": "Failed to process pending MCP call"}, status_code=500
        )
    return JSONResponse({"ok": True})


# ─── pending-merge-conflicts ─────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-merge-conflicts")
async def list_pending_merge_conflicts(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    conflicts = pending_merge_conflicts.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingMergeConflicts": [c.model_dump(by_alias=True) for c in conflicts]}
    )


@router.post(
    "/api/conversations/{conversation_id}/pending-merge-conflicts/{pending_id}/resolve"
)
async def resolve_pending_merge_conflict(
    conversation_id: str,
    pending_id: str,
    req: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    raw = await _read_json(req)
    if not isinstance(raw, dict) or raw.get("action") not in (
        "ours",
        "theirs",
        "edit",
        "abandon",
    ):
        return _invalid_body()

    action = raw["action"]
    file_contents: dict[str, str] | None = None
    if action == "edit":
        fc = raw.get("fileContents")
        if not isinstance(fc, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in fc.items()
        ):
            return _invalid_body()
        file_contents = fc

    existing = pending_merge_conflicts.get(pending_id)
    if existing is None or existing.conversation_id != conversation_id:
        return JSONResponse(
            {"error": "Pending merge conflict not found"}, status_code=404
        )

    resolution_strategy = "manual" if action != "abandon" else "abandoned"
    resolved_files = list(existing.conflict_files) if action != "abandon" else []

    decision: dict[str, Any] = {
        "action": action,
        "file_contents": file_contents,
        "resolution_strategy": resolution_strategy,
        "resolved_files": resolved_files,
    }

    ok = pending_merge_conflicts.resolve(pending_id, decision)
    if not ok:
        return JSONResponse(
            {"error": "Failed to resolve pending merge conflict"}, status_code=500
        )
    return JSONResponse({"ok": True})
