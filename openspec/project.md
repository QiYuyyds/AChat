# AChat OpenSpec Project

## Purpose

AChat is a local multi-agent collaboration app that turns agent work into an IM-style workspace. Users create single-agent or group conversations, route messages to Claude Code, Codex, or custom OpenAI-compatible agents (all behind a unified adapter layer), preview generated artifacts, and approve file changes inside local workspaces.

## Canonical Spec Layout

OpenSpec capability specs under `openspec/specs/` are the concise, testable contract layer. The legacy numbered docs under `specs/` remain the detailed design/reference layer until they are fully folded into OpenSpec.

| OpenSpec capability | Legacy source |
|---|---|
| `core-domain` | `specs/01-core-entities.md` |
| `stream-events` | `specs/02-stream-events.md` |
| `message-parts` | `specs/03-message-parts.md` |
| `artifacts` | `specs/04-artifacts.md` |
| `adapters` | `specs/05-adapter-interface.md` |
| `orchestrator` | `specs/06-orchestrator-flow.md` |
| `tools` | `specs/07-tools.md` |
| `persistence` | `specs/08-db-schema.md` |
| `frontend` | `specs/09-frontend-architecture.md` |
| `agent-builder` | `specs/10-agent-builder.md` |
| `platform-security` | `specs/11-platform.md` |
| `desktop-electron` | `specs/12-desktop-electron.md` |
| `conversation-context` | `specs/13-conversation-context.md` |
| `mobile-companion` | `specs/14-mobile-remote.md` |
| `run-internal-compaction` | `specs/19-unified-agent-loop.md`（Run 内压缩章节） |
| `user-auth` | `specs/11-platform.md`（用户认证与多用户隔离章节） |
| `guide-agent` | `openspec/changes/archive/2026-07-21-add-guide-agent/`（小A Guide Agent） |
| `model-profiles` | `openspec/changes/archive/`（ModelProfile 用户级模型配置） |
| `worktree-conflict-resolution` | `specs/19-unified-agent-loop.md`（Worktree 三层冲突解决章节） |

## Technology

- Frontend: Next.js 16 App Router + React 19, TypeScript strict, Tailwind v4 + shadcn/ui, Zustand + Immer, SSE
- Backend: Python 3.11+ / FastAPI, SQLAlchemy 2.0 async + asyncpg + aiosqlite, **PostgreSQL 16** + **SQLite (WAL)** (dual-DB: local SQLite for hot data + remote PG for user/knowledge data), Pydantic v2, ruff, pytest
- Adapter routes (see `specs/05-adapter-interface.md`):
  - **CLI subprocess route** — Claude Code (`spawn claude -p --output-format stream-json`) and Codex (`spawn codex app-server --listen stdio://`, JSON-RPC 2.0). The CLI owns tool execution, sandbox, and approval; AChat translates CLI events into `StreamEvent`.
  - **SDK route** — Custom adapter uses the `openai` Python SDK (Chat Completions) with an AChat-managed tool loop. Covers DeepSeek / OpenAI / 火山方舟 / OpenRouter / SiliconFlow etc.
  - **Mock route** — scripted event stream for development without token cost.
- AChat MCP Bridge (`backend/app/mcp_bridge.py`) exposes platform tools (`write_artifact`, `ask_user`, `task_dispatch`, …) to CLI agents via stdio MCP.
- Infrastructure (Docker Compose, independently degradable): Milvus (dense vector + sparse BM25 + graph entity/triple vector) · Neo4j (KG · PPR + entity/triple subgraph) · Kafka (optional). **Elasticsearch removed** — replaced by Milvus native BM25 sparse vector. **Redis removed** — replaced by dual-DB SQLite direct-write + in-process dict TTL cache.
- RAG subsystem (`backend/app/rag/`): parser registry (7 OCR engines + auto mode) · chunking presets (general/qa/semantic/separator) · file lifecycle state machine (11 states + optimistic concurrency) · async task queue (`rag_tasks` table + `RagTaskWorker`) · graph build task (async, fire-and-forget) · graph retrieval (PPR + entity/triple vector search) · MilvusGraphVectorStore (entity/triple Milvus collections) · RAG evaluation system (dataset CRUD + benchmark auto-generation + LLM-as-Judge + independent eval LLM config). `rewriter.py` removed.
- Database: 27 tables (22 core + `rag_tasks` + `eval_datasets` / `eval_dataset_items` / `eval_runs` / `eval_run_items`). Dual-DB routing: 14 local SQLite + 13 remote PostgreSQL. Schema migrations in `backend/app/db/migrations/`.
- Desktop shell: Electron 33; Mobile companion: Capacitor

## Rules

- UI MUST not call LLM SDKs directly.
- Adapter code MUST not write database rows directly except through documented event translation boundaries.
- Tools MUST enforce workspace path isolation and command safety before side effects.
- Specs and code MUST be updated together for entity, event, adapter, tool, persistence, and security contract changes.
