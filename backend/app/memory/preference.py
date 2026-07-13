"""User preference management — async PG persistence.

Ported from AGI-memory ``internal/memory/preference.py``.
改造: sync psycopg2 → SQLAlchemy async session; user_id fixed to "default_user".
"""

import logging
import threading
import time
from typing import Dict, Tuple

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select

from app.db.engine import get_db
from app.db.models import UserPreference

logger = logging.getLogger(__name__)

# Preference values beyond this length are almost always conversation garbage
# rather than real preferences (names/fonts/styles rarely exceed 100 chars).
_PREFERENCE_VALUE_MAX = 200

# Synonym → canonical key mapping. Layer 1 of the three-layer dedup strategy:
# common Chinese synonyms are collapsed to a single canonical key so that
# "喜欢=Python" / "偏好=Python" / "偏爱=Python" all update the same row.
_KEY_SYNONYMS: Dict[str, str] = {
    "喜欢": "喜好",
    "偏好": "喜好",
    "偏爱": "喜好",
    "爱好": "喜好",
    "名字": "姓名",
    "名称": "姓名",
    "编程语言": "语言",
    "编程偏好": "语言",
}


def _normalize_key(key: str) -> str:
    """Return the canonical form of *key* via synonym mapping.

    Unknown keys pass through unchanged.
    """
    return _KEY_SYNONYMS.get(key, key)


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

    async def set(self, key: str, value: str, source: str = "extracted") -> None:
        if not key or value is None:
            return
        key = _normalize_key(key)
        value = str(value)
        if source == "extracted" and len(value) > _PREFERENCE_VALUE_MAX:
            value = value[: _PREFERENCE_VALUE_MAX - 3] + "..."
        try:
            async with get_db() as session:
                existing = await session.get(
                    UserPreference, {"user_id": self.user_id, "key": key}
                )
                if existing:
                    if source == "extracted" and existing.source == "manual":
                        return
                    existing.value = value
                    existing.source = source
                    existing.updated_at = time.time()
                else:
                    session.add(UserPreference(
                        user_id=self.user_id,
                        key=key,
                        value=value,
                        source=source,
                        updated_at=time.time(),
                    ))
        except Exception as e:
            logger.warning("Preference save failed: %s", e)
        with self._lock:
            self.preferences[key] = value

    async def save_batch(self, kvs: Dict[str, str], source: str = "extracted") -> None:
        for k, v in (kvs or {}).items():
            await self.set(_normalize_key(str(k)), str(v), source=source)

    async def delete(self, key: str) -> bool:
        """Delete a preference key from memory and PG.

        Returns True if the key existed and was deleted.
        """
        key = _normalize_key(key)
        existed = False
        with self._lock:
            if key in self.preferences:
                del self.preferences[key]
                existed = True
        try:
            async with get_db() as session:
                existing = await session.get(
                    UserPreference, {"user_id": self.user_id, "key": key}
                )
                if existing:
                    await session.delete(existing)
                    existed = True
        except Exception as e:
            logger.warning("Preference delete failed (key=%s): %s", key, e)
        return existed

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
        Extracted values are always ``source='extracted'``.
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
                self.preferences[_normalize_key(key)] = value
            return key, value, True
        return "", "", False

    async def extract_and_save(self, msg: str) -> Tuple[str, str, bool]:
        """Extract preference from user message and persist to PG."""
        key, value, matched = self.extract_and_save_sync(msg)
        if matched and key:
            await self.set(key, value, source="extracted")
        return key, value, matched

    def build_context(self) -> str:
        """Render preferences block for prompt injection. Empty → empty string."""
        snap = self.snapshot()
        if not snap:
            return ""
        lines = [f"{k}: {v}" for k, v in snap.items()]
        return "【用户偏好】\n" + "\n".join(lines)

    async def cleanup_oversized(self) -> int:
        """One-shot cleanup: drop preference rows whose value length exceeds the cap.

        Removes oversized entries from PG and the in-memory cache. Returns the
        number of rows deleted. Intended to run once after deploying the length
        cap to purge legacy garbage already persisted before the guard existed.
        """
        try:
            async with get_db() as session:
                stmt = sa_delete(UserPreference).where(
                    (UserPreference.user_id == self.user_id)
                    & (func.length(UserPreference.value) > _PREFERENCE_VALUE_MAX)
                )
                result = await session.execute(stmt)
                deleted = result.rowcount or 0
        except Exception as e:
            logger.warning("Preference cleanup query failed: %s", e)
            deleted = 0

        with self._lock:
            self.preferences = {
                k: v for k, v in self.preferences.items()
                if len(v) <= _PREFERENCE_VALUE_MAX
            }
        logger.info("Preference cleanup: removed %d oversized rows", deleted)
        return deleted
