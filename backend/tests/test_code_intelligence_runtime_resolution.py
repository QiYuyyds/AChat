from pathlib import Path

import pytest


def test_runtime_resolution_prefers_packaged_then_verified_cache(tmp_path: Path) -> None:
    from app.code_intelligence.runtime import RuntimeManager

    packaged_root = tmp_path / "packaged"
    cache_root = tmp_path / "cache"
    manager = RuntimeManager(packaged_root=packaged_root, cache_root=cache_root)
    manager.write_install_marker(packaged_root / "win32-x64", "win32-x64")
    manager.write_install_marker(cache_root / "0.9.3" / "win32-x64", "win32-x64")

    packaged = manager.resolve("win32-x64", download_approved=False)
    assert packaged.source == "packaged"

    (packaged_root / "win32-x64" / "runtime.json").unlink()
    cached = manager.resolve("win32-x64", download_approved=False)
    assert cached.source == "cache"


def test_runtime_resolution_requires_explicit_download_approval(tmp_path: Path) -> None:
    from app.code_intelligence.runtime import RuntimeDownloadApprovalRequired, RuntimeManager

    manager = RuntimeManager(
        packaged_root=tmp_path / "packaged",
        cache_root=tmp_path / "cache",
    )

    with pytest.raises(RuntimeDownloadApprovalRequired):
        manager.resolve("win32-x64", download_approved=False)
