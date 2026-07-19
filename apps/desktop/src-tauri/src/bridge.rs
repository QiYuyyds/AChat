use crate::engine::{EngineManager, EngineStatus};
use crate::official::OfficialConfig;
use serde::Serialize;
use std::sync::Arc;
use tauri::{AppHandle, Manager, State};
use tauri_plugin_dialog::DialogExt;

pub struct BridgeState {
    pub engine: EngineManager,
    pub official: OfficialConfig,
}

impl BridgeState {
    pub fn new(engine: EngineManager, official: OfficialConfig) -> Arc<Self> {
        Arc::new(Self { engine, official })
    }
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct DesktopBridgePayload {
    pub is_desktop: bool,
    pub engine_base_url: String,
    pub engine_token: String,
    pub app_version: String,
}

#[tauri::command]
pub async fn select_directory(app: AppHandle) -> Result<Option<String>, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    app.dialog().file().pick_folder(move |folder| {
        let _ = tx.send(folder.map(|p| p.to_string()));
    });
    rx.recv()
        .map_err(|e| format!("directory picker channel closed: {e}"))
}

#[tauri::command]
pub async fn open_path(path: String) -> Result<(), String> {
    open::that(&path).map_err(|e| format!("open path failed: {e}"))
}

#[tauri::command]
pub async fn get_engine_status(state: State<'_, Arc<BridgeState>>) -> Result<String, String> {
    let snap = state.engine.snapshot().await;
    let label = match snap.status {
        EngineStatus::Starting => "starting",
        EngineStatus::Ready => "ready",
        EngineStatus::Error => "error",
    };
    Ok(label.into())
}

#[tauri::command]
pub async fn restart_engine(
    app: AppHandle,
    state: State<'_, Arc<BridgeState>>,
) -> Result<(), String> {
    state.engine.restart().await?;
    // Re-inject new engineBaseUrl/token WITHOUT navigating (full reload kills SSE).
    reinject_bridge(&app).await
}

/// Build the JS snippet that installs `window.achatDesktop`.
///
/// Idempotent: if the payload is unchanged, do not re-dispatch `achat-desktop-ready`
/// (avoids StreamProvider thrashing). Always re-bind methods so they stay fresh.
fn bridge_inject_script(json: &str) -> String {
    format!(
        r#"(function() {{
  const payload = {json};
  const prev = window.achatDesktop;
  const same = prev
    && prev.engineBaseUrl === payload.engineBaseUrl
    && prev.engineToken === payload.engineToken
    && prev.appVersion === payload.appVersion;
  window.achatDesktop = {{
    isDesktop: true,
    engineBaseUrl: payload.engineBaseUrl,
    engineToken: payload.engineToken,
    appVersion: payload.appVersion,
    selectDirectory: async function() {{
      return await window.__TAURI_INTERNALS__.invoke('select_directory');
    }},
    openPath: async function(path) {{
      return await window.__TAURI_INTERNALS__.invoke('open_path', {{ path }});
    }},
    getEngineStatus: async function() {{
      return await window.__TAURI_INTERNALS__.invoke('get_engine_status');
    }},
    restartEngine: async function() {{
      return await window.__TAURI_INTERNALS__.invoke('restart_engine');
    }},
  }};
  // Always sync sessionStorage so SPA routes / late modules see the live token.
  try {{
    if (payload.engineToken) sessionStorage.setItem('achat_engine_token', payload.engineToken);
    if (payload.engineBaseUrl) sessionStorage.setItem('achat_engine_base', payload.engineBaseUrl);
    if (payload.appVersion) sessionStorage.setItem('achat_engine_app_version', payload.appVersion);
  }} catch (_) {{}}
  if (!same) {{
    try {{ window.dispatchEvent(new Event('achat-desktop-ready')); }} catch (_) {{}}
  }}
}})();"#
    )
}

/// Choose which URL the webview should open.
///
/// - **dev (`tauri dev`)**: Next.js on localhost:3000 (or config `webUrl`) so the full app UI
///   is available while the local engine serves API/SSE only.
/// - **release**: local engine origin that hosts packaged static UI + API (same origin).
///   Embed a one-shot `?__et=` so the page can attach `X-Engine-Token` before shell
///   re-injection finishes (avoids login 401 "Invalid engine token" on cold start).
fn resolve_ui_url(official: &OfficialConfig, engine_base: &str, engine_token: &str) -> String {
    #[cfg(dev)]
    {
        let _ = (engine_base, engine_token);
        if !official.web_url.is_empty() {
            return official.web_url.clone();
        }
        // Match tauri.conf.json build.devUrl
        return "http://localhost:3000".to_string();
    }
    #[cfg(not(dev))]
    {
        let _ = official;
        let base = engine_base.trim_end_matches('/');
        if engine_token.is_empty() {
            return format!("{base}/");
        }
        // Token is per shell session (loopback only); stripped by frontend after read.
        format!("{base}/?__et={engine_token}")
    }
}

fn urls_equivalent(current: &str, target: &str) -> bool {
    // Own the normalized String so we never return a temporary borrow from a closure.
    // Query (?__et=) is ignored so reinject after handoff does not force a reload loop.
    let norm = |s: &str| -> String {
        let base = s.split(['?', '#']).next().unwrap_or(s);
        base.trim()
            .trim_end_matches('/')
            .replace("127.0.0.1", "localhost")
            .to_ascii_lowercase()
    };
    let c = norm(current);
    let t = norm(target);
    if c.is_empty() {
        return false;
    }
    // Same origin or already on the target path (dev Next may be on /login etc.)
    c == t
        || c.starts_with(&(t.clone() + "/"))
        || c.starts_with("http://localhost:3000") && t.starts_with("http://localhost:3000")
}

async fn payload_from_state(app: &AppHandle) -> Result<(DesktopBridgePayload, String), String> {
    let state = app
        .try_state::<Arc<BridgeState>>()
        .ok_or_else(|| "bridge state missing".to_string())?;
    let snap = state.engine.snapshot().await;
    if snap.status != EngineStatus::Ready {
        return Err(format!(
            "engine not ready: {:?}",
            snap.last_error.unwrap_or_else(|| "unknown".into())
        ));
    }
    let engine_base = snap.engine_base_url.clone();
    if engine_base.is_empty() {
        return Err("engine base URL empty after ready".into());
    }
    let engine_token = snap.engine_token.clone();
    let ui_url = resolve_ui_url(&state.official, &engine_base, &engine_token);
    let payload = DesktopBridgePayload {
        is_desktop: true,
        engine_base_url: engine_base,
        engine_token,
        app_version: snap.app_version,
    };
    Ok((payload, ui_url))
}

/// Inject `window.achatDesktop` only — never navigates (safe while chat is open).
pub async fn reinject_bridge(app: &AppHandle) -> Result<(), String> {
    let (payload, _ui) = payload_from_state(app).await?;
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window missing".to_string())?;
    let json = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
    let inject = bridge_inject_script(&json);
    window
        .eval(&inject)
        .map_err(|e| format!("bridge reinject eval failed: {e}"))?;
    log::info!(
        "desktop bridge reinjected engine={}",
        payload.engine_base_url
    );
    Ok(())
}

pub async fn inject_and_navigate(app: &AppHandle) -> Result<(), String> {
    let (payload, ui_url) = payload_from_state(app).await?;

    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window missing".to_string())?;

    let json = serde_json::to_string(&payload).map_err(|e| e.to_string())?;
    let inject = bridge_inject_script(&json);

    // Critical: tauri.conf.json already loads devUrl (http://localhost:3000).
    // Calling navigate() again to the same origin forces a full document reload
    // in WebView2, which tears down EventSource/fetch SSE and looks like the
    // "desktop keeps reloading / SSE disconnects" bug.
    let current = window
        .url()
        .map(|u| u.to_string())
        .unwrap_or_default();
    let need_navigate = !urls_equivalent(&current, &ui_url);

    log::info!(
        "desktop inject ui={ui_url} current={current} navigate={need_navigate} engine={}",
        payload.engine_base_url
    );

    // Inject first so page JS (if already running) sees the bridge immediately.
    let _ = window.eval(&inject);

    if need_navigate {
        window
            .navigate(ui_url.parse().map_err(|e| format!("invalid UI url: {e}"))?)
            .map_err(|e| format!("navigate to UI failed: {e}"))?;

        // After a real navigation, re-inject a few times (document load clears window).
        let win2 = window.clone();
        let inject2 = inject.clone();
        tauri::async_runtime::spawn(async move {
            for delay_ms in [200u64, 600, 1500, 3000] {
                tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
                let _ = win2.eval(&inject2);
            }
        });
    } else {
        // Already on the UI origin — one delayed reinject covers late script wipe only.
        let win2 = window.clone();
        let inject2 = inject.clone();
        tauri::async_runtime::spawn(async move {
            for delay_ms in [300u64, 1200] {
                tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
                let _ = win2.eval(&inject2);
            }
        });
    }

    Ok(())
}

mod open {
    use std::process::Command;

    pub fn that(path: &str) -> std::io::Result<()> {
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            // `start "" path` can flash a console; hide it.
            Command::new("cmd")
                .args(["/C", "start", "", path])
                .creation_flags(CREATE_NO_WINDOW)
                .spawn()?;
            return Ok(());
        }
        #[cfg(target_os = "macos")]
        {
            Command::new("open").arg(path).spawn()?;
            return Ok(());
        }
        #[cfg(not(any(target_os = "windows", target_os = "macos")))]
        {
            Command::new("xdg-open").arg(path).spawn()?;
            Ok(())
        }
    }
}
