import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_verified_packaged_archive_installs_without_download(tmp_path: Path) -> None:
    from app.code_intelligence.runtime import RuntimeManager

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("codegraph-win32-x64/node.exe", b"node")
        archive.writestr("codegraph-win32-x64/bin/codegraph.cmd", b"@echo off")
    payload = buffer.getvalue()
    packaged_root = tmp_path / "packaged"
    packaged_root.mkdir()
    (packaged_root / "runtime.zip").write_bytes(payload)
    manifest = {
        "version": "test",
        "license": {},
        "artifacts": {
            "win32-x64": {
                "url": "https://example.test/codegraph-win32-x64.zip",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "archiveType": "zip",
            }
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manager = RuntimeManager(
        packaged_root=packaged_root,
        cache_root=tmp_path / "cache",
        manifest_path=manifest_path,
    )

    packaged = manager.resolve("win32-x64", download_approved=False)
    assert packaged.source == "packaged_archive"
    installed = await manager.install_packaged(
        packaged,
        cancel_event=asyncio.Event(),
    )

    assert installed.source == "cache"
    assert installed.executable_path.is_file()

    resolved_again = manager.resolve("win32-x64", download_approved=False)
    assert resolved_again.source == "cache"
    assert resolved_again.root == installed.root
