"""CLI entrypoint for the desktop local engine.

Usage:
  python -m app.desktop.cli serve \\
    --bind 127.0.0.1 --port 0 --data-dir %APPDATA%/AChat \\
    --engine-token <token> --official-api-url https://api.example \\
    --allowed-origins https://app.example
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("achat.desktop.cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="achat-engine", description="AChat desktop local engine")
    sub = p.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start desktop local engine HTTP server")
    serve.add_argument("--bind", default="127.0.0.1", help="Bind address (loopback only)")
    serve.add_argument("--port", type=int, default=0, help="Port (0 = ephemeral)")
    serve.add_argument("--data-dir", required=True, help="Per-user AChat data directory")
    serve.add_argument("--engine-token", required=True, help="Session engine token from shell")
    serve.add_argument(
        "--official-api-url",
        required=True,
        help="Official cloud API base URL (HTTPS in production)",
    )
    serve.add_argument(
        "--allowed-origins",
        required=True,
        help="Comma-separated official frontend origins",
    )
    return p


def cmd_serve(args: argparse.Namespace) -> int:
    from app.desktop.runtime import (
        DesktopRuntime,
        allocate_port,
        assert_loopback_bind,
        set_desktop_runtime,
    )

    assert_loopback_bind(args.bind)

    data_dir = Path(args.data_dir).expanduser().resolve()
    origins = [o.strip() for o in str(args.allowed_origins).split(",") if o.strip()]
    if not origins:
        print("error: --allowed-origins must list at least one origin", file=sys.stderr)
        return 2

    runtime = DesktopRuntime(
        bind=args.bind,
        port=int(args.port),
        data_dir=data_dir,
        engine_token=str(args.engine_token),
        official_api_url=str(args.official_api_url).rstrip("/"),
        allowed_origins=origins,
    )
    set_desktop_runtime(runtime)

    # Desktop mode must not require cloud PG at process start for health;
    # services that need DB will use cloud client / sqlite offline path.
    # Prefer a local placeholder DB URL so create_app() can import without PG
    # if user has not configured DATABASE_URL (desktop online uses cloud API).
    os.environ.setdefault(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{(data_dir / 'sqlite' / 'bootstrap.db').as_posix()}",
    )
    os.environ.setdefault("DEBUG", "true")  # allow generated JWT secret for local engine
    if not os.environ.get("JWT_SECRET"):
        # Local engine validates cloud JWTs via cloud API; local secret only for any
        # residual local-signed tokens. Generate a long random value.
        import secrets

        os.environ["JWT_SECRET"] = secrets.token_urlsafe(48)

    port = allocate_port(runtime.bind, runtime.port)
    runtime.write_handshake(port, os.getpid())
    # Parent shell may scrape this line for port discovery.
    print(f"ENGINE_PORT={port}", flush=True)
    logger.info(
        "desktop engine starting bind=%s port=%s data_dir=%s",
        runtime.bind,
        port,
        runtime.data_dir,
    )

    import uvicorn

    # Import app after runtime env is set so Settings/middleware see env.
    from app.main import app

    uvicorn.run(
        app,
        host=runtime.bind,
        port=port,
        log_level="info",
        # No reload in packaged desktop mode.
        reload=False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "serve":
        return cmd_serve(args)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
