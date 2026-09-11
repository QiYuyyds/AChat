"""Pending approval API routes (bash commands, dispatch plans, questions, writes).

Auth: every endpoint requires authentication and verifies conversation ownership
before resolving any pending item.

The six list routes share one body (:func:`_list_pending`) and the structurally
identical approve/reject routes of writes / bash-commands / mcp-calls share
:func:`_resolve_simple`. Questions / dispatch-plans / merge-conflicts keep
specialized resolve bodies (answer validation, decision construction,
approve/revise/reject branching) but reuse the shared helpers. The
merge-conflicts resolve URL keeps its legacy ``/resolve`` suffix and each
store's response key is unchanged.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_conversation_ownership
from app.db.models import User
from app.schemas.dispatch import AskUserAnswer, DispatchPlanItem
from app.services import conversation_service
from app.services.pending_bash_commands import pending_bash_commands
from app.services.pending_dispatch_plans import pending_dispatch_plans
from app.services.pending_mcp_calls import pending_mcp_calls
from app.services.pending_merge_conflicts import pending_merge_conflicts
from app.services.pending_questions import pending_questions
from app.services.pending_store_base import PendingStoreBase
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


def _not_found(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=404)


async def _list_pending(
    store: PendingStoreBase, response_key: str, conversation_id: str, user: User
) -> JSONResponse:
    """Shared body of the six pending list routes."""
    await verify_conversation_ownership(conversation_id, user.id)
    items = store.list_by_conversation(conversation_id)
    return JSONResponse({response_key: [i.model_dump(by_alias=True) for i in items]})


async def _resolve_simple(
    store: PendingStoreBase,
    pending_id: str,
    conversation_id: str,
    raw: Any,
    *,
    label: str,
) -> JSONResponse:
    """Shared approve/reject path for the writes / bash-commands / mcp-calls routes.

    Enforces that the pending item belongs to the URL's conversation. This
    check is the one deliberate behavior alignment of the generalize-pending-store
    change: the writes route previously skipped it while the other five route
    pairs had it.
    """
    existing = store.get(pending_id)
    if existing is None or existing.conversation_id != conversation_id:
        return _not_found(f"Pending {label} not found")

    ok = (
        store.approve(pending_id)
        if raw["action"] == "approve"
        else store.reject(pending_id)
    )
    if not ok:
        return JSONResponse(
            {"error": f"Failed to process pending {label}"}, status_code=500
        )
    return JSONResponse({"ok": True})


# ─── pending-writes ──────────────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-writes")
async def list_pending_writes(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    return await _list_pending(pending_writes, "pendingWrites", conversation_id, user)


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
    return await _resolve_simple(pending_writes, pw_id, conversation_id, raw, label="write")


# ─── pending-questions ───────────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-questions")
async def list_pending_questions(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    return await _list_pending(pending_questions, "pendingQuestions", conversation_id, user)


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
        return _not_found("Pending question not found")

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
    return await _list_pending(pending_bash_commands, "pendingCommands", conversation_id, user)


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
    return await _resolve_simple(
        pending_bash_commands, command_id, conversation_id, raw, label="command"
    )


# ─── pending-dispatch-plans ──────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-dispatch-plans")
async def list_pending_dispatch_plans(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    return await _list_pending(
        pending_dispatch_plans, "pendingDispatchPlans", conversation_id, user
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
        return _not_found("Pending dispatch plan not found")

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

    # Parse optional modified plan when approving
    modified_plan: list[DispatchPlanItem] | None = None
    if action == "approve" and raw.get("plan") is not None:
        raw_plan = raw.get("plan")
        if not isinstance(raw_plan, list):
            return _invalid_body()
        try:
            modified_plan = [DispatchPlanItem.model_validate(item) for item in raw_plan]
        except Exception:
            return _invalid_body()

    result = pending_dispatch_plans.approve(plan_id, modified_plan=modified_plan)
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True})


# ─── pending-mcp-calls ────────────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-mcp-calls")
async def list_pending_mcp_calls(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    return await _list_pending(pending_mcp_calls, "pendingMcpCalls", conversation_id, user)


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
    return await _resolve_simple(
        pending_mcp_calls, call_id, conversation_id, raw, label="MCP call"
    )


# ─── pending-merge-conflicts ─────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-merge-conflicts")
async def list_pending_merge_conflicts(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    return await _list_pending(
        pending_merge_conflicts, "pendingMergeConflicts", conversation_id, user
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
        return _not_found("Pending merge conflict not found")

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
