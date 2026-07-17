import json
from pathlib import Path


def test_runtime_manifest_pins_supported_desktop_artifacts() -> None:
    manifest_path = (
        Path(__file__).parents[1]
        / "app"
        / "code_intelligence"
        / "runtime-manifest.json"
    )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["version"] == "0.9.3"
    assert manifest["license"] == {
        "spdx": "MIT",
        "name": "MIT License",
        "url": "https://github.com/colbymchenry/codegraph/blob/v0.9.3/LICENSE",
        "noticeFile": "CODEGRAPH-NOTICE.txt",
    }

    artifacts = manifest["artifacts"]
    assert set(artifacts) == {
        "darwin-arm64",
        "darwin-x64",
        "linux-arm64",
        "linux-x64",
        "win32-arm64",
        "win32-x64",
    }
    for platform_key, artifact in artifacts.items():
        assert artifact["url"].startswith(
            "https://github.com/colbymchenry/codegraph/releases/download/v0.9.3/"
        )
        assert artifact["archiveType"] in {"tar.gz", "zip"}
        assert len(artifact["sha256"]) == 64
        assert artifact["sha256"].isalnum(), platform_key

