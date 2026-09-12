"""first-suite.yaml 加载校验 (任务 4.2) — 真实链路验收的 YAML 关卡。"""

from __future__ import annotations

from pathlib import Path

from agent_eval.core.types import EvalSuite

SUITE_PATH = Path(__file__).resolve().parent.parent / "eval_suites" / "first-suite.yaml"


def test_first_suite_yaml_loads_and_validates():
    from agent_eval.core.suite import load_suite

    suite = load_suite(SUITE_PATH)
    assert isinstance(suite, EvalSuite)
    assert suite.name == "achat-first-suite"
    task_ids = [t.id for t in suite.tasks]
    assert task_ids == ["simple-qa", "file-creation", "seed-file-qa"]


def test_first_suite_seed_file_declaration():
    from agent_eval.core.suite import load_suite

    suite = load_suite(SUITE_PATH)
    seed_task = next(t for t in suite.tasks if t.id == "seed-file-qa")
    files = seed_task.env["files"]
    assert "project_code.txt" in files
    assert "AEVAL-2026" in files["project_code.txt"]
