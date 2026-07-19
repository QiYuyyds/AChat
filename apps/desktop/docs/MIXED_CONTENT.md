# https official page → http://127.0.0.1 engine (task 7.8)

## Expected behavior

Chromium / WebView2 generally treats loopback (`http://127.0.0.1`, `http://localhost`) as a special case for mixed content: an **https** page may fetch loopback http without the classic active mixed-content block.

AChat relies on:

1. Official frontend served at `https://…` (WebView navigates there)
2. Injected `window.achatDesktop.engineBaseUrl = "http://127.0.0.1:<port>"`
3. `fetch` / `EventSource` from that page to the local engine with `X-Engine-Token` (or `?engineToken=` for SSE)

CORS: local engine allows configured official Origins via desktop runtime env.

## Validation checklist (manual on a Windows machine with WebView2)

1. Build/run `apps/desktop` against a real `official.dev.json` / staging URLs
2. Open DevTools (if available) or use engine logs
3. After login, confirm:
   - `POST http://127.0.0.1:<port>/api/desktop/session` succeeds
   - `GET http://127.0.0.1:<port>/healthz` from page context succeeds
   - `POST …/api/conversations/{id}/messages` on engine starts a local run
   - SSE `…/api/stream?engineToken=…` connects

If any of the above fail with mixed-content / network errors in WebView2:

## Fallback (not implemented until blocked in smoke)

Shell-local reverse proxy:

- Tauri command or local https listener on loopback with a self-signed cert **or** a custom protocol / IPC bridge that forwards HTTP to the engine
- Inject `engineBaseUrl` as that proxy URL so page remains same-security-context friendly

Do **not** relax engine token / Origin checks when adding a proxy.

## Status

Code path is ready for loopback HTTP. Full WebView2 confirmation is part of install smoke (task 9.6 / 11.3). This document records the decision and fallback plan for 7.8.
