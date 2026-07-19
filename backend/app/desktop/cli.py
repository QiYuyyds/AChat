"""CLI entrypoint for the desktop local engine.

Usage:
  python -m app.desktop.cli serve \\
    --bind 127.0.0.1 --port 0 --data-dir %APPDATA%/AChat \\
    --engine-token <token> \\
    [--infra-config path/to/infra.default.json] \\
    [--ui-dir path/to/static] \\
    [--official-api-url https://... ]  # legacy optional \\
    [--allowed-origins http://127.0.0.1:...]
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
from pathlib import Path

logger = logging.getLogger("achat.desktop.cli")


def _load_dotenv_files() -> None:
    """Best-effort load of backend/.env into os.environ without overwriting."""
    candidates = [
        Path.cwd() / ".env",
        Path.cwd() / ".env.local",
        Path(__file__).resolve().parents[2] / ".env",  # backend/.env
        Path(__file__).resolve().parents[2] / ".env.local",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if key and key not in os.environ:
                    os.environ[key] = value
            logger.info("desktop loaded env file %s (non-overwriting)", path)
        except Exception as e:
            logger.warning("desktop failed to load %s: %s", path, e)


def _ensure_stable_jwt_secret(data_dir: Path) -> None:
    """Prefer env / infra config; else reuse persisted secret; else create once."""
    if os.environ.get("JWT_SECRET", "").strip():
        return
    secret_path = data_dir / "config" / "jwt.secret"
    if secret_path.is_file():
        existing = secret_path.read_text(encoding="utf-8").strip()
        if len(existing) >= 32:
            os.environ["JWT_SECRET"] = existing
            logger.info("desktop JWT_SECRET loaded from %s", secret_path)
            return
    # Last resort for pure offline installs with no .env / config
    generated = secrets.token_urlsafe(48)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(generated, encoding="utf-8")
    try:
        os.chmod(secret_path, 0o600)
    except Exception:
        pass
    os.environ["JWT_SECRET"] = generated
    logger.warning(
        "desktop generated stable JWT_SECRET at %s "
        "(prefer setting JWT_SECRET in backend/.env or infra.default.json)",
        secret_path,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="achat-engine", description="AChat desktop local engine")
    sub = p.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start desktop local engine HTTP server")
    serve.add_argument("--bind", default="127.0.0.1", help="Bind address (loopback only)")
    serve.add_argument("--port", type=int, default=0, help="Port (0 = ephemeral)")
    serve.add_argument("--data-dir", required=True, help="Per-user AChat data directory")
    serve.add_argument("--engine-token", required=True, help="Session engine token from shell")
    serve.add_argument(
        "--infra-config",
        default="",
        help="Path to packaged infra default JSON (merged under user override)",
    )
    serve.add_argument(
        "--ui-dir",
        default="",
        help="Directory of packaged static frontend assets",
    )
    serve.add_argument(
        "--official-api-url",
        default="",
        help="(legacy v0) Official cloud API base URL — optional; not required for v1 direct-infra",
    )
    serve.add_argument(
        "--allowed-origins",
        default="",
        help="Comma-separated extra allowed origins (local UI origin is auto-added)",
    )
    return p


def cmd_serve(args: argparse.Namespace) -> int:
    from app.desktop.config import (
        apply_config_to_environ,
        enforce_desktop_optional_infra,
        ensure_local_ui_origin,
        load_desktop_config,
        redact_config,
    )
    from app.desktop.runtime import (
        DesktopRuntime,
        allocate_port,
        assert_loopback_bind,
        set_desktop_runtime,
    )

    assert_loopback_bind(args.bind)

    data_dir = Path(args.data_dir).expanduser().resolve()
    packaged = Path(args.infra_config).expanduser() if args.infra_config else None
    if packaged and not packaged.is_file():
        # Also try next to common package locations
        print(f"warning: --infra-config not found: {packaged}", file=sys.stderr)
        packaged = None

    cfg = load_desktop_config(data_dir=data_dir, packaged_path=packaged)

    cli_origins = [o.strip() for o in str(args.allowed_origins).split(",") if o.strip()]
    if cli_origins:
        cfg.allowed_origins = list(dict.fromkeys([*cfg.allowed_origins, *cli_origins]))

    ui_dir: Path | None = None
    if args.ui_dir:
        ui_dir = Path(args.ui_dir).expanduser().resolve()
        cfg.ui_dir = str(ui_dir)
    elif cfg.ui_dir:
        ui_dir = Path(cfg.ui_dir).expanduser()

    # Apply infra → env before Settings / app import
    apply_config_to_environ(cfg)
    logger.info("desktop config applied (redacted)=%s", redact_config(cfg))

    # Load backend/.env into os.environ (never overwrite existing keys).
    # Critical: do NOT invent a random JWT_SECRET before this, or it would
    # shadow the real secret from .env and invalidate every login on restart.
    # Prefer JWT_SECRET already set by infra.default.json / user override / env;
    # only fall back to persisted data_dir/config/jwt.secret or generate once.
    _load_dotenv_files()
    # Desktop product auth must win over any leftover env from a developer shell.
    from app.desktop.config import apply_desktop_auth_environ

    apply_desktop_auth_environ(cfg)
    # Desktop infra JSON wins for optional services: empty = disabled.
    # Otherwise backend/.env (ES_ADDRESSES=http://localhost:9200 etc.) leaks in
    # and the engine spams connection errors when ES/Milvus/Neo4j are not up.
    enforce_desktop_optional_infra(cfg)
    # If .env / infra provided JWT_SECRET, keep data_dir jwt.secret in sync so a
    # later offline start without .env still validates the same tokens.
    env_jwt = os.environ.get("JWT_SECRET", "").strip()
    if env_jwt:
        secret_path = data_dir / "config" / "jwt.secret"
        try:
            secret_path.parent.mkdir(parents=True, exist_ok=True)
            existing = secret_path.read_text(encoding="utf-8").strip() if secret_path.is_file() else ""
            if existing != env_jwt:
                secret_path.write_text(env_jwt, encoding="utf-8")
                logger.info("desktop synced JWT_SECRET to %s", secret_path)
        except Exception as e:
            logger.warning("desktop failed to sync jwt.secret: %s", e)
    _ensure_stable_jwt_secret(data_dir)
    os.environ.setdefault("DEBUG", "true")

    # If no database_url after merge, fall back to local sqlite bootstrap (dev only)
    if not os.environ.get("DATABASE_URL"):
        sqlite_boot = data_dir / "sqlite" / "bootstrap.db"
        sqlite_boot.parent.mkdir(parents=True, exist_ok=True)
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{sqlite_boot.as_posix()}"
        logger.warning(
            "desktop: no databaseUrl in config; using bootstrap SQLite at %s "
            "(primary PG recommended for multi-user)",
            sqlite_boot,
        )

    port = allocate_port(args.bind, int(args.port))
    origins = ensure_local_ui_origin(cfg.allowed_origins, args.bind, port)
    cfg.allowed_origins = origins

    runtime = DesktopRuntime(
        bind=args.bind,
        port=int(args.port),
        data_dir=data_dir,
        engine_token=str(args.engine_token),
        official_api_url=str(args.official_api_url or cfg.api_url or "").rstrip("/"),
        allowed_origins=origins,
        actual_port=port,
        infra_config_path=packaged,
        ui_dir=ui_dir,
        desktop_config=cfg,
        feature_direct_infra=cfg.feature_flags.direct_infra,
        feature_cloud_api_client=cfg.feature_flags.cloud_api_client,
    )
    set_desktop_runtime(runtime)
    # Re-apply origins after runtime (includes local UI)
    apply_config_to_environ(cfg)

    runtime.write_handshake(port, os.getpid())
    print(f"ENGINE_PORT={port}", flush=True)
    logger.info(
        "desktop engine starting bind=%s port=%s data_dir=%s direct_infra=%s",
        runtime.bind,
        port,
        runtime.data_dir,
        runtime.feature_direct_infra,
    )

    import uvicorn

    from app.main import app

    uvicorn.run(
        app,
        host=runtime.bind,
        port=port,
        log_level="info",
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
