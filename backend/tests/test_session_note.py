"""Unit tests for SessionNote — structured 10-section YAML session note.

Verifies:
- to_yaml → from_yaml roundtrip preserves all fields
- from_yaml returns None on invalid YAML / plain text
- to_xml produces correct format with all section tags + covers_up_to
- from_yaml can extract content from ```yaml code blocks
"""

import pytest

from app.memory.session_note import SessionNote


def _make_full_note() -> SessionNote:
    """Create a SessionNote with all fields populated."""
    return SessionNote(
        title="优化压缩系统",
        current_state="正在讨论结构化 Session Note 的设计方案",
        key_decisions=["[14:32] 用 Milvus BM25", "[15:07] 改用 SQLite"],
        files_touched=["backend/app/memory/session_note.py (已改)", "backend/app/memory/session_memory.py (已改)"],
        commands_run=["pytest backend/tests/test_session_note.py", "ruff check backend/app/memory/"],
        artifacts_produced=["docs/design.md"],
        blockers=["Milvus 不可用 [已解决]"],
        open_questions=["是否需要前端面板？"],
        next_steps=["实现 _enforce_section_limits", "修改 conversation_context.py"],
        architecture_understanding="四层压缩 + 五阶段 + 三分支",
        covers_up_to=1724773500000.0,
    )


# ─── to_yaml / from_yaml roundtrip ──────────────────────────────────────


def test_session_note_yaml_roundtrip():
    """to_yaml → from_yaml restores all fields."""
    note = _make_full_note()
    yaml_str = note.to_yaml()

    restored = SessionNote.from_yaml(yaml_str)
    assert restored is not None
    assert restored.title == note.title
    assert restored.current_state == note.current_state
    assert restored.key_decisions == note.key_decisions
    assert restored.files_touched == note.files_touched
    assert restored.commands_run == note.commands_run
    assert restored.artifacts_produced == note.artifacts_produced
    assert restored.blockers == note.blockers
    assert restored.open_questions == note.open_questions
    assert restored.next_steps == note.next_steps
    assert restored.architecture_understanding == note.architecture_understanding
    assert restored.covers_up_to == note.covers_up_to


def test_session_note_yaml_preserves_chinese():
    """YAML output should not escape Chinese characters."""
    note = SessionNote(title="会话标题测试", current_state="当前状态")
    yaml_str = note.to_yaml()
    assert "会话标题测试" in yaml_str
    assert "当前状态" in yaml_str


# ─── from_yaml returns None on invalid input ────────────────────────────


def test_from_yaml_returns_none_on_invalid():
    """from_yaml returns None for invalid YAML and plain text."""
    assert SessionNote.from_yaml("not valid yaml {{{") is None
    assert SessionNote.from_yaml("This is just plain text summary.") is None
    assert SessionNote.from_yaml("") is None
    assert SessionNote.from_yaml(None) is None  # type: ignore[arg-type]


def test_from_yaml_extracts_code_block():
    """from_yaml can extract content from ```yaml code blocks."""
    raw = """Here is the note:

```yaml
title: 测试标题
current_state: 正在测试
key_decisions:
  - "[14:32] 决策A"
```
"""
    note = SessionNote.from_yaml(raw)
    assert note is not None
    assert note.title == "测试标题"
    assert note.current_state == "正在测试"
    assert note.key_decisions == ["[14:32] 决策A"]


# ─── to_xml format ──────────────────────────────────────────────────────


def test_to_xml_format():
    """to_xml produces correct XML with all section tags and covers_up_to."""
    note = _make_full_note()
    xml = note.to_xml()

    assert '<session_note covers_up_to="1724773500000.0">' in xml
    assert "</session_note>" in xml
    assert "<title>优化压缩系统</title>" in xml
    assert "<current_state>" in xml
    assert "<key_decisions>" in xml
    assert "<files_touched>" in xml
    assert "<commands_run>" in xml
    assert "<artifacts_produced>" in xml
    assert "<blockers>" in xml
    assert "<open_questions>" in xml
    assert "<next_steps>" in xml
    assert "<architecture_understanding>" in xml

    # Check list items are wrapped in <item>
    assert "<item>[14:32] 用 Milvus BM25</item>" in xml
    assert "<item>[15:07] 改用 SQLite</item>" in xml


def test_to_xml_without_covers_up_to():
    """to_xml works when covers_up_to is None."""
    note = SessionNote(title="test", current_state="state")
    xml = note.to_xml()
    assert "<session_note>" in xml
    assert "covers_up_to" not in xml


def test_to_xml_escapes_special_chars():
    """to_xml escapes XML special characters."""
    note = SessionNote(
        title="a < b & c > d",
        current_state="x < y",
    )
    xml = note.to_xml()
    assert "&lt;" in xml
    assert "&amp;" in xml
    assert "&gt;" in xml
    assert "< b" not in xml


# ─── _enforce_section_limits ─────────────────────────────────────────────


def test_enforce_section_limits_trims_lists():
    """_enforce_section_limits trims oversized lists to their limits."""
    from app.memory.session_note import _enforce_section_limits, SECTION_LIMITS

    note = SessionNote(
        key_decisions=[f"decision_{i}" for i in range(25)],
        files_touched=[f"file_{i}" for i in range(35)],
        commands_run=[f"cmd_{i}" for i in range(25)],
        artifacts_produced=[f"artifact_{i}" for i in range(15)],
        blockers=[f"blocker_{i}" for i in range(15)],
        open_questions=[f"question_{i}" for i in range(15)],
    )

    result = _enforce_section_limits(note)

    assert len(result.key_decisions) == SECTION_LIMITS["key_decisions"]
    assert len(result.files_touched) == SECTION_LIMITS["files_touched"]
    assert len(result.commands_run) == SECTION_LIMITS["commands_run"]
    assert len(result.artifacts_produced) == SECTION_LIMITS["artifacts_produced"]
    assert len(result.blockers) == SECTION_LIMITS["blockers"]
    assert len(result.open_questions) == SECTION_LIMITS["open_questions"]


# ─── _compress_note ──────────────────────────────────────────────────────


def test_compress_note_merges_similar():
    """_compress_note merges similar key_decisions entries."""
    from app.memory.session_note import _compress_note

    note = SessionNote(
        key_decisions=[
            "改了 a.py 文件",
            "改了 b.py 文件",
            "改了 c.py 文件",
            "改了 d.py 文件",
        ],
    )
    result = _compress_note(note)
    assert len(result.key_decisions) < 4
    assert any("等" in d and "条" in d for d in result.key_decisions)


def test_compress_note_removes_old_resolved():
    """_compress_note keeps only recent 5 resolved blockers."""
    from app.memory.session_note import _compress_note

    blockers = [f"blocker_{i} [已解决]" for i in range(6)]
    blockers.append("unresolved_blocker")
    note = SessionNote(blockers=blockers)

    result = _compress_note(note)
    resolved = [b for b in result.blockers if "[已解决]" in b]
    unresolved = [b for b in result.blockers if "[已解决]" not in b]
    assert len(resolved) == 5
    assert len(unresolved) == 1
