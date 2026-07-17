import json
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_desktop_build_prepares_and_bundles_platform_runtime() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert package["scripts"]["codegraph:prepare"] == (
        "node scripts/prepare-codegraph-runtime.mjs"
    )
    assert package["scripts"]["electron:build"].endswith(
        "pnpm codegraph:prepare && electron-builder"
    )
    assert package["build"]["win"]["extraResources"] == [
        {
            "from": "resources/codegraph/codegraph-win32-${arch}.zip",
            "to": "codegraph/runtime.zip",
        },
        {
            "from": "backend/app/code_intelligence/runtime-manifest.json",
            "to": "codegraph/runtime-manifest.json",
        },
        {
            "from": "backend/app/code_intelligence/CODEGRAPH-NOTICE.txt",
            "to": "codegraph/CODEGRAPH-NOTICE.txt",
        },
    ]
    assert package["build"]["mac"]["extraResources"][0] == {
        "from": "resources/codegraph/codegraph-darwin-${arch}.tar.gz",
        "to": "codegraph/runtime.tar.gz",
    }


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
