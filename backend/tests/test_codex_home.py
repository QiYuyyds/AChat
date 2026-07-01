"""Unit tests for codex_home.py — CODEX_HOME isolation and MCP config injection.

Tests cover:
- Per-run directory creation
- auth.json / sessions symlink (or copy on Windows)
- config.toml MCP block injection (format, idempotency)
- File permissions (0o600 on POSIX)
- cleanup_codex_home
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest

from app.adapters.codex_home import (
    _MCP_BLOCK_BEGIN,
    _MCP_BLOCK_END,
    cleanup_codex_home,
    prepare_codex_home,
)


@pytest.fixture
def fake_codex_home(tmp_path, monkeypatch):
    """Set CODEX_HOME to a fake ~/.codex with auth.json, sessions/, config.toml."""
    fake_home = tmp_path / "fake-codex"
    fake_home.mkdir()

    # auth.json
    (fake_home / "auth.json").write_text('{"token": "test"}', encoding="utf-8")

    # sessions dir
    (fake_home / "sessions").mkdir()
    (fake_home / "sessions" / "session1.json").write_text("{}", encoding="utf-8")

    # config.toml with user content
    (fake_home / "config.toml").write_text(
        "# user config\nmodel = \"gpt-5-codex\"\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_HOME", str(fake_home))
    return fake_home


@pytest.fixture
def empty_codex_home(tmp_path, monkeypatch):
    """Set CODEX_HOME to a non-existent dir (no auth.json, no config.toml)."""
    fake_home = tmp_path / "empty-codex"
    # Don't create the directory — prepare_codex_home should handle missing source
    monkeypatch.setenv("CODEX_HOME", str(fake_home))
    return fake_home


# ─── prepare_codex_home: directory structure ─────────────────────


def test_prepare_creates_per_run_directory(tmp_path, fake_codex_home):
    """prepare_codex_home creates <data_dir>/codex-home/<run_id>/ directory."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_123",
        data_dir=data_dir,
        mcp_env={"AGENTHUB_RUN_ID": "run_123"},
        mcp_script_path="/path/to/mcp.mjs",
    )
    assert Path(result).exists()
    assert Path(result).is_dir()
    assert "run_123" in result


def test_prepare_symlinks_auth_json(tmp_path, fake_codex_home):
    """auth.json is symlinked (or copied) from ~/.codex/auth.json."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_auth",
        data_dir=data_dir,
        mcp_env={},
        mcp_script_path="/path/to/mcp.mjs",
    )
    auth_link = Path(result) / "auth.json"
    assert auth_link.exists()
    content = auth_link.read_text(encoding="utf-8")
    assert "test" in content


def test_prepare_symlinks_sessions_dir(tmp_path, fake_codex_home):
    """sessions/ is symlinked (or copied) from ~/.codex/sessions/."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_sess",
        data_dir=data_dir,
        mcp_env={},
        mcp_script_path="/path/to/mcp.mjs",
    )
    sessions_link = Path(result) / "sessions"
    assert sessions_link.exists()
    assert (sessions_link / "session1.json").exists()


def test_prepare_copies_config_toml(tmp_path, fake_codex_home):
    """config.toml is copied from ~/.codex/config.toml and preserves user content."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_cfg",
        data_dir=data_dir,
        mcp_env={},
        mcp_script_path="/path/to/mcp.mjs",
    )
    config = Path(result) / "config.toml"
    assert config.exists()
    content = config.read_text(encoding="utf-8")
    # User content preserved
    assert "gpt-5-codex" in content


# ─── MCP config injection ────────────────────────────────────────


def test_mcp_block_has_correct_format(tmp_path, fake_codex_home):
    """MCP block has begin/end markers, command, args, and env."""
    data_dir = str(tmp_path / "data")
    mcp_env = {
        "AGENTHUB_INTERNAL_BASE_URL": "http://127.0.0.1:3000",
        "AGENTHUB_INTERNAL_TOOL_TOKEN": "secret-token",
        "AGENTHUB_CONVERSATION_ID": "conv_123",
        "AGENTHUB_AGENT_ID": "ag_456",
        "AGENTHUB_RUN_ID": "run_789",
        "AGENTHUB_ALLOWED_TOOLS": "write_artifact,fs_read",
    }
    result = prepare_codex_home(
        run_id="run_mcp",
        data_dir=data_dir,
        mcp_env=mcp_env,
        mcp_script_path="/abs/path/to/agenthub-codex-mcp.mjs",
    )
    config = Path(result) / "config.toml"
    content = config.read_text(encoding="utf-8")

    # Markers present
    assert _MCP_BLOCK_BEGIN in content
    assert _MCP_BLOCK_END in content

    # MCP server block
    assert "[mcp_servers.agenthub]" in content
    assert 'command = "node"' in content
    assert 'args = ["/abs/path/to/agenthub-codex-mcp.mjs"]' in content

    # Env vars (sorted)
    assert "AGENTHUB_AGENT_ID" in content
    assert "AGENTHUB_ALLOWED_TOOLS" in content
    assert "AGENTHUB_CONVERSATION_ID" in content
    assert "AGENTHUB_INTERNAL_BASE_URL" in content
    assert "AGENTHUB_INTERNAL_TOOL_TOKEN" in content
    assert "AGENTHUB_RUN_ID" in content
    assert "secret-token" in content


def test_mcp_block_idempotent(tmp_path, fake_codex_home):
    """Re-injecting MCP config replaces the old block, no duplicates."""
    data_dir = str(tmp_path / "data")
    mcp_env1 = {"AGENTHUB_RUN_ID": "run_old"}
    result = prepare_codex_home(
        run_id="run_idem",
        data_dir=data_dir,
        mcp_env=mcp_env1,
        mcp_script_path="/path/mcp.mjs",
    )
    config = Path(result) / "config.toml"

    # Re-inject with different env
    from app.adapters.codex_home import _inject_mcp_config

    mcp_env2 = {"AGENTHUB_RUN_ID": "run_new"}
    _inject_mcp_config(config, "/path/mcp.mjs", mcp_env2)

    content = config.read_text(encoding="utf-8")
    # Only one begin marker
    assert content.count(_MCP_BLOCK_BEGIN) == 1
    assert content.count(_MCP_BLOCK_END) == 1
    # New env replaces old
    assert "run_new" in content
    assert "run_old" not in content


def test_mcp_block_preserves_user_config(tmp_path, fake_codex_home):
    """User config content outside the managed block is preserved."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_user",
        data_dir=data_dir,
        mcp_env={"AGENTHUB_RUN_ID": "run_user"},
        mcp_script_path="/path/mcp.mjs",
    )
    config = Path(result) / "config.toml"
    content = config.read_text(encoding="utf-8")
    # User's model setting preserved
    assert 'model = "gpt-5-codex"' in content


def test_mcp_block_with_empty_env(tmp_path, fake_codex_home):
    """MCP block is valid even with empty env dict."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_empty",
        data_dir=data_dir,
        mcp_env={},
        mcp_script_path="/path/mcp.mjs",
    )
    config = Path(result) / "config.toml"
    content = config.read_text(encoding="utf-8")
    assert "[mcp_servers.agenthub]" in content
    assert "env = {" not in content  # no env block when empty


# ─── empty source handling ───────────────────────────────────────


def test_prepare_with_missing_source_files(tmp_path, empty_codex_home):
    """prepare_codex_home handles missing ~/.codex/auth.json and config.toml."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_missing",
        data_dir=data_dir,
        mcp_env={"AGENTHUB_RUN_ID": "run_missing"},
        mcp_script_path="/path/mcp.mjs",
    )
    # auth.json created as placeholder
    auth = Path(result) / "auth.json"
    assert auth.exists()

    # config.toml created as empty file + MCP block
    config = Path(result) / "config.toml"
    assert config.exists()
    content = config.read_text(encoding="utf-8")
    assert _MCP_BLOCK_BEGIN in content


# ─── file permissions ────────────────────────────────────────────


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="File permissions are not applicable on Windows (ACL-based)",
)
def test_config_permissions_0600(tmp_path, fake_codex_home):
    """config.toml has 0o600 permissions on POSIX."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_perm",
        data_dir=data_dir,
        mcp_env={"AGENTHUB_TOKEN": "secret"},
        mcp_script_path="/path/mcp.mjs",
    )
    config = Path(result) / "config.toml"
    mode = config.stat().st_mode & 0o777
    assert mode == 0o600


# ─── cleanup ─────────────────────────────────────────────────────


def test_cleanup_removes_directory(tmp_path, fake_codex_home):
    """cleanup_codex_home removes the per-run directory."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_cleanup",
        data_dir=data_dir,
        mcp_env={},
        mcp_script_path="/path/mcp.mjs",
    )
    assert Path(result).exists()

    cleanup_codex_home(result)
    assert not Path(result).exists()


def test_cleanup_nonexistent_is_safe(tmp_path):
    """cleanup_codex_home on a non-existent path does not raise."""
    cleanup_codex_home(str(tmp_path / "does-not-exist"))


def test_cleanup_handles_exceptions(tmp_path, fake_codex_home, monkeypatch):
    """cleanup_codex_home swallows exceptions (best-effort)."""
    data_dir = str(tmp_path / "data")
    result = prepare_codex_home(
        run_id="run_err",
        data_dir=data_dir,
        mcp_env={},
        mcp_script_path="/path/mcp.mjs",
    )

    # Patch shutil.rmtree to raise
    import app.adapters.codex_home as ch_mod

    original_rmtree = ch_mod.shutil.rmtree

    def raising_rmtree(*args, **kwargs):
        raise PermissionError("simulated")

    monkeypatch.setattr(ch_mod.shutil, "rmtree", raising_rmtree)
    # Should not raise
    cleanup_codex_home(result)
