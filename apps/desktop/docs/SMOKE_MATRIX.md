# Desktop smoke matrix (tasks 9.6 / 11.3)

Requires: Windows host, WebView2, built installer or `cargo tauri dev`, reachable official web+api (or local staging), optional Claude/Codex CLIs.

## 9.6 Install smoke

| Step | Action | Pass criteria |
|---|---|---|
| 1 | Run NSIS installer | Completes without error |
| 2 | Launch AChat | Window opens; engine status ready |
| 3 | Login with account | Official auth succeeds; handoff `POST /api/desktop/session` 200 |
| 4 | Send message | Local engine run starts; stream events; reply appears |
| 5 | Bind folder | Native picker; local workspace mode |
| 6 | Quit | Engine process exits; no orphan python |

## 11.3 Mode matrix

| Scenario | Steps | Pass |
|---|---|---|
| Web-only | Browser without bridge | No desktop chip; all API to official |
| Desktop online | Installer path above | Runs local; settings keys from cloud |
| Offline → reconnect | Disconnect network mid-run / after user msg | Outbox grows; reconnect flush; conflicts visible not silent |
| Missing CLI | Use Claude/Codex agent without CLI | Actionable error, not crash |
| Directory bind | selectDirectory + send | Tools operate under bound path |

## Not automated here

These need a real machine and server. CI can still run unit tests under `backend/tests/test_desktop_*.py`.
