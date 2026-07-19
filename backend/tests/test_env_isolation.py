"""Unit tests for backend/app/utils/env_isolation.py.

Covers:
- ``detect_achat_venv()`` — venv vs system Python (monkeypatch sys.executable).
- ``clean_path()`` — Windows / POSIX PATH scrubbing.
- ``build_tool_env()`` — blacklist removal, PATH scrub, project venv prepend,
  preservation of user system variables (JAVA_HOME etc.).
- ``detect_project_venv()`` — .venv present / absent.
- ``is_internal_env_key()`` — blacklist membership (incl. Windows casefold).

These tests are pure-function; no real subprocess or filesystem venv is
created (we use tmp_path for the project venv case).
"""

from __future__ import annotations

import os
import sys

import pytest

from app.utils import env_isolation
from app.utils.env_isolation import (
    _BLACKLISTED_KEYS,
    build_tool_env,
    clean_path,
    detect_achat_venv,
    detect_project_venv,
    is_internal_env_key,
)
from app.utils.platform import IS_WINDOWS


def _venv_bin_name() -> str:
    """Return the venv bin subdir name for the current platform."""
    return "Scripts" if IS_WINDOWS else "bin"


def _python_exe_name() -> str:
    """Return the python executable name for the current platform."""
    return "python.exe" if IS_WINDOWS else "python"


# ─── is_internal_env_key ────────────────────────────────────────────────────


class TestIsInternalEnvKey:
    def test_blacklisted_keys_are_internal(self):
        for key in _BLACKLISTED_KEYS:
            assert is_internal_env_key(key), f"{key} should be blacklisted"

    def test_user_system_vars_are_not_internal(self):
        assert not is_internal_env_key("JAVA_HOME")
        assert not is_internal_env_key("ANDROID_SDK_ROOT")
        assert not is_internal_env_key("PATH")
        assert not is_internal_env_key("HOME")
        assert not is_internal_env_key("USERPROFILE")

    @pytest.mark.skipif(not IS_WINDOWS, reason="Windows case-insensitive env")
    def test_windows_case_insensitive(self):
        assert is_internal_env_key("virtual_env")
        assert is_internal_env_key("Database_Url")
        assert is_internal_env_key("SECRET_key")

    @pytest.mark.skipif(IS_WINDOWS, reason="POSIX case-sensitive env")
    def test_posix_case_sensitive(self):
        # On POSIX, only the exact-case key is blacklisted.
        assert not is_internal_env_key("virtual_env")
        assert not is_internal_env_key("VIRTUAL_ENV".lower())

    def test_empty_key(self):
        assert not is_internal_env_key("")


# ─── detect_achat_venv ─────────────────────────────────────────────────────


class TestDetectAchatVenv:
    def test_system_python_returns_none(self, monkeypatch):
        # /usr/bin/python3 → bin parent is /usr, no pyvenv.cfg
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
        assert detect_achat_venv() is None

    def test_venv_detected(self, tmp_path, monkeypatch):
        # Build the venv layout that matches the current platform.
        venv_root = tmp_path / "myvenv"
        bin_dir = venv_root / _venv_bin_name()
        bin_dir.mkdir(parents=True)
        (venv_root / "pyvenv.cfg").write_text("home = /usr/bin\n")
        python_exe = bin_dir / _python_exe_name()
        python_exe.write_text("stub")
        monkeypatch.setattr(sys, "executable", str(python_exe))
        result = detect_achat_venv()
        assert result is not None
        assert os.path.normcase(result) == os.path.normcase(str(venv_root))

    def test_no_pyvenv_cfg_returns_none(self, tmp_path, monkeypatch):
        # bin dir exists but no pyvenv.cfg → not a venv
        bin_dir = tmp_path / _venv_bin_name()
        bin_dir.mkdir()
        python_exe = bin_dir / _python_exe_name()
        python_exe.write_text("stub")
        monkeypatch.setattr(sys, "executable", str(python_exe))
        # parent of bin_dir is tmp_path, no pyvenv.cfg there
        assert detect_achat_venv() is None

    def test_wrong_bin_dir_name_returns_none(self, tmp_path, monkeypatch):
        # A "bin" dir on Windows (where "Scripts" is expected) → not a venv.
        wrong_name = "bin" if IS_WINDOWS else "Scripts"
        bin_dir = tmp_path / wrong_name
        bin_dir.mkdir()
        python_exe = bin_dir / _python_exe_name()
        python_exe.write_text("stub")
        (tmp_path / "pyvenv.cfg").write_text("home = /usr/bin\n")
        monkeypatch.setattr(sys, "executable", str(python_exe))
        assert detect_achat_venv() is None


# ─── clean_path ─────────────────────────────────────────────────────────────


class TestCleanPath:
    def test_none_venv_returns_path_unchanged(self):
        path = os.pathsep.join(["/usr/bin", "/usr/local/bin"])
        assert clean_path(path, None) == path

    def test_empty_path(self):
        assert clean_path("", "/some/venv") == ""

    def test_removes_venv_bin_segment(self):
        # Build a PATH using the current platform's separator and bin name.
        if IS_WINDOWS:
            venv = r"D:\home\user\.venv"
            venv_bin = r"D:\home\user\.venv\Scripts"
            other1 = r"C:\Windows\System32"
            other2 = r"C:\Windows"
        else:
            venv = "/home/user/.venv"
            venv_bin = "/home/user/.venv/bin"
            other1 = "/usr/bin"
            other2 = "/usr/local/bin"
        path = os.pathsep.join([venv_bin, other1, other2])
        cleaned = clean_path(path, venv)
        segments = cleaned.split(os.pathsep)
        assert venv_bin not in segments
        assert other1 in segments
        assert other2 in segments

    @pytest.mark.skipif(not IS_WINDOWS, reason="Windows PATH case-insensitive")
    def test_removes_venv_scripts_segment_windows_casefold(self):
        venv = r"D:\project\.venv"
        venv_scripts = r"D:\PROJECT\.venV\Scripts"
        path = f"{venv_scripts};C:\\Windows\\System32;C:\\Windows"
        cleaned = clean_path(path, venv)
        segments = cleaned.split(os.pathsep)
        # The venv Scripts segment should be gone (case-insensitive match).
        assert not any(
            os.path.normcase(seg) == os.path.normcase(venv_scripts)
            for seg in segments
        )
        assert r"C:\Windows\System32" in segments

    def test_no_match_returns_unchanged(self):
        if IS_WINDOWS:
            path = r"C:\Windows\System32;C:\Windows"
            venv = r"D:\somewhere\else\.venv"
        else:
            path = "/usr/bin:/usr/local/bin"
            venv = "/somewhere/else/.venv"
        cleaned = clean_path(path, venv)
        assert cleaned == path

    def test_drops_empty_segments(self):
        if IS_WINDOWS:
            venv = r"D:\home\user\.venv"
            venv_bin = r"D:\home\user\.venv\Scripts"
            other = r"C:\Windows\System32"
        else:
            venv = "/home/user/.venv"
            venv_bin = "/home/user/.venv/bin"
            other = "/usr/bin"
        # Trailing separator produces an empty segment.
        path = venv_bin + os.pathsep + other + os.pathsep
        cleaned = clean_path(path, venv)
        segments = cleaned.split(os.pathsep)
        assert venv_bin not in segments
        assert "" not in segments
        assert other in segments


# ─── build_tool_env ────────────────────────────────────────────────────────


class TestBuildToolEnv:
    def test_blacklist_removed(self, monkeypatch):
        monkeypatch.setattr(
            os,
            "environ",
            {
                "VIRTUAL_ENV": "/fake/venv",
                "PYTHONHOME": "/fake/pythonhome",
                "DATABASE_URL": "postgresql://user:pass@host/db",
                "SECRET_KEY": "topsecret",
                "JWT_SECRET": "jwtsecret",
                "REDIS_URL": "redis://localhost",
                "MILVUS_HOST": "localhost",
                "MILVUS_PORT": "19530",
                "ES_HOST": "localhost",
                "ES_PORT": "9200",
                "NEO4J_URI": "bolt://localhost",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317",
                "OTEL_EXPORTER_OTLP_HEADERS": "k=v",
                "NPM_CONFIG_PREFIX": "/fake/npm",
                "M2_HOME": "/fake/m2",
                "GOPATH": "/fake/go",
                "GOMODCACHE": "/fake/gomod",
                "PATH": "/usr/bin",
                "HOME": "/home/user",
            },
        )
        # Force detect_achat_venv to return None so PATH is untouched.
        monkeypatch.setattr(env_isolation, "detect_achat_venv", lambda: None)
        env = build_tool_env()
        for key in _BLACKLISTED_KEYS:
            assert key not in env, f"{key} should have been stripped"

    def test_preserves_user_system_vars(self, monkeypatch):
        monkeypatch.setattr(
            os,
            "environ",
            {
                "JAVA_HOME": "/usr/lib/jvm/java-17",
                "ANDROID_SDK_ROOT": "/opt/android-sdk",
                "HOME": "/home/user",
                "LANG": "en_US.UTF-8",
                "PATH": "/usr/bin",
            },
        )
        monkeypatch.setattr(env_isolation, "detect_achat_venv", lambda: None)
        env = build_tool_env()
        assert env["JAVA_HOME"] == "/usr/lib/jvm/java-17"
        assert env["ANDROID_SDK_ROOT"] == "/opt/android-sdk"
        assert env["HOME"] == "/home/user"
        assert env["LANG"] == "en_US.UTF-8"

    def test_path_scrubs_achat_venv(self, monkeypatch):
        if IS_WINDOWS:
            venv = r"D:\home\achat\.venv"
            venv_bin = r"D:\home\achat\.venv\Scripts"
            other1 = r"C:\Windows\System32"
            other2 = r"C:\Windows"
        else:
            venv = "/home/achat/.venv"
            venv_bin = "/home/achat/.venv/bin"
            other1 = "/usr/bin"
            other2 = "/usr/local/bin"
        monkeypatch.setattr(
            os,
            "environ",
            {
                "VIRTUAL_ENV": venv,
                "PATH": os.pathsep.join([venv_bin, other1, other2]),
                "HOME": "/home/user",
            },
        )
        monkeypatch.setattr(env_isolation, "detect_achat_venv", lambda: venv)
        env = build_tool_env()
        # VIRTUAL_ENV must be gone (blacklisted).
        assert "VIRTUAL_ENV" not in env
        # PATH must no longer contain the AChat venv bin.
        segments = env["PATH"].split(os.pathsep)
        assert venv_bin not in segments
        assert other1 in segments
        assert other2 in segments

    def test_project_venv_prepended_to_path(self, monkeypatch, tmp_path):
        # Set up a fake project venv on disk so the isdir check passes.
        venv_root = tmp_path / ".venv"
        bin_subdir = venv_root / _venv_bin_name()
        bin_subdir.mkdir(parents=True)

        if IS_WINDOWS:
            other1 = r"C:\Windows\System32"
            other2 = r"C:\Windows"
        else:
            other1 = "/usr/bin"
            other2 = "/usr/local/bin"
        monkeypatch.setattr(
            os,
            "environ",
            {"PATH": os.pathsep.join([other1, other2]), "HOME": "/home/user"},
        )
        monkeypatch.setattr(env_isolation, "detect_achat_venv", lambda: None)
        env = build_tool_env(cwd=str(tmp_path), project_venv_path=str(venv_root))
        segments = env["PATH"].split(os.pathsep)
        # Project venv bin must be first.
        assert os.path.normcase(segments[0]) == os.path.normcase(str(bin_subdir))
        assert other1 in segments

    def test_project_venv_missing_dir_not_prepended(self, monkeypatch, tmp_path):
        # project_venv_path points at a non-existent dir → no prepend.
        if IS_WINDOWS:
            other = r"C:\Windows\System32"
        else:
            other = "/usr/bin"
        monkeypatch.setattr(
            os,
            "environ",
            {"PATH": other, "HOME": "/home/user"},
        )
        monkeypatch.setattr(env_isolation, "detect_achat_venv", lambda: None)
        env = build_tool_env(
            cwd=str(tmp_path),
            project_venv_path=str(tmp_path / "nonexistent"),
        )
        assert env["PATH"] == other

    def test_does_not_mutate_os_environ(self, monkeypatch):
        original = dict(os.environ)
        monkeypatch.setattr(env_isolation, "detect_achat_venv", lambda: None)
        build_tool_env()
        # os.environ is unchanged.
        assert dict(os.environ) == original

    def test_empty_environ(self, monkeypatch):
        monkeypatch.setattr(os, "environ", {})
        monkeypatch.setattr(env_isolation, "detect_achat_venv", lambda: None)
        env = build_tool_env()
        assert env == {}


# ─── detect_project_venv ───────────────────────────────────────────────────


class TestDetectProjectVenv:
    def test_none_cwd(self):
        assert detect_project_venv(None) is None

    def test_no_venv(self, tmp_path):
        assert detect_project_venv(str(tmp_path)) is None

    def test_venv_present(self, tmp_path):
        # Cross-platform: build the venv layout that matches the current platform.
        venv_root = tmp_path / ".venv"
        bin_dir = venv_root / _venv_bin_name()
        bin_dir.mkdir(parents=True)
        (bin_dir / _python_exe_name()).write_text("stub")
        result = detect_project_venv(str(tmp_path))
        assert result is not None
        assert os.path.normcase(result) == os.path.normcase(str(venv_root))

    def test_venv_dir_without_python_returns_none(self, tmp_path):
        # .venv exists but has no python binary → not a usable venv.
        venv_root = tmp_path / ".venv"
        venv_root.mkdir()
        assert detect_project_venv(str(tmp_path)) is None
