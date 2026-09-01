"""桌面模式本地端点：前端强制登录准入的会话探测（design D6 / 任务 4.4）。

仅桌面模式挂载。GET /api/desktop/session 返回 cloud_session.json 缓存标记：
有标记 → 前端直接进入主界面（离线容忍）；无标记 → 前端进登录页。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.desktop import read_cloud_session

router = APIRouter()


@router.get("/desktop/session")
async def desktop_session() -> dict:
    session = read_cloud_session()
    return {
        "mode": "desktop",
        "loggedIn": session is not None,
        "user": session,
    }
