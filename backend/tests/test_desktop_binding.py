"""桌面目录绑定端点测试 — add-desktop-runtime 任务 5.3 / 5.4。"""

import pytest


@pytest.mark.asyncio
async def test_validate_bound_path_rejects_missing_dir(desktop_client, tmp_path):
    resp = await desktop_client.post(
        "/api/workspaces/validate-bound-path", json={"path": str(tmp_path / "nope")}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["safe"] is False
    assert "不存在" in data["reason"]


@pytest.mark.asyncio
async def test_validate_bound_path_rejects_file(desktop_client, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x", encoding="utf-8")
    resp = await desktop_client.post(
        "/api/workspaces/validate-bound-path", json={"path": str(f)}
    )
    data = resp.json()
    assert data["safe"] is False
    assert data["isDir"] is False


@pytest.mark.asyncio
async def test_validate_bound_path_rejects_sensitive_dir(desktop_client):
    # is_path_safe 的系统根裁决：Windows 驱动器根 / POSIX 根必被拒（home 本身也拒）
    import os

    home = os.path.abspath(os.path.expanduser("~"))
    resp = await desktop_client.post(
        "/api/workspaces/validate-bound-path", json={"path": home}
    )
    data = resp.json()
    assert data["safe"] is False
    assert data["reason"]


@pytest.mark.asyncio
async def test_validate_bound_path_accepts_normal_dir(desktop_client, tmp_path):
    resp = await desktop_client.post(
        "/api/workspaces/validate-bound-path", json={"path": str(tmp_path)}
    )
    data = resp.json()
    assert data["safe"] is True
    assert data["isDir"] is True


@pytest.mark.asyncio
async def test_recent_projects_dedupes_and_orders_by_recency(desktop_client):
    from app.db.engine import get_db
    from app.db.models import Conversation, Workspace
    from app.utils.clock import now_ms

    now = now_ms()
    async with get_db() as session:
        for i, (conv_id, path, created) in enumerate(
            [
                ("conv_a", r"D:\work\alpha", now - 3000),
                ("conv_b", r"D:\work\beta", now - 2000),
                ("conv_c", r"D:\work\alpha", now - 1000),  # alpha 更新 → 排第一
            ]
        ):
            conv = Conversation(
                id=conv_id,
                title=f"conv {i}",
                mode="single",
                agent_ids='["ag_x"]',
                pinned_message_ids="[]",
                bookmarked_message_ids="[]",
                archived=0,
                pinned_at=None,
                dispatch_mode="solo",
                created_at=created,
                updated_at=created,
            )
            ws = Workspace(
                id=f"ws_{conv_id}",
                conversation_id=conv_id,
                root_path=f"/tmp/{conv_id}",
                mode="local",
                bound_path=path,
                created_at=created,
            )
            session.add(conv)
            session.add(ws)

    resp = await desktop_client.get("/api/workspaces/recent-projects")
    assert resp.status_code == 200
    projects = resp.json()["projects"]
    paths = [p["path"] for p in projects]
    assert paths[0] == r"D:\work\alpha"  # 最近使用优先
    assert r"D:\work\beta" in paths
    assert len(paths) == len(set(paths))  # 去重
    assert projects[0]["lastUsedAt"] >= projects[-1]["lastUsedAt"]
