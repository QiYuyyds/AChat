"""Pending approval API routes (bash commands, dispatch plans, questions, writes).

Faithful port of the TS routes under
``src/app/api/conversations/[id]/pending-*``. The pending stores are in-memory
singletons (synchronous); these routes are thin wrappers that mirror the TS HTTP
contract byte-for-byte: GET lists pending items for a conversation, POST on an
item id resolves it (approve / reject / answer / revise).
"""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.schemas.dispatch import AskUserAnswer
from app.services import conversation_service
from app.services.pending_bash_commands import pending_bash_commands
from app.services.pending_dispatch_plans import pending_dispatch_plans
from app.services.pending_questions import pending_questions
from app.services.pending_writes import pending_writes

router = APIRouter()


async def _read_json(req: Request) -> Any:
    """Mirror the TS ``req.json().catch(() => null)`` — never raise on bad JSON."""
    try:
        return await req.json()
    except Exception:  # noqa: BLE001 - malformed body becomes null, like the TS route
        return None


def _invalid_body() -> JSONResponse:
    return JSONResponse(
        {"error": "Invalid body", "issues": []},
        status_code=400,
    )


# ─── pending-writes ──────────────────────────────────────────────────────────
@router.get("/api/conversations/{conversation_id}/pending-writes")
async def list_pending_writes(conversation_id: str) -> JSONResponse:
    writes = pending_writes.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingWrites": [w.model_dump(by_alias=True) for w in writes]}
    )


@router.post("/api/conversations/{conversation_id}/pending-writes/{pw_id}")
async def resolve_pending_write(
    conversation_id: str, pw_id: str, req: Request
) -> JSONResponse:
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
async def list_pending_questions(conversation_id: str) -> JSONResponse:
    questions = pending_questions.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingQuestions": [q.model_dump(by_alias=True) for q in questions]}
    )


@router.post("/api/conversations/{conversation_id}/pending-questions/{qid}")
async def answer_pending_question(
    conversation_id: str, qid: str, req: Request
) -> JSONResponse:
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
        except Exception:  # noqa: BLE001 - shape mismatch is an invalid body
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
async def list_pending_bash_commands(conversation_id: str) -> JSONResponse:
    commands = pending_bash_commands.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingCommands": [c.model_dump(by_alias=True) for c in commands]}
    )


@router.post(
    "/api/conversations/{conversation_id}/pending-bash-commands/{command_id}"
)
async def resolve_pending_bash_command(
    conversation_id: str, command_id: str, req: Request
) -> JSONResponse:
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
async def list_pending_dispatch_plans(conversation_id: str) -> JSONResponse:
    plans = pending_dispatch_plans.list_by_conversation(conversation_id)
    return JSONResponse(
        {"pendingDispatchPlans": [p.model_dump(by_alias=True) for p in plans]}
    )


@router.post(
    "/api/conversations/{conversation_id}/pending-dispatch-plans/{plan_id}"
)
async def resolve_pending_dispatch_plan(
    conversation_id: str, plan_id: str, req: Request
) -> JSONResponse:
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
