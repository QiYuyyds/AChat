## 1. Managed runtime

- [x] 1.1 Add pinned platform manifest with version, HTTPS URLs, SHA256 and license metadata.
- [x] 1.2 Resolve packaged runtime, then verified cache, then user-approved download.
- [x] 1.3 Implement cancellable download, SHA256, safe extraction, atomic install and partial cleanup.
- [x] 1.4 Bundle the matching runtime per desktop platform and preserve MIT License/NOTICE.

## 2. Workspace index manager

- [x] 2.1 Add isolated manager with per-project lock, global concurrency 1, cancellation and shutdown cleanup.
- [x] 2.2 Persist enabled/status/phase/counts/timestamps/error atomically in internal Workspace metadata; no DB change.
- [x] 2.3 Implement all lifecycle states and restart-to-interrupted recovery.
- [x] 2.4 Create conversation immediately, then prepare runtime and run init in background.
- [x] 2.5 Implement cancel, retry, sync, rebuild and disable; disable preserves `.codegraph`.
- [x] 2.6 Add debounced incremental sync after ready.

## 3. REST API

- [x] 3.1 Add `codeIntelligenceEnabled` to local conversation creation.
- [x] 3.2 Add authenticated enable/status/cancel/sync/rebuild/retry/disable endpoints.
- [x] 3.3 Enforce ownership, local mode and valid state transitions.
- [x] 3.4 Keep progress outside StreamEvent/SSE.

## 4. code_explore and adapters

- [x] 4.1 Implement validated, bounded, cancellable `code_explore(query)` using Workspace-derived path.
- [x] 4.2 Register ToolDef and conditionally auto-inject it for ready Custom runs only.
- [x] 4.3 Expose the same ToolDef through CLI MCP Bridge and update Claude/Codex hints.
- [x] 4.4 Use only verified runtime and argv-safe Windows/POSIX process launch; terminate complete process trees.

## 5. Frontend

- [x] 5.1 Add default-OFF create-dialog switch below the local path warning; hide for sandbox.
- [x] 5.2 Add source-graph status icon beside the chat-header folder control.
- [x] 5.3 Add panel with first-row “源码智能” sliding switch and details below.
- [x] 5.4 Implement OFF→ON confirmation/enable, ON→OFF confirmation/disable, failure rollback and pending disabled state.
- [x] 5.5 Keep switch ON for preparing/indexing/ready/failed/interrupted enabled intent.
- [x] 5.6 Add progress, statistics, cancel/retry/sync/rebuild actions and one completion/failure toast.
- [x] 5.7 Poll only while panel open or task non-terminal; clean timers on conversation change/unmount.

## 6. Verification

- [x] 6.1 Test runtime verification, malicious archive, state machine, metadata, locks and restart recovery.
- [x] 6.2 Test REST auth/ownership/transitions and no SSE contract changes.
- [x] 6.3 Test slider confirmation, rollback, pending state, progress and polling cleanup.
- [x] 6.4 Test tool injection/Bridge, fallback, output bounds, cancellation and argv injection safety.
- [x] 6.5 Prove feature-off zero activity, sandbox untouched, other conversations unblocked and no orphan process.
- [x] 6.6 Run ruff/pytest, pnpm typecheck/lint/frontend tests and desktop runtime smoke tests.
- [x] 6.7 Compare representative tasks with and without code intelligence.

## 7. Acceptance fixes

- [x] 7.1 Stream CodeGraph verbose output, persist monotonic whole-run progress and expose it through status REST.
- [x] 7.2 Keep both switch thumbs inside their tracks and render one determinate active progress bar; preserve the approved ready state.
- [x] 7.3 Run backend/frontend regression, type/lint checks and live indexing verification; record evidence.
