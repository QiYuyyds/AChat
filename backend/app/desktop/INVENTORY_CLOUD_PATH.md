# Desktop path inventory (v1 pivot)

## Authority model (2026-07-19)

| Area | Desktop online path |
|---|---|
| Auth / register / refresh | **Local engine** REST → primary **PostgreSQL** (packaged default or user override) |
| Conversations / messages / agents / settings | **Local engine** SQLAlchemy against primary PG |
| Provider API keys | `user_settings` on primary store (local read) — **no** mandatory CloudApiClient key fetch |
| RAG / memory / KG | Optional infra via `infra/factory` **direct** (Milvus/ES/Neo4j) when configured; degrade when absent |
| Workspace files | **Local disk** |
| Agent runs / stream | **Local engine** bus + `/api/stream` |
| Offline | SQLite under `%APPDATA%/AChat/sqlite`; outbox flush → **primary DB** (not official business API) |

## Feature flags

| Flag | Default | Meaning |
|---|---|---|
| `featureFlags.directInfra` / `ACHAT_FEATURE_DIRECT_INFRA` | **on** | Wire `DATABASE_URL` + optional infra from desktop config |
| `featureFlags.cloudApiClient` / `ACHAT_FEATURE_CLOUD_API_CLIENT` | **off** | Legacy v0: CloudApiClient + `/api/auth/me` resolve |

## Retired / optional

- **Mandatory** official AChat business HTTPS hop for online persistence — **retired**
- `CloudApiClient` — kept under feature flag for emergency compatibility
- Dual-plane frontend (`DESKTOP_OFFICIAL_CLOUD_PATH_PREFIXES`) — desktop routes **all** business traffic to `engineBaseUrl`

## Config files

- Packaged: `infra.default.json` (or `--infra-config`)
- User override: `%APPDATA%/AChat/config/infra.user.json`
- Settings API: `GET/PUT/DELETE /api/desktop/infra-config`
