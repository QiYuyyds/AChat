# Whole-package updater (task 9.3)

## Config

`apps/desktop/src-tauri/tauri.conf.json` → `plugins.updater`:

```json
"updater": {
  "active": false,
  "endpoints": [],
  "pubkey": ""
}
```

v1 ships with updater **inactive** until a signing pubkey and feed URL are provisioned.

## Enabling for an internal channel

1. Generate an updater keypair (Tauri updater CLI / minisign flow per Tauri 2 docs).
2. Set `pubkey` to the public key string in `tauri.conf.json`.
3. Host a static feed JSON (or dynamic endpoint) that lists platform artifacts, e.g.:

```json
{
  "version": "0.1.1",
  "notes": "engine + shell",
  "pub_date": "2026-07-18T00:00:00Z",
  "platforms": {
    "windows-x86_64": {
      "signature": "<sig>",
      "url": "https://downloads.example/achat/AChat_0.1.1_x64-setup.nsis.zip"
    }
  }
}
```

4. Set `endpoints` to that feed URL and `active: true`.
5. Smoke: install 0.1.0 → publish 0.1.1 feed → launch app → confirm update discovery UI / log, download, install, relaunch.

## Scope

Whole package (shell + bundled engine artifacts). Not a split shell/engine channel in v1.

## Smoke script (manual)

```text
1. Install build A (version N)
2. Publish feed for version N+1 with valid signature
3. Start app online
4. Expect update prompt or automatic check log
5. Apply update; confirm version N+1 and engine still healthz-ok
```
