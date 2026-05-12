# YCollector 사용설명서

- **문서 버전**: 1.0 (Phase 0 Day 1 기준)
- **작성일**: 2026-05-13 (YYMMDD: 260513)
- **대상**: 데스크톱(Windows 우선) 사용자
- **연관**:
  [설계 계획](../plan/youtube_downloader_plan_260508.md) ·
  [경쟁/모티프 조사](../motif/youtube_downloader_motif_260508.md) ·
  [인덱스](../index.html)

---

## 0. 한눈에 보기

```
설치  →  uv sync           (의존성 설치)
       →  uv run ycollector URL    (CLI 1회 다운로드)
       →  uv run ycollector-gui    (GUI 실행)
```

| 단계 | 명령 | 결과 |
|---|---|---|
| 환경 | `uv sync` | `.venv` 자동 생성, yt-dlp+PySide6 설치 |
| 한 영상 | `uv run ycollector URL` | `./downloads/<업로더>/<제목>.mp4` |
| 여러 영상 | `uv run ycollector --from urls.txt` | 일괄 다운로드 |
| GUI | `uv run ycollector-gui` | 메인 윈도우 |

> 본 도구는 **자기 콘텐츠, 공개도메인, CC 라이선스, 또는 명시적 권한이 있는 콘텐츠** 다운로드를 위한 것입니다. 저작권 침해는 사용자 책임입니다.

---

## 1. 시작하기

### 1.1 시스템 요구사항

| 항목 | 권장 | 최소 |
|---|---|---|
| OS | Windows 11 / macOS 14 / Ubuntu 24.04 | Windows 10 1809+, macOS 12, Linux glibc 2.31+ |
| Python | 3.13 | 3.11 |
| RAM | 8 GB | 4 GB |
| 디스크 | 다운로드 크기 + 2 GB 여유 | 동일 |
| 네트워크 | 유선/Wi-Fi 안정 연결 | 동일 |

### 1.2 설치 — 4단계

#### Step 1. Python 설치

**Windows (winget)**:
```powershell
winget install Python.Python.3.13
```

또는 https://www.python.org/downloads/ 에서 3.13.x 인스톨러 → 설치 시 **"Add to PATH" 체크**.

확인:
```powershell
python --version
# Python 3.13.x
```

#### Step 2. uv 설치 (권장)

`uv`는 Astral의 빠른 Python 패키지 매니저. pip의 ~10×.

**Windows PowerShell**:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

또는:
```powershell
winget install astral-sh.uv
```

확인:
```powershell
uv --version
# uv 0.9.x
```

#### Step 3. FFmpeg 설치

YouTube는 영상/오디오를 분리(DASH)해 보내므로 병합용 FFmpeg가 필수.

**Windows (winget)**:
```powershell
winget install Gyan.FFmpeg
```

또는 https://www.gyan.dev/ffmpeg/builds/ 에서 **release essentials** 다운로드 → `C:\ffmpeg\` 압축 해제 → `C:\ffmpeg\bin`을 PATH에 추가.

확인:
```powershell
ffmpeg -version
# ffmpeg version 7.x ...
```

> Phase 1에서 FFmpeg 자동 번들 예정 — 사용자 설치 불필요해질 것.

#### Step 4. YCollector 설치

```powershell
git clone https://github.com/dalyulbam/YCollector.git
cd YCollector
uv sync
```

`uv sync`가 다음을 자동 수행:
1. `.venv/` 가상환경 생성
2. `yt-dlp`, `PySide6` 등 의존성 설치
3. `uv.lock` 파일 생성 (재현 가능 빌드)

확인:
```powershell
uv run ycollector --version
# ycollector 0.1.0
```

### 1.3 첫 다운로드

```powershell
uv run ycollector "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

성공 화면:

```
yt-dlp 2026.05.07  (ycollector 0.1.0)

[1/1] https://www.youtube.com/watch?v=dQw4w9WgXcQ
   23.5%   125.3 MB / 533.4 MB  @  12.3 MB/s
   ...
   ✓ downloads\Rick Astley\Never Gonna Give You Up [dQw4w9WgXcQ].mp4
  → downloads\Rick Astley\Never Gonna Give You Up [dQw4w9WgXcQ].mp4
```

기본 동작:
- **포맷**: `bv*[height<=1080]+ba/b[height<=1080]` (1080p 최고 화질)
- **컨테이너**: mp4
- **자막**: ko + en, 영상에 임베드
- **출력**: `./downloads/<업로더>/<제목> [<id>].mp4`

---

## 2. CLI 사용법

### 2.1 명령 구조

```
ycollector [옵션] <URL> [<URL> ...]
ycollector --from <FILE>
... | ycollector -
```

### 2.2 옵션 일람

| 옵션 | 단축 | 기본값 | 설명 |
|---|---|---|---|
| `--output-dir DIR` | `-o` | `./downloads` | 출력 폴더 |
| `--format SPEC` | `-f` | `bv*[height<=1080]+ba/b[height<=1080]` | yt-dlp 포맷 셀렉터 |
| `--container EXT` | | `mp4` | `mp4` / `mkv` / `webm` |
| `--no-subs` | | OFF | 자막 다운로드 / 임베드 스킵 |
| `--sub-langs LIST` | | `ko,en` | 자막 언어 (쉼표 구분) |
| `--cookies-from-browser NAME` | | (없음) | `chrome` / `firefox` / `edge` / `brave` |
| `--from FILE` | | (없음) | 파일에서 URL 읽기 |
| `--version` | | | 버전 표시 |
| `--help` | `-h` | | 도움말 |

### 2.3 실전 예시

#### A. 한 영상

```powershell
uv run ycollector "https://youtu.be/dQw4w9WgXcQ"
```

#### B. 여러 영상 한 번에

```powershell
uv run ycollector URL1 URL2 URL3
```

#### C. 출력 폴더 변경

```powershell
uv run ycollector URL -o "D:\Videos\Lectures"
```

#### D. 최고 화질 + mkv

```powershell
uv run ycollector URL -f "bv*+ba/b" --container mkv
```

→ 4K, 8K, HDR 모두 받음. AV1/VP9 코덱이 섞이면 mkv가 안전.

#### E. 오디오만 (m4a)

```powershell
uv run ycollector URL -f "bestaudio[ext=m4a]" --no-subs
```

> Phase 1에서 `--audio-format mp3 --audio-quality 320K` 추가 예정.

#### F. 한국어 자막만

```powershell
uv run ycollector URL --sub-langs ko
```

#### G. 비공개 / 멤버십 / 연령제한 영상

쿠키 임포트 필요. **반드시 해당 브라우저를 완전히 종료한 뒤** 실행:

```powershell
uv run ycollector URL --cookies-from-browser chrome
```

Firefox / Edge / Brave 모두 동일 옵션으로 가능.

#### H. URL 리스트 파일에서

`urls.txt`:
```
# 강의 시리즈
https://www.youtube.com/watch?v=A
https://www.youtube.com/watch?v=B

# 음악
https://music.youtube.com/watch?v=C
```

실행:
```powershell
uv run ycollector --from urls.txt
```

`#` 주석과 빈 줄은 무시됩니다.

#### I. stdin 파이프 (다른 명령에서)

```powershell
# 파일을 표준입력으로
Get-Content urls.txt | uv run ycollector -

# 클립보드 내용을
Get-Clipboard | uv run ycollector -

# yt-dlp 메타에서 추출한 URL을
uv run yt-dlp --flat-playlist -j "PLAYLIST_URL" | `
    ConvertFrom-Json | ForEach-Object url | `
    uv run ycollector -
```

#### J. 재생목록 / 채널 (yt-dlp가 자동 펼침)

```powershell
uv run ycollector "https://www.youtube.com/playlist?list=PL..."
uv run ycollector "https://www.youtube.com/@SomeChannel"
```

> Phase 2에서 펼침 후 영상 선택 UI 추가 예정.

### 2.4 종료 코드

| 코드 | 의미 |
|---|---|
| 0 | 모두 성공 |
| 1 | 부분 실패 (일부는 성공) |
| 2 | 모두 실패 또는 URL 없음 |
| 10+ | 시스템 에러 (yt-dlp 미설치 등) |

쉘 스크립트에서:
```powershell
uv run ycollector URL
if ($LASTEXITCODE -eq 0) { Write-Host "성공" }
```

---

## 3. GUI 사용법

실행:
```powershell
uv run ycollector-gui
```

### 3.1 메인 윈도우 (Phase 0 Day 1)

```
┌──────────────────────────────────────────────────────────────────┐
│ YCollector v0.1.0                                    [— □ ✕]    │
├──────────────────────────────────────────────────────────────────┤
│ URL                                                                │
│ ┌────────────────────────────────────────────────────────────┐ │
│ │ URL 붙여넣기 (한 줄에 하나, '#' 주석)                          │ │
│ │ 예: https://www.youtube.com/watch?v=dQw4w9WgXcQ              │ │
│ │                                                               │ │
│ │                                                               │ │
│ └────────────────────────────────────────────────────────────┘ │
│                                                                   │
│ 형식: [bv*[height<=1080]+ba/b... ]  폴더: [D:/…/downloads] [폴더…] [지금 다운로드] │
│                                                                   │
│ 로그                                                               │
│ ┌────────────────────────────────────────────────────────────┐ │
│ │ yt-dlp 2026.05.07  (ycollector 0.1.0)                       │ │
│ │                                                               │ │
│ │ [1/2] https://...                                             │ │
│ │ [download] Destination: downloads/Channel/Title [id].mp4      │ │
│ │   ✓ downloads/Channel/Title [id].mp4                          │ │
│ └────────────────────────────────────────────────────────────┘ │
│ 23.5%   125.3 MB   8.4 MB/s                                      │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 사용 흐름

```
[1] URL 박스에 영상 주소를 붙여넣기 (한 줄에 하나, # 주석 가능)
            │
            ▼
[2] (선택) 형식 / 폴더 변경
            │
            ▼
[3] "지금 다운로드" 클릭
            │
            ▼
[4] 로그 창에 진행 상황, 하단 상태바에 진행률 / 속도
            │
            ▼
[5] 완료 시 ✓ 표시 + 파일 경로
```

### 3.3 키보드

| 키 | 동작 |
|---|---|
| `Ctrl+V` (URL 박스 포커스) | 클립보드 붙여넣기 |
| `Tab` | 다음 위젯으로 이동 |
| `Enter` (버튼 포커스) | 다운로드 시작 |
| `Ctrl+Q` / `Alt+F4` | 종료 |

> Phase 1+에서 `Ctrl+L` (라이브러리 검색), `Ctrl+P` (프리셋), `Ctrl+,` (설정) 추가 예정.

### 3.4 출력 폴더 변경

"폴더…" 버튼 → 시스템 폴더 선택 다이얼로그 → 선택. 다음 다운로드부터 새 폴더에 저장.

> 변경 사항이 세션 간 보존되지는 않습니다 (Phase 1 SQLite 영속화 예정).

---

## 4. 상황별 가이드

### 4.1 강의 / 튜토리얼 시리즈 보관

```powershell
uv run ycollector `
    "https://www.youtube.com/playlist?list=PL_lecture_series" `
    -o "D:\Lectures" `
    --sub-langs ko,en
```

→ 재생목록의 모든 영상이 1080p mp4 + ko/en 자막으로 `D:\Lectures\<채널>\<재생목록>\` 에 저장.

### 4.2 음악 백업

현재 (Phase 0):
```powershell
uv run ycollector URL `
    -f "bestaudio[ext=m4a]" `
    --no-subs `
    -o "D:\Music\YouTube"
```

→ m4a (AAC) 최고 비트레이트로 추출. mp3 변환은 Phase 1에서.

### 4.3 자기 채널 백업 (비공개 포함)

Chrome을 완전히 종료한 뒤:
```powershell
uv run ycollector `
    "https://www.youtube.com/@MyChannel" `
    --cookies-from-browser chrome `
    -o "D:\Backup\MyChannel"
```

### 4.4 라이브 방송

현재 (Phase 0): 진행 중인 라이브의 URL을 그대로 넘기면 **지금 시점부터** 다운로드.

```powershell
uv run ycollector "https://www.youtube.com/watch?v=LIVE_ID"
```

> "처음부터(`--live-from-start`)" 옵션은 Phase 1에서 일급 시민 인자로 추가 예정. 현재 임시 우회:
> ```powershell
> uv run yt-dlp --live-from-start --hls-use-mpegts URL
> ```

### 4.5 4K HDR 받기

```powershell
uv run ycollector URL `
    -f "bv*[height<=2160]+ba/b[height<=2160]" `
    --container mkv
```

mp4는 일부 HDR 메타데이터를 못 담을 수 있음. mkv 권장.

### 4.6 정확한 포맷 ID 고르기

먼저 가용 포맷 확인:
```powershell
uv run yt-dlp -F URL
```

출력 예:
```
ID  EXT   RESOLUTION FPS │   FILESIZE   TBR  PROTO │ VCODEC          VBR  ACODEC      ABR ASR  MORE INFO
299 mp4   1920x1080  60  │     156.7MiB  4.3M https │ avc1.640033  4321k  audio only       │ ...
... 등 ...
```

원하는 ID로 받기:
```powershell
uv run ycollector URL -f 299+140
```

---

## 5. 문제 해결

### 5.1 `yt-dlp not found in PATH`

원인: 가상환경 활성화 안 됨, 또는 의존성 미설치.

```powershell
uv sync
uv run ycollector --version
```

**반드시 `uv run` 접두사** 사용. 직접 `ycollector`만 치면 글로벌 환경을 찾아 실패.

### 5.2 `FFmpeg not found` 또는 병합 실패

```powershell
ffmpeg -version
```
이 실패하면 PATH에 FFmpeg 없음. **§1.2 Step 3** 참고.

증상 예:
```
ERROR: Postprocessing: ffprobe and ffmpeg not found.
```

### 5.3 "Sign in to confirm you're not a bot"

YouTube의 봇 감지. **plan §6.2 (PoToken)** 의 원인. 대응 우선순위:

#### 1순위: 쿠키 임포트 (가장 효과적)

```powershell
# Chrome 완전 종료 후
uv run ycollector URL --cookies-from-browser chrome
```

Chrome이 잠겨 있으면 (브라우저 종료 안 되어 있으면) 에러. 작업관리자로 모든 `chrome.exe` 종료 후 재시도.

#### 2순위: 다른 브라우저 시도

```powershell
uv run ycollector URL --cookies-from-browser firefox
```

#### 3순위: yt-dlp 갱신

```powershell
uv lock --upgrade-package yt-dlp
uv sync
```

#### 4순위: PoToken provider (Phase 2에서 가이디드 UI로 추가 예정)

현재는 yt-dlp 인자 직접 전달 어려움. Phase 2까지 기다리거나 yt-dlp 단독 사용 권장.

### 5.4 다운로드가 너무 느림

YouTube가 의도적으로 단일 연결 속도를 떨어뜨리는 경우. 대응:

1. yt-dlp 최신 확인 (`uv lock --upgrade-package yt-dlp && uv sync`)
2. 시간대 변경 시도 (피크 시간대 회피)
3. 인터넷 속도 자체 확인

> Phase 1에서 aria2c 다중 연결 옵션이 추가됩니다.

### 5.5 한글 파일명 깨짐

PowerShell 콘솔 코드페이지:
```powershell
chcp 65001
```

또는 Windows 설정 → 시간 및 언어 → 언어 → 관리용 언어 설정 → **"Beta: Use Unicode UTF-8 for worldwide language support"** 체크 후 재부팅.

### 5.6 4K가 안 받아짐

기본 포맷은 `height<=1080`로 제한. 4K 원하면:
```powershell
uv run ycollector URL -f "bv*+ba/b"
```

또는 명시적으로:
```powershell
uv run ycollector URL -f "bv*[height<=2160]+ba/b[height<=2160]"
```

### 5.7 GUI가 안 뜨고 에러

```powershell
uv sync --reinstall
uv run python -c "import PySide6; print(PySide6.__version__)"
```

요구사항:
- Windows 10 1809 이상
- WebView2 런타임 (보통 자동 설치되어 있음)

PySide6는 약 80MB. 첫 `uv sync`에 시간 걸릴 수 있음.

### 5.8 `Requested format is not available`

요청 포맷 조합이 해당 영상에 없음.

진단:
```powershell
uv run yt-dlp -F URL
```

위 출력에서 가용 ID를 골라:
```powershell
uv run ycollector URL -f 137+140
```

> Phase 2에서 자동 fallback (요청 포맷 실패 시 가까운 대안 선택) 추가 예정.

### 5.9 SSL 인증서 에러

회사/학교 네트워크에서 SSL 가로채기가 있는 환경:
```powershell
$env:SSL_CERT_FILE = "C:\path\to\corporate_ca.pem"
uv run ycollector URL
```

(영구 설정은 Phase 1 컨피그 파일로)

### 5.10 디스크 가득 참

8시간 라이브 4K = 50GB+. 다운로드 전 예상 크기 확인:
```powershell
uv run yt-dlp --get-filename --get-format URL
```

> Phase 1+에서 사전 디스크 가드 + 작업 일시정지 자동화 예정.

---

## 6. 자주 묻는 질문 (FAQ)

### Q1. 무료인가요?
**A.** 네. 광고 없음. 라이선스는 v1.0 정식 배포 전 결정 예정 (plan §13 OQ-8).

### Q2. 어떤 사이트를 지원하나요?
**A.** yt-dlp가 지원하는 **1,800+ 사이트** 모두 (YouTube, Vimeo, Twitch, Bilibili, SoundCloud, Naver TV, Kakao TV 등). 다만 차별화 기능(라이브러리, 전사 등)은 **YouTube에 우선 최적화**됩니다.

### Q3. 모바일에서 쓸 수 있나요?
**A.** 현재는 데스크톱(Windows/macOS/Linux)만. iOS/Android는 [NewPipe](https://newpipe.net) 등 기존 솔루션 권장 (plan §1.3 N5).

### Q4. 클라우드 동기화 되나요?
**A.** 아니요. 본 도구는 로컬 우선. NAS/OneDrive 폴더에 출력 디렉토리를 설정하면 OS 동기화로 우회 가능.

### Q5. DRM 보호 영상(YouTube Movies 구매작 등)을 받을 수 있나요?
**A.** 아니요. DRM 우회는 명시적 비목표 (plan §1.3 N1). 시도하지 마세요.

### Q6. 저작권 문제는?
**A.** 본 도구는 **자기 콘텐츠, 공개도메인, CC 라이선스, 또는 명시적 권한이 있는 콘텐츠** 다운로드를 위한 것입니다. 저작권 침해는 사용자 책임입니다 (plan §6.10).

### Q7. 채널 자동 동기화는 언제?
**A.** Phase 3 (D5 차별화). "매주 일요일 03:00 채널 새 영상 동기화" 같은 스케줄링.

### Q8. 전사(Transcribe)는 언제?
**A.** Phase 3 (D6 차별화). 로컬 `faster-whisper` 통합. 한국어 영상도 정확한 자막 생성. plan §6.12 / §10.5 D6.

### Q9. 데이터 / 쿠키가 어디에 저장되나요?
**A.** 현재 (Phase 0):
- 다운로드 파일: `./downloads/` 또는 `-o` 지정 폴더
- 쿠키: `--cookies-from-browser`는 임시 추출 후 즉시 폐기 (저장 X)

향후 (Phase 1+):
- Windows: `%APPDATA%\YCollector\`
- macOS: `~/Library/Application Support/YCollector/`
- Linux: `~/.local/share/ycollector/`

라이브러리 DB는 SQLite 단일 파일. 쿠키는 OS 자격증명 저장소.

### Q10. yt-dlp를 직접 쓰는 것과 뭐가 다른가요?
**A.** YCollector는 yt-dlp를 코어로 쓰는 GUI/CLI 래퍼. 단순 한 영상은 yt-dlp 직접이 더 간결. YCollector의 가치:

| 기능 | yt-dlp 직접 | YCollector |
|---|---|---|
| 한 줄 다운로드 | ✓ (이미 간결) | ≈ |
| 큐 / 동시 / 일시정지 | shell 스크립트 | Phase 1 GUI |
| 라이브러리 + 검색 | (없음) | D1 (Phase 2) |
| 프리셋 + 채널별 오버라이드 | dotfiles | D2 (Phase 1+) |
| 가이디드 PoToken/쿠키 | 문서 grep | D3 (Phase 2) |
| 클립보드+브라우저 확장 | (없음) | D4 (Phase 2/3) |
| 채널 자동 동기화 | cron | D5 (Phase 3) |
| 로컬 Whisper 전사 통합 | (없음) | D6 (Phase 3) |

순수 한 줄 명령은 yt-dlp가 충분. YCollector는 **장기 사용 + 라이브러리 관리** 에 가치.

### Q11. macOS / Linux에서도 되나요?
**A.** 코드상으로는 지원. Phase 0에서는 Windows에 우선 최적화. Phase 2+에서 macOS notarization / Linux 패키징 정식 지원.

### Q12. 업데이트는 어떻게?

**Phase 0**:
```powershell
cd YCollector
git pull
uv sync
```

**Phase 4+**: GUI 내 자동 업데이트 알림 + 한 번 클릭 설치.

---

## 7. 향후 기능 (Roadmap 요약)

| Phase | 일정 (목표) | 주요 기능 |
|---|---|---|
| **0** | 진행 중 | CLI + 최소 GUI, 단일/일괄 URL, 기본 포맷/자막/쿠키 |
| **1** | 4~6주 | 큐, 영속화 (SQLite), 자가 갱신, mp3 추출, aria2 다중 연결, 자동 fallback, 진행률 정밀화 |
| **2** | 4~6주 | **D1** 라이브러리 + 자막 검색, **D2** 프리셋 + 채널 오버라이드, **D3** 가이디드 PoToken/쿠키, 재생목록 선택 UI |
| **3** | 4~6주 | 라이브 처음부터, **D4** 클립보드 + 브라우저 확장, **D5** 채널 스케줄, **D6** Whisper 전사 |
| **4** | 3~4주 | i18n (ko/en/ja), 자동 업데이트, 코드 사이닝, 다크모드, 접근성 |
| **5** | 지속 | 정식 배포 (인스톨러, CI 카나리, 텔레메트리 옵트인) |

자세한 일정 / 기능 분해: [plan §8 구현 로드맵](../plan/youtube_downloader_plan_260508.md#8-구현-로드맵).

---

## 8. 차별화 6가지 (D1~D6 — 미래 가치)

YCollector가 다른 OSS yt-dlp 래퍼와 다른 점. **현재 Phase 0에는 없고**, 로드맵에 따라 추가됩니다.

| ID | 이름 | 모티프 | Phase |
|---|---|---|---|
| **D1** | 라이브러리 + 태그 + 자막 검색 | Stacher (closed-source 대안) | 2 |
| **D2** | "Smart Mode" 프리셋 + 채널 오버라이드 | 4K Video Downloader | 1~2 |
| **D3** | 가이디드 PoToken / 쿠키 워크플로우 | (시장 빈틈) | 2 |
| **D4** | 클립보드 감시 + 브라우저 확장 | ByClick + Parabolic 결합 | 2~3 |
| **D5** | 채널별 스케줄 아카이빙 | Tartube를 모던 UI로 | 3 |
| **D6** | 로컬 Whisper 전사 | (시장 빈틈) | 3 |

상세: [plan §10.5 차별화 전략](../plan/youtube_downloader_plan_260508.md#105-차별화-전략-differentiation-strategy).

---

## 9. 도움 받기 / 기여

- **버그 / 기능 요청**: https://github.com/dalyulbam/YCollector/issues
- **소스 코드**: https://github.com/dalyulbam/YCollector
- **설계 문서 인덱스**: [`docs/index.html`](../index.html)
- **plan 전체**: [`docs/plan/youtube_downloader_plan_260508.md`](../plan/youtube_downloader_plan_260508.md)
- **경쟁/모티프 조사**: [`docs/motif/youtube_downloader_motif_260508.md`](../motif/youtube_downloader_motif_260508.md)

이슈 제출 시 포함해주시면 도움 됩니다:
- OS 버전 + Python 버전 (`python --version`)
- yt-dlp 버전 (`uv run yt-dlp --version`)
- YCollector 버전 (`uv run ycollector --version`)
- 실패 명령 전문
- 에러 메시지 / 스택 트레이스

---

**(끝)**
