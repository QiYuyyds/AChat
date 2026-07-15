"""Tests for O8: skill_auto_activator hook (auto-derived rules + dual handlers).

Tests mock list_skills() to provide controlled skill registries with
trigger_keywords, then verify post_tool_use and on_run_start matching logic,
skill existence checks, and load_skill availability checks.
"""

from __future__ import annotations

from unittest.mock import patch

from app.services.hook_registry import HookContext, HookEvent, HookRegistry
from app.services.hooks import skill_auto_activator
from app.services.hooks.skill_auto_activator import (
    _build_rule_table,
    _match_tool_against_keywords,
    _on_run_start,
    _post_tool_use,
    register,
)
from app.services.skill_service import SkillMeta


def _mock_skills(*metas: SkillMeta):
    """Create a patch context that makes list_skills() return the given metas."""
    return patch("app.services.skill_service.list_skills", return_value=list(metas))


# ── Test fixtures ──

SKILL_PY = SkillMeta(
    slug="python-best-practices",
    name="Python Best Practices",
    description="Python guide",
    trigger_keywords=["python", "pytest", ".py", "pip"],
)

SKILL_FRONTEND = SkillMeta(
    slug="frontend-guidelines",
    name="Frontend Guidelines",
    description="Frontend guide",
    trigger_keywords=["tsx", "jsx", "pnpm", "npm"],
)

SKILL_NO_KW = SkillMeta(
    slug="generic-skill",
    name="Generic",
    description="No trigger keywords",
    trigger_keywords=[],
)

TOOL_NAMES_WITH_LOAD = ["fs_read", "fs_write", "bash", "load_skill"]
TOOL_NAMES_WITHOUT_LOAD = ["fs_read", "fs_write", "bash"]


# ── Section 2: _build_rule_table ──


def test_build_rule_table_includes_skills_with_keywords():
    with _mock_skills(SKILL_PY, SKILL_NO_KW):
        table = _build_rule_table()
    assert "python-best-practices" in table
    assert table["python-best-practices"] == ["python", "pytest", ".py", "pip"]
    assert "generic-skill" not in table


def test_build_rule_table_excludes_skills_without_keywords():
    with _mock_skills(SKILL_NO_KW):
        table = _build_rule_table()
    assert table == {}


def test_build_rule_table_empty_registry():
    with _mock_skills():
        table = _build_rule_table()
    assert table == {}


# ── Section 2: _match_tool_against_keywords ──


def test_match_fs_write_extension():
    assert _match_tool_against_keywords("fs_write", {"path": "src/main.py"}, [".py"]) is True


def test_match_fs_write_case_insensitive():
    assert _match_tool_against_keywords("fs_write", {"path": "src/Main.PY"}, [".py"]) is True


def test_match_fs_write_no_match():
    assert _match_tool_against_keywords("fs_write", {"path": "readme.txt"}, [".py"]) is False


def test_match_bash_substring():
    assert _match_tool_against_keywords("bash", {"command": "pytest -v"}, ["pytest"]) is True


def test_match_bash_case_insensitive():
    assert _match_tool_against_keywords("bash", {"command": "PNPM install"}, ["pnpm"]) is True


def test_match_bash_no_match():
    assert _match_tool_against_keywords("bash", {"command": "ls -la"}, ["pytest"]) is False


def test_match_other_tool_no_match():
    assert _match_tool_against_keywords("fs_read", {"path": "test.py"}, [".py"]) is False


# ── Section 3: post_tool_use handler ──


async def test_post_tool_use_fs_write_py_matches():
    with _mock_skills(SKILL_PY):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.POST_TOOL_USE,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            tool_name="fs_write",
            args={"path": "src/main.py", "content": "print('hello')"},
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _post_tool_use(ctx)
    assert result is not None
    assert result.action == "inject"
    assert isinstance(result.data, list)
    assert len(result.data) == 1
    assert result.data[0]["type"] == "system_hint"
    assert "python-best-practices" in result.data[0]["content"]
    assert "load_skill" in result.data[0]["content"]


async def test_post_tool_use_bash_pnpm_matches():
    with _mock_skills(SKILL_FRONTEND):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.POST_TOOL_USE,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            tool_name="bash",
            args={"command": "pnpm install"},
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _post_tool_use(ctx)
    assert result is not None
    assert result.action == "inject"
    assert "frontend-guidelines" in result.data[0]["content"]


async def test_post_tool_use_skill_not_exists_skips():
    """Skill slug in rule table but not in list_skills → allow."""
    with _mock_skills():  # empty registry, but _RULE_TABLE has stale entries
        skill_auto_activator._RULE_TABLE = {"python-best-practices": ["python", ".py"]}
        ctx = HookContext(
            event=HookEvent.POST_TOOL_USE,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            tool_name="fs_write",
            args={"path": "app.py", "content": "x = 1"},
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _post_tool_use(ctx)
    assert result is not None
    assert result.action == "allow"


async def test_post_tool_use_load_skill_not_in_tools_skips():
    """Skill exists but load_skill not in tool_names → allow."""
    with _mock_skills(SKILL_PY):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.POST_TOOL_USE,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            tool_name="fs_write",
            args={"path": "app.py", "content": "x = 1"},
            tool_names=TOOL_NAMES_WITHOUT_LOAD,
        )
        result = await _post_tool_use(ctx)
    assert result is not None
    assert result.action == "allow"


async def test_post_tool_use_fs_read_no_match():
    with _mock_skills(SKILL_PY):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.POST_TOOL_USE,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            tool_name="fs_read",
            args={"path": "config.json"},
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _post_tool_use(ctx)
    assert result is not None
    assert result.action == "allow"


async def test_post_tool_use_case_insensitive_extension():
    with _mock_skills(SKILL_PY):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.POST_TOOL_USE,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            tool_name="fs_write",
            args={"path": "src/Main.PY", "content": "pass"},
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _post_tool_use(ctx)
    assert result is not None
    assert result.action == "inject"
    assert "python-best-practices" in result.data[0]["content"]


async def test_post_tool_use_non_matching_extension():
    with _mock_skills(SKILL_PY):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.POST_TOOL_USE,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            tool_name="fs_write",
            args={"path": "readme.txt", "content": "hello"},
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _post_tool_use(ctx)
    assert result is not None
    assert result.action == "allow"


# ── Section 4: on_run_start handler ──


async def test_on_run_start_matches_user_keyword():
    with _mock_skills(SKILL_PY):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.ON_RUN_START,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "帮我写个 Python 脚本"},
            ],
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _on_run_start(ctx)
    assert result is not None
    assert result.action == "inject"
    assert "python-best-practices" in result.data[0]["content"]


async def test_on_run_start_no_keyword_match():
    with _mock_skills(SKILL_PY):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.ON_RUN_START,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            messages=[
                {"role": "user", "content": "今天天气怎么样"},
            ],
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _on_run_start(ctx)
    assert result is not None
    assert result.action == "allow"


async def test_on_run_start_empty_messages():
    with _mock_skills(SKILL_PY):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.ON_RUN_START,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            messages=[],
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _on_run_start(ctx)
    assert result is not None
    assert result.action == "allow"


async def test_on_run_start_load_skill_not_in_tools():
    with _mock_skills(SKILL_PY):
        skill_auto_activator._RULE_TABLE = _build_rule_table()
        ctx = HookContext(
            event=HookEvent.ON_RUN_START,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            messages=[
                {"role": "user", "content": "帮我写个 Python 脚本"},
            ],
            tool_names=TOOL_NAMES_WITHOUT_LOAD,
        )
        result = await _on_run_start(ctx)
    assert result is not None
    assert result.action == "allow"


async def test_on_run_start_skill_not_exists():
    with _mock_skills():  # empty registry
        skill_auto_activator._RULE_TABLE = {"python-best-practices": ["python"]}
        ctx = HookContext(
            event=HookEvent.ON_RUN_START,
            run_id="r1",
            agent_id="a1",
            conversation_id="c1",
            messages=[
                {"role": "user", "content": "写个 Python 脚本"},
            ],
            tool_names=TOOL_NAMES_WITH_LOAD,
        )
        result = await _on_run_start(ctx)
    assert result is not None
    assert result.action == "allow"


# ── Section 4: register function ──


def test_register_adds_both_handlers():
    with _mock_skills(SKILL_PY):
        registry = HookRegistry()
        register(registry)
        assert registry.has_handlers(HookEvent.POST_TOOL_USE)
        assert registry.has_handlers(HookEvent.ON_RUN_START)
