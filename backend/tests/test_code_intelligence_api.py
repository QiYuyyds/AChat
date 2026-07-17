import asyncio
from pathlib import Path


class _FakeCodeIntelligenceService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def schedule_enable(self, **kwargs):
        self.calls.append("enable")
        return asyncio.create_task(asyncio.sleep(0))

    def schedule_operation(self, *, operation, **kwargs):
        self.calls.append(operation)
        return asyncio.create_task(asyncio.sleep(0))

    async def cancel(self, **kwargs):
        self.calls.append("cancel")
        return True

    async def disable(self, **kwargs):
        self.calls.append("disable")


async def test_authenticated_code_intelligence_endpoints_exist(
    api_client,
    agents,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.code_intelligence import service as code_service

    project_path = tmp_path / "local-project"
    project_path.mkdir()
    created = await api_client.post(
        "/api/conversations",
        json={
            "mode": "single",
            "agentIds": [agents["alice"]],
            "boundPath": str(project_path),
        },
    )
    conversation_id = created.json()["conversation"]["id"]
    fake = _FakeCodeIntelligenceService()
    monkeypatch.setattr(code_service, "_service", fake)

    status = await api_client.get(
        f"/api/conversations/{conversation_id}/code-intelligence"
    )
    assert status.status_code == 200
    assert status.json()["status"]["status"] == "disabled"

    expected = {
        "enable": 202,
        "cancel": 409,
        "sync": 409,
        "rebuild": 409,
        "retry": 409,
        "disable": 409,
    }
    for operation, expected_status in expected.items():
        response = await api_client.post(
            f"/api/conversations/{conversation_id}/code-intelligence/{operation}"
        )
        assert response.status_code == expected_status, operation

    assert fake.calls == ["enable"]
