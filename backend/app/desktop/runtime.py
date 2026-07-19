"""Desktop runtime configuration parsed from CLI flags / env."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

_RUNTIME: DesktopRuntime | None = None


@dataclass
class DesktopRuntime:
    bind: str = "127.0.0.1"
    port: int = 0
    data_dir: Path = field(default_factory=lambda: Path.home() / "AppData" / "Roaming" / "AChat")
    engine_token: str = ""
    official_api_url: str = ""
    allowed_origins: list[str] = field(default_factory=list)
    actual_port: int | None = None

    def ensure_layout(self) -> None:
        for rel in ("logs", "sqlite", "runtime", "workspaces"):
            (self.data_dir / rel).mkdir(parents=True, exist_ok=True)

    def write_handshake(self, port: int, pid: int) -> Path:
        self.actual_port = port
        path = self.data_dir / "runtime" / "engine.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "port": port,
            "pid": pid,
            "bind": self.bind,
            "tokenPresent": bool(self.engine_token),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def sqlite_path(self) -> Path:
        return self.data_dir / "sqlite" / "offline.db"

    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def is_desktop_mode() -> bool:
    return os.environ.get("ACHAT_RUNTIME", "").lower() == "desktop" or _RUNTIME is not None


def get_desktop_runtime() -> DesktopRuntime | None:
    return _RUNTIME


def set_desktop_runtime(runtime: DesktopRuntime) -> DesktopRuntime:
    global _RUNTIME
    runtime.ensure_layout()
    _RUNTIME = runtime
    os.environ["ACHAT_RUNTIME"] = "desktop"
    os.environ["ACHAT_DATA_DIR"] = str(runtime.data_dir)
    if runtime.engine_token:
        os.environ["ACHAT_ENGINE_TOKEN"] = runtime.engine_token
    if runtime.official_api_url:
        os.environ["ACHAT_OFFICIAL_API_URL"] = runtime.official_api_url
    if runtime.allowed_origins:
        os.environ["ACHAT_ALLOWED_ORIGINS"] = ",".join(runtime.allowed_origins)
        os.environ["CORS_ORIGINS"] = ",".join(runtime.allowed_origins)
    # Keep workspace under desktop data dir by default.
    os.environ.setdefault("WORKSPACE_ROOT", str(runtime.data_dir / "workspaces"))
    os.environ.setdefault("DATA_DIR", str(runtime.data_dir))
    return runtime


def allocate_port(bind: str, preferred: int) -> int:
    """Bind briefly to discover an free port when preferred == 0."""
    if preferred and preferred > 0:
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((bind, 0))
        return int(sock.getsockname()[1])


def assert_loopback_bind(bind: str) -> None:
    allowed = {"127.0.0.1", "localhost", "::1"}
    if bind not in allowed:
        raise SystemExit(
            f"desktop mode refuses non-loopback bind {bind!r}; "
            "only 127.0.0.1/localhost/::1 are allowed by default"
        )
