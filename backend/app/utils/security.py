"""Shared command-safety policy.

Port of src/server/security.ts. The blacklist comes from specs/11-platform.md
"命令黑名单" and branches by host platform: one set for POSIX (macOS/Linux),
one for Windows. Both the bash tool (``app/tools/bash.py``) and (later) the
Claude adapter run a command through here before executing / allowing it.

Keep this in sync with specs/11-platform.md and CLAUDE.md §5.2 when changing
rules — the blacklist itself is a contract with a single source of truth.
"""

from __future__ import annotations

import re

from app.utils.platform import IS_WINDOWS

# No shared rules today; kept to mirror the TS structure / extension point.
_SHARED_BANNED: list[re.Pattern[str]] = []

_POSIX_BANNED: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-rf\s+/"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bchmod\s+\d{3,4}\s+/"),
    re.compile(r":\(\)\{\s*:\|:&\s*\}"),  # fork bomb
    re.compile(r"curl\s+[^|]*\|\s*(bash|sh)"),
    re.compile(r"wget\s+[^|]*\|\s*(bash|sh)"),
    re.compile(r"\beval\b"),
    re.compile(r"\bexec\b\s+"),
]

_WINDOWS_BANNED: list[re.Pattern[str]] = [
    re.compile(r"\b(del|erase)\s+/[fsq\s/]*[a-z]:\\?", re.IGNORECASE),
    re.compile(r"\brd\s+/[sq\s/]*[a-z]:\\?", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b[^|;]*-Recurse[^|;]*-Force", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b[^|;]*-Force[^|;]*-Recurse", re.IGNORECASE),
    re.compile(r"\bri\b[^|;]*-Recurse[^|;]*-Force", re.IGNORECASE),
    # On PowerShell `rm` / `rmdir` / `erase` are Remove-Item aliases; block each.
    re.compile(r"\brm\b[^|;]*-Recurse[^|;]*-Force", re.IGNORECASE),
    re.compile(r"\brm\b[^|;]*-Force[^|;]*-Recurse", re.IGNORECASE),
    re.compile(r"\brmdir\b[^|;]*-Recurse[^|;]*-Force", re.IGNORECASE),
    re.compile(r"\brmdir\b[^|;]*-Force[^|;]*-Recurse", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\brestart-computer\b", re.IGNORECASE),
    re.compile(r"\bstop-computer\b", re.IGNORECASE),
    re.compile(r"\breg\s+delete\b", re.IGNORECASE),
    re.compile(r"\bRemove-ItemProperty\b", re.IGNORECASE),
    re.compile(r"\btaskkill\b[^|;]*/im\s*\*", re.IGNORECASE),
    re.compile(r"\bStop-Process\b[^|;]*-Force[^|;]*\*", re.IGNORECASE),
    re.compile(
        r"Invoke-Expression\s*\(\s*(Invoke-WebRequest|iwr|curl|wget)", re.IGNORECASE
    ),
    re.compile(r"\biex\b\s*\(\s*(iwr|curl|wget|Invoke-WebRequest)", re.IGNORECASE),
    re.compile(r"Set-ExecutionPolicy\s+(Unrestricted|Bypass)", re.IGNORECASE),
    re.compile(r"\bbcdedit\b", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\bcipher\s+/w", re.IGNORECASE),
]


def _is_windows(platform: str | None) -> bool:
    if platform is None:
        return IS_WINDOWS
    return platform == "windows"


def get_banned_patterns(platform: str | None = None) -> list[re.Pattern[str]]:
    """Return the active blacklist for ``platform`` ('windows' | 'posix')."""
    host = _WINDOWS_BANNED if _is_windows(platform) else _POSIX_BANNED
    return [*_SHARED_BANNED, *host]


def find_banned_pattern(command: str, platform: str | None = None) -> str | None:
    """Return the matched pattern source string, or None when nothing matches.

    Callers ``deny`` on a non-None result and surface the source in the error.
    """
    for pattern in get_banned_patterns(platform):
        if pattern.search(command):
            return pattern.pattern
    return None
