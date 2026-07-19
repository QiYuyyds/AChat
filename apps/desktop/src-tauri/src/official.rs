use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

/// Packaged desktop config (infra defaults + optional legacy web/api fields).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OfficialConfig {
    #[serde(default)]
    pub web_url: String,
    #[serde(default)]
    pub api_url: String,
    #[serde(default)]
    pub allowed_origins: Vec<String>,
    #[serde(default)]
    pub update_feed_url: String,
    #[serde(default)]
    pub flavor: String,
    #[serde(default)]
    pub infra: serde_json::Value,
    #[serde(default)]
    pub feature_flags: FeatureFlags,
    #[serde(default)]
    pub ui_dir: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FeatureFlags {
    #[serde(default = "default_true")]
    pub direct_infra: bool,
    #[serde(default)]
    pub cloud_api_client: bool,
}

fn default_true() -> bool {
    true
}

impl Default for FeatureFlags {
    fn default() -> Self {
        Self {
            direct_infra: true,
            cloud_api_client: false,
        }
    }
}

impl OfficialConfig {
    pub fn load(app: &AppHandle) -> Result<Self, String> {
        let candidates = [
            resource_path(app, "infra.default.json"),
            resource_path(app, "official.json"),
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("infra.default.json"),
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("official.json"),
        ];

        for path in candidates {
            if path.is_file() {
                let raw = fs::read_to_string(&path)
                    .map_err(|e| format!("read config at {}: {e}", path.display()))?;
                let cfg: OfficialConfig = serde_json::from_str(&raw)
                    .map_err(|e| format!("parse config at {}: {e}", path.display()))?;
                log::info!("loaded desktop config from {}", path.display());
                return Ok(cfg);
            }
        }

        // Dev-friendly empty defaults (engine may still get DATABASE_URL from env)
        log::warn!("no infra.default.json / official.json found; using empty defaults");
        Ok(OfficialConfig {
            web_url: String::new(),
            api_url: String::new(),
            allowed_origins: vec![],
            update_feed_url: String::new(),
            flavor: "dev".into(),
            infra: serde_json::json!({}),
            feature_flags: FeatureFlags::default(),
            ui_dir: String::new(),
        })
    }

    /// Path to pass as `--infra-config` to the engine (prefer infra.default.json).
    pub fn infra_config_file(app: &AppHandle) -> Option<PathBuf> {
        let candidates = [
            resource_path(app, "infra.default.json"),
            resource_path(app, "official.json"),
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("infra.default.json"),
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("official.json"),
        ];
        candidates.into_iter().find(|p| p.is_file())
    }
}

fn resource_path(app: &AppHandle, name: &str) -> PathBuf {
    app.path()
        .resource_dir()
        .map(|p| p.join(name))
        .unwrap_or_else(|_| PathBuf::from(name))
}
