"""Environment isolation for tool and CLI subprocesses.

The AChat backend runs inside its own virtualenv (typically ``.venv`` at the
repo root). When an agent's bash tool or a CLI adapter spawns a subprocess,
that subprocess MUST NOT inherit:

- AChat virtualenv markers (``VIRTUAL_ENV``, ``PYTHONHOME``) — otherwise
  ``pip install`` inside the user's project resolves to AChat's pip and
  pollutes AChat's ``site-packages``, which in turn crashes uvicorn with
  ``ModuleNotFoundError`` after a hot-reload.
- AChat internal secrets (``DATABASE_URL``, ``SECRET_KEY``, ``REDIS_URL``,
  ``MILVUS_HOST`` …) — these leak the platform's infra credentials to agent
  code.
- Global package-manager pollution (``NPM_CONFIG_PREFIX``, ``M2_HOME``,
  ``GOPATH``, ``GOMODCACHE``) — these cause agent-side ``npm`` / ``mvn`` /
  ``go`` commands to write into AChat-managed directories.

The cleaning strategy is *blacklist + PATH scrub*, NOT a pure whitelist:
a whitelist would drop user system variables (``JAVA_HOME``,
``ANDROID_SDK_ROOT``, custom ``PATH`` segments) that the agent needs to do
real work in the user's project. The blacklist is a security contract —
see ``specs/platform-security/spec.md`` and ``specs/11-platform.md``.

Public API:

- :func:`build_tool_env` — construct a cleaned env dict for a subprocess.
- :func:`is_internal_env_key` — check whether a key is blacklisted.
- :func:`detect_project_venv` — detect a project-level ``.venv``.
- :func:`detect_achat_venv` — locate the AChat virtualenv from
  ``sys.executable`` (exposed for tests / diagnostics).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.utils.platform import IS_WINDOWS

# ─── Blacklist (security contract) ──────────────────────────────────────────
# Keep in sync with specs/platform-security/spec.md and specs/11-platform.md.
# Each entry MUST be removed from every tool / CLI subprocess env.

_BLACKLISTED_KEYS: frozenset[str] = frozenset(
    {
        # ── AChat virtualenv markers ──
        # VIRTUAL_ENV is set by activate scripts; PYTHONHOME overrides the
        # interpreter's sys.prefix and breaks project-side Python resolution.
        "VIRTUAL_ENV",
        "PYTHONHOME",
        # ── AChat internal secrets ──
        "DATABASE_URL",
        "SECRET_KEY",
        "JWT_SECRET",
        "REDIS_URL",
        "MILVUS_HOST",
        "MILVUS_PORT",
        "ES_HOST",
        "ES_PORT",
        "NEO4J_URI",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        # ── Global package-manager pollution ──
        # NPM_CONFIG_PREFIX makes `npm install -g` write into AChat's prefix;
        # M2_HOME / GOPATH / GOMODCACHE point mvn / go at AChat-managed dirs.
        "NPM_CONFIG_PREFIX",
        "M2_HOME",
        "GOPATH",
        "GOMODCACHE",
    }
)

# On Windows, env var names are case-insensitive. We keep an uppercased copy
# so ``is_internal_env_key`` can do a casefolded membership check in O(1)
# without allocating a new frozenset per call.
_BLACKLISTED_KEYS_CI: frozenset[str] = frozenset(k.upper() for k in _BLACKLISTED_KEYS)


def is_internal_env_key(key: str) -> bool:
    """Return True if ``key`` MUST be stripped from a subprocess env.

    On Windows the comparison is case-insensitive (Windows env vars are
    case-insensitive); on POSIX it is exact-match.
    """
    if not key:
        return False
    if IS_WINDOWS:
        return key.upper() in _BLACKLISTED_KEYS_CI
    return key in _BLACKLISTED_KEYS


# ─── AChat venv detection ───────────────────────────────────────────────────


def detect_achat_venv() -> str | None:
    """Derive the AChat virtualenv root from ``sys.executable``.

    - Windows: ``<venv>/Scripts/python.exe`` → ``<venv>``
    - POSIX:   ``<venv>/bin/python``         → ``<venv>``

    Returns ``None`` when AChat is running under a system Python (no venv),
    in which case PATH cleaning has nothing to strip (the blacklist still
    applies). This is a graceful no-op, not an error.

    Why not read ``VIRTUAL_ENV``? That variable is only present when AChat
    was started through an ``activate`` script. ``uvicorn`` is commonly
    launched directly via ``.venv/bin/python -m uvicorn ...``, which leaves
    ``VIRTUAL_ENV`` unset. ``sys.executable`` always reflects the real
    interpreter path.
    """
    exe = sys.executable
    if not exe:
        return None
    exe_path = Path(exe)
    bin_dir = exe_path.parent  # Scripts (Windows) / bin (POSIX)
    # A venv bin dir is named ``Scripts`` on Windows and ``bin`` on POSIX.
    # The parent of the bin dir is the venv root.
    bin_dir_name = bin_dir.name.lower()
    if IS_WINDOWS:
        if bin_dir_name != "scripts":
            return None
    else:
        if bin_dir_name != "bin":
            return None
    # Sanity-check: the venv root should contain a pyvenv.cfg marker file.
    # This filters out ``/usr/bin`` (where parent of ``/usr/bin/python3`` is
    # ``/usr/bin`` — no, parent is ``/usr``) and similar system layouts.
    venv_root = bin_dir.parent
    if not (venv_root / "pyvenv.cfg").is_file():
        return None
    return str(venv_root)


def _venv_bin_subdir(venv_root: str) -> str:
    """Return the ``Scripts`` (Windows) / ``bin`` (POSIX) subdir path."""
    return os.path.join(venv_root, "Scripts" if IS_WINDOWS else "bin")


# ─── PATH cleaning ──────────────────────────────────────────────────────────


def _normcase(path: str) -> str:
    """Casefold a path for comparison only (Windows PATH is case-insensitive).

    On POSIX this is the identity; on Windows it lowercases. We never
    casefold the actual PATH entries we emit — only the comparison keys.
    """
    return os.path.normcase(path)


def _path_segments(path_str: str) -> list[str]:
    """Split a PATH string into raw segments (preserving original casing)."""
    if not path_str:
        return []
    return path_str.split(os.pathsep)


def clean_path(path_str: str, venv_to_remove: str | None) -> str:
    """Remove the AChat venv ``Scripts``/``bin`` segment from ``path_str``.

    The comparison is case-insensitive on Windows; the emitted PATH keeps
    the original casing of the surviving segments. If ``venv_to_remove`` is
    ``None`` or not present in ``path_str``, PATH is returned unchanged
    (still applies the blacklist elsewhere; this function only handles PATH).
    """
    if not venv_to_remove:
        return path_str
    segments = _path_segments(path_str)
    if not segments:
        return path_str

    # Build the set of paths to drop: the venv bin dir itself. We compare
    # normcase'd forms so Windows casing differences don't matter.
    drop_target = _normcase(os.path.normpath(_venv_bin_subdir(venv_to_remove)))

    kept: list[str] = []
    for seg in segments:
        seg_stripped = seg.strip()
        if not seg_stripped:
            # Preserve empty segments? PATH semantics vary; we drop them as
            # they would resolve to cwd and add no value.
            continue
        if _normcase(os.path.normpath(seg_stripped)) == drop_target:
            continue
        kept.append(seg)
    return os.pathsep.join(kept)


# ─── Project venv detection ─────────────────────────────────────────────────


def detect_project_venv(cwd: str | os.PathLike[str] | None) -> str | None:
    """Detect a project-level ``.venv`` rooted at ``cwd``.

    Returns the venv root path if ``<cwd>/.venv`` exists AND contains a
    Python executable (Windows: ``Scripts/python.exe``; POSIX:
    ``bin/python``). Returns ``None`` otherwise.

    Detection runs on every bash invocation (no caching) so a venv created
    mid-conversation (e.g. via the env hint card) is picked up immediately.
    The cost is a single ``os.path.isfile`` — negligible.
    """
    if not cwd:
        return None
    venv_root = os.path.join(str(cwd), ".venv")
    if IS_WINDOWS:
        python_exe = os.path.join(venv_root, "Scripts", "python.exe")
    else:
        python_exe = os.path.join(venv_root, "bin", "python")
    if os.path.isfile(python_exe):
        return venv_root
    return None


# ─── Top-level env builder ──────────────────────────────────────────────────


def build_tool_env(
    cwd: str | os.PathLike[str] | None = None,
    project_venv_path: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Construct a cleaned env dict for a bash / CLI subprocess.

    Pipeline:

    1. Copy ``os.environ`` (so user system variables survive).
    2. Drop every blacklisted key (see :data:`_BLACKLISTED_KEYS`).
    3. Scrub ``PATH``: remove the AChat venv ``Scripts``/``bin`` segment.
    4. If ``project_venv_path`` is given and exists, prepend its
       ``Scripts``/``bin`` to ``PATH`` so ``python`` / ``pip`` resolve to
       the project venv.

    The result is a fresh dict; ``os.environ`` is not mutated.
    """
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if is_internal_env_key(key):
            continue
        env[key] = value

    achat_venv = detect_achat_venv()
    if achat_venv:
        existing_path = env.get("PATH", "")
        env["PATH"] = clean_path(existing_path, achat_venv)

    if project_venv_path:
        venv_bin = _venv_bin_subdir(str(project_venv_path))
        if os.path.isdir(venv_bin):
            existing_path = env.get("PATH", "")
            env["PATH"] = (
                venv_bin + (os.pathsep + existing_path if existing_path else "")
            )

    return env


__all__ = [
    "_BLACKLISTED_KEYS",
    "build_tool_env",
    "clean_path",
    "detect_achat_venv",
    "detect_project_venv",
    "is_internal_env_key",
]
