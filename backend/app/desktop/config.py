"""Desktop infra config: packaged defaults + user override (D27).

Load order:
  1. user override: ``{data_dir}/config/infra.user.json``
  2. packaged / CLI ``--infra-config`` default
  3. safe empty defaults (env still usable)

Secrets are never logged in cleartext — use :func:`redact_config`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = frozenset(
    {
        "databaseurl",
        "password",
        "neo4jpassword",
        "jwtsecret",
        "redisurl",
        "apikey",
        "token",
        "secret",
    }
)


@dataclass
class InfraEndpoints:
    database_url: str = ""
    milvus_host: str = ""
    milvus_port: int = 19530
    es_addresses: str = ""
    neo4j_uri: str = ""
    neo4j_user: str = ""
    neo4j_password: str = ""
    redis_url: str = ""
    jwt_secret: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> InfraEndpoints:
        if not data:
            return cls()
        return cls(
            database_url=str(data.get("databaseUrl") or data.get("database_url") or ""),
            milvus_host=str(data.get("milvusHost") or data.get("milvus_host") or ""),
            milvus_port=int(data.get("milvusPort") or data.get("milvus_port") or 19530),
            es_addresses=str(data.get("esAddresses") or data.get("es_addresses") or ""),
            neo4j_uri=str(data.get("neo4jUri") or data.get("neo4j_uri") or ""),
            neo4j_user=str(data.get("neo4jUser") or data.get("neo4j_user") or ""),
            neo4j_password=str(data.get("neo4jPassword") or data.get("neo4j_password") or ""),
            redis_url=str(data.get("redisUrl") or data.get("redis_url") or ""),
            jwt_secret=str(data.get("jwtSecret") or data.get("jwt_secret") or ""),
        )

    def to_public_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "databaseUrl": self.database_url if include_secrets else _mask_url(self.database_url),
            "milvusHost": self.milvus_host,
            "milvusPort": self.milvus_port,
            "esAddresses": self.es_addresses,
            "neo4jUri": self.neo4j_uri,
            "neo4jUser": self.neo4j_user,
            "neo4jPassword": self.neo4j_password if include_secrets else _mask_secret(self.neo4j_password),
            "redisUrl": self.redis_url if include_secrets else _mask_url(self.redis_url),
            "jwtSecret": self.jwt_secret if include_secrets else _mask_secret(self.jwt_secret),
        }
        return d


@dataclass
class FeatureFlags:
    """v1: direct infra is default; cloud API client is optional legacy."""

    direct_infra: bool = True
    cloud_api_client: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> FeatureFlags:
        if not data:
            return cls()
        return cls(
            direct_infra=bool(data.get("directInfra", data.get("direct_infra", True))),
            cloud_api_client=bool(data.get("cloudApiClient", data.get("cloud_api_client", False))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "directInfra": self.direct_infra,
            "cloudApiClient": self.cloud_api_client,
        }


@dataclass
class DesktopConfig:
    flavor: str = "dev"
    allowed_origins: list[str] = field(default_factory=list)
    update_feed_url: str = ""
    infra: InfraEndpoints = field(default_factory=InfraEndpoints)
    feature_flags: FeatureFlags = field(default_factory=FeatureFlags)
    # Legacy v0 fields (optional; not used as runtime primary for UI navigation)
    web_url: str = ""
    api_url: str = ""
    ui_dir: str = ""
    source_path: str | None = None
    user_override_active: bool = False
    flags_provided: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str | None = None) -> DesktopConfig:
        origins = data.get("allowedOrigins") or data.get("allowed_origins") or []
        if isinstance(origins, str):
            origins = [o.strip() for o in origins.split(",") if o.strip()]
        # Partial payloads omit flavor/flags → empty so merge keeps packaged values.
        has_flags = isinstance(data.get("featureFlags"), dict) or isinstance(
            data.get("feature_flags"), dict
        )
        flags_raw = (
            data.get("featureFlags")
            if isinstance(data.get("featureFlags"), dict)
            else data.get("feature_flags")
        )
        if isinstance(data.get("infra"), dict):
            infra_raw = data["infra"]
        elif any(k in data for k in ("databaseUrl", "database_url", "milvusHost", "milvus_host")):
            infra_raw = data
        else:
            infra_raw = {}
        return cls(
            flavor=str(data["flavor"]) if data.get("flavor") not in (None, "") else "",
            allowed_origins=[str(o) for o in origins],
            update_feed_url=str(data.get("updateFeedUrl") or data.get("update_feed_url") or ""),
            infra=InfraEndpoints.from_dict(infra_raw if isinstance(infra_raw, dict) else {}),
            feature_flags=FeatureFlags.from_dict(flags_raw if has_flags else None),
            flags_provided=has_flags,
            web_url=str(data.get("webUrl") or data.get("web_url") or ""),
            api_url=str(data.get("apiUrl") or data.get("api_url") or ""),
            ui_dir=str(data.get("uiDir") or data.get("ui_dir") or ""),
            source_path=source,
        )

    def to_dict(self, *, include_secrets: bool = False) -> dict[str, Any]:
        return {
            "flavor": self.flavor,
            "allowedOrigins": list(self.allowed_origins),
            "updateFeedUrl": self.update_feed_url,
            "infra": self.infra.to_public_dict(include_secrets=include_secrets),
            "featureFlags": self.feature_flags.to_dict(),
            "webUrl": self.web_url,
            "apiUrl": self.api_url,
            "uiDir": self.ui_dir,
            "userOverrideActive": self.user_override_active,
        }

    def merge(self, override: DesktopConfig) -> DesktopConfig:
        """Return a new config: self as base, override non-empty fields win."""
        base = deepcopy(self)
        if override.flavor:
            base.flavor = override.flavor
        if override.allowed_origins:
            base.allowed_origins = list(override.allowed_origins)
        if override.update_feed_url:
            base.update_feed_url = override.update_feed_url
        if override.web_url:
            base.web_url = override.web_url
        if override.api_url:
            base.api_url = override.api_url
        if override.ui_dir:
            base.ui_dir = override.ui_dir

        o = override.infra
        b = base.infra
        if o.database_url:
            b.database_url = o.database_url
        if o.milvus_host:
            b.milvus_host = o.milvus_host
        if o.milvus_port:
            b.milvus_port = o.milvus_port
        if o.es_addresses:
            b.es_addresses = o.es_addresses
        if o.neo4j_uri:
            b.neo4j_uri = o.neo4j_uri
        if o.neo4j_user:
            b.neo4j_user = o.neo4j_user
        if o.neo4j_password:
            b.neo4j_password = o.neo4j_password
        if o.redis_url:
            b.redis_url = o.redis_url
        if o.jwt_secret:
            b.jwt_secret = o.jwt_secret

        if override.flags_provided:
            base.feature_flags = override.feature_flags
            base.flags_provided = True

        base.user_override_active = override.user_override_active or base.user_override_active
        if not base.flavor:
            base.flavor = "dev"
        return base


def user_config_path(data_dir: Path) -> Path:
    return data_dir / "config" / "infra.user.json"


def load_json_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"config root must be object: {path}")
    return data


def load_desktop_config(
    *,
    data_dir: Path,
    packaged_path: Path | None = None,
) -> DesktopConfig:
    """Merge packaged default + user override. Raises if no usable config at all."""
    packaged = DesktopConfig()
    if packaged_path and packaged_path.is_file():
        packaged = DesktopConfig.from_dict(load_json_file(packaged_path), source=str(packaged_path))
        logger.info("desktop config loaded packaged=%s", packaged_path)
    else:
        logger.info("desktop config: no packaged default at %s", packaged_path)

    user_path = user_config_path(data_dir)
    if user_path.is_file():
        user_cfg = DesktopConfig.from_dict(load_json_file(user_path), source=str(user_path))
        user_cfg.user_override_active = True
        merged = packaged.merge(user_cfg)
        merged.user_override_active = True
        logger.info("desktop config merged user override=%s", user_path)
        return merged

    if packaged_path and packaged_path.is_file():
        return packaged

    # Last resort: env-only shell (valid for dev)
    env_cfg = DesktopConfig(
        infra=InfraEndpoints(
            database_url=os.environ.get("DATABASE_URL", ""),
            milvus_host=os.environ.get("MILVUS_HOST", ""),
            milvus_port=int(os.environ.get("MILVUS_PORT") or 19530),
            es_addresses=os.environ.get("ES_ADDRESSES", ""),
            neo4j_uri=os.environ.get("NEO4J_URI", ""),
            neo4j_user=os.environ.get("NEO4J_USER", ""),
            neo4j_password=os.environ.get("NEO4J_PASSWORD", ""),
            redis_url=os.environ.get("REDIS_URL", ""),
            jwt_secret=os.environ.get("JWT_SECRET", ""),
        )
    )
    return env_cfg


def save_user_override(data_dir: Path, payload: dict[str, Any]) -> Path:
    path = user_config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Validate by parsing
    cfg = DesktopConfig.from_dict(payload)
    cfg.user_override_active = True
    path.write_text(
        json.dumps(cfg.to_dict(include_secrets=True), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def clear_user_override(data_dir: Path) -> bool:
    path = user_config_path(data_dir)
    if path.is_file():
        path.unlink()
        return True
    return False


# Desktop product auth defaults (packaged installs have no backend/.env).
_DESKTOP_JWT_SECRET_DEFAULT = "achAt-dev-jwt-s3cr3t-key-f0r-l0cal-0nly-2024"
_DESKTOP_JWT_ACCESS_EXPIRY = "3600"
_DESKTOP_JWT_REFRESH_EXPIRY = "604800"
_DESKTOP_DEFAULT_USER_EMAIL = "admin@local"
_DESKTOP_DEFAULT_USER_PASSWORD = "123456"


def apply_desktop_auth_environ(cfg: DesktopConfig | None = None) -> None:
    """Force desktop auth env used by Settings / ensure_default_local_user.

    Values match the product local defaults the user ships:
      JWT_SECRET / access+refresh expiry / ALLOW_REGISTRATION=false /
      DEFAULT_USER_EMAIL + DEFAULT_USER_PASSWORD.
    """
    jwt = ""
    if cfg is not None and (cfg.infra.jwt_secret or "").strip():
        jwt = cfg.infra.jwt_secret.strip()
    if not jwt:
        jwt = os.environ.get("JWT_SECRET", "").strip() or _DESKTOP_JWT_SECRET_DEFAULT
    os.environ["JWT_SECRET"] = jwt
    os.environ["JWT_ACCESS_TOKEN_EXPIRY"] = _DESKTOP_JWT_ACCESS_EXPIRY
    os.environ["JWT_REFRESH_TOKEN_EXPIRY"] = _DESKTOP_JWT_REFRESH_EXPIRY
    os.environ["ALLOW_REGISTRATION"] = "false"
    os.environ["DEFAULT_USER_EMAIL"] = _DESKTOP_DEFAULT_USER_EMAIL
    os.environ["DEFAULT_USER_PASSWORD"] = _DESKTOP_DEFAULT_USER_PASSWORD


def apply_config_to_environ(cfg: DesktopConfig) -> None:
    """Push infra endpoints into process env for Settings / infra.factory."""
    infra = cfg.infra
    if infra.database_url:
        os.environ["DATABASE_URL"] = infra.database_url
    if infra.milvus_host:
        os.environ["MILVUS_HOST"] = infra.milvus_host
        os.environ["MILVUS_PORT"] = str(infra.milvus_port)
    if infra.es_addresses:
        os.environ["ES_ADDRESSES"] = infra.es_addresses
    if infra.neo4j_uri:
        os.environ["NEO4J_URI"] = infra.neo4j_uri
    if infra.neo4j_user:
        os.environ["NEO4J_USER"] = infra.neo4j_user
    if infra.neo4j_password:
        os.environ["NEO4J_PASSWORD"] = infra.neo4j_password
        os.environ["ENABLE_GRAPH"] = "true"
    if infra.redis_url:
        os.environ["REDIS_URL"] = infra.redis_url
    apply_desktop_auth_environ(cfg)
    if cfg.allowed_origins:
        joined = ",".join(cfg.allowed_origins)
        os.environ["ACHAT_ALLOWED_ORIGINS"] = joined
        os.environ["CORS_ORIGINS"] = joined
    if cfg.ui_dir:
        os.environ["ACHAT_UI_DIR"] = cfg.ui_dir
    os.environ["ACHAT_FEATURE_DIRECT_INFRA"] = "1" if cfg.feature_flags.direct_infra else "0"
    os.environ["ACHAT_FEATURE_CLOUD_API_CLIENT"] = "1" if cfg.feature_flags.cloud_api_client else "0"

    # Settings is lru_cached — clear so next get_settings() sees new env.
    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def enforce_desktop_optional_infra(cfg: DesktopConfig) -> None:
    """Make desktop infra JSON authoritative for optional services.

    ``apply_config_to_environ`` only sets non-empty values, then
    ``backend/.env`` may fill ES/Milvus/Neo4j via dotenv. For desktop, an
    empty field means *disabled* — clear the env so factory skips them and
    does not spam connection errors for services the user never started.
    """
    infra = cfg.infra
    if not (infra.milvus_host or "").strip():
        os.environ["MILVUS_HOST"] = ""
        os.environ.pop("MILVUS_PORT", None)
    if not (infra.es_addresses or "").strip():
        os.environ["ES_ADDRESSES"] = ""
    if not (infra.neo4j_uri or "").strip():
        os.environ["NEO4J_URI"] = ""
        os.environ["NEO4J_USER"] = ""
        os.environ["NEO4J_PASSWORD"] = ""
        os.environ["ENABLE_GRAPH"] = "false"
    if not (infra.redis_url or "").strip():
        os.environ["REDIS_URL"] = ""

    try:
        from app.config import get_settings

        get_settings.cache_clear()
    except Exception:
        pass


def ensure_local_ui_origin(origins: list[str], bind: str, port: int) -> list[str]:
    """Guarantee loopback UI origin is allowlisted for same-origin desktop UI.

    Also includes common Next.js dev origins so CORS + Origin middleware accept
    EventSource/fetch from http://localhost:3000 → http://127.0.0.1:<engine>.
    """
    result = list(origins)
    candidates = {
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        # Dev frontend (Tauri shell navigates here in `pnpm dev`)
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    }
    if bind in ("::1",):
        candidates.add(f"http://[::1]:{port}")
    for c in candidates:
        if c not in result:
            result.append(c)
    return result


def redact_config(obj: Any) -> Any:
    """Deep-redact sensitive keys/values for safe logging."""
    if isinstance(obj, DesktopConfig):
        return redact_config(obj.to_dict(include_secrets=True))
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key_l = re.sub(r"[^a-z0-9]", "", str(k).lower())
            if key_l in _SENSITIVE_KEYS or any(s in key_l for s in ("password", "secret", "token", "apikey")):
                out[k] = _mask_value(v)
            else:
                out[k] = redact_config(v)
        return out
    if isinstance(obj, list):
        return [redact_config(x) for x in obj]
    if isinstance(obj, str) and ("://" in obj or "postgres" in obj.lower()):
        return _mask_url(obj)
    return obj


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return value[:2] + "****" + value[-2:]


def _mask_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if parsed.password:
            user = parsed.username or ""
            host = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port else ""
            netloc = f"{user}:****@{host}{port}" if user else f"****@{host}{port}"
            return urlunparse(
                (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
            )
        if parsed.username and not parsed.password:
            return url
    except Exception:
        pass
    return _mask_secret(url) if len(url) > 12 else "****"


def _mask_value(v: Any) -> Any:
    if v is None or v == "":
        return v
    if isinstance(v, str):
        return _mask_url(v) if "://" in v else _mask_secret(v)
    return "****"
