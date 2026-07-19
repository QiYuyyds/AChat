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
    // Release: log to %APPDATA%/AChat/logs so we never need a console window.
    // Dev: keep stderr logging for `tauri dev`.
    #[cfg(debug_assertions)]
    {
        env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    }
    #[cfg(not(debug_assertions))]
    {
        init_release_file_logger();
    }

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

            // Window starts hidden (tauri.conf visible:false). Paint warm splash into the
            // off-screen webview so the first frame after show() is already correct —
            // never flash a blank / intermediate page or a console-like popup.
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval(
                    r#"(function(){
  try {
    document.open();
    document.write('<!doctype html><html><head><meta charset="utf-8"/><style>:root{--bg:oklch(0.985 0.006 75);--fg:oklch(0.52 0.012 65);--ring:oklch(0.90 0.010 75)}html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,\"Segoe UI\",sans-serif}main{min-height:100%;display:grid;place-items:center}.wrap{display:flex;flex-direction:column;align-items:center;gap:.875rem}.s{width:24px;height:24px;border:2px solid var(--ring);border-top-color:var(--fg);border-radius:50%;animation:spin .75s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}p{margin:0;font-size:.8125rem;letter-spacing:.01em}</style></head><body><main><div class="wrap" role="status" aria-label="正在启动"><div class="s"></div><p>正在启动</p></div></main></body></html>');
    document.close();
  } catch (_) {}
})();"#,
                );
            }

            let official = OfficialConfig::load(app.handle())?;
            let engine = EngineManager::new(app.handle().clone(), official.clone())?;
            let bridge = BridgeState::new(engine.clone(), official.clone());
            app.manage(bridge);

            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let show_main = |h: &tauri::AppHandle| {
                    if let Some(window) = h.get_webview_window("main") {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                };

                if let Err(err) = engine.start_and_wait_ready().await {
                    log::error!("local engine failed to start: {err}");
                    if let Some(window) = handle.get_webview_window("main") {
                        let err_js = serde_json::to_string(&err.to_string())
                            .unwrap_or_else(|_| "\"unknown error\"".into());
                        let script = format!(
                            r#"(function(){{
  document.open();
  document.write('<!doctype html><html><body style="font-family:system-ui,sans-serif;padding:2rem;background:oklch(0.985 0.006 75);color:oklch(0.22 0.012 60)"><h1 style="font-size:1.15rem;font-weight:600">启动失败</h1><pre id="e" style="color:oklch(0.52 0.012 65);white-space:pre-wrap;word-break:break-word;max-width:40rem;font:12px/1.45 ui-monospace,Consolas,monospace"></pre><p style="color:oklch(0.52 0.012 65);font-size:.9rem">请查看 %APPDATA%\\\\AChat\\\\logs\\\\desktop.log 与 engine-crash.log。若日志含 WinError 10106，需用最新 build_engine_windows.ps1 重新打包本机引擎（剥离 Anaconda UCRT 转发 DLL）。</p></body></html>');
  document.close();
  var p = document.getElementById('e');
  if (p) p.textContent = {err_js};
}})();"#
                        );
                        let _ = window.eval(&script);
                    }
                    show_main(&handle);
                    return;
                }

                if let Err(err) = bridge::inject_and_navigate(&handle).await {
                    log::error!("failed to inject desktop bridge / navigate: {err}");
                    // Still show window so user is not stuck with an invisible process.
                    show_main(&handle);
                    return;
                }

                // Give the first navigation a beat to paint before revealing the window.
                tokio::time::sleep(std::time::Duration::from_millis(120)).await;
                show_main(&handle);
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

/// File logger for release builds — never requires a console window.
#[cfg(not(debug_assertions))]
fn init_release_file_logger() {
    use std::fs::OpenOptions;

    let log_path = dirs::data_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join("AChat")
        .join("logs");
    let _ = std::fs::create_dir_all(&log_path);
    let file_path = log_path.join("desktop.log");

    match OpenOptions::new()
        .create(true)
        .append(true)
        .open(&file_path)
    {
        Ok(f) => {
            let target = std::sync::Mutex::new(f);
            env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
                .target(env_logger::Target::Pipe(Box::new(MutexWriter(target))))
                .init();
        }
        Err(_) => {
            // Swallow logs rather than open a console.
            env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("off")).init();
        }
    }
}

#[cfg(not(debug_assertions))]
struct MutexWriter(std::sync::Mutex<std::fs::File>);

#[cfg(not(debug_assertions))]
impl std::io::Write for MutexWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let mut g = self
            .0
            .lock()
            .map_err(|_| std::io::Error::other("log mutex poisoned"))?;
        std::io::Write::write(&mut *g, buf)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        let mut g = self
            .0
            .lock()
            .map_err(|_| std::io::Error::other("log mutex poisoned"))?;
        std::io::Write::flush(&mut *g)
    }
}
