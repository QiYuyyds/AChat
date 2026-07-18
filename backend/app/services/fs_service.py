"""Workspace filesystem helpers shared by the fs tools and the file-browser API.

Port of src/server/fs-service.ts. All paths go through the workspace sandbox
(``assert_path_within_workspace``); sandbox-mode workspaces additionally enforce
a total quota.
"""

from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass

from app.db.models import Workspace
from app.infra.cache_helpers import get_workspace_cached
from app.utils.workspace_utils import assert_path_within_workspace, get_effective_cwd

MAX_READ_BYTES = 1_048_576  # 1 MB
MAX_READ_CHARS = 50_000
MAX_WRITE_BYTES = 100 * 1024  # 100 KB
SANDBOX_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MB
SANDBOX_TOTAL_FILES = 1000

# Directories skipped during recursive listing (depth > 1).
# Mirrors fs_grep._SKIP_DIRS so both tools agree on what counts as "dependency".
_SKIP_DIRS = frozenset({
    "node_modules", ".git", ".venv", "__pycache__", ".next", "dist", "build",
})

MAX_RECURSIVE_ENTRIES = 500


async def get_workspace_for_conversation(conversation_id: str) -> Workspace | None:
    return await get_workspace_cached(conversation_id)


def read_if_exists(workspace: Workspace, target: str) -> str | None:
    """Read a workspace file, returning None if missing/too large (for diffs)."""
    try:
        abs_path = assert_path_within_workspace(workspace, target)
    except ValueError:
        return None
    try:
        if not os.path.exists(abs_path) or not os.path.isfile(abs_path):
            return None
        if os.path.getsize(abs_path) > MAX_READ_BYTES:
            return None
        with open(abs_path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


@dataclass
class ReadResult:
    path: str
    absolute_path: str
    cwd: str
    size: int
    content: str
    truncated: bool
    start_line: int = 0
    end_line: int = 0
    total_lines: int = 0


def read_file_in_workspace(
    workspace: Workspace,
    target: str,
    offset: int = 0,
    limit: int = 0,
) -> ReadResult:
    abs_path = assert_path_within_workspace(workspace, target)
    if not os.path.isfile(abs_path):
        raise ValueError(f"Not a file: {target}")
    size = os.path.getsize(abs_path)
    if size > MAX_READ_BYTES:
        raise ValueError(f"File too large ({size / 1024 / 1024:.2f} MB > 1 MB limit)")

    if offset > 0 or limit > 0:
        start_line = offset if offset > 0 else 1
        collected: list[str] = []
        total_lines = 0
        with open(abs_path, encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                total_lines = i
                if i < start_line:
                    continue
                if limit > 0 and len(collected) >= limit:
                    continue
                collected.append(line)
        raw = "".join(collected)
        end_line = start_line + len(collected) - 1 if collected else start_line
    else:
        with open(abs_path, encoding="utf-8") as f:
            raw = f.read()
        start_line = 0
        end_line = 0
        total_lines = 0

    truncated = len(raw) > MAX_READ_CHARS
    content = (
        raw[:MAX_READ_CHARS] + f"\n\n[TRUNCATED at {MAX_READ_CHARS} chars]"
        if truncated
        else raw
    )
    return ReadResult(
        path=target,
        absolute_path=abs_path,
        cwd=get_effective_cwd(workspace),
        size=size,
        content=content,
        truncated=truncated,
        start_line=start_line,
        end_line=end_line,
        total_lines=total_lines,
    )


@dataclass
class WriteResult:
    path: str
    absolute_path: str
    cwd: str
    bytes: int


def write_file_in_workspace(workspace: Workspace, target: str, content: str) -> WriteResult:
    byte_len = len(content.encode("utf-8"))
    if byte_len > MAX_WRITE_BYTES:
        raise ValueError(f"Content too large ({byte_len / 1024:.1f} KB > 100 KB limit)")
    abs_path = assert_path_within_workspace(workspace, target)

    if workspace.mode == "sandbox":
        used_bytes, used_files = _scan_workspace_usage(workspace.root_path)
        if used_bytes + byte_len > SANDBOX_TOTAL_BYTES:
            raise ValueError(
                f"Workspace quota exceeded ({used_bytes / 1024 / 1024:.1f} MB used "
                f"+ {byte_len / 1024:.1f} KB > 100 MB cap)"
            )
        if used_files + 1 > SANDBOX_TOTAL_FILES:
            raise ValueError(f"Workspace file count exceeded ({used_files} + 1 > 1000 cap)")

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    return WriteResult(
        path=target,
        absolute_path=abs_path,
        cwd=get_effective_cwd(workspace),
        bytes=byte_len,
    )


@dataclass
class ListEntry:
    name: str
    is_directory: bool
    size: int | None = None
    relative_path: str | None = None
    depth: int | None = None


@dataclass
class ListResult:
    rel_path: str
    absolute_path: str
    parent: str | None
    entries: list[ListEntry]


def list_dir_in_workspace(
    workspace: Workspace, target: str, show_hidden: bool = False
) -> ListResult:
    rel_path = target if target else ""
    abs_path = (
        get_effective_cwd(workspace)
        if rel_path == ""
        else assert_path_within_workspace(workspace, target)
    )

    if not os.path.isdir(abs_path):
        raise ValueError(f"Not a directory: {target or '(root)'}")

    entries: list[ListEntry] = []
    with os.scandir(abs_path) as it:
        for e in it:
            if not show_hidden and e.name.startswith("."):  # hide dotfiles by default
                continue
            is_dir = e.is_dir()
            entry = ListEntry(name=e.name, is_directory=is_dir)
            if e.is_file():
                with contextlib.suppress(OSError):
                    entry.size = e.stat().st_size
            entries.append(entry)

    # directories first, then case-sensitive name order (matches localeCompare-ish)
    entries.sort(key=lambda x: (not x.is_directory, x.name))

    parent: str | None
    if rel_path == "":
        parent = None
    else:
        p = os.path.dirname(rel_path.replace("\\", "/"))
        parent = "" if p in (".", "") else p

    return ListResult(
        rel_path=rel_path, absolute_path=abs_path, parent=parent, entries=entries
    )


@dataclass
class RecursiveListResult:
    rel_path: str
    absolute_path: str
    entries: list[ListEntry]
    truncated: bool


def list_dir_recursive(
    workspace: Workspace,
    target: str,
    depth: int,
    show_hidden: bool = False,
) -> RecursiveListResult:
    """Recursively list entries up to *depth* levels, skipping dependency dirs.

    Returns a flat list where each entry carries ``relative_path`` and ``depth``.
    Entry count is capped at ``MAX_RECURSIVE_ENTRIES`` (500); ``truncated`` is set
    when the cap is hit.
    """
    rel_path = target if target else ""
    abs_path = (
        get_effective_cwd(workspace)
        if rel_path == ""
        else assert_path_within_workspace(workspace, target)
    )
    if not os.path.isdir(abs_path):
        raise ValueError(f"Not a directory: {target or '(root)'}")

    entries: list[ListEntry] = []
    truncated = False

    def _scan(dir_abs: str, dir_rel: str, current_depth: int) -> None:
        nonlocal truncated
        if truncated or current_depth > depth:
            return
        try:
            scanned = sorted(os.scandir(dir_abs), key=lambda e: e.name)
        except OSError:
            return
        for e in scanned:
            if len(entries) >= MAX_RECURSIVE_ENTRIES:
                truncated = True
                return
            if not show_hidden and e.name.startswith("."):
                continue
            entry_rel = f"{dir_rel}/{e.name}" if dir_rel else e.name
            is_dir = e.is_dir()
            entry = ListEntry(
                name=e.name,
                is_directory=is_dir,
                relative_path=entry_rel,
                depth=current_depth,
            )
            if e.is_file():
                with contextlib.suppress(OSError):
                    entry.size = e.stat().st_size
            entries.append(entry)
            if is_dir and e.name not in _SKIP_DIRS and current_depth < depth:
                _scan(os.path.join(dir_abs, e.name), entry_rel, current_depth + 1)
                if truncated:
                    return

    _scan(abs_path, rel_path, 1)

    # directories first within each depth, then by name
    entries.sort(key=lambda x: (x.depth or 0, not x.is_directory, x.name))

    return RecursiveListResult(
        rel_path=rel_path, absolute_path=abs_path, entries=entries, truncated=truncated
    )


def _scan_workspace_usage(root_path: str) -> tuple[int, int]:
    """(bytes, files) under root_path; realpath-dedup guards against symlink cycles."""
    total_bytes = 0
    total_files = 0
    if not os.path.exists(root_path):
        return total_bytes, total_files
    visited: set[str] = set()
    stack = [root_path]
    while stack:
        directory = stack.pop()
        try:
            real = os.path.realpath(directory)
        except OSError:
            continue
        if real in visited:
            continue
        visited.add(real)
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for e in entries:
            full = os.path.join(directory, e.name)
            if e.is_dir():
                stack.append(full)
            elif e.is_file():
                try:
                    total_bytes += e.stat().st_size
                    total_files += 1
                except OSError:
                    pass
    return total_bytes, total_files


# ─── Language detection & outline extraction ─────────────────────────────────

_EXT_LANGUAGE_MAP: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".css": "css",
    ".scss": "css",
    ".less": "css",
    ".html": "html",
    ".xml": "xml",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".sql": "sql",
    ".dart": "dart",
    ".lua": "lua",
    ".r": "r",
}


def detect_language(path: str) -> str:
    """Detect programming language from file extension."""
    _, ext = os.path.splitext(path)
    return _EXT_LANGUAGE_MAP.get(ext.lower(), "unknown")


# Per-language outline regex patterns.
# Each pattern matches a single line representing a structural element.
# Patterns use [ \t] instead of \s to avoid matching newlines (which would
# cause cross-line matches and incorrect line number computation).
# Patterns are applied with re.MULTILINE so ^ matches start-of-line.
_OUTLINE_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "typescript": [
        ("import", r"^import[ \t]+.*$"),
        ("import", r"^const[ \t]+\w+[ \t]*=[ \t]*require\(.*$"),
        ("class", r"^[ \t]*(export[ \t]+)?(default[ \t]+)?(abstract[ \t]+)?class[ \t]+\w+.*$"),
        ("interface", r"^[ \t]*(export[ \t]+)?interface[ \t]+\w+.*$"),
        ("type", r"^[ \t]*(export[ \t]+)?type[ \t]+\w+.*$"),
        ("enum", r"^[ \t]*(export[ \t]+)?(const[ \t]+)?enum[ \t]+\w+.*$"),
        ("function", r"^[ \t]*(export[ \t]+)?(default[ \t]+)?(async[ \t]+)?function[ \t]*\*?[ \t]*\w+[ \t]*\(.*$"),
        ("variable", r"^[ \t]*(export[ \t]+)?(const|let|var)[ \t]+\w+[ \t]*[:=].*$"),
    ],
    "javascript": [
        ("import", r"^import[ \t]+.*$"),
        ("import", r"^const[ \t]+\w+[ \t]*=[ \t]*require\(.*$"),
        ("class", r"^[ \t]*(export[ \t]+)?(default[ \t]+)?(abstract[ \t]+)?class[ \t]+\w+.*$"),
        ("function", r"^[ \t]*(export[ \t]+)?(default[ \t]+)?(async[ \t]+)?function[ \t]*\*?[ \t]*\w+[ \t]*\(.*$"),
        ("variable", r"^[ \t]*(export[ \t]+)?(const|let|var)[ \t]+\w+[ \t]*[:=].*$"),
    ],
    "python": [
        ("import", r"^(import[ \t]+\S+|from[ \t]+\S+[ \t]+import[ \t]+.*)$"),
        ("class", r"^class[ \t]+\w+.*$"),
        ("function", r"^[ \t]*(async[ \t]+)?def[ \t]+\w+[ \t]*\(.*$"),
    ],
    "go": [
        ("import", r"^[ \t]*import[ \t]+.*$"),
        ("func", r"^[ \t]*func[ \t]+.*$"),
        ("type", r"^[ \t]*type[ \t]+\w+.*$"),
    ],
    "java": [
        ("import", r"^[ \t]*import[ \t]+.*;$"),
        ("class", r"^[ \t]*(public|private|protected)?[ \t]*(abstract[ \t]+|final[ \t]+)*class[ \t]+\w+.*$"),
        ("interface", r"^[ \t]*(public|private|protected)?[ \t]*interface[ \t]+\w+.*$"),
        ("enum", r"^[ \t]*(public|private|protected)?[ \t]*enum[ \t]+\w+.*$"),
    ],
    "kotlin": [
        ("import", r"^[ \t]*import[ \t]+.*$"),
        ("class", r"^[ \t]*(\w+[ \t]+)*class[ \t]+\w+.*$"),
        ("interface", r"^[ \t]*(\w+[ \t]+)*interface[ \t]+\w+.*$"),
        ("enum", r"^[ \t]*(\w+[ \t]+)*enum[ \t]+class[ \t]+\w+.*$"),
        ("function", r"^[ \t]*(\w+[ \t]+)*fun[ \t]+\w+[ \t]*\(.*$"),
        ("object", r"^[ \t]*(\w+[ \t]+)*object[ \t]+\w+.*$"),
    ],
    "rust": [
        ("use", r"^[ \t]*use[ \t]+.*;$"),
        ("fn", r"^[ \t]*(pub[ \t]+)?(async[ \t]+)?(unsafe[ \t]+)?fn[ \t]+\w+.*$"),
        ("struct", r"^[ \t]*(pub[ \t]+)?struct[ \t]+\w+.*$"),
        ("enum", r"^[ \t]*(pub[ \t]+)?enum[ \t]+\w+.*$"),
        ("trait", r"^[ \t]*(pub[ \t]+)?trait[ \t]+\w+.*$"),
        ("impl", r"^[ \t]*impl[ \t]+.*$"),
        ("type", r"^[ \t]*(pub[ \t]+)?type[ \t]+\w+.*$"),
    ],
    "c": [
        ("include", r"^[ \t]*#include[ \t]+.*$"),
        ("function", r"^[ \t]*(static[ \t]+)?\w[\w \t\*]*[ \t]+\w+[ \t]*\(.*\)[ \t]*\{?$"),
        ("struct", r"^[ \t]*(typedef[ \t]+)?struct[ \t]+\w+.*$"),
        ("typedef", r"^[ \t]*typedef[ \t]+.*$"),
    ],
    "cpp": [
        ("include", r"^[ \t]*#include[ \t]+.*$"),
        ("class", r"^[ \t]*(class|struct)[ \t]+\w+.*$"),
        ("function", r"^[ \t]*(\w[\w \t\*:]*)[ \t]+\w+[ \t]*\(.*\)[ \t]*\{?$"),
        ("typedef", r"^[ \t]*(typedef|using)[ \t]+\w+.*$"),
    ],
    "ruby": [
        ("require", r"^[ \t]*require(_relative)?[ \t]+.*$"),
        ("class", r"^[ \t]*(class|module)[ \t]+\w+.*$"),
        ("function", r"^[ \t]*def[ \t]+\w+.*$"),
    ],
    "php": [
        ("use", r"^[ \t]*use[ \t]+.*;$"),
        ("class", r"^[ \t]*(abstract[ \t]+|final[ \t]+)*class[ \t]+\w+.*$"),
        ("interface", r"^[ \t]*interface[ \t]+\w+.*$"),
        ("function", r"^[ \t]*(public|private|protected|static|[ \t])*function[ \t]+\w+[ \t]*\(.*$"),
    ],
    "swift": [
        ("import", r"^[ \t]*import[ \t]+\w+.*$"),
        ("class", r"^[ \t]*(public|private|internal|final|[ \t])*class[ \t]+\w+.*$"),
        ("struct", r"^[ \t]*(public|private|internal|[ \t])*struct[ \t]+\w+.*$"),
        ("enum", r"^[ \t]*(public|private|internal|[ \t])*enum[ \t]+\w+.*$"),
        ("protocol", r"^[ \t]*(public|private|internal|[ \t])*protocol[ \t]+\w+.*$"),
        ("function", r"^[ \t]*(public|private|internal|static|[ \t])*func[ \t]+\w+[ \t]*\(.*$"),
    ],
    "scala": [
        ("import", r"^[ \t]*import[ \t]+.*$"),
        ("class", r"^[ \t]*(class|object|trait)[ \t]+\w+.*$"),
        ("function", r"^[ \t]*def[ \t]+\w+.*$"),
    ],
    "dart": [
        ("import", r"^[ \t]*import[ \t]+.*;$"),
        ("class", r"^[ \t]*(abstract[ \t]+)?class[ \t]+\w+.*$"),
        ("function", r"^[ \t]*(static[ \t]+)?\w+[ \t]+\w+[ \t]*\(.*$"),
    ],
    "lua": [
        ("require", r"^[ \t]*(local[ \t]+)?\w+[ \t]*=[ \t]*require\(.*$"),
        ("function", r"^[ \t]*(local[ \t]+)?function[ \t]+\w+.*$"),
    ],
}

# Fallback patterns for unknown languages — generic structural keywords.
_FALLBACK_PATTERNS: list[tuple[str, str]] = [
    ("import", r"^[ \t]*import[ \t]+.*$"),
    ("include", r"^[ \t]*#include[ \t]+.*$"),
    ("class", r"^[ \t]*(export[ \t]+)?(abstract[ \t]+)?class[ \t]+\w+.*$"),
    ("interface", r"^[ \t]*interface[ \t]+\w+.*$"),
    ("function", r"^[ \t]*(export[ \t]+)?(async[ \t]+)?function[ \t]+\w+[ \t]*\(.*$"),
    ("function", r"^[ \t]*def[ \t]+\w+[ \t]*\(.*$"),
    ("function", r"^[ \t]*func[ \t]+\w+.*$"),
    ("function", r"^[ \t]*fn[ \t]+\w+.*$"),
    ("type", r"^[ \t]*type[ \t]+\w+.*$"),
    ("variable", r"^[ \t]*(const|let|var|val)[ \t]+\w+[ \t]*[:=].*$"),
]


def extract_outline(content: str, language: str) -> list[dict]:
    """Extract structural skeleton from source code using regex patterns.

    Returns a list of ``{"type": str, "line": int, "content": str}`` dicts.
    Uses per-language regex patterns; falls back to generic patterns for
    unknown languages. Does not invoke an LLM.
    """
    patterns = _OUTLINE_PATTERNS.get(language, _FALLBACK_PATTERNS)
    compiled = [(ptype, re.compile(pat, re.MULTILINE)) for ptype, pat in patterns]
    results: list[dict] = []
    seen_lines: set[int] = set()

    lines = content.splitlines()
    for ptype, regex in compiled:
        for match in regex.finditer(content):
            line_no = content.count("\n", 0, match.start()) + 1
            if line_no in seen_lines:
                continue
            if line_no > len(lines):
                continue
            line_content = lines[line_no - 1].rstrip()
            if not line_content:
                continue
            seen_lines.add(line_no)
            results.append({
                "type": ptype,
                "line": line_no,
                "content": line_content,
            })

    results.sort(key=lambda x: x["line"])
    return results
