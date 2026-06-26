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
