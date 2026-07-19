"""Desktop config merge / redact / load order."""

from __future__ import annotations

import json
from pathlib import Path

from app.desktop.config import (
    DesktopConfig,
    apply_config_to_environ,
    clear_user_override,
    ensure_local_ui_origin,
    load_desktop_config,
    redact_config,
    save_user_override,
)


def test_merge_override_wins(tmp_path: Path):
    packaged = tmp_path / "infra.default.json"
    packaged.write_text(
        json.dumps(
            {
                "flavor": "prod",
                "allowedOrigins": ["http://a"],
                "infra": {
                    "databaseUrl": "postgresql+asyncpg://packaged/db",
                    "milvusHost": "m1",
                },
            }
        ),
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    save_user_override(
        data_dir,
        {
            "infra": {
                "databaseUrl": "postgresql+asyncpg://user:secret@custom:5432/db",
            }
        },
    )
    cfg = load_desktop_config(data_dir=data_dir, packaged_path=packaged)
    assert cfg.user_override_active is True
    assert "custom:5432" in cfg.infra.database_url
    assert cfg.infra.milvus_host == "m1"  # inherited from packaged
    assert cfg.flavor == "prod"


def test_redact_hides_password():
    cfg = DesktopConfig.from_dict(
        {
            "infra": {
                "databaseUrl": "postgresql+asyncpg://u:supersecret@h/db",
                "neo4jPassword": "pwd12345",
            }
        }
    )
    red = redact_config(cfg)
    blob = json.dumps(red)
    assert "supersecret" not in blob
    assert "pwd12345" not in blob
    assert "****" in blob


def test_ensure_local_ui_origin():
    origins = ensure_local_ui_origin(["https://example.com"], "127.0.0.1", 4567)
    assert "http://127.0.0.1:4567" in origins
    assert "http://localhost:4567" in origins


def test_clear_user_override(tmp_path: Path):
    data_dir = tmp_path / "d"
    data_dir.mkdir()
    path = save_user_override(data_dir, {"infra": {"databaseUrl": "x"}})
    assert path.is_file()
    assert clear_user_override(data_dir) is True
    assert not path.is_file()


def test_apply_config_sets_env(monkeypatch, tmp_path: Path):
    cfg = DesktopConfig.from_dict(
        {
            "infra": {
                "databaseUrl": "postgresql+asyncpg://local/db",
                "milvusHost": "127.0.0.1",
                "milvusPort": 19530,
            },
            "allowedOrigins": ["http://127.0.0.1:9"],
            "featureFlags": {"directInfra": True, "cloudApiClient": False},
        }
    )
    apply_config_to_environ(cfg)
    import os

    assert os.environ["DATABASE_URL"].endswith("/db")
    assert os.environ["MILVUS_HOST"] == "127.0.0.1"
    assert os.environ["ACHAT_FEATURE_DIRECT_INFRA"] == "1"
    assert os.environ["ACHAT_FEATURE_CLOUD_API_CLIENT"] == "0"
