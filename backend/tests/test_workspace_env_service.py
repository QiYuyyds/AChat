"""Unit tests for workspace_env_service.

Covers:
- ``detect_project_env()`` — marker file detection for Python / Node / Java / Go / unknown.
- ``detect_python_venv()`` — .venv present / absent.
- ``detect_and_hint()`` — hint event emission logic (mocked DB + EventBus).
- ``create_project_venv()`` — success / failure paths (mocked subprocess).
- ``set_env_preference()`` — validation and persistence.

These tests use tmp_path for filesystem operations and mock the DB / EventBus
/ subprocess to avoid real side effects.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import workspace_env_service

# ─── detect_project_env ──────────────────────────────────────────────────────


class TestDetectProjectEnv:
    def test_python_with_requirements_txt(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("fastapi")
        info = workspace_env_service.detect_project_env(tmp_path)
        assert info.language == "python"
        assert info.venv_present is False

    def test_python_with_pyproject_toml(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        info = workspace_env_service.detect_project_env(tmp_path)
        assert info.language == "python"

    def test_python_with_setup_py(self, tmp_path: Path):
        (tmp_path / "setup.py").write_text("from setuptools import setup")
        info = workspace_env_service.detect_project_env(tmp_path)
        assert info.language == "python"

    def test_python_with_venv(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("fastapi")
        venv_dir = tmp_path / ".venv"
        bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        bin_dir.mkdir(parents=True)
        (bin_dir / ("python.exe" if os.name == "nt" else "python")).write_text("")
        info = workspace_env_service.detect_project_env(tmp_path)
        assert info.language == "python"
        assert info.venv_present is True

    def test_nodejs(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name":"test"}')
        info = workspace_env_service.detect_project_env(tmp_path)
        assert info.language == "nodejs"
        assert info.venv_present is False

    def test_java(self, tmp_path: Path):
        (tmp_path / "pom.xml").write_text("<project></project>")
        info = workspace_env_service.detect_project_env(tmp_path)
        assert info.language == "java"

    def test_go(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module test")
        info = workspace_env_service.detect_project_env(tmp_path)
        assert info.language == "go"

    def test_unknown(self, tmp_path: Path):
        info = workspace_env_service.detect_project_env(tmp_path)
        assert info.language == "unknown"

    def test_nonexistent_dir(self):
        info = workspace_env_service.detect_project_env("/nonexistent/path/xyz")
        assert info.language == "unknown"

    def test_python_takes_priority_over_node(self, tmp_path: Path):
        """Python markers take priority because env isolation is most critical."""
        (tmp_path / "requirements.txt").write_text("fastapi")
        (tmp_path / "package.json").write_text('{"name":"test"}')
        info = workspace_env_service.detect_project_env(tmp_path)
        assert info.language == "python"


# ─── detect_python_venv ──────────────────────────────────────────────────────


class TestDetectPythonVenv:
    def test_venv_present(self, tmp_path: Path):
        venv_dir = tmp_path / ".venv"
        bin_dir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
        bin_dir.mkdir(parents=True)
        (bin_dir / ("python.exe" if os.name == "nt" else "python")).write_text("")
        assert workspace_env_service.detect_python_venv(tmp_path) is True

    def test_venv_absent(self, tmp_path: Path):
        assert workspace_env_service.detect_python_venv(tmp_path) is False


# ─── set_env_preference ──────────────────────────────────────────────────────


class TestSetEnvPreference:
    @pytest.mark.asyncio
    async def test_valid_preferences(self):
        """Valid preferences should not raise."""
        with patch.object(
            workspace_env_service, "_set_env_preference", new_callable=AsyncMock
        ) as mock:
            for pref in ("venv_created", "skip", "system_python"):
                await workspace_env_service.set_env_preference("conv_1", pref, "user_1")
            assert mock.call_count == 3

    @pytest.mark.asyncio
    async def test_invalid_preference_raises(self):
        with pytest.raises(ValueError, match="Invalid env preference"):
            await workspace_env_service.set_env_preference(
                "conv_1", "invalid_choice", "user_1"
            )


# ─── detect_and_hint ─────────────────────────────────────────────────────────


class TestDetectAndHint:
    @pytest.mark.asyncio
    async def test_hint_emitted_for_python_without_venv(self, tmp_path: Path):
        """Python project without .venv → hint event published."""
        (tmp_path / "requirements.txt").write_text("fastapi")

        mock_workspace = MagicMock()
        mock_workspace.mode = "local"
        mock_workspace.bound_path = str(tmp_path)
        mock_workspace.env_preference = None

        with (
            patch.object(
                workspace_env_service,
                "_get_workspace_by_conversation",
                new_callable=AsyncMock,
                return_value=mock_workspace,
            ),
            patch.object(workspace_env_service, "event_bus") as mock_bus,
            patch.object(
                workspace_env_service,
                "detect_project_env",
                return_value=workspace_env_service.ProjectEnvInfo(
                    language="python", venv_present=False
                ),
            ),
        ):
            await workspace_env_service.detect_and_hint("conv_1", "user_1")
            assert mock_bus.publish.call_count == 1
            event = mock_bus.publish.call_args[0][0]
            assert event.type == "workspace_env_hint"
            assert event.language == "python"
            assert event.venv_present is False

    @pytest.mark.asyncio
    async def test_no_hint_when_env_preference_set(self, tmp_path: Path):
        """User already decided → no hint."""
        mock_workspace = MagicMock()
        mock_workspace.mode = "local"
        mock_workspace.bound_path = str(tmp_path)
        mock_workspace.env_preference = "skip"

        with (
            patch.object(
                workspace_env_service,
                "_get_workspace_by_conversation",
                new_callable=AsyncMock,
                return_value=mock_workspace,
            ),
            patch.object(workspace_env_service, "event_bus") as mock_bus,
        ):
            await workspace_env_service.detect_and_hint("conv_1", "user_1")
            mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_hint_for_sandbox_workspace(self):
        """Sandbox workspaces have no user project → no hint."""
        mock_workspace = MagicMock()
        mock_workspace.mode = "sandbox"
        mock_workspace.bound_path = None

        with (
            patch.object(
                workspace_env_service,
                "_get_workspace_by_conversation",
                new_callable=AsyncMock,
                return_value=mock_workspace,
            ),
            patch.object(workspace_env_service, "event_bus") as mock_bus,
        ):
            await workspace_env_service.detect_and_hint("conv_1", "user_1")
            mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_hint_when_venv_exists(self, tmp_path: Path):
        """Python project WITH .venv → no hint needed."""
        mock_workspace = MagicMock()
        mock_workspace.mode = "local"
        mock_workspace.bound_path = str(tmp_path)
        mock_workspace.env_preference = None

        with (
            patch.object(
                workspace_env_service,
                "_get_workspace_by_conversation",
                new_callable=AsyncMock,
                return_value=mock_workspace,
            ),
            patch.object(workspace_env_service, "event_bus") as mock_bus,
            patch.object(
                workspace_env_service,
                "detect_project_env",
                return_value=workspace_env_service.ProjectEnvInfo(
                    language="python", venv_present=True
                ),
            ),
        ):
            await workspace_env_service.detect_and_hint("conv_1", "user_1")
            mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_hint_for_non_python(self, tmp_path: Path):
        """Node.js project → no hint (only Python needs venv advisory)."""
        mock_workspace = MagicMock()
        mock_workspace.mode = "local"
        mock_workspace.bound_path = str(tmp_path)
        mock_workspace.env_preference = None

        with (
            patch.object(
                workspace_env_service,
                "_get_workspace_by_conversation",
                new_callable=AsyncMock,
                return_value=mock_workspace,
            ),
            patch.object(workspace_env_service, "event_bus") as mock_bus,
            patch.object(
                workspace_env_service,
                "detect_project_env",
                return_value=workspace_env_service.ProjectEnvInfo(
                    language="nodejs", venv_present=False
                ),
            ),
        ):
            await workspace_env_service.detect_and_hint("conv_1", "user_1")
            mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_swallowed(self):
        """detect_and_hint must never raise — failures are logged and swallowed."""
        with patch.object(
            workspace_env_service,
            "_get_workspace_by_conversation",
            new_callable=AsyncMock,
            side_effect=Exception("DB down"),
        ):
            # Should not raise.
            await workspace_env_service.detect_and_hint("conv_1", "user_1")


# ─── create_project_venv ─────────────────────────────────────────────────────


class TestCreateProjectVenv:
    @pytest.mark.asyncio
    async def test_success_path(self, tmp_path: Path):
        """Successful venv creation → ready event + preference persisted."""
        mock_workspace = MagicMock()
        mock_workspace.mode = "local"
        mock_workspace.bound_path = str(tmp_path)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with (
            patch.object(
                workspace_env_service,
                "_get_workspace_by_conversation",
                new_callable=AsyncMock,
                return_value=mock_workspace,
            ),
            patch.object(workspace_env_service, "event_bus") as mock_bus,
            patch.object(
                workspace_env_service,
                "_set_env_preference",
                new_callable=AsyncMock,
            ),
            patch(
                "app.services.workspace_env_service.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch("os.path.isfile", return_value=True),
        ):
            await workspace_env_service.create_project_venv("conv_1", "user_1")
            # Should have published: creating + ready
            statuses = [
                call[0][0].status for call in mock_bus.publish.call_args_list
            ]
            assert "creating" in statuses
            assert "ready" in statuses

    @pytest.mark.asyncio
    async def test_failure_path(self, tmp_path: Path):
        """Failed venv creation → failed event with error."""
        mock_workspace = MagicMock()
        mock_workspace.mode = "local"
        mock_workspace.bound_path = str(tmp_path)

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"Error: pip not found")
        )

        with (
            patch.object(
                workspace_env_service,
                "_get_workspace_by_conversation",
                new_callable=AsyncMock,
                return_value=mock_workspace,
            ),
            patch.object(workspace_env_service, "event_bus") as mock_bus,
            patch(
                "app.services.workspace_env_service.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            await workspace_env_service.create_project_venv("conv_1", "user_1")
            statuses = [
                call[0][0].status for call in mock_bus.publish.call_args_list
            ]
            assert "creating" in statuses
            assert "failed" in statuses

    @pytest.mark.asyncio
    async def test_no_local_workspace(self):
        """No local workspace → failed event immediately."""
        mock_workspace = MagicMock()
        mock_workspace.mode = "sandbox"
        mock_workspace.bound_path = None

        with (
            patch.object(
                workspace_env_service,
                "_get_workspace_by_conversation",
                new_callable=AsyncMock,
                return_value=mock_workspace,
            ),
            patch.object(workspace_env_service, "event_bus") as mock_bus,
        ):
            await workspace_env_service.create_project_venv("conv_1", "user_1")
            event = mock_bus.publish.call_args[0][0]
            assert event.status == "failed"
            assert "No local workspace" in event.error
