// `cargo run --release` 빌드에서 콘솔 창이 뜨지 않도록.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod jobs;
mod settings;
mod sidecar;

use std::sync::Arc;

use tauri::{Emitter, Manager};

use crate::jobs::JobRegistry;
use crate::sidecar::SidecarHandle;

/// Tauri 전역 상태: sidecar 핸들 + job 레지스트리.
pub struct AppState {
    pub sidecar: tokio::sync::Mutex<Option<SidecarHandle>>,
    pub jobs: Arc<JobRegistry>,
}

#[tauri::command]
async fn probe(
    url: String,
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<String, String> {
    let job_id = uuid::Uuid::new_v4().to_string();
    let mut guard = state.sidecar.lock().await;
    let handle = ensure_sidecar(&mut guard, &app).map_err(|e| e.to_string())?;
    handle
        .send_op(serde_json::json!({
            "op": "probe",
            "job_id": job_id,
            "url": url,
        }))
        .await
        .map_err(|e| e.to_string())?;
    Ok(job_id)
}

#[tauri::command]
async fn start_job(
    url: String,
    settings: serde_json::Value,
    state: tauri::State<'_, AppState>,
    app: tauri::AppHandle,
) -> Result<String, String> {
    let job_id = uuid::Uuid::new_v4().to_string();
    state.jobs.register(&job_id, &url);
    let mut guard = state.sidecar.lock().await;
    let handle = ensure_sidecar(&mut guard, &app).map_err(|e| e.to_string())?;
    handle
        .send_op(serde_json::json!({
            "op": "download",
            "job_id": job_id,
            "url": url,
            "settings": settings,
        }))
        .await
        .map_err(|e| e.to_string())?;
    Ok(job_id)
}

#[tauri::command]
async fn cancel(
    job_id: String,
    state: tauri::State<'_, AppState>,
) -> Result<(), String> {
    let guard = state.sidecar.lock().await;
    if let Some(handle) = guard.as_ref() {
        handle
            .send_op(serde_json::json!({"op": "cancel", "job_id": job_id}))
            .await
            .map_err(|e| e.to_string())?;
    }
    state.jobs.mark_cancelled(&job_id);
    Ok(())
}

#[tauri::command]
fn load_settings() -> Result<serde_json::Value, String> {
    settings::load().map_err(|e| e.to_string())
}

#[tauri::command]
fn save_settings(settings: serde_json::Value) -> Result<(), String> {
    settings::save(&settings).map_err(|e| e.to_string())
}

#[tauri::command]
async fn pick_folder(
    app: tauri::AppHandle,
    start: Option<String>,
) -> Result<Option<String>, String> {
    use tauri_plugin_dialog::DialogExt;
    let (tx, rx) = tokio::sync::oneshot::channel();
    let mut builder = app.dialog().file();
    if let Some(s) = start {
        builder = builder.set_directory(s);
    }
    builder.pick_folder(move |p| {
        let _ = tx.send(p.map(|fp| fp.to_string()));
    });
    rx.await.map_err(|e| e.to_string())
}

#[tauri::command]
fn open_path(path: String, app: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_shell::ShellExt;
    app.shell()
        .open(path, None)
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn list_library() -> Result<serde_json::Value, String> {
    crate::jobs::library::read_manifest().map_err(|e| e.to_string())
}

fn ensure_sidecar(
    guard: &mut tokio::sync::MutexGuard<'_, Option<SidecarHandle>>,
    app: &tauri::AppHandle,
) -> anyhow::Result<&mut SidecarHandle> {
    if guard.is_none() {
        let handle = SidecarHandle::spawn(app.clone())?;
        **guard = Some(handle);
    }
    Ok(guard.as_mut().unwrap())
}

fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "ycollector_app=info,tauri=info".into()),
        )
        .init();

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let state = AppState {
                sidecar: tokio::sync::Mutex::new(None),
                jobs: Arc::new(JobRegistry::default()),
            };
            app.manage(state);
            // 부팅 직후 sidecar는 lazy spawn. 첫 invoke에서 띄움.
            let _ = app.emit("app:ready", serde_json::json!({"ok": true}));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            probe,
            start_job,
            cancel,
            load_settings,
            save_settings,
            pick_folder,
            open_path,
            list_library,
        ])
        .run(tauri::generate_context!())
        .expect("error while running YCollector app");
}
