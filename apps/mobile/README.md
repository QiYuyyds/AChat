# AgentHub Mobile

Capacitor companion app workspace for Spec 14.

This app is planned as a mobile client for the desktop AgentHub host. It should not run SQLite, LLM SDKs, workspace tools, or Next.js API routes locally. It connects to the desktop host over Tailscale/tailnet or LAN and uses `/api/mobile/*` endpoints for snapshot, events, chat, approvals, and `ask_user` responses.

Status: Vite + React + Capacitor scaffold initialized. Android and iOS native projects are generated.

Useful commands from the repo root:

```bash
pnpm mobile:dev
pnpm mobile:build
pnpm --filter @agenthub/mobile exec capacitor add ios
pnpm --filter @agenthub/mobile exec capacitor add android
pnpm mobile:sync
pnpm mobile:open:ios
pnpm mobile:open:android
```

Native prerequisites:

- Android: Android Studio / Android SDK / Java runtime.
- iOS: Xcode + CocoaPods + an installed iOS platform/runtime from Xcode > Settings > Components.

Capacitor owns `ios/` and `android/`; do not create placeholder directories for them manually.
