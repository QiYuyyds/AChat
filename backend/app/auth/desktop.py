"""桌面模式身份：固定本地用户 + 云端会话缓存标记。

桌面模式（AGENTHUB_DESKTOP=1）下本地 API 以固定本地用户作用域执行，
不做逐请求 JWT 验证（platform-security delta 例外）。本地用户与云端登录
账号相互独立：云端账号仅用于认证代理与后续统计上报，不回写本地数据。

强制登录判定：data dir 下 `cloud_session.json`（登录成功写入、登出清除）；
有标记 → 允许进入（离线容忍），无标记 → 登录页。
"""

from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.password import hash_password
from app.config import get_settings
from app.db.models import User
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

LOCAL_USER_ID = "local_desktop_user"
LOCAL_USER_EMAIL = "local@desktop.local"
LOCAL_USER_NAME = "本地用户"

CLOUD_SESSION_FILE = "cloud_session.json"


def is_desktop_mode() -> bool:
    return get_settings().agenthub_desktop


async def get_or_seed_local_user(db: AsyncSession) -> User:
    """返回固定本地用户；不存在则幂等 seed（并发首启安全）。"""
    result = await db.execute(select(User).where(User.id == LOCAL_USER_ID))
    user = result.scalar_one_or_none()
    if user is not None:
        return user

    # password_hash 不可用于登录（随机口令，无人持有）
    user = User(
        id=LOCAL_USER_ID,
        email=LOCAL_USER_EMAIL,
        name=LOCAL_USER_NAME,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        token_version=0,
        created_at=now_ms(),
        updated_at=now_ms(),
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        # 并发首启的竞态：另一请求已 seed，回退到读取
        await db.rollback()
        result = await db.execute(select(User).where(User.id == LOCAL_USER_ID))
        user = result.scalar_one_or_none()
        if user is None:
            raise
    return user


def cloud_session_path() -> Path:
    return get_settings().data_path / CLOUD_SESSION_FILE


def read_cloud_session() -> dict[str, Any] | None:
    """读取云端会话缓存标记；无标记或损坏返回 None。"""
    try:
        parsed = json.loads(cloud_session_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(parsed, dict) or not parsed.get("email"):
        return None
    return parsed


def write_cloud_session(user: dict[str, Any]) -> None:
    """登录成功后写入缓存标记（email / name / loggedInAt）。"""
    payload = {
        "email": user.get("email"),
        "name": user.get("name"),
        "loggedInAt": now_ms(),
    }
    path = cloud_session_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("cloud session marker written (%s)", payload.get("email"))


def clear_cloud_session() -> None:
    """登出时清除缓存标记；文件不存在时静默。"""
    try:
        cloud_session_path().unlink()
        logger.info("cloud session marker cleared")
    except OSError:
        pass
