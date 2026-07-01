"""Per-run CODEX_HOME isolation for the Codex CLI adapter.

Creates a temporary ``$CODEX_HOME`` directory for each codex run so that:
- ``auth.json`` is symlinked from ``~/.codex/auth.json`` (shared token refresh)
- ``sessions/`` is symlinked from ``~/.codex/sessions/`` (shared logs)
- ``config.toml`` is copied from ``~/.codex/config.toml`` (isolated config) and
  augmented with a managed ``[mcp_servers.agenthub]`` TOML block pointing to
  ``scripts/agenthub-codex-mcp.mjs`` with per-run environment variables.

Inspired by Multica's ``execenv/codex_home.go``.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MCP_BLOCK_BEGIN = "# >>> agenthub managed mcp block begin"
_MCP_BLOCK_END = "# >>> agenthub managed mcp block end"


def prepare_codex_home(
    run_id: str,
    data_dir: str,
    mcp_env: dict[str, str],
    mcp_script_path: str,
) -> str:
    """Create an isolated CODEX_HOME for this run and return its path.

    Parameters
    ----------
    run_id : str
        Unique run identifier (used in directory name).
    data_dir : str
        AgentHub data directory (e.g. ``.agenthub-data``).
    mcp_env : dict[str, str]
        Environment variables to inject into the MCP server block.
    mcp_script_path : str
        Absolute path to ``agenthub-codex-mcp.mjs``.

    Returns
    -------
    str
        Absolute path to the per-run CODEX_HOME directory.
    """
    codex_home_dir = Path(data_dir) / "codex-home" / run_id
    codex_home_dir.mkdir(parents=True, exist_ok=True)

    user_codex_home = _get_user_codex_home()

    # symlink auth.json (shared token refresh)
    _symlink_shared_file(
        user_codex_home / "auth.json",
        codex_home_dir / "auth.json",
        is_dir=False,
    )

    # symlink sessions/ (shared logs)
    _symlink_shared_file(
        user_codex_home / "sessions",
        codex_home_dir / "sessions",
        is_dir=True,
    )

    # copy config.toml (isolated, will be augmented with MCP block)
    config_path = codex_home_dir / "config.toml"
    source_config = user_codex_home / "config.toml"
    if source_config.exists():
        shutil.copy2(source_config, config_path)
    else:
        config_path.touch()

    # inject MCP server block
    _inject_mcp_config(config_path, mcp_script_path, mcp_env)

    # set file permissions to 0o600 (may carry secrets in env)
    _set_secure_permissions(config_path)

    return str(codex_home_dir)


def cleanup_codex_home(codex_home_dir: str) -> None:
    """Remove a per-run CODEX_HOME directory (best-effort).

    Called after a run finishes; failures are logged but not raised.
    """
    try:
        p = Path(codex_home_dir)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("[codex_home] cleanup failed for %s: %s", codex_home_dir, e)


# ─── helpers ──────────────────────────────────────────────────


def _get_user_codex_home() -> Path:
    """Return ``~/.codex`` (or ``CODEX_HOME`` env if set globally)."""
    env_home = os.environ.get("CODEX_HOME")
    if env_home:
        return Path(env_home)
    return Path.home() / ".codex"


def _symlink_shared_file(
    source: Path, target: Path, *, is_dir: bool
) -> None:
    """Create a symlink at ``target`` pointing to ``source``.

    On Windows, symlinks require elevated privileges or developer mode.
    Fallback: for files, copy; for directories, create junction (dir) or copy.
    """
    if target.exists() or target.is_symlink():
        return  # already linked

    if not source.exists():
        # Source doesn't exist yet — create a placeholder so the symlink
        # target is valid when codex tries to read it.
        if is_dir:
            source.mkdir(parents=True, exist_ok=True)
        else:
            source.parent.mkdir(parents=True, exist_ok=True)
            source.touch()

    try:
        os.symlink(source, target, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        # Fallback for Windows without symlink privileges
        if is_dir:
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)


def _inject_mcp_config(
    config_path: Path,
    mcp_script_path: str,
    mcp_env: dict[str, str],
) -> None:
    """Write/replace the managed MCP block in ``config.toml``.

    Uses ``# >>> agenthub managed mcp block begin/end`` markers so that
    re-injection is idempotent.
    """
    existing = ""
    if config_path.exists():
        existing = config_path.read_text(encoding="utf-8")

    # Remove any previous managed block
    if _MCP_BLOCK_BEGIN in existing:
        before = existing.split(_MCP_BLOCK_BEGIN)[0]
        after_marker = existing.split(_MCP_BLOCK_END, 1)
        after = after_marker[1] if len(after_marker) > 1 else ""
        base = before.rstrip() + "\n" + after.rstrip()
    else:
        base = existing.rstrip()

    # Build the MCP block
    # TOML inline tables must be on a single line; use sub-table for env vars
    # to avoid multi-line inline table syntax errors.
    # All string values must be TOML-escaped (backslashes are escape chars in
    # double-quoted TOML strings, so Windows paths like D:\java become D:\\java).
    def _toml_escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')

    env_pairs = []
    for key in sorted(mcp_env.keys()):
        env_pairs.append(f'{key} = "{_toml_escape(mcp_env[key])}"')

    block_lines = [
        _MCP_BLOCK_BEGIN,
        "[mcp_servers.agenthub]",
        'command = "node"',
        f'args = ["{_toml_escape(mcp_script_path)}"]',
    ]
    if env_pairs:
        block_lines.append("[mcp_servers.agenthub.env]")
        block_lines.extend(env_pairs)
    block_lines.append(_MCP_BLOCK_END)

    block = "\n".join(block_lines)
    new_content = base + "\n\n" + block + "\n"

    config_path.write_text(new_content, encoding="utf-8")


def _set_secure_permissions(config_path: Path) -> None:
    """Set file mode to 0o600 on POSIX; no-op on Windows (ACL-based)."""
    if platform.system() == "Windows":
        return
    try:
        os.chmod(config_path, 0o600)
    except OSError as e:
        logger.warning("[codex_home] chmod 0o600 failed: %s", e)
