"""Desktop runtime configuration parsed from CLI flags / env."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.desktop.config import DesktopConfig

_RUNTIME: DesktopRuntime | None = None


@dataclass
class DesktopRuntime:
    bind: str = "127.0.0.1"
    port: int = 0
    data_dir: Path = field(default_factory=lambda: Path.home() / "AppData" / "Roaming" / "AChat")
    engine_token: str = ""
    official_api_url: str = ""  # legacy v0 optional
    allowed_origins: list[str] = field(default_factory=list)
    actual_port: int | None = None
    infra_config_path: Path | None = None
    ui_dir: Path | None = None
    desktop_config: DesktopConfig | None = None
    feature_direct_infra: bool = True
    feature_cloud_api_client: bool = False

    def ensure_layout(self) -> None:
        for rel in ("logs", "sqlite", "runtime", "workspaces", "config", "ui"):
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
            "uiOrigin": f"http://{self.bind if self.bind != '0.0.0.0' else '127.0.0.1'}:{port}",
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def sqlite_path(self) -> Path:
        return self.data_dir / "sqlite" / "offline.db"

    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


def is_desktop_mode() -> bool:
    return os.environ.get("ACHAT_RUNTIME", "").lower() == "desktop" or _RUNTIME is not None


def cloud_api_client_enabled() -> bool:
    """v0 CloudApiClient path — off by default in v1 (direct infra)."""
    if os.environ.get("ACHAT_FEATURE_CLOUD_API_CLIENT", "").strip() in ("1", "true", "yes"):
        return True
    rt = _RUNTIME
    return bool(rt and rt.feature_cloud_api_client)


def direct_infra_enabled() -> bool:
    if os.environ.get("ACHAT_FEATURE_DIRECT_INFRA", "1").strip() in ("0", "false", "no"):
        return False
    rt = _RUNTIME
    if rt is not None:
        return rt.feature_direct_infra
    return True


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
    if runtime.ui_dir:
        os.environ["ACHAT_UI_DIR"] = str(runtime.ui_dir)
    os.environ["ACHAT_FEATURE_DIRECT_INFRA"] = "1" if runtime.feature_direct_infra else "0"
    os.environ["ACHAT_FEATURE_CLOUD_API_CLIENT"] = "1" if runtime.feature_cloud_api_client else "0"
    # Keep workspace under desktop data dir by default.
    os.environ.setdefault("WORKSPACE_ROOT", str(runtime.data_dir / "workspaces"))
    os.environ.setdefault("DATA_DIR", str(runtime.data_dir))
    return runtime


def allocate_port(bind: str, preferred: int) -> int:
    """Bind briefly to discover a free port when preferred == 0."""
    if preferred and preferred > 0:
        return preferred
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((bind, 0))
            return int(sock.getsockname()[1])
    except OSError as e:
        # WinError 10106 (WSAEPROVIDERFAILEDINIT) = broken Winsock inside a
        # frozen Anaconda build that shipped private ucrtbase/api-ms-win DLLs.
        raise OSError(
            e.errno,
            f"failed to allocate loopback port on {bind!r}: {e}. "
            "On Windows packaged builds this is often WinError 10106 from "
            "bundled Anaconda UCRT forwarders (api-ms-win-*.dll / ucrtbase.dll) "
            "— rebuild with build_engine_windows.ps1 (strips those DLLs).",
        ) from e


def assert_loopback_bind(bind: str) -> None:
    allowed = {"127.0.0.1", "localhost", "::1"}
    if bind not in allowed:
        raise SystemExit(
            f"desktop mode refuses non-loopback bind {bind!r}; "
            "only 127.0.0.1/localhost/::1 are allowed by default"
        )
