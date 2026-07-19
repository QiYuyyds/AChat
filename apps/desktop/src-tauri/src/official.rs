use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OfficialConfig {
    pub web_url: String,
    pub api_url: String,
    pub allowed_origins: Vec<String>,
    #[serde(default)]
    pub update_feed_url: String,
    #[serde(default)]
    pub flavor: String,
}

impl OfficialConfig {
    pub fn load(app: &AppHandle) -> Result<Self, String> {
        let candidates = [
            resource_path(app, "official.json"),
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("official.json"),
        ];

        for path in candidates {
            if path.is_file() {
                let raw = fs::read_to_string(&path)
                    .map_err(|e| format!("read official.json at {}: {e}", path.display()))?;
                let cfg: OfficialConfig = serde_json::from_str(&raw)
                    .map_err(|e| format!("parse official.json at {}: {e}", path.display()))?;
                if cfg.web_url.is_empty() || cfg.api_url.is_empty() {
                    return Err("official.json requires webUrl and apiUrl".into());
                }
                if cfg.allowed_origins.is_empty() {
                    return Err("official.json requires non-empty allowedOrigins".into());
                }
                return Ok(cfg);
            }
        }

        Err("official.json not found in package resources".into())
    }
}

fn resource_path(app: &AppHandle, name: &str) -> PathBuf {
    app.path()
        .resource_dir()
        .map(|p| p.join(name))
        .unwrap_or_else(|_| PathBuf::from(name))
}
