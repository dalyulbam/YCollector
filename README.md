# YCollector

YouTube 영상을 손쉽게 다운로드하기 위한 데스크톱 도구. **yt-dlp** 코어 + **Tauri + React + shadcn/ui** UI (신규) / **PySide6** UI (legacy).

> **Phase 0 — Day 1**: 단일 URL CLI/GUI가 동작하는 최소 스캐폴딩.
> **Frontend r1 (2026-05-16)**: Tauri + React 웹 프런트로 전환 진행 중 ([계획](docs/plan/youtube_downloader_frontend_plan_260516.md)).
> 자세한 설계는 [`docs/`](docs/) 또는 [`docs/index.html`](docs/index.html) 참고.

---

## 새 데스크톱 앱 (Tauri + React)

URL 붙여넣기 → 버튼 한 번으로 다운로드. 설정은 우측 슬라이드 패널, 완료 항목은 라이브러리 탭에서 검색. 시각 가이드는 [`README.html`](README.html)을 브라우저에서 열어보세요.

### 1) 사전 요구사항

| 도구 | 버전 | 설치 |
|---|---|---|
| **Python** | 3.11+ (3.13 권장) | [python.org](https://www.python.org/) |
| **uv** | 최신 | `winget install astral-sh.uv` |
| **Node.js** | 18+ | [nodejs.org](https://nodejs.org/) |
| **pnpm** | 9+ (npm으로도 OK) | `npm i -g pnpm` |
| **Rust** | 1.77+ | `winget install Rustlang.Rustup` 후 `rustup default stable` |
| **WebView2 Runtime** | 시스템 동봉 | Windows 11은 기본 포함, 10은 [Edge 페이지](https://developer.microsoft.com/microsoft-edge/webview2/)에서 |
| **FFmpeg** | 시스템 PATH | `winget install Gyan.FFmpeg` (DASH 머지에 필수) |

> Windows 외 OS는 현재 1차 타깃이 아닙니다. 동작은 하지만 인스톨러 빌드(`scripts/build_tauri.ps1`)는 PowerShell 전용.

### 2) 5분 설치 (한 번만)

> ⚠️ **백신·사내 프록시가 TLS 를 가로채는 환경**이라면 — 설치 중 `CERTIFICATE_VERIFY_FAILED` 가 나거나, 받은 패키지가 깨져 나중에 `module … has no attribute …` 가 난다면 해당 — `uv` 가 휠을 절반만 받아 **부분 설치**가 될 수 있습니다. 아래 명령들 **전에** 이 셸에서 한 번 `$env:UV_NATIVE_TLS = "1"` 를 실행하세요. 이 셸의 모든 `uv` 명령이 OS 인증서 저장소를 쓰게 됩니다(영구 적용은 `setx UV_NATIVE_TLS 1` 후 새 터미널, 또는 명령마다 `--native-tls`). 이미 깨졌을 때의 복구는 아래 §5) 트러블슈팅.

```powershell
# 저장소 클론 + 진입
git clone https://github.com/dalyulbam/YCollector.git
cd YCollector

# Python sidecar
uv sync

# 프런트엔드
pnpm --dir frontend install
#  └─ pnpm 없으면:  cd frontend ; npm install ; cd ..
```

### 3) 개발 모드 (HMR 가동)

dev 모드는 **터미널 1개**로 충분합니다. Tauri가 Vite를 자기 자식으로 띄우고, Rust가 PATH의 `ycollector`를 sidecar로 spawn합니다.

```powershell
# uv sync 가 만든 .venv 가 활성화돼있는 셸에서:
cd src-tauri
cargo tauri dev
```

처음 가동 시 Cargo 의존성 컴파일에 5~10분 걸립니다(정상). 두 번째부터는 수 초.

**확인 포인트**:
- 창 우상단에 `yt-dlp <버전>` 표시 → sidecar 가동 OK
- URL 붙여넣고 Enter → 잠시 후 썸네일/제목이 나타나면 NDJSON 왕복 OK
- ⚙ 설정 → 화질 변경 → 저장 → CLI에서 `uv run ycollector ...`도 같은 설정 사용 (`%APPDATA%\YCollector\settings.ini` 공유)

### 4) 프로덕션 빌드 (.msi / .exe 인스톨러)

```powershell
.\scripts\build_tauri.ps1
# 산출물: src-tauri/target/release/bundle/{msi,nsis}/
```

스크립트가 자동으로:
1. **Nuitka**로 `ycollector` CLI를 빌드 (`uv run python scripts/build_exe.py --target cli`)
2. 산출물을 `src-tauri/binaries/ycollector-x86_64-pc-windows-msvc.exe`로 복사 (Tauri sidecar 네이밍 규칙)
3. `pnpm --dir frontend build` 또는 `npm run build`로 Vite 산출
4. `cargo tauri build` → `.msi` (Windows Installer) + `.exe` (NSIS)

옵션:
- `-SkipSidecar` — 이미 `src-tauri/binaries/`에 sidecar가 있으면 1~2단계 스킵
- `-Triple <triple>` — 기본 `x86_64-pc-windows-msvc`. arm64 등 크로스 빌드 시.

### 비공개·멤버십·연령제한 영상 (쿠키 로그인)

메인 Chrome을 종료할 수 없거나, `Could not copy Chrome cookie database` 에러를 만났다면 **가상 Chromium 한 번 로그인 → cookies.txt 영구 사용** 흐름을 권장합니다.

```powershell
# 한 번만:
uv sync --extra cookies-headed
uv run playwright install chromium    # Chromium 다운로드 (~150MB, 1회)

# 별 Chromium 창이 뜸 → YouTube 로그인 → 자동 감지 후 창이 닫힘 + 쿠키 저장
uv run ycollector-login
#  → 저장: %APPDATA%\YCollector\cookies.txt

# 이후엔 그냥 받으면 됨 — ycollector 가 위 경로를 자동 탐지
uv run ycollector --yes-playlist "https://www.youtube.com/watch?v=...&list=PL..."
```

특징:
- 메인 Chrome은 평생 켜둔 채로 OK (Playwright는 별 user-data-dir).
- 프로필이 `%APPDATA%\YCollector\pw-profile\` 에 영속 → 재로그인 불필요.
- `--cookies <FILE>` 로 명시 경로도 가능. settings.ini의 `cookies_file =` 로 영구.
- 자세한 동작은 `src/ycollector/cookies.py` docstring.

### 5) 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `cargo: command not found` | Rust 미설치 → `winget install Rustlang.Rustup ; rustup default stable` 후 새 터미널 |
| `Could not copy Chrome cookie database` | Chrome 실행 중 락. 위 §"비공개… (쿠키 로그인)" 의 `ycollector-login` 흐름 권장 |
| `tauri: command not found` (cargo는 있음) | tauri-cli 미설치 → `cargo install tauri-cli --version "^2.0"` |
| `cargo tauri dev`에서 sidecar spawn 실패 | venv 미활성화 → 같은 터미널에서 `uv sync` 했는지 확인. 또는 `uv pip install -e .`로 PATH 등록 |
| 창은 뜨지만 `yt-dlp <버전>` 표시 없음 | sidecar가 죽음. DevTools(F12) → Console에서 `job:log` 이벤트 / 시스템에 yt-dlp 또는 FFmpeg가 없는지 확인 |
| 한글 제목이 깨짐 | Windows cp949 콘솔 문제. cli.py와 sidecar가 자체적으로 UTF-8 강제하므로 최신 커밋 사용 |
| `pnpm install` 시 `error EPERM` | OneDrive 등 동기화 폴더와 충돌. node_modules는 동기화 제외 또는 저장소를 동기화 밖으로 이동 |
| Nuitka가 MinGW를 받겠다고 함 | 한 번 `Y`로 수락하면 됨. MSVC가 있으면 그쪽을 자동 선택 |
| 다운로드가 "준비 중…"에서 멈춤 | yt-dlp가 deno(JS 런타임)를 기다림 → `winget install DenoLand.Deno` 후 새 터미널 |
| 설치 후 `module 'httptools' has no attribute 'HttpRequestParser'` (또는 다른 패키지의 `has no attribute` / `ImportError`) | 휠이 잘려 받아진 **부분 설치** — 소스 `.py`가 빠진 채 `.pyd`/`__pycache__`만 남음(주로 TLS 가로채기 환경). 해당 패키지만 재설치: `uv pip install --reinstall --no-cache --native-tls httptools`. 광범위하면: `uv sync --reinstall --no-cache --native-tls` |
| `uv sync`가 `CERTIFICATE_VERIFY_FAILED` / `unable to get local issuer certificate` | 백신·사내 프록시의 TLS 가로채기로 certifi가 CA를 모름. `$env:UV_NATIVE_TLS="1"`(또는 명령마다 `--native-tls`)로 OS 인증서 저장소 사용. Python 코드의 HTTPS 는 `truststore` 사용(이미 `generator/sora.py` 처리) |

### 6) 한눈에 보는 아키텍처

```
 ┌──────────────────────────────────────────────────────┐
 │  Tauri Window (WebView2)                             │
 │  React + shadcn/ui                                   │
 │  ┌────────────────────────────────────────────────┐  │
 │  │ <UrlPasteBar/>  <SettingsSheet/>  <JobList/>   │  │
 │  │ <LibraryTab/>                                  │  │
 │  └────────────────────────────────────────────────┘  │
 └──────────────┬───────────────────────────────────────┘
                │  invoke(...)  +  listen('job:*')
 ┌──────────────▼───────────────────────────────────────┐
 │  Rust shell  (src-tauri/src/main.rs + sidecar.rs)    │
 │  • commands: probe / start_job / cancel / settings / │
 │    pick_folder / open_path / list_library            │
 │  • NDJSON 펌프 (stdout → emit, stderr → log)         │
 └──────────────┬───────────────────────────────────────┘
                │  NDJSON over stdin/stdout
 ┌──────────────▼───────────────────────────────────────┐
 │  Python sidecar  (ycollector --json)                 │
 │  src/ycollector/sidecar.py                           │
 │  • _Worker queue + _JobRegistry (취소 핸들)          │
 └──────────────┬───────────────────────────────────────┘
                │  Popen
 ┌──────────────▼───────────────────────────────────────┐
 │  yt-dlp  +  FFmpeg                                   │
 └──────────────────────────────────────────────────────┘
```

추가 시각 자료 (스택, 빌드 파이프라인, 번들 크기 비교, 마일스톤 타임라인)는 [`README.html`](README.html)을 브라우저로 열면 SVG로 볼 수 있습니다.

---

## 로컬 서버 — 브라우저에서 바로 (Tauri 불필요)

Tauri/Rust 없이 한 줄로 가동되는 로컬 웹 UI. FastAPI + SSE 백엔드 + 단일 HTML.
URL 붙여넣고 다운로드, 그리고 **참조 영상 URL과 프롬프트를 리스트로 쌓아 Sora 2 Pro 영상 생성**까지 한 페이지에서.

### 사전 요구사항

- 위 §사전 요구사항의 Python/uv/FFmpeg
- **OpenAI API 키** — `.env`의 `OPENAI_API_KEY=sk-...` (자동 로드)

### 가동

```powershell
# 한 번만:
uv sync --extra web --extra video-gen

# 가동 (브라우저 자동 오픈):
uv run ycollector-server
# → http://127.0.0.1:8765/
```

> 💡 `ycollector-server` 가 `module 'httptools' has no attribute 'HttpRequestParser'` 로 죽으면 `--extra web` 의존성이 부분 설치된 것입니다(TLS 가로채기 환경에서 흔함). `uv pip install --reinstall --no-cache --native-tls httptools` 로 복구. 예방은 위 §2) 설치의 `UV_NATIVE_TLS` 안내 참고.

UI 구성:

1. **YouTube 다운로드** — URL 붙여넣고 Enter. 진행률은 SSE로 실시간.
2. **영상 생성** — Sora 2 Pro
   - **① 참고 이미지/영상** — **로컬 파일 업로드**(드래그드롭, 이미지·영상) 또는 URL. YouTube면 thumbnail, 영상 파일은 **첫 프레임**을 앵커로. 출력 size에 맞게 자동 리사이즈.
   - **② 프롬프트 문장** — 입력창 + `+` 버튼. 여러 줄을 누적 → 호출 시 한 문장으로 합쳐 전송.
   - 옵션: model(sora-2-pro / sora-2), size(가로 1280×720·1792×1024 / 세로 720×1280·1024×1792), seconds(4/8/12).
   - **예상 비용** 실시간 표시(references 수만큼 곱). 클릭 직전 confirm.
   - 프롬프트 **1개** → 단일 영상. **프롬프트 2개 이상**(또는 reference 2개 이상) → **연속형**: 각 장면을 **last-frame chaining**(직전 클립의 마지막 프레임을 다음 앵커로)으로 이어 **1개 연속 영상**. 첫 reference가 시작 앵커, 비용은 세그먼트 수만큼.
3. **작업 목록** — 다운로드/생성이 같은 카드 UI. SSE 진행률 바.

상세 시각화는 [`README.html`](README.html) §7~§8.

---

## 영상 생성 CLI (`ycollector-generate`)

자동화·배치·CI용 단축 진입점.

```powershell
uv run ycollector-generate "고양이가 비를 맞으며 우산을 들고 걷는 장면, 영화적 톤" `
  --references "C:\Users\user\Pictures\ref.jpg" `
  --references "https://www.youtube.com/watch?v=Y0913p-bfqY" `
  --references "https://example.com/photo.jpg" `
  --model sora-2-pro --size 1280x720 --seconds 8 `
  --out generated/cat.mp4 --budget-usd 5
```

플래그:
- `--references REF` (반복) — **로컬 이미지 파일 경로**(예: `C:\img\ref.jpg`, `./ref.png`, `~/ref.jpg`), 이미지 URL, 또는 YouTube URL(thumbnail 자동 추출). N번 주면 N개 잡으로 분할 → `cat-01.mp4`, `cat-02.mp4`, …
- `--model {sora-2, sora-2-pro}` (기본 `sora-2-pro`)
- `--size {1280x720, 1792x1024, 720x1280, 1024x1792}`
- `--seconds {4, 8, 12}` (단일 생성 최대 12초)
- `--budget-usd 5` — 잡 1개 예상이 한도 초과면 실행 거부 (가격 함정 방지)
- `--dry-run` — 견적·계획만 출력, API 호출 X

가격(추정 — 생성 전 확인창에 표시): sora-2 720p ≈ **$0.10/s**, sora-2-pro 720p ≈ **$0.30/s**, 고해상(1792×1024 등) ≈ $0.50–0.70/s. 정확한 단가는 OpenAI 요금표 기준이며, 단일 생성은 최대 12초입니다.

> ⚠ **OpenAI Videos API 는 2026-09-24 sunset 공지**. 본 통합은 `Provider` 추상화(`src/ycollector/generator/base.py`)로 만들어 후속 모델(Veo/Runway/Pika 등) 어댑터 추가가 1파일 변경입니다.

생성된 영상은 자동으로 **라이브러리 manifest**에 `source: "generated"` 표지로 등록되어 Tauri UI / 로컬 서버 라이브러리 탭에서 다운로드한 영상과 같이 검색됩니다.

---

## CLI / Legacy GUI 빠른 시작

새 데스크톱 앱이 기능 패리티에 도달하기 전까지 병존합니다. CLI는 자동화/배치에 계속 권장.

요구사항:
- **Python 3.11+** (3.13 권장 — `.python-version` 참고)
- **[uv](https://github.com/astral-sh/uv)** (권장 패키지 매니저)
- **FFmpeg** — 시스템 PATH에 있어야 함 (자동 번들은 Phase 1에서)

```powershell
# 1. 의존성 설치 (.venv 자동 생성)
uv sync

# 2. CLI — 단일 URL (기본: 1080p mp4 + ko/en 자막)
uv run ycollector https://www.youtube.com/watch?v=dQw4w9WgXcQ

# 3. CLI — 화질/코덱 프리셋
uv run ycollector URL --quality 2160p --codec av1 --container mkv

# 4. CLI — 오디오만
uv run ycollector URL --quality audio --audio m4a --no-subs

# 5. CLI — 파일에서 (한 줄에 하나, '#' 주석)
uv run ycollector --from urls.txt

# 6. CLI — stdin 파이프
Get-Content urls.txt | uv run ycollector -

# 7. GUI (legacy PySide6 — 신규 Tauri UI로 대체 예정. 위 §"새 데스크톱 앱" 참고)
uv run ycollector-gui

# 8. JSON sidecar 모드 (Tauri UI 가 spawn) — stdin NDJSON / stdout NDJSON
uv run ycollector --json
```

> 기본값은 저장소의 [`settings.ini`](settings.ini)에 모여 있습니다. 화질/포맷/멈춤 대응(`throttled_rate = 100K` 등)을 한곳에서 조정 가능. 자세히는 [사용설명서 §8.5](docs/howToUse/user_manual_260513.md#85-설정-파일-settingsini).

플래그 일부:
- `--quality {144p,240p,360p,480p,720p,1080p,1440p,2160p,best,audio}` — 화질 프리셋
- `--codec {auto,h264,vp9,av1}` — 비디오 코덱 선호
- `--audio {best,m4a,opus}` — 오디오 선호
- `--container {mp4,mkv,webm}` — 머지 컨테이너
- `-f / --format SPEC` — raw yt-dlp 포맷 셀렉터 (위 프리셋 모두 무시)
- `-o / --output-dir` — 출력 폴더 (기본 `./downloads`)
- `--no-subs` — 자막 다운로드 스킵
- `--sub-langs ko,en,ja` — 자막 언어
- `--cookies-from-browser chrome` — 비공개/연령제한/멤버십 (브라우저는 닫혀 있어야 함)

### 단일 .exe 빌드 (uv 없이 더블클릭으로 실행하기)

```powershell
# 빌드 의존성 설치
uv sync --extra build

# Nuitka로 단일 .exe 빌드 (권장 — 빠른 시작, AV 오탐 ↓)
uv run python scripts/build_exe.py

# 또는 PyInstaller (대안)
uv run python scripts/build_exe.py --mode pyinstaller

# CLI .exe도 같이
uv run python scripts/build_exe.py --target both
```

결과: `dist/gui.dist/YCollector.exe` (Nuitka) 또는 `dist/YCollector/YCollector.exe` (PyInstaller).
바로가기를 만들어 시작 메뉴 / 바탕화면에 고정하면 됩니다.

---

## 프로젝트 구조

```
YCollector/
├── pyproject.toml             ← uv 프로젝트 + entry points
├── .python-version            ← 3.13
├── .gitignore
├── README.md
├── docs/                      ← 설계 문서 (.md + .html)
│   ├── index.html             ← 랜딩 (브라우저로 열기)
│   ├── plan/                  ← 설계 계획 v1.2
│   ├── motif/                 ← 경쟁 / 모티프 조사
│   └── _build_html.py         ← .md → .html 빌드 스크립트
├── src/ycollector/
│   ├── __init__.py
│   ├── __main__.py            ← `python -m ycollector` 진입
│   ├── cli.py                 ← CLI 진입 (argparse) — `--json` 시 sidecar 라우팅
│   ├── sidecar.py             ← NDJSON sidecar (Tauri UI 가 spawn)
│   ├── gui.py                 ← PySide6 메인 윈도우 (legacy)
│   ├── config.py              ← settings.ini 로더/타입
│   └── engine/
│       ├── __init__.py
│       └── ytdlp.py           ← subprocess 래퍼 + 진행률 파서 + 에러 분류
├── frontend/                  ← Tauri 웹뷰용 React + Vite + Tailwind + shadcn/ui
│   ├── package.json, vite.config.ts, tailwind.config.ts, components.json
│   ├── index.html
│   └── src/{App.tsx, components/, lib/{ipc,types,utils}, store/jobs}
└── src-tauri/                 ← Tauri 쉘 (Rust)
    ├── Cargo.toml, tauri.conf.json, build.rs
    └── src/{main.rs, sidecar.rs, settings.rs, jobs.rs}
```

향후 모듈 추가 (plan §12 참고):
- `config/` — TOML 컨피그 트리 (D2/D5)
- `queue/` — 작업 큐, 워커 풀
- `library/` — SQLite + FTS5 (D1)
- `transcribe/` — faster-whisper 통합 (D6)
- `input/` — 클립보드 감시, 브라우저 확장 endpoint (D4)
- `schedule/` — 채널 동기화 (D5)
- `update/` — yt-dlp 자가 갱신
- `classifier/` — 가이디드 워크플로우 (D3)

---

## 문서

- **설계 계획 (plan v1.2)** — [`docs/plan/youtube_downloader_plan_260508.md`](docs/plan/youtube_downloader_plan_260508.md)
  17개 섹션 + 부록 3개. 핵심 도전과제, YouTube 변경 적응 전략, 차별화 D1~D6, 입출력 워크플로우, 컨피그 매핑.
- **경쟁 / 모티프 조사 (motif r1)** — [`docs/motif/youtube_downloader_motif_260508.md`](docs/motif/youtube_downloader_motif_260508.md)
  20+개 프로젝트 분석 (Parabolic, Stacher, Tartube, Open Video Downloader, Persepolis, Seal, Cobalt, NewPipe 등).
- **HTML 시각화** — `docs/index.html` 더블클릭 (또는 `start docs/index.html` PowerShell). `python docs/_build_html.py`로 .md → .html 재생성.

---

## 로드맵 (요약)

| Phase | 산출물 | 상태 |
|---|---|---|
| 0 | CLI + 최소 GUI, 단일 URL | **진행 중** (이 커밋) |
| 1 | 큐, 포맷 픽커, SQLite 영속화, 자가 갱신 v1 | 예정 |
| 2 | 라이브러리 (D1), 프리셋 (D2), 가이디드 PoToken (D3) | 예정 |
| 3 | 라이브, Whisper 전사 (D6), 클립보드 감시 (D4), 채널 스케줄 (D5) | 예정 |
| 4 | i18n (ko/en/ja), 자동 업데이트, 코드 사이닝 | 예정 |
| 5 | 정식 배포 | 예정 |

자세한 일정: plan §8.

---

## 개발

```powershell
# 린트
uv run ruff check src/

# 포맷
uv run ruff format src/

# 테스트 (현재는 없음)
uv run pytest
```

---

## 라이선스

미정 (TBD — plan §13 OQ-8 참고). v1.0 정식 배포 전 결정.

## 면책

본 도구는 **자기 콘텐츠, 공개도메인, CC 라이선스, 또는 명시적 권한이 있는 콘텐츠** 다운로드를 위한 것입니다. 저작권 침해는 사용자 책임입니다 (plan §6.10 참고).
