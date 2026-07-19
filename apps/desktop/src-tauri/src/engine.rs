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
}

impl EngineManager {
    pub fn new(app: AppHandle, official: OfficialConfig) -> Result<Self, String> {
        let data_dir = default_data_dir()?;
        ensure_data_layout(&data_dir)?;
        let token = generate_engine_token();
        Ok(Self {
            inner: Arc::new(Mutex::new(EngineInner {
                status: EngineStatus::Starting,
                token,
                base_url: None,
                port: None,
                data_dir,
                child: None,
                last_error: None,
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
        self.wait_health(Duration::from_secs(45)).await
    }

    pub async fn restart(&self) -> Result<(), String> {
        self.shutdown().await;
        {
            let mut g = self.inner.lock().await;
            g.status = EngineStatus::Starting;
            g.last_error = None;
            g.token = generate_engine_token();
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
            let allowed = self.official.allowed_origins.join(",");
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
                "--official-api-url".into(),
                self.official.api_url.clone(),
                "--allowed-origins".into(),
                allowed,
            ]);
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

        let mut command = Command::new(&launch.program);
        command
            .args(&launch.args)
            .kill_on_drop(true)
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
        if let Some(cwd) = &launch.cwd {
            command.current_dir(cwd);
        }

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
            tauri::async_runtime::spawn(async move {
                let mut lines = BufReader::new(stderr).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    log::warn!("[engine:err] {line}");
                }
            });
        }

        {
            let mut g = self.inner.lock().await;
            g.child = Some(child);
            g.status = EngineStatus::Starting;
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
                let msg = "local engine health check timed out".to_string();
                let mut g = self.inner.lock().await;
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

fn default_data_dir() -> Result<PathBuf, String> {
    let base = dirs::data_dir().ok_or_else(|| "cannot resolve per-user data dir".to_string())?;
    Ok(base.join("AChat"))
}

fn ensure_data_layout(data_dir: &Path) -> Result<(), String> {
    for rel in ["logs", "sqlite", "runtime", "workspaces"] {
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
    // Explicit override for local development / CI
    if let Ok(custom) = std::env::var("ACHAT_ENGINE_BIN") {
        return Ok(EngineLaunch {
            program: PathBuf::from(custom),
            args: vec![],
            cwd: None,
        });
    }

    // Packaged sidecar first
    if let Ok(resource) = app.path().resource_dir() {
        let candidates = [
            resource.join("resources/engine/achat-engine.exe"),
            resource.join("resources/engine/achat-engine"),
            resource.join("engine/achat-engine.exe"),
        ];
        for c in candidates {
            if c.is_file() {
                return Ok(EngineLaunch {
                    program: c,
                    args: vec![],
                    cwd: None,
                });
            }
        }
    }

    // Dev: prefer backend/.venv python, then PATH python, run -m app.desktop.cli
    let repo_backend = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .join("backend");
    if repo_backend.is_dir() {
        let py = which_python_for_backend(&repo_backend)?;
        return Ok(EngineLaunch {
            program: py,
            args: vec!["-m".into(), "app.desktop.cli".into()],
            cwd: Some(repo_backend),
        });
    }

    Err(
        "local engine binary not found; build engine package or set ACHAT_ENGINE_BIN".into(),
    )
}

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
