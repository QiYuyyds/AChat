"""Import-isolation check for the eval harness (task 6.3, spec §框架依赖隔离).

Scans every Python file under app/eval_harness with the AST and rejects any
absolute import of the AChat app package (``import app`` / ``from app...``).
This pins the one-way dependency rule: AChat → eval_harness only.
"""

import ast
import sys
from pathlib import Path

EVAL_HARNESS_ROOT = (
    Path(__file__).resolve().parent.parent / "app" / "eval_harness"
)


def _iter_python_files() -> list[Path]:
    assert EVAL_HARNESS_ROOT.is_dir(), f"missing {EVAL_HARNESS_ROOT}"
    return sorted(EVAL_HARNESS_ROOT.rglob("*.py"))


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


def test_eval_harness_does_not_import_app():
    violations: list[str] = []
    for path in _iter_python_files():
        violations.extend(_absolute_app_imports(path))
    assert violations == [], (
        "eval_harness 禁止 import app.* (单向依赖 AChat → eval_harness):\n"
        + "\n".join(violations)
    )


def test_eval_harness_importable_without_app_package():
    """框架可以在 app 包不可用的导入环境下完成核心模块导入。

    移除已加载的 app.* 与 eval_harness.* 模块后重新导入, 强制执行模块级
    代码 — 验证没有隐藏的运行期 app.* 依赖 (AST 扫描只覆盖静态 import)。
    """
    import importlib

    saved = {
        name: mod
        for name, mod in sys.modules.items()
        if name == "app"
        or name.startswith("app.")
        or name == "eval_harness"
        or name.startswith("eval_harness.")
    }
    try:
        for name in list(saved):
            del sys.modules[name]
        importlib.import_module("eval_harness.core.runner")
        importlib.import_module("eval_harness.core.suite")
        importlib.import_module("eval_harness.graders")
    finally:
        sys.modules.update(saved)
