from pathlib import Path


async def test_local_creation_auto_enables_code_intelligence(
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
        },
    )

    assert response.status_code == 201
    assert response.json()["conversation"]["workspaceMode"] == "local"
    assert scheduled[0]["project_path"] == project_path.resolve()


async def test_sandbox_creation_does_not_enable_code_intelligence(
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


async def test_local_creation_ignores_deprecated_code_intelligence_flag(
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

    # Even with codeIntelligenceEnabled=false, local workspace still auto-enables
    response = await api_client.post(
        "/api/conversations",
        json={
            "mode": "single",
            "agentIds": [agents["alice"]],
            "boundPath": str(project_path),
            "codeIntelligenceEnabled": False,
        },
    )

    assert response.status_code == 201
    assert response.json()["conversation"]["workspaceMode"] == "local"
    assert len(scheduled) == 1
