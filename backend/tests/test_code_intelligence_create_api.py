from pathlib import Path


async def test_local_creation_accepts_code_intelligence_enabled(
    api_client,
    agents,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.code_intelligence import service as code_service

    project_path = tmp_path / "local-project"
    project_path.mkdir()
    scheduled: list[dict] = []
    monkeypatch.setattr(
        code_service,
        "schedule_workspace_enable",
        lambda **kwargs: scheduled.append(kwargs),
    )

    response = await api_client.post(
        "/api/conversations",
        json={
            "mode": "single",
            "agentIds": [agents["alice"]],
            "boundPath": str(project_path),
            "codeIntelligenceEnabled": True,
        },
    )

    assert response.status_code == 201
    assert response.json()["conversation"]["workspaceMode"] == "local"
    assert scheduled[0]["project_path"] == project_path.resolve()


async def test_code_intelligence_enabled_defaults_false(
    api_client,
    agents,
    monkeypatch,
) -> None:
    from app.code_intelligence import service as code_service

    scheduled: list[dict] = []
    monkeypatch.setattr(
        code_service,
        "schedule_workspace_enable",
        lambda **kwargs: scheduled.append(kwargs),
    )

    response = await api_client.post(
        "/api/conversations",
        json={"mode": "single", "agentIds": [agents["alice"]]},
    )

    assert response.status_code == 201
    assert scheduled == []
