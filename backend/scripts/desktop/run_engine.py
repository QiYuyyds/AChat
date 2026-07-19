#!/usr/bin/env python3
"""Dev launcher / PyInstaller entry: run desktop engine from the backend package root."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Only chdir for non-frozen (repo) launches. Frozen one-folder keeps cwd next to
# achat-engine.exe so native DLLs resolve correctly.
if not getattr(sys, "frozen", False):
    os.chdir(BACKEND_ROOT)


def _crash_log_path() -> Path:
    """Best-effort path for fatal logs when windowed PyInstaller hides stderr."""
    override = os.environ.get("ACHAT_DATA_DIR", "").strip()
    if override:
        base = Path(override)
    else:
        appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        base = Path(appdata) / "AChat" if appdata else Path.cwd()
    logs = base / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except Exception:
        return Path.cwd() / "achat-engine-crash.log"
    return logs / "engine-crash.log"


def _report_fatal(exc: BaseException) -> None:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    msg = f"achat-engine fatal: {exc}\n{tb}"
    try:
        sys.stderr.write(msg)
        sys.stderr.flush()
    except Exception:
        pass
    try:
        path = _crash_log_path()
        path.write_text(msg, encoding="utf-8")
        try:
            sys.stderr.write(f"crash log: {path}\n")
            sys.stderr.flush()
        except Exception:
            pass
    except Exception:
        pass


if __name__ == "__main__":
    # Catch-all so --windowed builds don't show an empty "Unhandled exception"
    # dialog; shell already drains stderr and health-check surfaces the failure.
    try:
        from app.desktop.cli import main  # noqa: E402

        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — last-resort freeze entry
        _report_fatal(exc)
        raise SystemExit(1) from exc
