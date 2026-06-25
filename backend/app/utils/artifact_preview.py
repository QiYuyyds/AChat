"""Artifact preview path + web_app → single-file HTML bundling.

Port of src/lib/artifact-preview.ts. ``build_web_app_html`` inlines a web_app
artifact's css/js into its HTML so a deployment (or preview iframe) can serve a
single self-contained document.
"""

from __future__ import annotations

import re
from urllib.parse import quote

_HEAD_CLOSE_RE = re.compile(r"</head>", re.IGNORECASE)
_BODY_CLOSE_RE = re.compile(r"</body>", re.IGNORECASE)


def artifact_preview_path(artifact_id: str) -> str:
    return f"/api/artifacts/{quote(artifact_id, safe='')}/preview"


def build_web_app_html(files: dict[str, str], entry: str) -> str:
    return build_iframe_html(files, entry)


def build_iframe_html(files: dict[str, str], entry: str) -> str:
    html = files.get(entry) or files.get("index.html") or ""
    css = files.get("style.css") or files.get("styles.css") or ""
    js = files.get("script.js") or files.get("main.js") or files.get("app.js") or ""

    style_tag = f"<style>\n{css}\n</style>" if css else ""
    script_tag = "<script>(function(){\n" + js + "\n})();<" + "/script>" if js else ""

    if _HEAD_CLOSE_RE.search(html):
        # Use callables so backslash/group-ref sequences inside css/js are not
        # interpreted by re.sub's replacement mini-language.
        html = _HEAD_CLOSE_RE.sub(lambda _m: f"{style_tag}\n</head>", html, count=1)
        html = _BODY_CLOSE_RE.sub(lambda _m: f"{script_tag}\n</body>", html, count=1)
        return html

    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="zh-CN">',
            "<head>",
            '<meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1">',
            style_tag,
            "</head>",
            "<body>",
            html,
            script_tag,
            "</body>",
            "</html>",
        ]
    )
