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
pub async fn restart_engine(state: State<'_, Arc<BridgeState>>) -> Result<(), String> {
    state.engine.restart().await
}

pub async fn inject_and_navigate(app: &AppHandle) -> Result<(), String> {
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

    let payload = DesktopBridgePayload {
        is_desktop: true,
        engine_base_url: snap.engine_base_url,
        engine_token: snap.engine_token,
        app_version: snap.app_version,
    };

    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "main window missing".to_string())?;

    // Navigate to official frontend first, then inject bridge on page load.
    let web_url = state.official.web_url.clone();
    let json = serde_json::to_string(&payload).map_err(|e| e.to_string())?;

    // Use initialization script so the object exists before page JS runs.
    // Tauri 2: eval after navigation as a reliable fallback.
    window
        .eval(&format!(
            r#"(function() {{
  const payload = {json};
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
}})();"#
        ))
        .map_err(|e| format!("inject eval failed: {e}"))?;

    window
        .navigate(web_url.parse().map_err(|e| format!("invalid webUrl: {e}"))?)
        .map_err(|e| format!("navigate failed: {e}"))?;

    // Re-inject after load (remote page may not have seen prior eval).
    let win2 = window.clone();
    let json2 = json.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(800)).await;
        let _ = win2.eval(&format!(
            r#"(function() {{
  if (window.achatDesktop && window.achatDesktop.isDesktop) return;
  const payload = {json2};
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
}})();"#
        ));
    });

    Ok(())
}

// Minimal open crate substitute via std::process on Windows
mod open {
    use std::process::Command;

    pub fn that(path: &str) -> std::io::Result<()> {
        #[cfg(target_os = "windows")]
        {
            Command::new("cmd")
                .args(["/C", "start", "", path])
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
