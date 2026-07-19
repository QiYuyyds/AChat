#!/usr/bin/env python3
"""Dev launcher: run desktop engine from the backend package root."""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

os.chdir(BACKEND_ROOT)

from app.desktop.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
