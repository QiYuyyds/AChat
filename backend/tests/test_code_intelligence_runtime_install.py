import asyncio
import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _manager_for_archive(tmp_path: Path, payload: bytes):
    from app.code_intelligence.runtime import RuntimeManager

    manifest = {
        "version": "test-version",
        "license": {},
        "artifacts": {
            "win32-x64": {
                "url": "https://example.test/codegraph.zip",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "archiveType": "zip",
            }
        },
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return RuntimeManager(
        packaged_root=tmp_path / "packaged",
        cache_root=tmp_path / "cache",
        manifest_path=manifest_path,
    )


@pytest.mark.asyncio
async def test_download_verifies_and_atomically_installs_runtime(tmp_path: Path) -> None:
    payload = _zip_bytes(
        {
            "codegraph-win32-x64/node.exe": b"node",
            "codegraph-win32-x64/bin/codegraph.cmd": b"@echo off",
        }
    )
    manager = _manager_for_archive(tmp_path, payload)
    resolved = manager.resolve("win32-x64", download_approved=True)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    )

    installed = await manager.download_and_install(
        resolved,
        cancel_event=asyncio.Event(),
        client=client,
    )
    await client.aclose()

    assert installed.source == "cache"
    assert installed.executable_path.read_bytes() == b"@echo off"
    assert (installed.root / "runtime.json").is_file()
    assert not list((tmp_path / "cache").rglob("*.partial-*"))


@pytest.mark.asyncio
async def test_digest_mismatch_removes_partial_data(tmp_path: Path) -> None:
    payload = _zip_bytes({"codegraph-win32-x64/bin/codegraph.cmd": b"bad"})
    manager = _manager_for_archive(tmp_path, payload + b"expected-different")
    resolved = manager.resolve("win32-x64", download_approved=True)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    )

    with pytest.raises(RuntimeError, match="SHA256 mismatch"):
        await manager.download_and_install(
            resolved,
            cancel_event=asyncio.Event(),
            client=client,
        )
    await client.aclose()

    assert not resolved.root.exists()
    assert not list((tmp_path / "cache").rglob("*.partial-*"))


@pytest.mark.asyncio
async def test_unsafe_archive_path_is_rejected_and_cleaned(tmp_path: Path) -> None:
    payload = _zip_bytes(
        {
            "../escaped.txt": b"escape",
            "codegraph-win32-x64/bin/codegraph.cmd": b"@echo off",
        }
    )
    manager = _manager_for_archive(tmp_path, payload)
    resolved = manager.resolve("win32-x64", download_approved=True)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    )

    with pytest.raises(RuntimeError, match="unsafe path"):
        await manager.download_and_install(
            resolved,
            cancel_event=asyncio.Event(),
            client=client,
        )
    await client.aclose()

    assert not (tmp_path / "escaped.txt").exists()
    assert not resolved.root.exists()


@pytest.mark.asyncio
async def test_cancelled_download_leaves_no_partial_data(tmp_path: Path) -> None:
    payload = _zip_bytes({"codegraph-win32-x64/bin/codegraph.cmd": b"@echo off"})
    manager = _manager_for_archive(tmp_path, payload)
    resolved = manager.resolve("win32-x64", download_approved=True)
    cancel_event = asyncio.Event()
    cancel_event.set()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    )

    with pytest.raises(asyncio.CancelledError):
        await manager.download_and_install(
            resolved,
            cancel_event=cancel_event,
            client=client,
        )
    await client.aclose()

    assert not resolved.root.exists()
    assert not list((tmp_path / "cache").rglob("*.partial-*"))
