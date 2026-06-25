"""Pure tests for the artifact-content builder + command security (phase 3)."""

from app.services.artifact_service import (
    build_artifact_content,
    describe_artifact_content_error,
)
from app.utils.security import find_banned_pattern


def test_web_app_from_html_string():
    content = build_artifact_content("web_app", "<h1>hi</h1>")
    assert content == {
        "type": "web_app",
        "files": {"index.html": "<h1>hi</h1>"},
        "entry": "index.html",
    }


def test_web_app_from_files_object():
    content = build_artifact_content(
        "web_app", {"files": {"index.html": "<p>x</p>", "style.css": "p{}"}}
    )
    assert content is not None
    assert content["entry"] == "index.html"
    assert set(content["files"]) == {"index.html", "style.css"}


def test_web_app_unwraps_stringified_content():
    content = build_artifact_content(
        "web_app", '{"files":{"index.html":"<p>ok</p>"},"entry":"index.html"}'
    )
    assert content is not None
    assert content["files"]["index.html"] == "<p>ok</p>"


def test_document_variants():
    assert build_artifact_content("document", {"markdown": "# h"}) == {
        "type": "document",
        "format": "markdown",
        "content": "# h",
    }
    assert build_artifact_content("document", "plain")["content"] == "plain"


def test_diagram_valid_and_invalid():
    ok = build_artifact_content("diagram", {"source": "flowchart TD\nA[中文] --> B[结果]"})
    assert ok is not None
    assert ok["type"] == "diagram" and ok["syntax"] == "mermaid"
    # Chinese labels are auto-quoted by normalisation.
    assert 'A["中文"]' in ok["source"]

    assert build_artifact_content("diagram", {"source": "not a diagram"}) is None
    msg = describe_artifact_content_error("diagram", {"source": "not a diagram"})
    assert msg and "Mermaid" in msg


def test_ppt_with_blocks():
    content = build_artifact_content(
        "ppt",
        {
            "title": "Deck",
            "slides": [
                {"title": "S1", "blocks": [{"type": "bullets", "items": ["a", "b"]}]},
            ],
        },
    )
    assert content is not None
    assert content["type"] == "ppt"
    assert content["title"] == "Deck"
    assert content["slides"][0]["blocks"][0]["items"] == ["a", "b"]


def test_ppt_rejects_base64_payload():
    assert (
        build_artifact_content(
            "ppt", {"slides": [{"title": "x", "image": "data:image/png;base64,AAAA"}]}
        )
        is None
    )


def test_unknown_type_returns_none():
    assert build_artifact_content("nonsense", {"x": 1}) is None


def test_security_posix_blacklist():
    assert find_banned_pattern("rm -rf /", "posix") is not None
    assert find_banned_pattern("sudo apt update", "posix") is not None
    assert find_banned_pattern("ls -la", "posix") is None


def test_security_windows_blacklist():
    assert find_banned_pattern("Remove-Item -Recurse -Force C:/data", "windows") is not None
    assert find_banned_pattern("shutdown /s", "windows") is not None
    assert find_banned_pattern("Get-ChildItem -Force", "windows") is None
