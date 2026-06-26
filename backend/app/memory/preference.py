"""User preference management — async PG persistence.

Ported from AGI-memory ``internal/memory/preference.py``.
改造: sync psycopg2 → SQLAlchemy async session; user_id fixed to "default_user".
"""

import logging
import threading
import time
from typing import Dict, Tuple

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import UserPreference

logger = logging.getLogger(__name__)


class Preference:
    """User preference extraction and persistence.

    Thread-safe in-memory dict; async PG persistence via ``get_db()``.
    """

    def __init__(self, user_id: str = "default_user"):
        self.user_id = user_id
        self.preferences: Dict[str, str] = {}
        self._lock = threading.RLock()

    @property
    def data(self) -> Dict[str, str]:
        return self.preferences

    async def load_from_storage(self) -> None:
        """Load all preferences for this user from PG."""
        async with get_db() as session:
            stmt = select(UserPreference).where(UserPreference.user_id == self.user_id)
            result = await session.execute(stmt)
            rows = result.scalars().all()

        loaded = {r.key: r.value for r in rows}
        with self._lock:
            self.preferences = dict(loaded)
        logger.info("Loaded %d preferences for user %s", len(loaded), self.user_id)

    async def set(self, key: str, value: str) -> None:
        if not key or value is None:
            return
        with self._lock:
            self.preferences[key] = value
        try:
            async with get_db() as session:
                existing = await session.get(
                    UserPreference, {"user_id": self.user_id, "key": key}
                )
                if existing:
                    existing.value = value
                    existing.updated_at = time.time()
                else:
                    session.add(UserPreference(
                        user_id=self.user_id,
                        key=key,
                        value=value,
                        updated_at=time.time(),
                    ))
        except Exception as e:
            logger.warning("Preference save failed: %s", e)

    async def save_batch(self, kvs: Dict[str, str]) -> None:
        for k, v in (kvs or {}).items():
            await self.set(str(k), str(v))

    def get(self, key: str, default: str = "") -> str:
        with self._lock:
            return self.preferences.get(key, default)

    def get_all(self) -> Dict[str, str]:
        with self._lock:
            return dict(self.preferences)

    def snapshot(self) -> Dict[str, str]:
        return self.get_all()

    def extract_and_save_sync(self, msg: str) -> Tuple[str, str, bool]:
        """Synchronous preference extraction (for use in non-async hooks).

        Returns (key, value, matched). Rules: "我喜欢" / "我爱" / "我叫".
        DB write is deferred — caller should call ``set()`` separately if needed.
        """
        if not msg:
            return "", "", False

        rules = [
            ("我喜欢", "喜欢", "喜好"),
            ("我爱", "爱", "喜好"),
            ("我叫", "叫", "姓名"),
        ]
        for marker, sep, key in rules:
            if marker not in msg:
                continue
            parts = msg.split(sep, 1)
            if len(parts) < 2:
                continue
            value = parts[1].strip()
            if not value:
                continue
            with self._lock:
                self.preferences[key] = value
            return key, value, True
        return "", "", False

    async def extract_and_save(self, msg: str) -> Tuple[str, str, bool]:
        """Extract preference from user message and persist to PG."""
        key, value, matched = self.extract_and_save_sync(msg)
        if matched and key:
            await self.set(key, value)
        return key, value, matched

    def build_context(self) -> str:
        """Render preferences block for prompt injection. Empty → empty string."""
        snap = self.snapshot()
        if not snap:
            return ""
        lines = [f"{k}: {v}" for k, v in snap.items()]
        return "【用户偏好】\n" + "\n".join(lines)
