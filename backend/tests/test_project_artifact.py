"""Tests for project_artifact (port of src/server/project-artifact.test.ts)."""

from __future__ import annotations

import io
import os
import zipfile

from app.schemas.artifacts import ProjectFile
from app.services.project_artifact import build_project_files, zip_project_from_workspace
from app.utils.dispatch_run_evidence import RunFileEvidence

ROOT = os.path.abspath(os.path.join(os.sep, "ws", "proj"))


def fw(rel: str, byte_count: int | None = None) -> RunFileEvidence:
    return RunFileEvidence(
        path="whatever",
        absolute_path=os.path.join(ROOT, *rel.split("/")),
        bytes=byte_count,
    )


def _as_dicts(files: list[ProjectFile]) -> list[dict]:
    return [{"path": f.path, "sizeBytes": f.size_bytes} for f in files]


def test_relativizes_and_sorts():
    result = build_project_files([fw("src/a.ts", 10), fw("b.ts", 20)], ROOT)
    assert _as_dicts(result) == [
        {"path": "b.ts", "sizeBytes": 20},
        {"path": "src/a.ts", "sizeBytes": 10},
    ]


def test_dedupes_keeping_last_size():
    result = build_project_files([fw("a.ts", 10), fw("a.ts", 30)], ROOT)
    assert _as_dicts(result) == [{"path": "a.ts", "sizeBytes": 30}]


def test_skips_paths_outside_root():
    outside = RunFileEvidence(
        path="x",
        absolute_path=os.path.abspath(os.path.join(os.sep, "other", "x.ts")),
        bytes=9,
    )
    result = build_project_files([fw("a.ts", 5), outside], ROOT)
    assert _as_dicts(result) == [{"path": "a.ts", "sizeBytes": 5}]


def test_empty_writes():
    assert build_project_files([], ROOT) == []


def test_missing_bytes_falls_back_to_zero():
    result = build_project_files([fw("a.ts", None)], ROOT)
    assert _as_dicts(result) == [{"path": "a.ts", "sizeBytes": 0}]


def test_zip_includes_only_normalized_files_inside_workspace(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.ts").write_text("export const ok = true\n")

    buf = zip_project_from_workspace(
        str(tmp_path),
        [
            ProjectFile(path="src/app.ts", sizeBytes=23),
            ProjectFile(path="../outside.ts", sizeBytes=1),
            ProjectFile(path="src/../app.ts", sizeBytes=1),
        ],
        "project",
        "2026-01-01T00:00:00.000Z",
    )

    with zipfile.ZipFile(io.BytesIO(buf)) as zf:
        names = set(zf.namelist())
    assert "src/app.ts" in names
    assert "../outside.ts" not in names
    assert "src/../app.ts" not in names
    assert "README.txt" in names
