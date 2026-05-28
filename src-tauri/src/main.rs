// `cargo run --release` 빌드에서 콘솔 창이 뜨지 않도록.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// sidecar 프로토콜 제거: FastAPI HTTP API로 통일.
// mod jobs;     — 작업 레지스트리는 FastAPI 서버가 관리.
// mod settings; — 설정 R/W는 /api/settings가 담당.
// mod sidecar;  — NDJSON sidecar는 더 이상 사용 안 함.

use std::process::Stdio;

use tauri::Manager;

/// Tauri 전역 상태: FastAPI 서버 URL.
pub struct AppState {
    pub api_base: String,
}

#[tauri::command]
fn get_api_base(state: tauri::State<'_, AppState>) -> String {
    state.api_base.clone()
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

fn pick_free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(18765)
}

fn resolve_server_command() -> Option<std::process::Command> {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()));
    let name = if cfg!(windows) { "ycollector-server.exe" } else { "ycollector-server" };
    if let Some(p) = exe_dir.as_ref().map(|d| d.join(name)).filter(|p| p.exists()) {
        return Some(std::process::Command::new(p));
    }
    // PATH fallback
    if std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths).map(|p| p.join(name)).find(|p| p.is_file())
    }).is_some() {
        return Some(std::process::Command::new("ycollector-server"));
    }
    // uv run fallback (dev)
    let mut cmd = std::process::Command::new("uv");
    cmd.args(["run", "ycollector-server"]);
    Some(cmd)
}

fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "ycollector_app=info,tauri=info".into()),
        )
        .init();

    let port = pick_free_port();
    let api_base = format!("http://127.0.0.1:{port}");

    // FastAPI 서버를 백그라운드로 spawn.
    if let Some(mut cmd) = resolve_server_command() {
        cmd.args(["--no-browser", "--port", &port.to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::inherit());
        match cmd.spawn() {
            Ok(child) => {
                std::mem::forget(child);
                tracing::info!("FastAPI server spawned on {api_base}");
            }
            Err(e) => {
                tracing::error!("server spawn 실패: {e}");
            }
        }
    }

    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .setup(move |app| {
            app.manage(AppState { api_base });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_api_base,
            pick_folder,
            open_path,
        ])
        .run(tauri::generate_context!())
        .expect("error while running YCollector app");
}
