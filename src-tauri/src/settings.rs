//! settings.ini ↔ Settings DTO.
//!
//! 우선순위: <user-config-dir>/YCollector/settings.ini → <cwd>/settings.ini.
//! 쓰기는 user-config-dir로만 (cwd의 저장소 settings.ini는 건드리지 않음).

use anyhow::{Context, Result};
use configparser::ini::Ini;
use serde_json::{json, Value};
use std::path::PathBuf;

pub fn user_path() -> Result<PathBuf> {
    let base = dirs::config_dir().context("user config dir not found")?;
    Ok(base.join("YCollector").join("settings.ini"))
}

pub fn read_path() -> Result<Option<PathBuf>> {
    if let Ok(p) = user_path() {
        if p.exists() {
            return Ok(Some(p));
        }
    }
    let cwd_p = std::env::current_dir()?.join("settings.ini");
    if cwd_p.exists() {
        return Ok(Some(cwd_p));
    }
    Ok(None)
}

pub fn load() -> Result<Value> {
    let Some(path) = read_path()? else {
        return Ok(defaults());
    };
    let mut ini = Ini::new();
    ini.load(&path).map_err(|e| anyhow::anyhow!(e))?;
    let g = |sec: &str, key: &str| ini.get(sec, key);
    let s = json!({
        "quality": g("defaults", "quality").unwrap_or_else(|| "1080p".into()),
        "codec": g("defaults", "codec").unwrap_or_else(|| "auto".into()),
        "audio": g("defaults", "audio").unwrap_or_else(|| "best".into()),
        "container": g("defaults", "container").unwrap_or_else(|| "mp4".into()),
        "output_dir": g("output", "output_dir").unwrap_or_else(|| "downloads".into()),
        "embed_subs": parse_bool(g("output", "embed_subs"), true),
        "sub_langs": g("output", "sub_langs").unwrap_or_else(|| "ko,en".into()),
        "cookies_from_browser": g("output", "cookies_from_browser").unwrap_or_default(),
        "playlist_mode": g("playlist", "mode").unwrap_or_else(|| "auto".into()),
        "max_downloads": g("playlist", "max_downloads").unwrap_or_default(),
        "playlist_items": g("playlist", "items").unwrap_or_default(),
        "socket_timeout": parse_int(g("network", "socket_timeout"), 30),
        "retries": parse_int(g("network", "retries"), 10),
        "fragment_retries": parse_int(g("network", "fragment_retries"), 10),
        "throttled_rate": g("network", "throttled_rate").unwrap_or_else(|| "100K".into()),
        "_source": path.display().to_string(),
    });
    Ok(s)
}

pub fn save(v: &Value) -> Result<()> {
    let path = user_path()?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).ok();
    }
    let mut ini = Ini::new();
    let g = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
    let gi = |k: &str| v.get(k).and_then(|x| x.as_i64()).map(|n| n.to_string()).unwrap_or_default();
    let gb = |k: &str| match v.get(k).and_then(|x| x.as_bool()) {
        Some(true) => "true",
        Some(false) => "false",
        None => "true",
    };

    ini.set("defaults", "quality", Some(g("quality")));
    ini.set("defaults", "codec", Some(g("codec")));
    ini.set("defaults", "audio", Some(g("audio")));
    ini.set("defaults", "container", Some(g("container")));

    ini.set("output", "output_dir", Some(g("output_dir")));
    ini.set("output", "embed_subs", Some(gb("embed_subs").to_string()));
    ini.set("output", "sub_langs", Some(g("sub_langs")));
    ini.set("output", "cookies_from_browser", Some(g("cookies_from_browser")));

    ini.set("playlist", "mode", Some(g("playlist_mode")));
    ini.set("playlist", "max_downloads", Some(g("max_downloads")));
    ini.set("playlist", "items", Some(g("playlist_items")));

    ini.set("network", "socket_timeout", Some(gi("socket_timeout")));
    ini.set("network", "retries", Some(gi("retries")));
    ini.set("network", "fragment_retries", Some(gi("fragment_retries")));
    ini.set("network", "throttled_rate", Some(g("throttled_rate")));

    ini.write(&path).map_err(|e| anyhow::anyhow!(e))?;
    Ok(())
}

fn parse_bool(s: Option<String>, default: bool) -> bool {
    s.map(|v| matches!(v.trim().to_lowercase().as_str(), "1" | "true" | "yes" | "on"))
        .unwrap_or(default)
}

fn parse_int(s: Option<String>, default: i64) -> i64 {
    s.and_then(|v| v.trim().parse::<i64>().ok()).unwrap_or(default)
}

fn defaults() -> Value {
    json!({
        "quality": "1080p",
        "codec": "auto",
        "audio": "best",
        "container": "mp4",
        "output_dir": "downloads",
        "embed_subs": true,
        "sub_langs": "ko,en",
        "cookies_from_browser": "",
        "playlist_mode": "auto",
        "max_downloads": "",
        "playlist_items": "",
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "throttled_rate": "100K",
        "_source": null,
    })
}
