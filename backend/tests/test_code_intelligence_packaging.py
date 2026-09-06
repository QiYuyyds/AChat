import json
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_desktop_build_prepares_and_bundles_platform_runtime() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["codegraph:prepare"] == (
        "node scripts/prepare-codegraph-runtime.mjs"
    )
    build_chain = package["scripts"]["electron:build"]
    # Runtimes must be prepared before electron-builder bundles the app.
    # (python-runtime:prepare was added for the packaged Python runtime.)
    assert "pnpm codegraph:prepare && pnpm python-runtime:prepare && electron-builder" in build_chain
    # Windows resources must bundle the codegraph runtime + manifest + notice;
    # python-runtime and backend/app entries were added alongside.
    win_resources = package["build"]["win"]["extraResources"]
    assert {
        "from": "resources/codegraph/codegraph-win32-${arch}.zip",
        "to": "codegraph/runtime.zip",
    } in win_resources
    assert {
        "from": "backend/app/code_intelligence/runtime-manifest.json",
        "to": "codegraph/runtime-manifest.json",
    } in win_resources
    assert {
        "from": "backend/app/code_intelligence/CODEGRAPH-NOTICE.txt",
        "to": "codegraph/CODEGRAPH-NOTICE.txt",
    } in win_resources
    assert {
        "from": "resources/python-runtime/python-runtime-win32-${arch}.zip",
        "to": "python-runtime/python-runtime.zip",
    } in win_resources
    assert {
        "from": "resources/python-runtime/python-runtime-darwin-${arch}.tar.gz",
        "to": "python-runtime/python-runtime.tar.gz",
    } in package["build"]["mac"]["extraResources"]


def test_codegraph_notice_preserves_mit_license() -> None:
    notice = (
        ROOT
        / "backend"
        / "app"
        / "code_intelligence"
        / "CODEGRAPH-NOTICE.txt"
    ).read_text(encoding="utf-8")

    assert "CodeGraph v0.9.3" in notice
    assert "MIT License" in notice
    assert "Copyright (c) 2026 Colby Mchenry" in notice
    assert "THE SOFTWARE IS PROVIDED \"AS IS\"" in notice
