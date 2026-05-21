//! 진행 중 작업 레지스트리 + 완료 항목 manifest.
//!
//! 라이브러리 백엔드는 Phase 2에서 SQLite+FTS5로 교체 예정.
//! 본 모듈의 `library::read_manifest` 시그니처는 그 시점에 그대로 유지한다.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Mutex;

#[derive(Debug, Default)]
pub struct JobRegistry {
    inner: Mutex<HashMap<String, JobRow>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobRow {
    pub url: String,
    pub status: String, // "running" | "cancelled" | "done" | "failed"
}

impl JobRegistry {
    pub fn register(&self, job_id: &str, url: &str) {
        if let Ok(mut g) = self.inner.lock() {
            g.insert(
                job_id.to_string(),
                JobRow {
                    url: url.to_string(),
                    status: "running".into(),
                },
            );
        }
    }
    pub fn mark_cancelled(&self, job_id: &str) {
        if let Ok(mut g) = self.inner.lock() {
            if let Some(row) = g.get_mut(job_id) {
                row.status = "cancelled".into();
            }
        }
    }
}

/// 라이브러리 manifest (JSON, Phase 1 임시).
///
/// 경로: <user-config-dir>/YCollector/library.json
/// Phase 2에서 SQLite+FTS5로 마이그레이션. 이 파일은 그 시점에 read-only로 읽어
/// 일회성 import 한 뒤 archive/library.json.bak으로 옮긴다.
pub mod library {
    use anyhow::{Context, Result};
    use serde_json::Value;
    use std::path::PathBuf;

    pub fn manifest_path() -> Result<PathBuf> {
        let base = if cfg!(windows) {
            dirs::config_dir().context("APPDATA dir not found")?
        } else if cfg!(target_os = "macos") {
            dirs::config_dir().context("Application Support dir not found")?
        } else {
            dirs::config_dir().context("XDG config dir not found")?
        };
        Ok(base.join("YCollector").join("library.json"))
    }

    pub fn read_manifest() -> Result<Value> {
        let p = manifest_path()?;
        if !p.exists() {
            return Ok(serde_json::json!({"version": 1, "items": []}));
        }
        let text = std::fs::read_to_string(&p)
            .with_context(|| format!("manifest 읽기 실패: {}", p.display()))?;
        let v: Value = serde_json::from_str(&text).unwrap_or_else(|_| {
            serde_json::json!({"version": 1, "items": []})
        });
        Ok(v)
    }

    pub fn append_item(item: Value) -> Result<()> {
        let p = manifest_path()?;
        if let Some(parent) = p.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let mut v = read_manifest()?;
        if let Some(items) = v.get_mut("items").and_then(|x| x.as_array_mut()) {
            items.push(item);
        }
        std::fs::write(&p, serde_json::to_string_pretty(&v)?)?;
        Ok(())
    }
}
