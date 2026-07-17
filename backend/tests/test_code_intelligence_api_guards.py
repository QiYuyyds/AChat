import asyncio
from pathlib import Path

from app.utils.clock import now_ms


class _FakeService:
    def schedule_enable(self, **kwargs):
        return asyncio.create_task(asyncio.sleep(0))

    def schedule_operation(self, **kwargs):
        return asyncio.create_task(asyncio.sleep(0))

    async def cancel(self, **kwargs):
        return True

    async def disable(self, **kwargs):
        return None


async def _create_local(api_client, agent_id: str, project_path: Path) -> str:
    project_path.mkdir()
    response = await api_client.post(
        "/api/conversations",
        json={
            "mode": "single",
            "agentIds": [agent_id],
            "boundPath": str(project_path),
        },
    )
    return response.json()["conversation"]["id"]


async def test_code_intelligence_requires_authentication(raw_client) -> None:
    response = await raw_client.get(
        "/api/conversations/conv_missing/code-intelligence"
    )
    assert response.status_code == 401


async def test_code_intelligence_enforces_conversation_ownership(
    api_client,
    raw_client,
    agents,
    db,
) -> None:
    from app.auth.jwt_handler import create_access_token
    from app.auth.password import hash_password
    from app.db.engine import get_db
    from app.db.models import User

    created = await api_client.post(
        "/api/conversations",
        json={"mode": "single", "agentIds": [agents["alice"]]},
    )
    conversation_id = created.json()["conversation"]["id"]
    async with get_db() as session:
        session.add(
            User(
                id="other_user",
                email="other@example.com",
                name="Other",
                password_hash=hash_password("testpass123"),
                token_version=0,
                created_at=now_ms(),
                updated_at=now_ms(),
            )
        )
    token = create_access_token("other_user", "other@example.com", 0)

    response = await raw_client.get(
        f"/api/conversations/{conversation_id}/code-intelligence",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


async def test_code_intelligence_rejects_sandbox_workspace(api_client, agents) -> None:
    created = await api_client.post(
        "/api/conversations",
        json={"mode": "single", "agentIds": [agents["alice"]]},
    )
    conversation_id = created.json()["conversation"]["id"]

    response = await api_client.get(
        f"/api/conversations/{conversation_id}/code-intelligence"
    )
    assert response.status_code == 400


async def test_operations_enforce_valid_state_transitions(
    api_client,
    agents,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.code_intelligence import service as code_service
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.services.fs_service import get_workspace_for_conversation

    conversation_id = await _create_local(
        api_client,
        agents["alice"],
        tmp_path / "local-project",
    )
    workspace = await get_workspace_for_conversation(conversation_id)
    assert workspace is not None
    store = MetadataStore(Path(workspace.root_path))
    monkeypatch.setattr(code_service, "_service", _FakeService())
    base = f"/api/conversations/{conversation_id}/code-intelligence"

    for operation in ("cancel", "sync", "rebuild", "retry", "disable"):
        response = await api_client.post(f"{base}/{operation}")
        assert response.status_code == 409, operation

    store.write(CodeIntelligenceMetadata(enabled=True, status="ready"))
    assert (await api_client.post(f"{base}/enable")).status_code == 409
    assert (await api_client.post(f"{base}/retry")).status_code == 409
    assert (await api_client.post(f"{base}/sync")).status_code == 202
    assert (await api_client.post(f"{base}/rebuild")).status_code == 202
    assert (await api_client.post(f"{base}/disable")).status_code == 200

    store.write(CodeIntelligenceMetadata(enabled=True, status="failed", error="boom"))
    assert (await api_client.post(f"{base}/retry")).status_code == 202
