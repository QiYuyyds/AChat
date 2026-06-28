"""Static deployment creation + external publishing.

Port of the creation/publish half of src/server/deployment-service.ts. A
deployment is a directory under ``<data_dir>/deployments/<id>`` holding the
runnable ``index.html`` plus a ``.agenthub/`` private area (manifest + original
source). ``deploy_artifact`` / ``deploy_workspace`` build these.

阶段 6 (API routes): asset serving (``read_deployment_asset``) and the
source/container ZIP downloads are implemented here for the HTTP routes.
"""

from __future__ import annotations

import io
import json
import os
import posixpath
import re
import shutil
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlparse, urlunparse

from app.config import get_settings
from app.schemas.messages import DeployStatusRecord
from app.utils.artifact_preview import build_web_app_html
from app.utils.clock import now_ms
from app.utils.workspace_utils import is_path_within

_DEPLOYMENT_ID_RE = re.compile(r"^dep_[0-9A-Za-z]+$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
PRIVATE_DIR = ".agenthub"
MANIFEST_PATH = f"{PRIVATE_DIR}/manifest.json"
SOURCE_ROOT = f"{PRIVATE_DIR}/source"
RUNTIME_ENTRY = "index.html"

DEPLOYMENT_SUMMARY_INSTRUCTION = (
    "User-facing summaries must not invent a hostname or public URL for this "
    "deployment. The previewPath is a local relative path for the current "
    "AChat instance; tell the user to use the deployment card buttons, or "
    "quote previewPath exactly."
)

WORKSPACE_DEPLOY_MAX_FILES = 2000
WORKSPACE_DEPLOY_MAX_BYTES = 100 * 1024 * 1024
WORKSPACE_DEPLOY_IGNORED_DIRS = {".agenthub", ".git", "node_modules"}


@dataclass
class StaticPublishResult:
    public_url: str
    publish_path: str
    local_preview_path: str
    publish_target_type: str = "static_directory"


@dataclass
class DeploymentAssetResult:
    ok: bool
    body: bytes | None = None
    content_type: str | None = None
    headers: dict[str, str] | None = None
    status: int | None = None
    error: str | None = None


@dataclass
class DeploymentDownload:
    body: bytes
    file_name: str
    content_type: str


# ─── creation ───────────────────────────────────────────────────────────────
def create_local_static_deployment(
    *,
    id: str,
    artifact_id: str,
    title: str,
    version: int,
    content: dict,
    created_at: int | None = None,
    data_dir: str | None = None,
) -> DeployStatusRecord:
    _assert_deployment_id(id)
    deployments_root = _get_deployments_root(data_dir)
    deployment_dir = _get_deployment_dir(id, data_dir)
    if os.path.exists(deployment_dir):
        raise ValueError(f"Deployment already exists: {id}")

    created = created_at if created_at is not None else now_ms()
    files = _normalize_web_app_files(content.get("files") or {})
    source_entry = _resolve_source_entry(content.get("entry") or "", files)
    source_files = sorted(files.keys())
    manifest = {
        "id": id,
        "artifactId": artifact_id,
        "title": title,
        "version": version,
        "deploymentType": "local_static",
        "createdAt": created,
        "sourceEntry": source_entry,
        "runtimeEntry": RUNTIME_ENTRY,
        "sourceFiles": source_files,
        "sourceType": "artifact",
    }

    try:
        os.makedirs(deployment_dir, exist_ok=True)
        for name, body in files.items():
            _write_text_within(deployment_dir, posixpath.join(SOURCE_ROOT, name), body)
            _write_text_within(deployment_dir, name, body)
        runtime_html = build_web_app_html(files, source_entry)
        _write_text_within(deployment_dir, RUNTIME_ENTRY, runtime_html)
        _write_text_within(deployment_dir, MANIFEST_PATH, json.dumps(manifest, indent=2))
    except Exception:
        _cleanup_partial(deployment_dir, deployments_root)
        raise

    return DeployStatusRecord(
        id=id,
        artifactId=artifact_id,
        title=title,
        version=version,
        previewPath=deployment_preview_path(id),
        deploymentType="local_static",
        deploymentPath=deployment_preview_path(id),
        sourceDownloadPath=deployment_download_path(id, "source"),
        containerDownloadPath=deployment_download_path(id, "container"),
        summaryInstruction=DEPLOYMENT_SUMMARY_INSTRUCTION,
        status="ready",
        createdAt=created,
        sourceType="artifact",
    )


def create_workspace_static_deployment(
    *,
    id: str,
    title: str,
    source_dir: str,
    workspace_path: str,
    entry: str | None = None,
    created_at: int | None = None,
    data_dir: str | None = None,
) -> DeployStatusRecord:
    _assert_deployment_id(id)
    if not os.path.isdir(source_dir):
        raise ValueError(f"Workspace deployment source is not a directory: {workspace_path}")

    deployments_root = _get_deployments_root(data_dir)
    deployment_dir = _get_deployment_dir(id, data_dir)
    if os.path.exists(deployment_dir):
        raise ValueError(f"Deployment already exists: {id}")

    created = created_at if created_at is not None else now_ms()
    source_files = _list_workspace_static_files(source_dir)
    source_entry = _resolve_workspace_entry(entry, source_files)
    manifest = {
        "id": id,
        "artifactId": f"workspace:{workspace_path}",
        "title": title,
        "version": 0,
        "deploymentType": "local_static",
        "createdAt": created,
        "sourceEntry": source_entry,
        "runtimeEntry": RUNTIME_ENTRY,
        "sourceFiles": source_files,
        "sourceType": "workspace",
        "workspacePath": workspace_path,
    }

    try:
        os.makedirs(deployment_dir, exist_ok=True)
        for rel in source_files:
            src = os.path.join(source_dir, *rel.split("/"))
            with open(src, "rb") as f:
                body = f.read()
            _write_binary_within(deployment_dir, posixpath.join(SOURCE_ROOT, rel), body)
            _write_binary_within(deployment_dir, rel, body)
        if source_entry != RUNTIME_ENTRY:
            with open(os.path.join(source_dir, *source_entry.split("/")), "rb") as f:
                runtime_html = f.read()
            _write_binary_within(deployment_dir, RUNTIME_ENTRY, runtime_html)
        _write_text_within(deployment_dir, MANIFEST_PATH, json.dumps(manifest, indent=2))
    except Exception:
        _cleanup_partial(deployment_dir, deployments_root)
        raise

    return DeployStatusRecord(
        id=id,
        artifactId=manifest["artifactId"],
        title=title,
        version=0,
        previewPath=deployment_preview_path(id),
        deploymentType="local_static",
        deploymentPath=deployment_preview_path(id),
        sourceDownloadPath=deployment_download_path(id, "source"),
        containerDownloadPath=deployment_download_path(id, "container"),
        summaryInstruction=DEPLOYMENT_SUMMARY_INSTRUCTION,
        status="ready",
        createdAt=created,
        sourceType="workspace",
        workspacePath=workspace_path,
    )


# ─── paths ──────────────────────────────────────────────────────────────────
def deployment_preview_path(deployment_id: str) -> str:
    return f"/deployments/{quote(deployment_id, safe='')}"


def deployment_download_path(deployment_id: str, kind: str) -> str:
    return f"/api/deployments/{quote(deployment_id, safe='')}/download/{kind}"


# ─── external publishing ────────────────────────────────────────────────────
def publish_deployment_to_static_directory(
    deployment_id: str,
    *,
    publish_dir: str,
    public_base_url: str,
    data_dir: str | None = None,
) -> StaticPublishResult:
    manifest = read_deployment_manifest(deployment_id, data_dir)
    if manifest is None:
        raise ValueError(f"Deployment not found: {deployment_id}")

    publish_root = _normalize_publish_root(publish_dir)
    target_dir = os.path.join(publish_root, deployment_id)
    if not is_path_within(target_dir, publish_root):
        raise ValueError(f"Publish path escapes configured directory: {target_dir}")

    deployment_dir = _get_deployment_dir(deployment_id, data_dir)
    shutil.rmtree(target_dir, ignore_errors=True)
    os.makedirs(target_dir, exist_ok=True)

    for rel in _list_public_deployment_files(deployment_dir):
        source = _safe_join_deployment_path(deployment_dir, rel)
        dest = os.path.join(target_dir, *rel.split("/"))
        if not is_path_within(dest, target_dir):
            raise ValueError(f"Publish file path escapes deployment directory: {rel}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(source, "rb") as src_f, open(dest, "wb") as dst_f:
            dst_f.write(src_f.read())

    return StaticPublishResult(
        public_url=_public_deployment_url(public_base_url, deployment_id),
        publish_path=target_dir,
        local_preview_path=deployment_preview_path(deployment_id),
    )


def read_deployment_manifest(deployment_id: str, data_dir: str | None = None) -> dict | None:
    if not is_deployment_id(deployment_id):
        return None
    manifest_path = _safe_join_deployment_path(
        _get_deployment_dir(deployment_id, data_dir), MANIFEST_PATH
    )
    if not os.path.exists(manifest_path):
        return None
    try:
        with open(manifest_path, encoding="utf-8") as f:
            parsed = json.load(f)
        return parsed if _is_deployment_manifest(parsed) else None
    except (ValueError, OSError):
        return None


# ─── asset serving ──────────────────────────────────────────────────────────
def read_deployment_asset(
    deployment_id: str,
    path_parts: list[str] | None = None,
    data_dir: str | None = None,
) -> DeploymentAssetResult:
    if not is_deployment_id(deployment_id):
        return DeploymentAssetResult(ok=False, status=404, error="Deployment not found")

    manifest = read_deployment_manifest(deployment_id, data_dir)
    if manifest is None:
        return DeploymentAssetResult(ok=False, status=404, error="Deployment not found")

    requested = "/".join(path_parts) if path_parts else manifest["runtimeEntry"]
    raw_requested = requested.strip().replace("\\", "/")
    normalized_requested = posixpath.normpath(raw_requested)
    if normalized_requested == PRIVATE_DIR or normalized_requested.startswith(f"{PRIVATE_DIR}/"):
        return DeploymentAssetResult(ok=False, status=404, error="Deployment asset not found")

    relative_path = normalize_deployment_file_path(requested)
    if not relative_path:
        return DeploymentAssetResult(ok=False, status=400, error="Invalid deployment path")

    deployment_dir = _get_deployment_dir(deployment_id, data_dir)
    abs_path = _safe_join_deployment_path(deployment_dir, relative_path)
    if not os.path.isfile(abs_path):
        return DeploymentAssetResult(ok=False, status=404, error="Deployment asset not found")

    content_type = _content_type_for(relative_path)
    with open(abs_path, "rb") as f:
        body = f.read()
    return DeploymentAssetResult(
        ok=True,
        body=body,
        content_type=content_type,
        headers=_response_headers_for(content_type),
    )


# ─── zip downloads ──────────────────────────────────────────────────────────
def build_deployment_source_zip(
    deployment_id: str, data_dir: str | None = None
) -> DeploymentDownload | None:
    manifest = read_deployment_manifest(deployment_id, data_dir)
    if manifest is None:
        return None

    deployment_dir = _get_deployment_dir(deployment_id, data_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in manifest["sourceFiles"]:
            abs_path = _safe_join_deployment_path(
                deployment_dir, posixpath.join(SOURCE_ROOT, rel)
            )
            if os.path.isfile(abs_path):
                with open(abs_path, "rb") as f:
                    zf.writestr(rel, f.read())
        zf.writestr("README.txt", _source_readme(manifest))
    return DeploymentDownload(
        body=buf.getvalue(),
        file_name=f"{_download_base_name(manifest)}-source.zip",
        content_type="application/zip",
    )


def build_deployment_container_zip(
    deployment_id: str, data_dir: str | None = None
) -> DeploymentDownload | None:
    manifest = read_deployment_manifest(deployment_id, data_dir)
    if manifest is None:
        return None

    deployment_dir = _get_deployment_dir(deployment_id, data_dir)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in _list_public_deployment_files(deployment_dir):
            abs_path = _safe_join_deployment_path(deployment_dir, rel)
            with open(abs_path, "rb") as f:
                zf.writestr(posixpath.join("app", rel), f.read())
        zf.writestr("Dockerfile", _dockerfile())
        zf.writestr("nginx.conf", _nginx_conf())
        zf.writestr("README.txt", _container_readme(manifest))
    return DeploymentDownload(
        body=buf.getvalue(),
        file_name=f"{_download_base_name(manifest)}-container.zip",
        content_type="application/zip",
    )


# ─── path normalisation ─────────────────────────────────────────────────────
def normalize_deployment_file_path(file_path: str) -> str | None:
    if "\0" in file_path:
        return None
    raw = file_path.strip().replace("\\", "/")
    if not raw:
        return None
    if raw.startswith("/") or raw.startswith("//") or _DRIVE_RE.match(raw):
        return None
    if any((not seg) or seg == ".." for seg in raw.split("/")):
        return None

    normalized = posixpath.normpath(raw)
    if (
        normalized == "."
        or normalized == ".."
        or normalized.startswith("../")
        or posixpath.isabs(normalized)
    ):
        return None

    segments = normalized.split("/")
    if any((not seg) or seg == "." or seg == ".." for seg in segments):
        return None
    if segments[0].lower() == PRIVATE_DIR:
        return None
    return normalized


def is_deployment_id(value: str) -> bool:
    return bool(_DEPLOYMENT_ID_RE.match(value))


# ─── internals ──────────────────────────────────────────────────────────────
def _assert_deployment_id(value: str) -> None:
    if not is_deployment_id(value):
        raise ValueError(f"Invalid deployment id: {value}")


def _get_data_dir(data_dir: str | None) -> str:
    if data_dir:
        return data_dir
    env = os.environ.get("AGENTHUB_DATA_DIR")
    if env:
        return os.path.abspath(env)
    return str(get_settings().data_path)


def _get_deployments_root(data_dir: str | None) -> str:
    return os.path.join(_get_data_dir(data_dir), "deployments")


def _get_deployment_dir(deployment_id: str, data_dir: str | None) -> str:
    _assert_deployment_id(deployment_id)
    return os.path.join(_get_deployments_root(data_dir), deployment_id)


def _normalize_web_app_files(files: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, body in files.items():
        normalized = normalize_deployment_file_path(name)
        if not normalized:
            raise ValueError(f"Unsafe web app file path: {name}")
        if normalized in out:
            raise ValueError(f"Duplicate web app file path after normalization: {name}")
        out[normalized] = body
    if not out:
        raise ValueError("Web app artifact has no deployable files")
    return out


def _resolve_source_entry(entry: str, files: dict[str, str]) -> str:
    normalized_entry = normalize_deployment_file_path(entry) if entry else None
    if entry and not normalized_entry:
        raise ValueError(f"Unsafe web app entry path: {entry}")
    if normalized_entry and normalized_entry in files:
        return normalized_entry
    if "index.html" in files:
        return "index.html"
    first_html = next((n for n in files if n.lower().endswith(".html")), None)
    if first_html:
        return first_html
    raise ValueError(f"Web app entry file not found: {entry}")


def _write_text_within(root: str, relative_path: str, body: str) -> None:
    abs_path = _safe_join_deployment_path(root, relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(body)


def _write_binary_within(root: str, relative_path: str, body: bytes) -> None:
    abs_path = _safe_join_deployment_path(root, relative_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(body)


def _safe_join_deployment_path(root: str, relative_path: str) -> str:
    normalized = normalize_deployment_file_path(relative_path)
    if (
        not normalized
        and relative_path != MANIFEST_PATH
        and not relative_path.startswith(f"{SOURCE_ROOT}/")
    ):
        raise ValueError(f"Invalid deployment file path: {relative_path}")
    parts = relative_path.replace("\\", "/").split("/")
    abs_path = os.path.abspath(os.path.join(root, *parts))
    if not is_path_within(abs_path, root):
        raise ValueError(f"Deployment path escapes root: {relative_path}")
    return abs_path


def _cleanup_partial(deployment_dir: str, deployments_root: str) -> None:
    if is_path_within(deployment_dir, deployments_root):
        shutil.rmtree(deployment_dir, ignore_errors=True)


def _list_public_deployment_files(root: str, relative_dir: str = "") -> list[str]:
    abs_dir = _safe_join_deployment_path(root, relative_dir) if relative_dir else root
    out: list[str] = []
    for entry in os.scandir(abs_dir):
        if entry.name == PRIVATE_DIR:
            continue
        rel = posixpath.join(relative_dir, entry.name) if relative_dir else entry.name
        if entry.is_dir():
            out.extend(_list_public_deployment_files(root, rel))
        elif entry.is_file():
            out.append(rel)
    return sorted(out)


def _list_workspace_static_files(
    source_dir: str, relative_dir: str = "", acc: dict | None = None
) -> list[str]:
    if acc is None:
        acc = {"files": 0, "bytes": 0}
    abs_dir = os.path.join(source_dir, *relative_dir.split("/")) if relative_dir else source_dir
    out: list[str] = []
    for entry in os.scandir(abs_dir):
        if entry.name.startswith(".") and entry.name != ".well-known":
            continue
        if entry.is_dir() and entry.name in WORKSPACE_DEPLOY_IGNORED_DIRS:
            continue
        rel = posixpath.join(relative_dir, entry.name) if relative_dir else entry.name
        normalized = normalize_deployment_file_path(rel)
        if not normalized:
            continue
        abs_path = os.path.join(source_dir, *normalized.split("/"))
        if entry.is_dir():
            out.extend(_list_workspace_static_files(source_dir, normalized, acc))
            continue
        if not entry.is_file():
            continue
        size = os.path.getsize(abs_path)
        acc["files"] += 1
        acc["bytes"] += size
        if acc["files"] > WORKSPACE_DEPLOY_MAX_FILES:
            raise ValueError(
                f"Workspace deployment has too many files (>{WORKSPACE_DEPLOY_MAX_FILES})"
            )
        if acc["bytes"] > WORKSPACE_DEPLOY_MAX_BYTES:
            raise ValueError(
                f"Workspace deployment is too large "
                f"(>{round(WORKSPACE_DEPLOY_MAX_BYTES / 1024 / 1024)}MB)"
            )
        out.append(normalized)
    return sorted(out)


def _resolve_workspace_entry(entry: str | None, files: list[str]) -> str:
    raw = (entry or "").strip() or RUNTIME_ENTRY
    normalized = normalize_deployment_file_path(raw)
    if not normalized:
        raise ValueError(f"Unsafe workspace deployment entry path: {raw}")
    if normalized not in files:
        raise ValueError(f"Workspace deployment entry not found: {normalized}")
    if not normalized.lower().endswith(".html"):
        raise ValueError(f"Workspace deployment entry must be an HTML file: {normalized}")
    return normalized


def _normalize_publish_root(publish_dir: str) -> str:
    trimmed = publish_dir.strip()
    if not trimmed:
        raise ValueError("Deployment publish directory is empty")
    if not os.path.isabs(trimmed):
        raise ValueError("Deployment publish directory must be an absolute path")
    resolved = os.path.abspath(trimmed)
    if resolved == os.path.splitdrive(resolved)[0] + os.sep or resolved == os.sep:
        raise ValueError("Deployment publish directory must not be the filesystem root")
    return resolved


def _public_deployment_url(base_url: str, deployment_id: str) -> str:
    _assert_deployment_id(deployment_id)
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Deployment public base URL must use http or https")
    if not parsed.netloc:
        raise ValueError("Deployment public base URL must be a valid absolute URL")
    base_path = parsed.path if parsed.path.endswith("/") else f"{parsed.path}/"
    new_path = f"{base_path}{quote(deployment_id, safe='')}/"
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", "", ""))


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}


def _content_type_for(file_path: str) -> str:
    ext = posixpath.splitext(file_path)[1].lower()
    return _CONTENT_TYPES.get(ext, "application/octet-stream")


def _response_headers_for(content_type: str) -> dict[str, str]:
    headers = {
        "Content-Type": content_type,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }
    if content_type.startswith("text/html"):
        headers["Content-Security-Policy"] = "; ".join(
            [
                "sandbox allow-scripts",
                "default-src 'self'",
                "script-src 'self' 'unsafe-inline'",
                "style-src 'self' 'unsafe-inline'",
                "img-src 'self' data: blob: http: https:",
                "font-src 'self' data:",
                "connect-src 'none'",
                "object-src 'none'",
                "base-uri 'none'",
                "form-action 'none'",
                "frame-ancestors 'self'",
            ]
        )
    elif content_type == "image/svg+xml":
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    return headers


def _iso(created_at: int) -> str:
    dt = datetime.fromtimestamp(created_at / 1000, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _source_readme(manifest: dict) -> str:
    if manifest.get("sourceType") == "workspace":
        return "\n".join(
            [
                f"Workspace deployment: {manifest['title']}",
                f"Source path: {manifest.get('workspacePath') or '(unknown)'}",
                f"Deployment: {manifest['id']}",
                f"Entry: {manifest['sourceEntry']}",
                "",
                "This ZIP contains files copied from a workspace static output directory.",
                f"Generated at: {_iso(manifest['createdAt'])}",
                "",
            ]
        )
    return "\n".join(
        [
            f"Artifact: {manifest['title']}",
            f"Version: v{manifest['version']}",
            f"Deployment: {manifest['id']}",
            f"Entry: {manifest['sourceEntry']}",
            "",
            "This ZIP contains the original web_app artifact source files.",
            f"Generated at: {_iso(manifest['createdAt'])}",
            "",
        ]
    )


def _container_readme(manifest: dict) -> str:
    is_workspace = manifest.get("sourceType") == "workspace"
    return "\n".join(
        [
            f"{'Workspace deployment' if is_workspace else 'Artifact'}: {manifest['title']}",
            (
                f"Source path: {manifest.get('workspacePath') or '(unknown)'}"
                if is_workspace
                else f"Version: v{manifest['version']}"
            ),
            f"Deployment: {manifest['id']}",
            "",
            "Build and run:",
            f"  docker build -t agenthub-{manifest['id']} .",
            f"  docker run --rm -p 8080:80 agenthub-{manifest['id']}",
            "",
            "Then open http://127.0.0.1:8080/",
            f"Generated at: {_iso(manifest['createdAt'])}",
            "",
        ]
    )


def _dockerfile() -> str:
    return (
        "\n".join(
            [
                "FROM nginx:1.27-alpine",
                "COPY app/ /usr/share/nginx/html/",
                "COPY nginx.conf /etc/nginx/conf.d/default.conf",
                "EXPOSE 80",
            ]
        )
        + "\n"
    )


def _nginx_conf() -> str:
    return (
        "\n".join(
            [
                "server {",
                "  listen 80;",
                "  server_name _;",
                "  root /usr/share/nginx/html;",
                "  index index.html;",
                "",
                "  location / {",
                "    try_files $uri $uri/ /index.html;",
                "  }",
                "",
                '  add_header X-Content-Type-Options "nosniff" always;',
                "}",
            ]
        )
        + "\n"
    )


def _download_base_name(manifest: dict) -> str:
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", manifest["title"])
    safe_title = re.sub(r"\s+", "_", safe_title)[:60].strip()
    return f"{safe_title or 'artifact'}-v{manifest['version']}-{manifest['id']}"


def _is_deployment_manifest(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        isinstance(value.get("id"), str)
        and is_deployment_id(value["id"])
        and isinstance(value.get("artifactId"), str)
        and isinstance(value.get("title"), str)
        and isinstance(value.get("version"), int)
        and value.get("deploymentType") == "local_static"
        and isinstance(value.get("createdAt"), int)
        and isinstance(value.get("sourceEntry"), str)
        and isinstance(value.get("runtimeEntry"), str)
        and isinstance(value.get("sourceFiles"), list)
        and all(isinstance(x, str) for x in value["sourceFiles"])
    )
