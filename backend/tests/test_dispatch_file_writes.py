"""Unit tests for same-wave write-conflict tracking (specs/06 降级路径).

Covers the pure detection function plus the record/get/clear lifecycle and
the bounded-cache guarantee of ``app.utils.dispatch_file_writes``.
"""

from __future__ import annotations

import hashlib

import app.utils.dispatch_file_writes as dfw
from app.utils.dispatch_file_writes import (
    RunFileWrites,
    clear_file_writes,
    detect_wave_conflicts,
    get_file_writes,
    record_file_write,
)


def _run(task_id: str, run_id: str, writes: dict[str, str]) -> RunFileWrites:
    return RunFileWrites(task_id=task_id, agent_id=f"ag_{task_id}", run_id=run_id, writes=writes)


# ─── record / get / clear ─────────────────────────────────────────────────────


def test_record_and_get():
    record_file_write("run_1", "/ws/a.py", "content-a")
    try:
        assert get_file_writes("run_1") == {
            "/ws/a.py": hashlib.sha1(b"content-a").hexdigest(),
        }
    finally:
        clear_file_writes("run_1")


def test_get_unknown_run_returns_empty():
    assert get_file_writes("run_missing") == {}


def test_record_overwrites_same_path_keeps_last_hash():
    record_file_write("run_1", "/ws/a.py", "first")
    record_file_write("run_1", "/ws/a.py", "second")
    try:
        assert get_file_writes("run_1") == {
            "/ws/a.py": hashlib.sha1(b"second").hexdigest(),
        }
    finally:
        clear_file_writes("run_1")


def test_clear_removes_records():
    record_file_write("run_x", "/ws/b.py", "content")
    clear_file_writes("run_x")
    assert get_file_writes("run_x") == {}


# ─── detect_wave_conflicts (pure) ─────────────────────────────────────────────


def test_no_conflict_for_single_writer():
    runs = [_run("t1", "r1", {"/ws/a.py": "h1"})]
    assert detect_wave_conflicts(runs) == []


def test_no_conflict_for_disjoint_paths():
    runs = [
        _run("t1", "r1", {"/ws/a.py": "h1"}),
        _run("t2", "r2", {"/ws/b.py": "h2"}),
    ]
    assert detect_wave_conflicts(runs) == []


def test_no_conflict_for_identical_content():
    runs = [
        _run("t1", "r1", {"/ws/a.py": "same-hash"}),
        _run("t2", "r2", {"/ws/a.py": "same-hash"}),
    ]
    assert detect_wave_conflicts(runs) == []


def test_conflict_on_differing_content():
    runs = [
        _run("t1", "r1", {"/ws/a.py": "h1", "/ws/only1.py": "x"}),
        _run("t2", "r2", {"/ws/a.py": "h2"}),
        _run("t3", "r3", {"/ws/only3.py": "y"}),
    ]
    conflicts = detect_wave_conflicts(runs)
    assert len(conflicts) == 1
    assert conflicts[0].path == "/ws/a.py"
    assert {c["taskId"] for c in conflicts[0].contributors} == {"t1", "t2"}


def test_three_way_conflict_lists_all_contributors():
    runs = [
        _run("t1", "r1", {"/ws/a.py": "h1"}),
        _run("t2", "r2", {"/ws/a.py": "h2"}),
        _run("t3", "r3", {"/ws/a.py": "h3"}),
    ]
    conflicts = detect_wave_conflicts(runs)
    assert len(conflicts) == 1
    assert {c["taskId"] for c in conflicts[0].contributors} == {"t1", "t2", "t3"}


# ─── bounded cache ────────────────────────────────────────────────────────────


def test_cache_is_bounded_evicts_oldest():
    dfw._writes_by_run.clear()
    try:
        for i in range(dfw._MAX_TRACKED_RUNS + 5):
            record_file_write(f"run_{i}", "/ws/a.py", f"content-{i}")

        assert len(dfw._writes_by_run) == dfw._MAX_TRACKED_RUNS
        # The 5 oldest entries were evicted; the newest are retained.
        assert "run_0" not in dfw._writes_by_run
        assert "run_4" not in dfw._writes_by_run
        assert f"run_{dfw._MAX_TRACKED_RUNS + 4}" in dfw._writes_by_run
    finally:
        dfw._writes_by_run.clear()
