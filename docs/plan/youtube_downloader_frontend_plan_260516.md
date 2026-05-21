# YCollector — 웹 프런트 전환 계획 (frontend r1, 2026-05-16)

> 본 문서는 [`youtube_downloader_plan_260508.md`](./youtube_downloader_plan_260508.md) v1.2의 **§11.5 (UI 프레임워크)** 결정을 갱신·세분화한 하위 계획이다.
> 상위 plan에서 "Phase 1 스파이크 후 Tauri 마이그레이션 재평가"로 적어둔 항목을, 본 문서에서 **확정**한다.

## 결정 사항 (2026-05-16)
- **Shell**: Tauri v2 (Rust) + WebView2(Windows)
- **Frontend**: React + Vite + TypeScript + Tailwind CSS + **shadcn/ui** (Radix)
- **Backend(=sidecar)**: 기존 Python(`src/ycollector/`) 그대로. CLI에 `--json` NDJSON 모드를 추가해 Rust에서 sidecar로 spawn.
- **PySide6 GUI**: 당분간 병존(`ycollector-gui`). 새 UI가 기능 패리티 도달 시 제거.
- **OQ-W1 → Tauri v2**, **OQ-W2 → zustand**, **OQ-W3 → shadcn as-needed**, **OQ-W4 → OS 추종 + 수동 toggle (localStorage)**, **OQ-W5 → 마이그레이션 함수 시그니처만 명세**, **OQ-W6 → 자동 업데이트는 분리 PR**.

## 1. 아키텍처

```
┌──────────────────────────────────────────────────────────┐
│  Tauri Window (WebView2)                                 │
│  React + shadcn/ui                                       │
│   <UrlPasteBar/>  <SettingsSheet/>  <JobList/>           │
│   <ProgressCard/> <LibraryTab/>                          │
│  ─ window.__TAURI__.invoke(...) ─ event.listen('job:*')─ │
│  Rust (src-tauri/src/main.rs)                            │
│   commands: probe, start_job, cancel, load_settings,     │
│             save_settings, pick_folder, open_path,       │
│             list_library                                 │
│   sidecar: spawn ycollector --json                       │
│   NDJSON 펌프 → emit('job:progress' …)                   │
│  Python sidecar (ycollector CLI, 기존)                   │
│   engine/ytdlp.py  config.py  format_spec.py             │
└──────────────────────────────────────────────────────────┘
```

## 2. 디렉토리 구조

```
YCollector/
├── src/ycollector/            ← 기존 (cli.py에 --json 추가)
├── frontend/                  ← 신규
│   ├── package.json, vite.config.ts, tailwind.config.ts
│   ├── tsconfig.json, components.json, postcss.config.js
│   ├── index.html
│   └── src/
│       ├── main.tsx, App.tsx, global.css
│       ├── lib/{ipc.ts, types.ts, utils.ts}
│       ├── store/jobs.ts
│       ├── components/ui/  (shadcn 생성물)
│       └── components/{UrlPasteBar,SettingsSheet,JobCard,JobList,LibraryTab}.tsx
├── src-tauri/                 ← 신규
│   ├── Cargo.toml, tauri.conf.json, build.rs
│   ├── icons/
│   └── src/{main.rs, sidecar.rs, settings.rs, jobs.rs}
└── scripts/
    ├── build_exe.py           ← 기존(Nuitka) — sidecar 빌드 재사용
    └── build_tauri.ps1        ← 신규
```

## 3. 마일스톤

| M | 산출물 | 검증 |
|---|---|---|
| M0 | 스캐폴딩 (frontend/ + src-tauri/ 파일, 빌드 X) | 파일 트리 존재, README 갱신 |
| M1 | sidecar NDJSON (Python `--json` + Rust 펌프) | `echo '{"op":"probe","url":"…"}' \| ycollector --json` 로 NDJSON 출력 |
| M2 | 다운로드 + 진행률 카드 | URL 1개 다운, 진행률 바 동작, 취소 시 .part 잔존 |
| M3 | SettingsSheet (settings.ini 양방향) | 화질 변경 → CLI 직접 실행에도 반영 |
| M4 | LibraryTab + manifest.json | 3개 받은 후 검색·정렬 |
| M5 | 빌드 스크립트 + installer | 깨끗한 PC에서 .msi 더블클릭 동작 |

## 4. NDJSON 프로토콜

**Rust → Python (stdin, 한 줄 = 한 명령)**
```json
{"op":"probe","url":"https://…"}
{"op":"download","job_id":"j_01","url":"…","settings":{...}}
{"op":"cancel","job_id":"j_01"}
{"op":"shutdown"}
```

**Python → Rust (stdout, NDJSON 이벤트)**
```json
{"event":"ready","yt_dlp":"2026.02.0"}
{"event":"meta","job_id":"j_01","title":"…","channel":"…","duration":215,"thumbnail":"…","video_id":"…"}
{"event":"progress","job_id":"j_01","phase":"download","percent":42.1,"downloaded":12345,"total":67890,"speed":3300000,"eta":58}
{"event":"progress","job_id":"j_01","phase":"postprocess"}
{"event":"done","job_id":"j_01","filepath":"D:\\…\\video.mp4"}
{"event":"error","job_id":"j_01","category":"po_token_required","message":"…"}
{"event":"log","level":"info","job_id":"j_01","line":"…"}
```

stderr는 로그/상태(사람 가독). stdout만 기계가독 NDJSON.

## 5. 빌드 / 배포 (Windows 1차)

1. `uv run python scripts/build_exe.py --target cli` → `dist/ycollector.exe`
2. 산출물을 `src-tauri/binaries/ycollector-x86_64-pc-windows-msvc.exe`로 복사 (Tauri sidecar 네이밍 규칙).
3. `cd frontend && pnpm install && pnpm build` → `frontend/dist/`
4. `cd src-tauri && cargo tauri build` → `.msi` + `.exe` 인스톨러
5. (`scripts/build_tauri.ps1`이 위 단계 자동화)

## 6. 비목표

- SQLite/FTS5 라이브러리 백엔드(D1) — Phase 2.
- 클립보드 감시(D4), 채널 스케줄(D5), Whisper 전사(D6).
- 자동 업데이트(Tauri updater) + 코드 사이닝.
- macOS/Linux 빌드.

## 7. 의존성 (개발자 환경)

- Node 18+ (현재 24.13.0 확인)
- pnpm 9+ (또는 npm — `pnpm` 우선)
- Rust 1.77+ (rustup으로 설치)
- WebView2 Runtime (Windows 11 기본 포함)
