"""Same-wave code-conflict tracking for the Orchestrator.

Port of src/server/dispatch-file-writes.ts. Records which workspace files each
child run wrote via fs_write (absolute path -> content hash).

Blind spot (see specs/06): the bash tool and SDK-native write tools don't go
through fs_write and so aren't recorded here.
"""

from __future__ import annotations

import hashlib

_writes_by_run: dict[str, dict[str, str]] = {}


def record_file_write(run_id: str, absolute_path: str, content: str) -> None:
    files = _writes_by_run.get(run_id)
    if files is None:
        files = {}
        _writes_by_run[run_id] = files
    files[absolute_path] = hashlib.sha1(content.encode("utf-8")).hexdigest()
