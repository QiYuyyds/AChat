use crate::official::OfficialConfig;
use rand::RngCore;
use serde::Serialize;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;
use tauri::{AppHandle, Manager};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;
use tokio::time::{sleep, timeout};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum EngineStatus {
    Starting,
    Ready,
    Error,
}

#[derive(Clone)]
pub struct EngineManager {
    inner: Arc<Mutex<EngineInner>>,
    app: AppHandle,
    official: OfficialConfig,
}

struct EngineInner {
    status: EngineStatus,
    token: String,
    base_url: Option<String>,
    port: Option<u16>,
    data_dir: PathBuf,
    child: Option<Child>,
    last_error: Option<String>,
    /// Recent stderr lines from the engine process (for health-timeout diagnosis).
    recent_stderr: Vec<String>,
}

impl EngineManager {
    pub fn new(app: AppHandle, official: OfficialConfig) -> Result<Self, String> {
        let data_dir = default_data_dir()?;
        ensure_data_layout(&data_dir)?;
        // Stable per-machine token under %APPDATA%/AChat so UI sessionStorage /
        // late inject never drifts from the engine after close+reopen.
        let token = load_or_create_engine_token(&data_dir)?;
        Ok(Self {
            inner: Arc::new(Mutex::new(EngineInner {
                status: EngineStatus::Starting,
                token,
                base_url: None,
                port: None,
                data_dir,
                child: None,
                last_error: None,
                recent_stderr: Vec::new(),
            })),
            app,
            official,
        })
    }

    pub async fn snapshot(&self) -> EngineSnapshot {
        let g = self.inner.lock().await;
        EngineSnapshot {
            status: g.status,
            engine_base_url: g.base_url.clone().unwrap_or_default(),
            engine_token: g.token.clone(),
            app_version: self.app.package_info().version.to_string(),
            last_error: g.last_error.clone(),
            data_dir: g.data_dir.display().to_string(),
        }
    }

    pub async fn start_and_wait_ready(&self) -> Result<(), String> {
        self.spawn_process().await?;
        // Remote Postgres / first-time create_all can take >45s on cold network.
        self.wait_health(Duration::from_secs(90)).await
    }

    pub async fn restart(&self) -> Result<(), String> {
        self.shutdown().await;
        {
            let mut g = self.inner.lock().await;
            g.status = EngineStatus::Starting;
            g.last_error = None;
            g.recent_stderr.clear();
            // Keep the same stable token so open pages keep working.
            g.base_url = None;
            g.port = None;
        }
        self.start_and_wait_ready().await
    }

    pub async fn shutdown(&self) {
        let mut g = self.inner.lock().await;
        if let Some(mut child) = g.child.take() {
            let _ = child.kill().await;
            let _ = child.wait().await;
        }
        g.status = EngineStatus::Error;
        g.base_url = None;
        g.port = None;
    }

    async fn spawn_process(&self) -> Result<(), String> {
        let (data_dir, token, launch) = {
            let g = self.inner.lock().await;
            let launch = resolve_engine_launch(&self.app)?;
            let mut args = launch.args;
            args.extend([
                "serve".to_string(),
                "--bind".into(),
                "127.0.0.1".into(),
                "--port".into(),
                "0".into(),
                "--data-dir".into(),
                g.data_dir.display().to_string(),
                "--engine-token".into(),
                g.token.clone(),
            ]);
            if let Some(infra) = crate::official::OfficialConfig::infra_config_file(&self.app) {
                args.push("--infra-config".into());
                args.push(infra.display().to_string());
            }
            if let Some(ui) = resolve_ui_dir(&self.app) {
                args.push("--ui-dir".into());
                args.push(ui.display().to_string());
            }
            if !self.official.allowed_origins.is_empty() {
                args.push("--allowed-origins".into());
                args.push(self.official.allowed_origins.join(","));
            }
            // Legacy optional official API (only if present in config)
            if !self.official.api_url.is_empty() {
                args.push("--official-api-url".into());
                args.push(self.official.api_url.clone());
            }
            (
                g.data_dir.clone(),
                g.token.clone(),
                EngineLaunch {
                    program: launch.program,
                    args,
                    cwd: launch.cwd,
                },
            )
        };

        log::info!(
            "spawning local engine: {:?} {:?} cwd={:?}",
            launch.program,
            launch.args,
            launch.cwd
        );

        // Prefer launching the windowed sidecar with no console. On Windows apply
        // CREATE_NO_WINDOW last (via std CommandExt) so tokio does not drop the flags.
        let mut std_cmd = std::process::Command::new(&launch.program);
        std_cmd
            .args(&launch.args)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        if let Some(cwd) = &launch.cwd {
            std_cmd.current_dir(cwd);
        } else if let Some(parent) = launch.program.parent() {
            // Resolve native DLLs next to achat-engine.exe quietly.
            std_cmd.current_dir(parent);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // CREATE_NO_WINDOW = no console allocation even if the PE is CUI.
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            std_cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut command = Command::from(std_cmd);
        command.kill_on_drop(true);

        let mut child = command
            .spawn()
            .map_err(|e| format!("failed to spawn engine {:?}: {e}", launch.program))?;

        // Drain stdout for port handshake lines: ENGINE_PORT=<n>
        if let Some(stdout) = child.stdout.take() {
            let data_dir_clone = data_dir.clone();
            let inner = self.inner.clone();
            tauri::async_runtime::spawn(async move {
                let mut lines = BufReader::new(stdout).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    log::info!("[engine] {line}");
                    if let Some(port_str) = line.strip_prefix("ENGINE_PORT=") {
                        if let Ok(port) = port_str.trim().parse::<u16>() {
                            let mut g = inner.lock().await;
                            g.port = Some(port);
                            g.base_url = Some(format!("http://127.0.0.1:{port}"));
                            let handshake = data_dir_clone.join("runtime").join("engine.json");
                            let body = serde_json::json!({
                                "port": port,
                                "pid": std::process::id(),
                                "tokenPresent": true,
                            });
                            let _ = std::fs::write(handshake, body.to_string());
                        }
                    }
                }
            });
        }

        if let Some(stderr) = child.stderr.take() {
            let inner = self.inner.clone();
            tauri::async_runtime::spawn(async move {
                let mut lines = BufReader::new(stderr).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    log::warn!("[engine:err] {line}");
                    let mut g = inner.lock().await;
                    g.recent_stderr.push(line);
                    // Keep a short ring so health-timeout messages stay readable.
                    const MAX_STDERR_LINES: usize = 40;
                    if g.recent_stderr.len() > MAX_STDERR_LINES {
                        let drain = g.recent_stderr.len() - MAX_STDERR_LINES;
                        g.recent_stderr.drain(0..drain);
                    }
                }
            });
        }

        {
            let mut g = self.inner.lock().await;
            g.child = Some(child);
            g.status = EngineStatus::Starting;
            g.recent_stderr.clear();
            let _ = token; // kept for future handshake validation
        }

        // Fallback: poll runtime/engine.json written by Python side
        for _ in 0..50 {
            if let Some(port) = read_port_from_handshake(&data_dir) {
                let mut g = self.inner.lock().await;
                g.port = Some(port);
                g.base_url = Some(format!("http://127.0.0.1:{port}"));
                break;
            }
            sleep(Duration::from_millis(100)).await;
        }

        Ok(())
    }

    async fn wait_health(&self, max_wait: Duration) -> Result<(), String> {
        let started = std::time::Instant::now();
        loop {
            let (base, token) = {
                let g = self.inner.lock().await;
                (g.base_url.clone(), g.token.clone())
            };

            if let Some(base_url) = base {
                let url = format!("{base_url}/healthz");
                let client = reqwest::Client::new();
                let req = client
                    .get(&url)
                    .header("X-Engine-Token", &token)
                    .timeout(Duration::from_secs(2));
                match timeout(Duration::from_secs(2), req.send()).await {
                    Ok(Ok(resp)) if resp.status().is_success() => {
                        let mut g = self.inner.lock().await;
                        g.status = EngineStatus::Ready;
                        g.last_error = None;
                        return Ok(());
                    }
                    Ok(Ok(resp)) => {
                        log::debug!("healthz status {}", resp.status());
                    }
                    Ok(Err(err)) => log::debug!("healthz error: {err}"),
                    Err(_) => log::debug!("healthz timeout"),
                }
            }

            if started.elapsed() > max_wait {
                // Prefer a still-running child's recent stderr over a generic timeout —
                // WinError 10106 / missing engine binary / DB failures all land here.
                let mut g = self.inner.lock().await;
                let mut msg = "local engine health check timed out".to_string();
                if !g.recent_stderr.is_empty() {
                    let tail: Vec<&str> = g
                        .recent_stderr
                        .iter()
                        .rev()
                        .take(8)
                        .map(String::as_str)
                        .collect();
                    let joined = tail.into_iter().rev().collect::<Vec<_>>().join(" | ");
                    msg = format!("{msg}: {joined}");
                } else if g.port.is_none() {
                    msg = format!(
                        "{msg}: engine never published ENGINE_PORT (process may have crashed on startup; see %APPDATA%\\\\AChat\\\\logs)"
                    );
                }
                g.status = EngineStatus::Error;
                g.last_error = Some(msg.clone());
                return Err(msg);
            }
            sleep(Duration::from_millis(250)).await;
        }
    }
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineSnapshot {
    pub status: EngineStatus,
    pub engine_base_url: String,
    pub engine_token: String,
    pub app_version: String,
    pub last_error: Option<String>,
    pub data_dir: String,
}

fn generate_engine_token() -> String {
    let mut bytes = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Load stable engine token from data_dir/config/engine.token (create once).
/// Loopback + Origin middleware still apply; rotating every launch caused
/// "Invalid engine token" when WebView kept a previous sessionStorage token.
fn load_or_create_engine_token(data_dir: &Path) -> Result<String, String> {
    let path = data_dir.join("config").join("engine.token");
    if let Ok(existing) = std::fs::read_to_string(&path) {
        let t = existing.trim().to_string();
        if t.len() >= 32 {
            return Ok(t);
        }
    }
    let token = generate_engine_token();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("create {}: {e}", parent.display()))?;
    }
    std::fs::write(&path, &token).map_err(|e| format!("write engine token: {e}"))?;
    Ok(token)
}

fn default_data_dir() -> Result<PathBuf, String> {
    let base = dirs::data_dir().ok_or_else(|| "cannot resolve per-user data dir".to_string())?;
    Ok(base.join("AChat"))
}

fn ensure_data_layout(data_dir: &Path) -> Result<(), String> {
    for rel in ["logs", "sqlite", "runtime", "workspaces", "config"] {
        let p = data_dir.join(rel);
        std::fs::create_dir_all(&p)
            .map_err(|e| format!("create {}: {e}", p.display()))?;
    }
    Ok(())
}

fn read_port_from_handshake(data_dir: &Path) -> Option<u16> {
    let path = data_dir.join("runtime").join("engine.json");
    let raw = std::fs::read_to_string(path).ok()?;
    let v: serde_json::Value = serde_json::from_str(&raw).ok()?;
    v.get("port")?.as_u64().map(|p| p as u16)
}

struct EngineLaunch {
    program: PathBuf,
    args: Vec<String>,
    cwd: Option<PathBuf>,
}

fn resolve_engine_launch(app: &AppHandle) -> Result<EngineLaunch, String> {
    // Explicit override for local development / CI.
    // In release, refuse python.exe — it is a console subsystem binary and flashes a black cmd.
    if let Ok(custom) = std::env::var("ACHAT_ENGINE_BIN") {
        let custom_path = PathBuf::from(&custom);
        #[cfg(not(dev))]
        {
            let name = custom_path
                .file_name()
                .and_then(|s| s.to_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            if name == "python.exe"
                || name == "python"
                || name == "pythonw.exe"
                || name == "py.exe"
                || name == "py"
            {
                return Err(
                    "ACHAT_ENGINE_BIN points at Python; release builds require achat-engine.exe (GUI, no console flash)".into(),
                );
            }
        }
        if let Ok(extra) = std::env::var("ACHAT_ENGINE_ARGS") {
            let args: Vec<String> = extra.split_whitespace().map(|s| s.to_string()).collect();
            return Ok(EngineLaunch {
                program: custom_path,
                args,
                cwd: std::env::var("ACHAT_ENGINE_CWD").ok().map(PathBuf::from),
            });
        }
        return Ok(EngineLaunch {
            program: custom_path,
            args: vec![],
            cwd: None,
        });
    }

    // Dev builds: prefer repo Python for iteration (console is acceptable in `tauri dev`).
    #[cfg(dev)]
    {
        let repo_backend = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../..")
            .join("backend");
        if repo_backend.is_dir() {
            let py = which_python_for_backend(&repo_backend)?;
            log::info!(
                "dev mode: launching engine via python -m app.desktop.cli ({})",
                py.display()
            );
            return Ok(EngineLaunch {
                program: py,
                args: vec!["-m".into(), "app.desktop.cli".into()],
                cwd: Some(repo_backend),
            });
        }
    }

    // Release / packaged: only the windowed sidecar. Never fall back to Python
    // (that is the #1 cause of a black cmd flash for end users).
    for c in packaged_engine_candidates(app) {
        if c.is_file() {
            log::info!("using packaged engine sidecar: {}", c.display());
            return Ok(EngineLaunch {
                program: c,
                args: vec![],
                cwd: None,
            });
        }
    }

    Err(
        "local engine binary not found under install resources; rebuild package with engine sidecar (achat-engine.exe)".into(),
    )
}

// Only referenced from the #[cfg(dev)] branch of resolve_engine_launch.
#[cfg(dev)]
fn which_python_for_backend(backend: &Path) -> Result<PathBuf, String> {
    let venv_candidates = [
        backend.join(".venv/Scripts/python.exe"),
        backend.join(".venv/bin/python"),
        backend.join("venv/Scripts/python.exe"),
        backend.join("venv/bin/python"),
    ];
    for c in venv_candidates {
        if c.is_file() {
            log::info!("using backend venv python: {}", c.display());
            return Ok(c);
        }
    }
    which_python()
}

#[cfg(dev)]
fn which_python() -> Result<PathBuf, String> {
    for name in ["python", "python3", "py"] {
        if let Ok(output) = std::process::Command::new(name).arg("--version").output() {
            if output.status.success() {
                return Ok(PathBuf::from(name));
            }
        }
    }
    Err("python not found on PATH for desktop engine dev mode (create backend/.venv or set ACHAT_ENGINE_BIN)".into())
}

fn packaged_engine_candidates(app: &AppHandle) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(resource) = app.path().resource_dir() {
        out.push(resource.join("resources/engine/achat-engine.exe"));
        out.push(resource.join("resources/engine/achat-engine"));
        out.push(resource.join("engine/achat-engine.exe"));
        out.push(resource.join("engine/achat-engine"));
    }
    // NSIS currentUser layout: <install>/resources/engine/achat-engine.exe
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            out.push(dir.join("resources/engine/achat-engine.exe"));
            out.push(dir.join("resources/engine/achat-engine"));
            out.push(dir.join("engine/achat-engine.exe"));
        }
    }
    out
}

fn resolve_ui_dir(app: &AppHandle) -> Option<PathBuf> {
    if let Ok(custom) = std::env::var("ACHAT_UI_DIR") {
        let p = PathBuf::from(custom);
        if p.is_dir() {
            log::info!("UI dir from ACHAT_UI_DIR: {}", p.display());
            return Some(p);
        }
    }

    let mut candidates: Vec<PathBuf> = Vec::new();
    if let Ok(resource) = app.path().resource_dir() {
        candidates.push(resource.join("resources/ui"));
        candidates.push(resource.join("ui"));
    }
    // Same install layout as engine: <install>/resources/ui
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("resources/ui"));
            candidates.push(dir.join("ui"));
        }
    }

    for c in &candidates {
        if c.is_dir() && c.join("index.html").is_file() {
            // Refuse the repo bootstrap placeholder even if somehow on path
            if let Ok(html) = std::fs::read_to_string(c.join("index.html")) {
                if html.contains("引擎占位页") || html.contains("不是完整聊天 UI") {
                    log::warn!("skipping placeholder UI at {}", c.display());
                    continue;
                }
            }
            log::info!("using packaged UI dir: {}", c.display());
            return Some(c.clone());
        }
    }

    // Dev-only: allow repo bootstrap / resources for `tauri dev`
    #[cfg(dev)]
    {
        let full = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../resources/ui");
        if full.is_dir() && full.join("index.html").is_file() {
            log::info!("dev UI dir: {}", full.display());
            return Some(full);
        }
        let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../ui");
        if dev.is_dir() {
            log::warn!("dev fallback UI (placeholder): {}", dev.display());
            return Some(dev);
        }
    }

    log::error!("no packaged UI dir found beside the desktop shell");
    None
}
