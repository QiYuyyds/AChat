# AgentHub

<p align="center">
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white">
  <img alt="React" src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=0B1F2A">
  <img alt="Electron" src="https://img.shields.io/badge/Electron-33-47848F?logo=electron&logoColor=white">
  <img alt="TypeScript" src="https://img.shields.io/badge/TypeScript-strict-3178C6?logo=typescript&logoColor=white">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-local--first-044A64?logo=sqlite&logoColor=white">
  <img alt="pnpm" src="https://img.shields.io/badge/pnpm-workspace-F69220?logo=pnpm&logoColor=white">
</p>

<p align="center">
  <b>English</b> · <a href="./README.zh-CN.md">简体中文</a>
</p>

AgentHub is a local-first multi-agent workspace that turns AI collaboration into an IM-style experience.

Instead of treating every agent run as an isolated terminal transcript, AgentHub organizes work around conversations: agents are contacts, a conversation is a workspace, files and artifacts are shared context, and the Orchestrator can split work across multiple agents.

<p align="center">
    <img src="docs/images/agenthub-preview.png" alt="AgentHub multi-agent collaboration and artifact preview" width="100%" />
</p>

> Current status: active local development. The app is usable as a web app and Electron desktop app; the mobile companion app is under development.

## Contents

- [Why AgentHub](#why-agenthub)
- [Features](#features)
  - [IM-style agent workspace](#im-style-agent-workspace)
  - [Multi-agent support](#multi-agent-support)
  - [Orchestrator and task dispatch](#orchestrator-and-task-dispatch)
  - [Artifacts and deployment previews](#artifacts-and-deployment-previews)
- [Tech Stack](#tech-stack)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [Desktop App](#desktop-app)
  - [Specify Electron build platform](#specify-electron-build-platform)
  - [SQLite ABI notes](#sqlite-abi-notes)
- [Mobile Companion](#mobile-companion)
- [Common Commands](#common-commands)
- [Architecture](#architecture)
- [Security Model](#security-model)
- [Known Limits](#known-limits)
- [Contributing](#contributing)

---

## Why AgentHub

Modern coding agents are powerful, but real work often needs more than one prompt:

- keep multiple conversations and workspaces alive
- route work to different agents and models
- inspect reasoning, tool calls, file writes, command output, and artifacts
- approve risky changes before they touch the workspace
- continue from desktop, and eventually monitor from a phone

AgentHub is built for that workflow. It is local by default, uses SQLite, and keeps agent execution on your own machine.

---

## Features

### IM-style agent workspace

- Conversation list, group chats, mentions, unread state, bookmarks, pins, reply quoting, edit-and-resend, withdraw, and regenerate.
- Messages are structured parts, not one raw markdown blob: text, code, thinking, tool calls, tool results, attachments, artifact refs, deployment cards, and dispatch plans render differently.
- Tool calls are visible in the chat stream, including long bash commands and command output.

### Multi-agent support

| Adapter | Use case |
| --- | --- |
| Claude Code | Uses `@anthropic-ai/claude-agent-sdk`, with Claude Code tools and session continuation. |
| Codex | Uses `@openai/codex-sdk`, with isolated AgentHub `CODEX_HOME` / `CODEX_SQLITE_HOME`. |
| Custom Agent | OpenAI Chat Completions-compatible providers such as OpenAI, DeepSeek, Volcano Ark, OpenRouter, SiliconFlow, and other compatible endpoints. |
| Mock | Local development without spending tokens. |

You can create custom agents in the UI with their own model, provider, system prompt, base URL, API key, and tool set.

### Orchestrator and task dispatch

The Orchestrator is a normal agent with extra tools. It can:

- ask structured clarification questions
- create a task plan
- wait for plan approval or revision
- dispatch tasks to child agents
- track child task completion, failures, blockers, and artifacts
- aggregate the final answer back into the conversation

### Workspace, files, and approvals

- Each conversation has a workspace.
- Sandbox mode stores files under `.agenthub-data/workspaces/<conversationId>`.
- Local mode binds a conversation to a real local project directory.
- `fs_read`, `fs_write`, and `bash` are constrained to the effective workspace directory.
- Review mode can require approval before file writes.
- High-risk bash commands can require approval before execution.

### Artifacts and deployment previews

Agents can create and reference structured artifacts:

- `web_app`: sandboxed iframe preview
- `document`: markdown rendering
- `image`: image preview
- `ppt`: slide preview and real `.pptx` export
- `code_file`: workspace file reference
- `diff`: version comparison

For local frontend projects, agents can deploy static output directories such as `dist`, `build`, `out`, or `client/dist` into a local preview card.

### Desktop and mobile

- Electron desktop packaging is supported.
- A Capacitor mobile companion app lives in `apps/mobile`.
- The intended mobile workflow is companion-client style: phone connects to the desktop AgentHub host over LAN or Tailscale, then observes runs, sends messages, and handles approvals.

---

## Tech Stack

- Next.js 16 App Router + React 19
- TypeScript strict mode
- Tailwind CSS v4 + shadcn/ui
- Zustand + Immer
- SQLite + Drizzle + `better-sqlite3`
- SSE for live updates
- Electron 33 for desktop packaging
- Capacitor for the mobile companion app
- pnpm workspaces

Next.js is pinned to `16.2.6`. If you modify framework-level behavior, read the local Next docs in `node_modules/next/dist/docs/` first.

---

## Requirements

- Node.js 20+
- pnpm
- macOS or Windows for the desktop app path
- Xcode and CocoaPods only if you work on the iOS companion app

Optional provider setup:

- Claude Code login for Claude Code adapter OAuth usage
- API keys for Anthropic, OpenAI, DeepSeek, Volcano Ark, or custom OpenAI-compatible providers

---

## Quick Start

```bash
pnpm install

# Optional
cp .env.example .env.local

# Run dev
pnpm dev
```

Open:

```text
http://localhost:3000
```

The app auto-creates the SQLite database and seeds the built-in agents on first startup.

You can configure API keys either in `.env.local` or in the app settings panel. Agent-specific keys override global settings.

Common env vars:

```bash
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
ARK_API_KEY=...
```

Claude Code can also work from an existing local Claude Code login without a separate Anthropic API key.

---

## Desktop App

Development mode:

```bash
pnpm electron:dev
```

Default package command:

```bash
pnpm electron:build
```

Output goes to:

```text
release/
```

The current `package.json#build` config targets:

- macOS: `dmg`, `arm64`
- Windows: `nsis`, `x64`

### Specify Electron build platform

`pnpm electron:build` is a convenience script. If you want to choose the exact platform/arch, run the same prebuild pipeline and call `electron-builder` with platform flags:

```bash
# macOS arm64 DMG
pnpm build && pnpm electron:prebuild && pnpm electron:tsc && pnpm exec electron-builder --mac dmg --arm64

# macOS x64 DMG
pnpm build && pnpm electron:prebuild && pnpm electron:tsc && pnpm exec electron-builder --mac dmg --x64

# Windows x64 NSIS installer
pnpm build && pnpm electron:prebuild && pnpm electron:tsc && pnpm exec electron-builder --win nsis --x64
```

Short forms also work when you are calling `electron-builder` directly:

```bash
pnpm exec electron-builder -m --arm64
pnpm exec electron-builder -w --x64
```

Native modules matter here. `better-sqlite3` is compiled for a specific Node/Electron ABI and CPU architecture. For reliable releases, build on the target OS/architecture or use a CI matrix. Cross-building Windows from macOS may require extra toolchain support such as Wine, and cross-building native modules across CPU architectures is not always reliable.

### SQLite ABI notes

This project flips `better-sqlite3` between Node ABI and Electron ABI depending on the command:

- `pnpm dev`, `pnpm test`, `pnpm e2e`: Node ABI
- `pnpm build`, `pnpm start`, `pnpm db:*`, packaged Electron app: Electron ABI

The package scripts try to check and rebuild automatically. If you see a native module version error, run one of:

```bash
pnpm rebuild better-sqlite3
pnpm electron:rebuild
```

Then rerun the failed command.

---

## Mobile Companion

Mobile app workspace:

```bash
apps/mobile
```

Useful commands:

```bash
pnpm mobile:dev
pnpm mobile:build
pnpm mobile:sync
pnpm mobile:open:ios
pnpm mobile:open:android
```

The mobile app is designed to connect to a desktop AgentHub host over LAN or Tailscale. Agent execution, file writes, command execution, and workspace state remain on the desktop side.

---

## Common Commands

```bash
pnpm dev                 # Web dev server
pnpm typecheck           # TypeScript check
pnpm lint                # ESLint
pnpm test                # Vitest
pnpm e2e                 # Playwright E2E
pnpm build               # Next production build under Electron Node ABI
pnpm start               # Start production server
pnpm db:push             # Apply Drizzle schema
pnpm db:seed             # Re-seed built-in agents
pnpm electron:dev        # Desktop development mode
pnpm electron:build      # Desktop package
```

Local data:

```text
.agenthub-data/agenthub.db
.agenthub-data/workspaces/
```

Packaged desktop data:

```text
macOS:   ~/Library/Application Support/AgentHub/data
Windows: %APPDATA%/AgentHub/data
```

---

## Architecture

AgentHub follows a five-layer structure:

```text
L5 UI
  React components, shadcn/ui, message/artifact rendering

L4 State + Transport
  Zustand store, SSE client, reducers for StreamEvent

L3 Application Services
  AgentRunner, ConversationService, EventBus, ToolExecutor

L2 Agent Platform Adapters
  Claude Code, Codex, Custom OpenAI-compatible adapter, Mock

L1 Persistence
  SQLite, Drizzle, workspace filesystem
```

The central contract is `StreamEvent`. Adapter output, tool activity, artifact creation, pending approvals, dispatch state, and usage updates flow through this event model before reaching the UI.

Key docs:

- [CLAUDE.md](./CLAUDE.md): project rules for AI collaborators
- [OVERVIEW.md](./OVERVIEW.md): codebase map and current implementation status
- [openspec/project.md](./openspec/project.md): OpenSpec capability index
- [specs/](./specs): detailed numbered specs

---

## Security Model

AgentHub assumes LLM output is untrusted.

- File tools resolve paths under the conversation effective workspace.
- Bash commands run inside the workspace cwd.
- Dangerous bash patterns are blocked.
- Risky commands can require approval.
- Generated web app artifacts render in sandboxed iframes.
- API keys are local settings or environment variables; there is no hosted key service.

This is a local single-user app, not a multi-tenant hosted service.

---

## Known Limits

- Linux desktop packaging is not configured yet.
- Cross-platform Electron builds with native modules should be handled through target-platform machines or CI.
- Claude Code SDK can write files through its own tool layer; sandbox quota enforcement only applies to AgentHub-managed file tools.
- Some SDK-level command/file approval bridges depend on what the underlying adapter exposes.
- The mobile app is a companion client, not a standalone agent runtime.

---

## Contributing

Before making code changes, read:

1. [CLAUDE.md](./CLAUDE.md)
2. [openspec/project.md](./openspec/project.md)
3. Relevant files under [openspec/specs](./openspec/specs) and [specs](./specs)

When changing entities, stream events, tools, adapters, persistence, platform behavior, or security rules, update code and specs together.

---

## License

AGPL-3.0-only. See [LICENSE](./LICENSE).
