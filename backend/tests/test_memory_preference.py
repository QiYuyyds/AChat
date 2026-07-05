"""Unit tests for Preference — rule-based extraction, mock DB."""

import pytest
from unittest.mock import patch

from app.memory.preference import Preference


class TestPreference:
    def _make_pref(self) -> Preference:
        return Preference(user_id="default_user")

    def test_extract_i_like(self):
        pref = self._make_pref()
        key, value, matched = pref.extract_and_save_sync("我喜欢吃火锅")
        assert matched is True
        assert key == "喜好"
        assert "吃火锅" in value

    def test_extract_i_love(self):
        pref = self._make_pref()
        key, value, matched = pref.extract_and_save_sync("我爱编程")
        assert matched is True
        assert key == "喜好"
        assert "编程" in value

    def test_extract_my_name(self):
        pref = self._make_pref()
        key, value, matched = pref.extract_and_save_sync("我叫小明")
        assert matched is True
        assert key == "姓名"
        assert "小明" in value

    def test_extract_no_match(self):
        pref = self._make_pref()
        key, value, matched = pref.extract_and_save_sync("今天天气不错")
        assert matched is False
        assert key == ""

    def test_extract_empty_input(self):
        pref = self._make_pref()
        key, value, matched = pref.extract_and_save_sync("")
        assert matched is False

    def test_get_and_get_all(self):
        pref = self._make_pref()
        pref.extract_and_save_sync("我喜欢猫")
        pref.extract_and_save_sync("我叫小红")
        assert pref.get("姓名") == "小红"
        all_prefs = pref.get_all()
        assert "姓名" in all_prefs
        assert "喜好" in all_prefs

    def test_build_context_empty(self):
        pref = self._make_pref()
        assert pref.build_context() == ""

    def test_build_context_with_data(self):
        pref = self._make_pref()
        pref.extract_and_save_sync("我喜欢猫")
        ctx = pref.build_context()
        assert "用户偏好" in ctx
        assert "喜好" in ctx

    @pytest.mark.asyncio
    async def test_async_extract_and_save(self):
        """Async version should call set() when matched, with DB mocked."""
        pref = self._make_pref()
        with patch("app.memory.preference.get_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            key, value, matched = await pref.extract_and_save("我喜欢游泳")

        assert matched is True
        assert key == "喜好"
        # In-memory should still be updated even if DB failed
        assert pref.get("喜好") == "游泳"

    def test_snapshot_returns_copy(self):
        pref = self._make_pref()
        pref.extract_and_save_sync("我叫小明")
        snap = pref.snapshot()
        snap["姓名"] = "modified"
        assert pref.get("姓名") == "小明"


# ─── set() length validation (Task 1.2) ──────────────────────────────────────


class TestPreferenceSetLengthCap:
    def _make_pref(self) -> Preference:
        return Preference(user_id="default_user")

    @pytest.mark.asyncio
    async def test_normal_value_unchanged(self):
        pref = self._make_pref()
        with patch("app.memory.preference.get_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await pref.set("喜好", "清新风格")
        assert pref.get("喜好") == "清新风格"

    @pytest.mark.asyncio
    async def test_oversized_value_truncated(self):
        pref = self._make_pref()
        long_value = "x" * 500
        with patch("app.memory.preference.get_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await pref.set("喜好", long_value)
        stored = pref.get("喜好")
        assert len(stored) == 200
        assert stored.startswith("x" * 197)
        assert stored.endswith("...")

    @pytest.mark.asyncio
    async def test_boundary_200_chars_not_truncated(self):
        """A value of exactly 200 chars is kept verbatim — the cap is >200, not >=200."""
        pref = self._make_pref()
        value = "y" * 200
        with patch("app.memory.preference.get_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await pref.set("喜好", value)
        assert pref.get("喜好") == value
        assert len(pref.get("喜好")) == 200

    @pytest.mark.asyncio
    async def test_boundary_201_chars_truncated(self):
        """A value of 201 chars trips the cap → truncated to 197 + '...' = 200."""
        pref = self._make_pref()
        value = "z" * 201
        with patch("app.memory.preference.get_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await pref.set("喜好", value)
        stored = pref.get("喜好")
        assert len(stored) == 200
        assert stored.endswith("...")

    @pytest.mark.asyncio
    async def test_empty_key_ignored(self):
        pref = self._make_pref()
        with patch("app.memory.preference.get_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await pref.set("", "some value")
        assert pref.get_all() == {}

    @pytest.mark.asyncio
    async def test_cleanup_oversized_removes_long_in_memory(self):
        """cleanup_oversized purges in-memory entries exceeding the cap."""
        pref = self._make_pref()
        # Bypass set() to inject a legacy oversized value directly.
        pref.preferences["垃圾"] = "g" * 300
        pref.preferences["姓名"] = "小明"
        with patch("app.memory.preference.get_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            deleted = await pref.cleanup_oversized()
        # DB failed so rowcount is 0, but in-memory purge still runs.
        assert deleted == 0
        assert "垃圾" not in pref.get_all()
        assert pref.get("姓名") == "小明"


# ─── optimize-preference-system: Tasks 7.5-7.6 (key normalization) ──────────


from app.memory.preference import _normalize_key


class TestPreferenceKeyNormalization:
    """Tests for the synonym-based key normalization (Layer 1 dedup)."""

    def test_7_5_normalize_key_synonym(self):
        """Task 7.5: _normalize_key('喜欢') returns '喜好'."""
        assert _normalize_key("喜欢") == "喜好"
        assert _normalize_key("偏好") == "喜好"
        assert _normalize_key("偏爱") == "喜好"
        assert _normalize_key("爱好") == "喜好"
        assert _normalize_key("名字") == "姓名"
        assert _normalize_key("名称") == "姓名"
        assert _normalize_key("编程语言") == "语言"
        assert _normalize_key("编程偏好") == "语言"

    def test_7_5_normalize_key_unknown_passthrough(self):
        """Task 7.5: _normalize_key('unknown') returns 'unknown'."""
        assert _normalize_key("unknown") == "unknown"
        assert _normalize_key("编程框架") == "编程框架"
        assert _normalize_key("") == ""

    @pytest.mark.asyncio
    async def test_7_6_set_normalizes_synonym_to_existing_key(self):
        """Task 7.6: preference.set('喜欢', 'Python') normalizes to '喜好'
        and updates the existing entry instead of creating a new row.
        """
        pref = Preference(user_id="default_user")
        with patch("app.memory.preference.get_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            # First set with canonical key
            await pref.set("喜好", "Java")
            assert pref.get("喜好") == "Java"
            assert len(pref.get_all()) == 1

            # Now set with synonym — should update existing '喜好', not create '喜欢'
            await pref.set("喜欢", "Python")
            assert pref.get("喜好") == "Python"
            assert "喜欢" not in pref.get_all()
            assert len(pref.get_all()) == 1

    @pytest.mark.asyncio
    async def test_save_batch_normalizes_keys(self):
        """save_batch also normalizes keys before passing to set()."""
        pref = Preference(user_id="default_user")
        with patch("app.memory.preference.get_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await pref.save_batch({"喜欢": "Python", "名字": "小明"})
            assert pref.get("喜好") == "Python"
            assert pref.get("姓名") == "小明"
            assert "喜欢" not in pref.get_all()
            assert "名字" not in pref.get_all()
