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

## Technology

- Frontend: Next.js 16 App Router + React 19, TypeScript strict, Tailwind v4 + shadcn/ui, Zustand + Immer, SSE
- Backend: Python 3.11+ / FastAPI, SQLAlchemy 2.0 async + asyncpg, **PostgreSQL 16**, Pydantic v2, ruff, pytest
- Adapter routes (see `specs/05-adapter-interface.md`):
  - **CLI subprocess route** — Claude Code (`spawn claude -p --output-format stream-json`) and Codex (`spawn codex app-server --listen stdio://`, JSON-RPC 2.0). The CLI owns tool execution, sandbox, and approval; AChat translates CLI events into `StreamEvent`.
  - **SDK route** — Custom adapter uses the `openai` Python SDK (Chat Completions) with an AChat-managed tool loop. Covers DeepSeek / OpenAI / 火山方舟 / OpenRouter / SiliconFlow etc.
  - **Mock route** — scripted event stream for development without token cost.
- AChat MCP Bridge (`backend/app/mcp_bridge.py`) exposes platform tools (`report_task_result`, `write_artifact`, `ask_user`, …) to CLI agents via stdio MCP.
- Infrastructure (Docker Compose, independently degradable): Milvus (vector) · Elasticsearch (BM25) · Neo4j (KG) · Kafka (optional)
- Desktop shell: Electron 33; Mobile companion: Capacitor

## Rules

- UI MUST not call LLM SDKs directly.
- Adapter code MUST not write database rows directly except through documented event translation boundaries.
- Tools MUST enforce workspace path isolation and command safety before side effects.
- Specs and code MUST be updated together for entity, event, adapter, tool, persistence, and security contract changes.
