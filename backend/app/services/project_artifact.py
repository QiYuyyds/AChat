"""Project artifact helpers.

Port of src/server/project-artifact.ts: turn applied fs_write evidence into a
project file list, and zip the live workspace files for export.
"""

from __future__ import annotations

import io
import os
import zipfile
from collections.abc import Sequence

from app.schemas.artifacts import ProjectFile
from app.utils.dispatch_run_evidence import RunFileEvidence
from app.utils.workspace_utils import is_path_within


def build_project_files(
    file_writes: Sequence[RunFileEvidence],
    workspace_root: str,
) -> list[ProjectFile]:
    """Build the project file list from applied fs_write evidence.

    absolute_path is the trustworthy field; the tool input path can be relative or absolute.
    """
    by_path: dict[str, ProjectFile] = {}
    for fw in file_writes:
        if not is_path_within(fw.absolute_path, workspace_root):
            continue
        rel = _to_rel(fw.absolute_path, workspace_root)
        if not rel:
            continue
        # Dedupe by path; keep the last write's size (fall back to prior size, else 0).
        prior = by_path.get(rel)
        size = fw.bytes if fw.bytes is not None else (prior.size_bytes if prior else 0)
        by_path[rel] = ProjectFile(path=rel, sizeBytes=size)
    return sorted(by_path.values(), key=lambda f: f.path)


def _to_rel(abs_path: str, root: str) -> str | None:
    rel = os.path.relpath(abs_path, root)
    if not rel or rel.startswith("..") or os.path.isabs(rel):
        return None
    return rel.replace(os.sep, "/")


def zip_project_from_workspace(
    workspace_root: str,
    files: Sequence[ProjectFile],
    title: str,
    exported_at_iso: str,
) -> bytes:
    """Zip the named workspace files (skipping any that escape the root) plus a README."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in files:
            rel = _normalize_project_path(file.path)
            if not rel:
                continue
            abs_path = os.path.abspath(os.path.join(workspace_root, rel))
            if not is_path_within(abs_path, workspace_root):
                continue
            try:
                # The live workspace may have changed since the artifact was created.
                if os.path.isfile(abs_path):
                    with open(abs_path, "rb") as fh:
                        zf.writestr(rel, fh.read())
            except OSError:
                continue
        zf.writestr(
            "README.txt",
            f"Project artifact: {title}\nFiles: {len(files)}\nExported at: {exported_at_iso}\n",
        )
    return buffer.getvalue()


def _normalize_project_path(input_path: str) -> str | None:
    if not input_path or os.path.isabs(input_path) or _has_drive_prefix(input_path):
        return None
    parts = [p for p in _split_path(input_path) if p and p != "."]
    if not parts or ".." in parts:
        return None
    return "/".join(parts)


def _has_drive_prefix(value: str) -> bool:
    return len(value) >= 2 and value[1] == ":" and value[0].isalpha()


def _split_path(value: str) -> list[str]:
    return [seg for seg in value.replace("\\", "/").split("/")]
