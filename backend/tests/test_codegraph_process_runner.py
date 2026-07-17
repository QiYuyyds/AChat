import asyncio
import sys
import threading
import time
from pathlib import Path

import pytest


def _resolved_runtime(tmp_path: Path):
    from app.code_intelligence.runtime import ResolvedRuntime, RuntimeArtifact

    artifact = RuntimeArtifact(
        platform_key="win32-x64",
        version="0.9.3",
        url="https://example.test/codegraph.zip",
        sha256="a" * 64,
        archive_type="zip",
    )
    return ResolvedRuntime("cache", tmp_path / "runtime", artifact)


def test_query_metacharacters_remain_one_argv_item(tmp_path: Path) -> None:
    from app.code_intelligence.process_runner import CodeGraphCommandRunner

    runtime = _resolved_runtime(tmp_path)
    query = 'quotes " newline\n Unicode 中文 & | < > ^ % !'

    argv = CodeGraphCommandRunner.build_argv(
        runtime,
        operation="explore",
        project_path=tmp_path / "project",
        query=query,
    )

    assert argv.count(query) == 1
    assert "context" in argv
    assert "--path" in argv
    assert str((tmp_path / "project").resolve()) in argv
    assert all(arg != "shell=True" for arg in argv)


def test_index_operations_use_fixed_subcommands(tmp_path: Path) -> None:
    from app.code_intelligence.process_runner import CodeGraphCommandRunner

    runtime = _resolved_runtime(tmp_path)
    project = tmp_path / "project"

    assert "init" in CodeGraphCommandRunner.build_argv(
        runtime, operation="init", project_path=project
    )
    assert "--verbose" in CodeGraphCommandRunner.build_argv(
        runtime, operation="init", project_path=project
    )
    assert "sync" in CodeGraphCommandRunner.build_argv(
        runtime, operation="sync", project_path=project
    )
    rebuild = CodeGraphCommandRunner.build_argv(
        runtime, operation="rebuild", project_path=project
    )
    assert "index" in rebuild
    assert "--force" in rebuild
    assert "--verbose" in rebuild
    assert "--quiet" not in rebuild


@pytest.mark.asyncio
async def test_process_cancellation_returns_promptly(tmp_path: Path) -> None:
    from app.code_intelligence.process_runner import run_process

    cancel_event = asyncio.Event()
    started = time.monotonic()
    task = asyncio.create_task(
        run_process(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path,
            cancel_event=cancel_event,
            timeout_seconds=60,
        )
    )
    await asyncio.sleep(0.1)
    cancel_event.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 5


@pytest.mark.asyncio
async def test_process_streams_stdout_lines_before_exit(tmp_path: Path) -> None:
    from app.code_intelligence.process_runner import run_process

    first_line = threading.Event()
    lines: list[str] = []

    def on_stdout_line(line: str) -> None:
        lines.append(line)
        first_line.set()

    task = asyncio.create_task(
        run_process(
            [
                sys.executable,
                "-c",
                "import time; print('Phase: parsing', flush=True); time.sleep(1); print('done', flush=True)",
            ],
            cwd=tmp_path,
            cancel_event=asyncio.Event(),
            timeout_seconds=5,
            on_stdout_line=on_stdout_line,
        )
    )

    assert await asyncio.to_thread(first_line.wait, 0.75)
    assert task.done() is False
    result = await task
    assert lines == ["Phase: parsing", "done"]
    assert "Phase: parsing" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows selector-loop regression")
@pytest.mark.asyncio
async def test_windows_process_runner_does_not_require_asyncio_subprocess_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.code_intelligence.process_runner import run_process

    async def unsupported_subprocess(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr(asyncio, "create_subprocess_exec", unsupported_subprocess)

    result = await run_process(
        [sys.executable, "-c", "print('selector-compatible')"],
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        timeout_seconds=5,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "selector-compatible"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows subprocess regression")
@pytest.mark.asyncio
async def test_windows_process_runner_closes_stdin_for_non_interactive_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.code_intelligence import process_runner

    real_popen = process_runner.subprocess.Popen
    observed_stdin: list[object] = []

    def recording_popen(*args, **kwargs):
        observed_stdin.append(kwargs.get("stdin"))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(process_runner.subprocess, "Popen", recording_popen)

    await process_runner.run_process(
        [sys.executable, "-c", "print('non-interactive')"],
        cwd=tmp_path,
        cancel_event=asyncio.Event(),
        timeout_seconds=5,
    )

    assert observed_stdin == [process_runner.subprocess.DEVNULL]


def test_status_json_maps_to_bounded_counts() -> None:
    from app.code_intelligence.process_runner import parse_status_counts

    assert parse_status_counts(
        '{"fileCount": 5, "nodeCount": 40, "edgeCount": 12}'
    ) == {"files": 5, "symbols": 40, "relationships": 12}
