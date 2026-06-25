"""API tests for the pending-approval routes (writes / questions / bash / plans).

The pending stores are in-memory singletons, so each test registers an entry
directly on the relevant store and then drives the HTTP route. Stores are reset
between tests via the autouse ``_reset_pending_stores`` fixture so ids never leak.
"""

import httpx
import pytest
import pytest_asyncio

from app.api.pending import router as pending_router
from app.db.models import Workspace
from app.schemas.dispatch import AskUserQuestionItem
from app.services import conversation_service
from app.services.pending_bash_commands import pending_bash_commands
from app.services.pending_dispatch_plans import pending_dispatch_plans
from app.services.pending_questions import pending_questions
from app.services.pending_writes import pending_writes


@pytest_asyncio.fixture
async def api_client(db):
    """An httpx client over an app that mounts ONLY the pending router.

    The shared conftest ``api_client`` builds the app via ``create_app()``, but
    the pending router is wired by the Integrate stage (main.py), which this
    stage must not touch. Mounting the router directly here keeps verification
    self-contained while still sharing the isolated ``db`` fixture's DB.
    """
    from fastapi import FastAPI

    import app.services.agent_runner  # noqa: F401  wires runner into registry

    app = FastAPI()
    app.include_router(pending_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture(autouse=True)
def _reset_pending_stores():
    for store in (
        pending_writes,
        pending_questions,
        pending_bash_commands,
        pending_dispatch_plans,
    ):
        store._map.clear()
    yield
    for store in (
        pending_writes,
        pending_questions,
        pending_bash_commands,
        pending_dispatch_plans,
    ):
        store._map.clear()


def _make_workspace() -> Workspace:
    return Workspace(
        id="ws_test",
        conversation_id="conv_test",
        mode="sandbox",
        root_path="/tmp/ws",
        bound_path=None,
        created_at=0,
    )


# ─── pending-writes ──────────────────────────────────────────────────────────
async def test_list_pending_writes_empty(api_client):
    resp = await api_client.get("/api/conversations/conv_x/pending-writes")
    assert resp.status_code == 200
    assert resp.json() == {"pendingWrites": []}


async def test_list_pending_writes_returns_registered(api_client):
    write = pending_writes.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        path="a.txt",
        absolute_path="/tmp/ws/a.txt",
        old_content=None,
        new_content="hello",
        workspace=_make_workspace(),
        skip_write=True,
    )
    resp = await api_client.get("/api/conversations/conv_x/pending-writes")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pendingWrites"]) == 1
    item = body["pendingWrites"][0]
    assert item["id"] == write.id
    assert item["conversationId"] == "conv_x"
    assert item["newContent"] == "hello"


async def test_approve_pending_write(api_client):
    write = pending_writes.register(
        conversation_id="conv_x",
        agent_id="ag_alice",
        run_id="run_1",
        path="a.txt",
        absolute_path="/tmp/ws/a.txt",
        old_content=None,
        new_content="hello",
        workspace=_make_workspace(),
        skip_write=True,
    )
    resp = await api_client.post(
        f"/api/conversations/conv_x/pending-writes/{write.id}",
        json={"action": "approve"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert pending_writes.get(write.id) is None


async def test_pending_write_invalid_body(api_client):
    resp = await api_client.post(
        "/api/conversations/conv_x/pending-writes/pw_missing",
        json={"action": "nope"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


async def test_pending_write_not_found(api_client):
    resp = await api_client.post(
        "/api/conversations/conv_x/pending-writes/pw_missing",
        json={"action": "approve"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "Pending write not found"}


# ─── pending-questions ───────────────────────────────────────────────────────
def _register_question(conversation_id: str = "conv_x"):
    return pending_questions.register(
        conversation_id=conversation_id,
        agent_id="ag_alice",
        run_id="run_1",
        questions=[
            AskUserQuestionItem.model_validate(
                {
                    "question": "Pick one",
                    "header": "Pick",
                    "options": [{"label": "A"}, {"label": "B"}],
                    "multiSelect": False,
                }
            )
        ],
    )


async def test_list_pending_questions(api_client):
    q = _register_question()
    resp = await api_client.get("/api/conversations/conv_x/pending-questions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pendingQuestions"]) == 1
    assert body["pendingQuestions"][0]["id"] == q.id


async def test_answer_pending_question(api_client):
    q = _register_question()
    resp = await api_client.post(
        f"/api/conversations/conv_x/pending-questions/{q.id}",
        json={"answers": {"Pick one": {"selectedLabels": ["A"], "freeformNote": "ok"}}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert pending_questions.get(q.id) is None


async def test_answer_pending_question_invalid_body(api_client):
    q = _register_question()
    resp = await api_client.post(
        f"/api/conversations/conv_x/pending-questions/{q.id}",
        json={"answers": "not-a-record"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


async def test_answer_pending_question_not_found(api_client):
    resp = await api_client.post(
        "/api/conversations/conv_x/pending-questions/q_missing",
        json={"answers": {"q": {"selectedLabels": ["A"]}}},
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "Pending question not found"}


# ─── pending-bash-commands ───────────────────────────────────────────────────
def _register_bash(conversation_id: str = "conv_x"):
    return pending_bash_commands.register(
        conversation_id=conversation_id,
        agent_id="ag_alice",
        run_id="run_1",
        command="ls",
        cwd="/tmp/ws",
        reason="list files",
    )


async def test_list_pending_bash_commands(api_client):
    cmd = _register_bash()
    resp = await api_client.get("/api/conversations/conv_x/pending-bash-commands")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pendingCommands"]) == 1
    assert body["pendingCommands"][0]["id"] == cmd.id
    assert body["pendingCommands"][0]["command"] == "ls"


async def test_reject_pending_bash_command(api_client):
    cmd = _register_bash()
    resp = await api_client.post(
        f"/api/conversations/conv_x/pending-bash-commands/{cmd.id}",
        json={"action": "reject"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert pending_bash_commands.get(cmd.id) is None


async def test_pending_bash_command_wrong_conversation_404(api_client):
    cmd = _register_bash(conversation_id="conv_x")
    resp = await api_client.post(
        f"/api/conversations/conv_other/pending-bash-commands/{cmd.id}",
        json={"action": "approve"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "Pending command not found"}


async def test_pending_bash_command_invalid_body(api_client):
    cmd = _register_bash()
    resp = await api_client.post(
        f"/api/conversations/conv_x/pending-bash-commands/{cmd.id}",
        json={},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


# ─── pending-dispatch-plans ──────────────────────────────────────────────────
def _register_plan(conversation_id: str, *, resolver=None):
    plan = pending_dispatch_plans.register(
        conversation_id=conversation_id,
        agent_id="ag_orch",
        run_id="run_1",
        plan=[],
        validator=lambda p: p,
    )
    pending_dispatch_plans.attach_resolver(plan.id, resolver or (lambda outcome: None))
    return plan


async def test_list_pending_dispatch_plans(api_client):
    plan = _register_plan("conv_x")
    resp = await api_client.get("/api/conversations/conv_x/pending-dispatch-plans")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["pendingDispatchPlans"]) == 1
    assert body["pendingDispatchPlans"][0]["id"] == plan.id


async def test_approve_pending_dispatch_plan(api_client):
    plan = _register_plan("conv_x")
    resp = await api_client.post(
        f"/api/conversations/conv_x/pending-dispatch-plans/{plan.id}",
        json={"action": "approve"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert pending_dispatch_plans.get(plan.id) is None


async def test_reject_pending_dispatch_plan(api_client):
    plan = _register_plan("conv_x")
    resp = await api_client.post(
        f"/api/conversations/conv_x/pending-dispatch-plans/{plan.id}",
        json={"action": "reject"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert pending_dispatch_plans.get(plan.id) is None


async def test_revise_pending_dispatch_plan(api_client, agents):
    conv = await conversation_service.create_conversation(
        mode="single", agent_ids=[agents["orch"]]
    )
    plan = _register_plan(conv.id)
    resp = await api_client.post(
        f"/api/conversations/{conv.id}/pending-dispatch-plans/{plan.id}",
        json={"action": "revise", "feedback": "do it differently"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert pending_dispatch_plans.get(plan.id) is None


async def test_revise_pending_dispatch_plan_invalid_body(api_client):
    plan = _register_plan("conv_x")
    resp = await api_client.post(
        f"/api/conversations/conv_x/pending-dispatch-plans/{plan.id}",
        json={"action": "revise", "feedback": ""},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


async def test_pending_dispatch_plan_wrong_conversation_404(api_client):
    plan = _register_plan("conv_x")
    resp = await api_client.post(
        f"/api/conversations/conv_other/pending-dispatch-plans/{plan.id}",
        json={"action": "approve"},
    )
    assert resp.status_code == 404
    assert resp.json() == {"error": "Pending dispatch plan not found"}
