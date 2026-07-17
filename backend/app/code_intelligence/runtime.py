"""Resolve and safely install the pinned CodeGraph runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

MANIFEST_PATH = Path(__file__).with_name("runtime-manifest.json")


class RuntimeDownloadApprovalRequired(RuntimeError):
    """Raised when no local runtime exists and download is not approved."""


@dataclass(frozen=True)
class RuntimeArtifact:
    platform_key: str
    version: str
    url: str
    sha256: str
    archive_type: Literal["tar.gz", "zip"]


@dataclass(frozen=True)
class ResolvedRuntime:
    source: Literal["packaged", "packaged_archive", "cache", "download"]
    root: Path
    artifact: RuntimeArtifact
    archive_path: Path | None = None

    @property
    def executable_path(self) -> Path:
        executable = "codegraph.cmd" if self.artifact.platform_key.startswith("win32-") else "codegraph"
        return self.root / "bin" / executable


class RuntimeManager:
    def __init__(
        self,
        *,
        packaged_root: Path,
        cache_root: Path,
        manifest_path: Path = MANIFEST_PATH,
    ) -> None:
        self.packaged_root = packaged_root
        self.cache_root = cache_root
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    @property
    def version(self) -> str:
        return str(self._manifest["version"])

    def artifact_for(self, platform_key: str) -> RuntimeArtifact:
        try:
            entry = self._manifest["artifacts"][platform_key]
        except KeyError as exc:
            raise RuntimeError(f"Unsupported CodeGraph platform: {platform_key}") from exc
        return RuntimeArtifact(
            platform_key=platform_key,
            version=self.version,
            url=str(entry["url"]),
            sha256=str(entry["sha256"]),
            archive_type=entry["archiveType"],
        )

    def resolve(
        self,
        platform_key: str | None = None,
        *,
        download_approved: bool,
    ) -> ResolvedRuntime:
        key = platform_key or current_platform_key()
        artifact = self.artifact_for(key)
        packaged = self.packaged_root / key
        if self._is_verified_install(packaged, artifact):
            return ResolvedRuntime("packaged", packaged, artifact)

        cached = self.cache_root / self.version / key
        if self._is_verified_install(cached, artifact):
            return ResolvedRuntime("cache", cached, artifact)

        packaged_archive = self._packaged_archive_path(artifact)
        if (
            packaged_archive is not None
            and self._file_sha256(packaged_archive) == artifact.sha256
        ):
            return ResolvedRuntime(
                "packaged_archive", cached, artifact, packaged_archive
            )

        if not download_approved:
            raise RuntimeDownloadApprovalRequired(
                f"CodeGraph {self.version} for {key} requires an approved download"
            )
        return ResolvedRuntime("download", cached, artifact)

    async def install_packaged(
        self,
        resolved: ResolvedRuntime,
        *,
        cancel_event: asyncio.Event,
    ) -> ResolvedRuntime:
        if resolved.source != "packaged_archive" or resolved.archive_path is None:
            return resolved
        if cancel_event.is_set():
            raise asyncio.CancelledError
        actual = await asyncio.to_thread(self._file_sha256, resolved.archive_path)
        if actual != resolved.artifact.sha256:
            raise RuntimeError(
                "Packaged CodeGraph archive SHA256 mismatch: "
                f"expected {resolved.artifact.sha256}, got {actual}"
            )
        await asyncio.to_thread(self._install_archive, resolved.archive_path, resolved)
        return ResolvedRuntime("cache", resolved.root, resolved.artifact)

    def _packaged_archive_path(self, artifact: RuntimeArtifact) -> Path | None:
        extension = "zip" if artifact.archive_type == "zip" else "tar.gz"
        candidates = [
            self.packaged_root / f"runtime.{extension}",
            self.packaged_root / Path(artifact.url).name,
        ]
        return next((candidate for candidate in candidates if candidate.is_file()), None)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    async def download_and_install(
        self,
        resolved: ResolvedRuntime,
        *,
        cancel_event: asyncio.Event,
        client: httpx.AsyncClient | None = None,
    ) -> ResolvedRuntime:
        if resolved.source != "download":
            return resolved
        download_dir = self.cache_root / ".downloads"
        archive_path = download_dir / f"{uuid.uuid4().hex}.partial-{resolved.artifact.archive_type.replace('.', '-')}"
        own_client = client is None
        http_client = client or httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        try:
            if cancel_event.is_set():
                raise asyncio.CancelledError
            download_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            async with http_client.stream("GET", resolved.artifact.url) as response:
                response.raise_for_status()
                with archive_path.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        if cancel_event.is_set():
                            raise asyncio.CancelledError
                        digest.update(chunk)
                        output.write(chunk)
            actual_sha256 = digest.hexdigest()
            if actual_sha256 != resolved.artifact.sha256:
                raise RuntimeError(
                    "CodeGraph archive SHA256 mismatch: "
                    f"expected {resolved.artifact.sha256}, got {actual_sha256}"
                )
            if cancel_event.is_set():
                raise asyncio.CancelledError
            await asyncio.to_thread(self._install_archive, archive_path, resolved)
            return ResolvedRuntime("cache", resolved.root, resolved.artifact)
        finally:
            archive_path.unlink(missing_ok=True)
            if own_client:
                await http_client.aclose()

    def _install_archive(self, archive_path: Path, resolved: ResolvedRuntime) -> None:
        target = resolved.root
        target.parent.mkdir(parents=True, exist_ok=True)
        extract_root = target.parent / f".{target.name}.partial-{uuid.uuid4().hex}"
        try:
            extract_root.mkdir()
            if resolved.artifact.archive_type == "zip":
                self._extract_zip_safely(archive_path, extract_root)
            else:
                self._extract_tar_safely(archive_path, extract_root)
            payload_root = self._payload_root(extract_root)
            executable = ResolvedRuntime("cache", payload_root, resolved.artifact).executable_path
            if not executable.is_file():
                raise RuntimeError(
                    f"CodeGraph archive is missing executable: {executable.relative_to(payload_root)}"
                )
            if os.name != "nt":
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            self.write_install_marker(payload_root, resolved.artifact.platform_key)
            if target.exists():
                shutil.rmtree(target)
            os.replace(payload_root, target)
        finally:
            shutil.rmtree(extract_root, ignore_errors=True)

    @classmethod
    def _extract_zip_safely(cls, archive_path: Path, destination: Path) -> None:
        with zipfile.ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise RuntimeError(f"CodeGraph archive contains unsafe symlink: {entry.filename}")
                target = cls._safe_archive_target(destination, entry.filename)
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

    @classmethod
    def _extract_tar_safely(cls, archive_path: Path, destination: Path) -> None:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            for entry in archive.getmembers():
                if entry.issym() or entry.islnk():
                    raise RuntimeError(f"CodeGraph archive contains unsafe link: {entry.name}")
                target = cls._safe_archive_target(destination, entry.name)
                if entry.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not entry.isfile():
                    raise RuntimeError(f"CodeGraph archive contains unsupported entry: {entry.name}")
                source = archive.extractfile(entry)
                if source is None:
                    raise RuntimeError(f"Cannot read CodeGraph archive entry: {entry.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)

    @staticmethod
    def _safe_archive_target(destination: Path, member_name: str) -> Path:
        normalized = member_name.replace("\\", "/")
        member_path = Path(normalized)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise RuntimeError(f"CodeGraph archive contains unsafe path: {member_name}")
        root = destination.resolve()
        target = (root / member_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RuntimeError(f"CodeGraph archive contains unsafe path: {member_name}") from exc
        return target

    @staticmethod
    def _payload_root(extract_root: Path) -> Path:
        children = list(extract_root.iterdir())
        if len(children) == 1 and children[0].is_dir():
            return children[0]
        return extract_root

    def write_install_marker(self, root: Path, platform_key: str) -> None:
        artifact = self.artifact_for(platform_key)
        root.mkdir(parents=True, exist_ok=True)
        marker = {
            "version": artifact.version,
            "platform": artifact.platform_key,
            "archiveSha256": artifact.sha256,
        }
        (root / "runtime.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _is_verified_install(root: Path, artifact: RuntimeArtifact) -> bool:
        marker_path = root / "runtime.json"
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return False
        return marker == {
            "version": artifact.version,
            "platform": artifact.platform_key,
            "archiveSha256": artifact.sha256,
        }


def current_platform_key() -> str:
    os_name = {"darwin": "darwin", "linux": "linux", "win32": "win32"}.get(
        sys.platform
    )
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64" if machine in {
        "amd64",
        "x86_64",
    } else None
    if os_name is None or arch is None:
        raise RuntimeError(
            f"Unsupported CodeGraph host platform: {sys.platform}/{platform.machine()}"
        )
    return f"{os_name}-{arch}"
