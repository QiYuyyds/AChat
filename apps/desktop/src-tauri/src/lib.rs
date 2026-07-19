mod bridge;
mod engine;
mod official;

use tauri::image::Image;
use tauri::Manager;

use crate::bridge::{
    get_engine_status, open_path, restart_engine, select_directory, BridgeState,
};
use crate::engine::EngineManager;
use crate::official::OfficialConfig;

/// Product mark baked at compile time (regenerate via apps/desktop/scripts/generate-icons.py).
const APP_ICON_PNG: &[u8] = include_bytes!("../icons/icon.png");

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            // Ensure window chrome uses the product mark even if an old .exe resource was cached.
            if let Ok(icon) = Image::from_bytes(APP_ICON_PNG) {
                if let Some(window) = app.get_webview_window("main") {
                    if let Err(err) = window.set_icon(icon) {
                        log::warn!("failed to set window icon: {err}");
                    }
                }
            } else {
                log::warn!("failed to decode embedded app icon bytes");
            }

            let official = OfficialConfig::load(app.handle())?;
            let engine = EngineManager::new(app.handle().clone(), official.clone())?;
            let bridge = BridgeState::new(engine.clone(), official.clone());
            app.manage(bridge);

            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                if let Err(err) = engine.start_and_wait_ready().await {
                    log::error!("local engine failed to start: {err}");
                    if let Some(window) = handle.get_webview_window("main") {
                        // Tauri 2 has no load_html; render error page via document.write.
                        let err_js = serde_json::to_string(&err.to_string())
                            .unwrap_or_else(|_| "\"unknown error\"".into());
                        let script = format!(
                            r#"(function(){{
  document.open();
  document.write('<!doctype html><html><body style="font-family:sans-serif;padding:2rem;background:#0a0a0a;color:#f5f5f5"><h1>AChat engine failed to start</h1><p></p><p>Check logs under %APPDATA%\\\\AChat\\\\logs</p></body></html>');
  document.close();
  var p = document.querySelector('p');
  if (p) p.textContent = {err_js};
}})();"#
                        );
                        let _ = window.eval(&script);
                    }
                    return;
                }

                if let Err(err) = bridge::inject_and_navigate(&handle).await {
                    log::error!("failed to inject desktop bridge / navigate: {err}");
                }
            });

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            select_directory,
            open_path,
            get_engine_status,
            restart_engine
        ])
        .run(tauri::generate_context!())
        .expect("error while running AChat desktop");
}
