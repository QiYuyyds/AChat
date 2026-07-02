"""Unit tests for workspace_has_build_toolchain."""

import os
import tempfile

from app.utils.workspace_utils import workspace_has_build_toolchain


def _create_temp_workspace(files: list[str] | None = None) -> str:
    """Create a temp directory with the given files (empty content)."""
    tmp = tempfile.mkdtemp()
    for name in (files or []):
        open(os.path.join(tmp, name), "w").close()
    return tmp


def test_workspace_with_package_json_returns_true():
    ws = _create_temp_workspace(["package.json"])
    try:
        assert workspace_has_build_toolchain(ws) is True
    finally:
        import shutil
        shutil.rmtree(ws)


def test_workspace_with_only_index_html_returns_false():
    ws = _create_temp_workspace(["index.html"])
    try:
        assert workspace_has_build_toolchain(ws) is False
    finally:
        import shutil
        shutil.rmtree(ws)


def test_workspace_with_pyproject_toml_returns_true():
    ws = _create_temp_workspace(["pyproject.toml"])
    try:
        assert workspace_has_build_toolchain(ws) is True
    finally:
        import shutil
        shutil.rmtree(ws)


def test_empty_workspace_returns_false():
    ws = _create_temp_workspace([])
    try:
        assert workspace_has_build_toolchain(ws) is False
    finally:
        import shutil
        shutil.rmtree(ws)
