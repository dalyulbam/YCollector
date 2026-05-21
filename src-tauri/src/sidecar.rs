//! Python sidecar (ycollector CLI) spawn + NDJSON pump.
//!
//! 한 sidecar 프로세스가 stdin으로 NDJSON 명령을 받고,
//! stdout으로 NDJSON 이벤트를 흘려보낸다. Rust는 stdout 라인을 파싱해
//! webview로 emit한다. stderr는 사용자 가시 로그.

use anyhow::{anyhow, Context, Result};
use serde_json::Value;
use std::process::Stdio;
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{ChildStdin, Command};
use tokio::sync::Mutex;

pub struct SidecarHandle {
    stdin: Mutex<ChildStdin>,
}

impl SidecarHandle {
    pub fn spawn(app: AppHandle) -> Result<Self> {
        let mut command = resolve_command()?;
        let mut child = command
            .arg("--json")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .context("ycollector sidecar spawn 실패")?;

        let stdout = child.stdout.take().context("sidecar stdout 없음")?;
        let stderr = child.stderr.take().context("sidecar stderr 없음")?;
        let stdin = child.stdin.take().context("sidecar stdin 없음")?;

        // stdout pump → 이벤트 emit
        {
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                let mut reader = BufReader::new(stdout).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    let trimmed = line.trim();
                    if trimmed.is_empty() {
                        continue;
                    }
                    match serde_json::from_str::<Value>(trimmed) {
                        Ok(v) => {
                            let ev = v
                                .get("event")
                                .and_then(|e| e.as_str())
                                .unwrap_or("unknown")
                                .to_string();
                            // 'done' 이벤트는 라이브러리 manifest에 추가.
                            // (실패해도 emit은 그대로 진행 — 라이브러리는 best-effort)
                            if ev == "done" {
                                if let Err(e) = append_done_to_library(&v) {
                                    tracing::warn!("library append 실패: {e}");
                                }
                            }
                            let _ = app.emit(&format!("job:{ev}"), v);
                        }
                        Err(_) => {
                            let _ = app.emit(
                                "job:log",
                                serde_json::json!({
                                    "event": "log",
                                    "level": "raw",
                                    "line": trimmed,
                                }),
                            );
                        }
                    }
                }
                let _ = app.emit("sidecar:exit", serde_json::json!({"reason": "stdout-eof"}));
            });
        }

        // stderr pump → 로그 이벤트
        {
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                let mut reader = BufReader::new(stderr).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    let _ = app.emit(
                        "job:log",
                        serde_json::json!({
                            "event": "log",
                            "level": "stderr",
                            "line": line,
                        }),
                    );
                }
            });
        }

        // child 자체는 drop 되지 않도록 별도 보관: detach (Drop=SIGKILL은 아님).
        // sidecar는 부모(앱) 종료 시 OS가 자식 stdin EOF로 정리.
        // 향후 graceful shutdown용으로 child를 별도 핸들에 두고 싶다면 여기 확장.
        std::mem::forget(child);

        Ok(SidecarHandle {
            stdin: Mutex::new(stdin),
        })
    }

    pub async fn send_op(&self, op: Value) -> Result<()> {
        let mut line = serde_json::to_string(&op)?;
        line.push('\n');
        let mut stdin = self.stdin.lock().await;
        stdin.write_all(line.as_bytes()).await?;
        stdin.flush().await?;
        Ok(())
    }
}

/// 실행할 sidecar Command를 결정한다.
///
/// 1) 배포 빌드: 앱 .exe와 같은 디렉토리의 `ycollector(.exe)` (Tauri externalBin이 동봉).
/// 2) dev fallback: 시스템 PATH의 `ycollector` (예: `uv pip install -e .` 후).
fn resolve_command() -> Result<Command> {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()));
    let bundled = exe_dir.as_ref().map(|d| {
        d.join(if cfg!(windows) {
            "ycollector.exe"
        } else {
            "ycollector"
        })
    });
    if let Some(p) = bundled.as_ref().filter(|p| p.exists()) {
        return Ok(Command::new(p));
    }
    // PATH fallback.
    if which_in_path("ycollector").is_some() {
        return Ok(Command::new("ycollector"));
    }
    Err(anyhow!(
        "sidecar 바이너리를 찾지 못함: 동봉본도, PATH의 ycollector도 없음. \
         (dev: `uv sync` 후 `uv run --frozen ycollector --json` 가 동작해야 함)"
    ))
}

/// done 이벤트(`{job_id, filepath}`) + 최근 메타를 합쳐 manifest에 append.
/// 메타(title/channel/thumbnail)는 별도 emit 순서로 들어오므로 본 함수는
/// done 시점의 정보로만 작성한다. UI는 메타 캐시(jobs store)에서 따로 그려도 됨.
fn append_done_to_library(v: &Value) -> anyhow::Result<()> {
    use crate::jobs::library;
    let job_id = v.get("job_id").and_then(|x| x.as_str()).unwrap_or("");
    let filepath = v.get("filepath").and_then(|x| x.as_str()).unwrap_or("");
    if filepath.is_empty() {
        return Ok(());
    }
    let item = serde_json::json!({
        "job_id": job_id,
        "url": "",
        "title": "",
        "channel": "",
        "duration": "",
        "video_id": "",
        "thumbnail": "",
        "filepath": filepath,
        "finished_at": std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as u64)
            .unwrap_or(0),
    });
    library::append_item(item)
}

fn which_in_path(name: &str) -> Option<std::path::PathBuf> {
    let exe_name = if cfg!(windows) {
        format!("{name}.exe")
    } else {
        name.to_string()
    };
    std::env::var_os("PATH").and_then(|paths| {
        std::env::split_paths(&paths)
            .map(|p| p.join(&exe_name))
            .find(|p| p.is_file())
    })
}
