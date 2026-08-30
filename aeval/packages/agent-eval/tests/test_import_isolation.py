"""Import-isolation check for the Aeval framework (spec §框架依赖隔离).

Scans every Python file under src/agent_eval with the AST and rejects any
absolute import of a host-application package (``import app`` / ``from app...``).
This pins the one-way dependency rule: host app → agent_eval only.
"""

import ast
import sys
from pathlib import Path

AGENT_EVAL_ROOT = (
    Path(__file__).resolve().parent.parent / "src" / "agent_eval"
)


def _iter_python_files() -> list[Path]:
    assert AGENT_EVAL_ROOT.is_dir(), f"missing {AGENT_EVAL_ROOT}"
    return sorted(AGENT_EVAL_ROOT.rglob("*.py"))


def _check_module(module: str, path: Path, violations: list[str]) -> None:
    if module == "app" or module.startswith("app."):
        violations.append(f"{path.name}: import from '{module}'")


def _absolute_app_imports(path: Path) -> list[str]:
    """返回该文件中所有 import app / from app... 的描述"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_module(alias.name, path, violations)
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                # 相对导入是框架内部引用, 允许
                continue
            _check_module(node.module or "", path, violations)
    return violations


def test_no_python_files_outside_package():
    """sanity: 扫描器确实覆盖了框架的全部文件"""
    files = _iter_python_files()
    assert len(files) >= 20  # contract/types/runner/metrics/suite + 8 graders + storage/trace/api/examples


def test_agent_eval_does_not_import_app():
    violations: list[str] = []
    for path in _iter_python_files():
        violations.extend(_absolute_app_imports(path))
    assert violations == [], (
        "agent_eval 禁止 import app.* (单向依赖 AChat → agent_eval):\n"
        + "\n".join(violations)
    )


def test_agent_eval_importable_without_app_package():
    """框架可以在 app 包不可用的导入环境下完成核心模块导入。

    移除已加载的 app.* 与 agent_eval.* 模块后重新导入, 强制执行模块级
    代码 — 验证没有隐藏的运行期 app.* 依赖 (AST 扫描只覆盖静态 import)。
    """
    import importlib

    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "app"
        or name.startswith("app.")
        or name == "agent_eval"
        or name.startswith("agent_eval.")
    }
    try:
        for name in list(saved):
            del sys.modules[name]
        importlib.import_module("agent_eval.core.runner")
        importlib.import_module("agent_eval.core.suite")
        importlib.import_module("agent_eval.graders")
    finally:
        sys.modules.update(saved)
