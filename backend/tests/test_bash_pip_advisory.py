"""Unit tests for the bash tool's pip install advisory.

Tests the advisory trigger conditions (specs/workspace-env-isolation §D8):
- No project venv + `pip install` in command + env_preference != 'system_python' → advisory
- Project venv exists → no advisory
- env_preference == 'system_python' → no advisory
- Command doesn't contain `pip install` → no advisory

The advisory is a non-blocking UX nudge appended to stdout; the env-isolation
layer (env_isolation.build_tool_env) is the actual safety barrier.
"""

from __future__ import annotations

import pytest

from app.tools.bash import _ENV_ADVISORY, _PIP_INSTALL_RE


class TestPipInstallRegex:
    """Verify the regex catches the common `pip install` invocation patterns."""

    @pytest.mark.parametrize(  # type: ignore[prop-deprecated]
        "command",
        [
            "pip install foo",
            "pip3 install foo",
            "pip install --upgrade pip",
            "pip install -r requirements.txt",
            "pip install foo bar baz",
            "python -m pip install foo",
            "python3 -m pip install foo",
            "py -m pip install foo",
            "sudo pip install foo",
            "pip install foo && pip install bar",
            "  pip   install   foo  ",
        ],
    )
    def test_matches_pip_install(self, command: str):
        assert _PIP_INSTALL_RE.search(command) is not None, (
            f"Should match: {command!r}"
        )

    @pytest.mark.parametrize(  # type: ignore[prop-deprecated]
        "command",
        [
            "pip uninstall foo",
            "pip list",
            "pip show foo",
            "pip freeze",
            "pip --version",
            "npm install foo",
            "cargo install foo",
            "pip-install --help",  # hyphen, not space
        ],
    )
    def test_does_not_match_non_install(self, command: str):
        assert _PIP_INSTALL_RE.search(command) is None, (
            f"Should NOT match: {command!r}"
        )


class TestAdvisoryConditions:
    """Verify the advisory trigger logic via the three conditions."""

    def _should_advisory(
        self, command: str, project_venv: str | None, env_preference: str | None
    ) -> bool:
        """Mirror the condition in _run_shell_command for unit testing."""
        return (
            not project_venv
            and env_preference != "system_python"
            and _PIP_INSTALL_RE.search(command) is not None
        )

    def test_triggers_no_venv_no_preference(self):
        """No venv + pip install + null preference → advisory."""
        assert self._should_advisory("pip install foo", None, None)

    def test_triggers_no_venv_skip_preference(self):
        """No venv + pip install + 'skip' preference → still advisory (user skipped venv creation but didn't choose system_python)."""
        assert self._should_advisory("pip install foo", None, "skip")

    def test_triggers_no_venv_venv_created_preference(self):
        """No venv (user may have deleted it) + pip install + 'venv_created' → advisory (venv missing despite preference)."""
        assert self._should_advisory("pip install foo", None, "venv_created")

    def test_no_trigger_with_venv(self):
        """Project venv exists → no advisory regardless of pip install."""
        assert not self._should_advisory("pip install foo", "/path/.venv", None)
        assert not self._should_advisory(
            "pip install foo", "/path/.venv", "venv_created"
        )

    def test_no_trigger_system_python(self):
        """User chose system_python → no advisory (they opted out)."""
        assert not self._should_advisory("pip install foo", None, "system_python")

    def test_no_trigger_no_pip_install(self):
        """Command doesn't contain pip install → no advisory."""
        assert not self._should_advisory("python script.py", None, None)
        assert not self._should_advisory("ls -la", None, None)
        assert not self._should_advisory("npm install", None, None)

    def test_advisory_text_format(self):
        """The advisory text has the expected [AChat] prefix and separator."""
        assert _ENV_ADVISORY.startswith("\n\n[AChat Env Advisory]")
        assert "pip install" in _ENV_ADVISORY
        assert ".venv" in _ENV_ADVISORY
