"""Same-wave code-conflict tracking for the Orchestrator.

Port of src/server/dispatch-file-writes.ts. Records which workspace files each
child run wrote via fs_write (absolute path -> content hash), so AgentRunner can
detect "two child agents wrote the same file differently" after a parallel wave.

Blind spot (see specs/06): the bash tool and SDK-native write tools don't go
through fs_write and so aren't recorded here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

_writes_by_run: dict[str, dict[str, str]] = {}


def record_file_write(run_id: str, absolute_path: str, content: str) -> None:
    files = _writes_by_run.get(run_id)
    if files is None:
        files = {}
        _writes_by_run[run_id] = files
    files[absolute_path] = hashlib.sha1(content.encode("utf-8")).hexdigest()


def get_file_writes(run_id: str) -> dict[str, str]:
    return _writes_by_run.get(run_id, {})


def clear_file_writes(run_id: str) -> None:
    _writes_by_run.pop(run_id, None)


@dataclass
class RunFileWrites:
    task_id: str
    agent_id: str
    run_id: str
    writes: dict[str, str]  # absolute_path -> content hash


@dataclass
class FileWriteConflict:
    path: str  # conflicting file's absolute path
    contributors: list[dict[str, str]]  # {taskId, agentId, runId}


def detect_wave_conflicts(runs: list[RunFileWrites]) -> list[FileWriteConflict]:
    """≥2 child runs wrote the same path with differing content (differing hash).

    Identical concurrent writes are not a conflict. Pure function, easy to test.
    """
    by_path: dict[str, list[dict[str, str]]] = {}
    for run in runs:
        for abs_path, hash_ in run.writes.items():
            writers = by_path.setdefault(abs_path, [])
            writers.append(
                {
                    "taskId": run.task_id,
                    "agentId": run.agent_id,
                    "runId": run.run_id,
                    "hash": hash_,
                }
            )

    conflicts: list[FileWriteConflict] = []
    for abs_path, writers in by_path.items():
        if len(writers) < 2:
            continue
        if len({w["hash"] for w in writers}) < 2:
            continue
        conflicts.append(
            FileWriteConflict(
                path=abs_path,
                contributors=[
                    {"taskId": w["taskId"], "agentId": w["agentId"], "runId": w["runId"]}
                    for w in writers
                ],
            )
        )
    return conflicts
