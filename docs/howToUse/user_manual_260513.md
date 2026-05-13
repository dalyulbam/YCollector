# YCollector 사용설명서

- **문서 버전**: 1.3 (Phase 0 Day 3 기준 — settings.ini 추가)
- **작성일**: 2026-05-13 (YYMMDD: 260513), 최종 갱신 2026-05-13
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

### 3.1 메인 윈도우 (Phase 0 Day 2)

```
┌─────────────────────────────────────────────────────────────────────┐
│ YCollector v0.1.0                                       [— □ ✕]    │
├─────────────────────────────────────────────────────────────────────┤
│ URL                                                                  │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ URL 붙여넣기 (한 줄에 하나, '#' 주석)                              │ │
│ │ 예: https://www.youtube.com/watch?v=dQw4w9WgXcQ                  │ │
│ └──────────────────────────────────────────────────────────────┘ │
│                                                                      │
│ ┌─ 포맷 ──────────────────────────────────┐ ┌─ 출력 ───────────┐  │
│ │ 화질:    ○360 ○480 ○720 ●1080 ○1440 ○4K   │ │ 폴더:[D:/...] [폴더…]│ │
│ │          ○ 최고  ○ 오디오만                  │ │                    │ │
│ │ 컨테이너: ●mp4 ○mkv ○webm                  │ │ 자막: ☑ 임베드      │ │
│ │ 코덱:    ●자동 ○H.264 ○VP9 ○AV1            │ │   언어:[ko,en]     │ │
│ │ 오디오:  ●최고 ○m4a ○opus                  │ │                    │ │
│ │ [가용 포맷 보기…]   spec: bv*[height<=1080]+ba/b[height<=1080]    │ │ 쿠키: [chrome    ] │ │
│ └──────────────────────────────────────────────┘ └────────────────────┘ │
│                                                                      │
│                                              [지금 다운로드]           │
│                                                                      │
│ 로그                                                                  │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ yt-dlp 2026.05.07  (ycollector 0.1.0)                          │ │
│ │ [1/2] https://...                                                │ │
│ │   ✓ downloads/Channel/Title [id].mp4                             │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ 포맷 spec: bv*[height<=1080]+ba/b[height<=1080]                     │
└─────────────────────────────────────────────────────────────────────┘
```

**포맷 패널** — radio 버튼으로 모든 옵션 조절. 우하단의 `spec:` 표시는 현재 선택이 yt-dlp의 어떤 포맷 셀렉터로 변환되는지 실시간으로 보여줍니다 (디버깅 / 학습용).

**가용 포맷 보기** 버튼 — URL 박스의 첫 번째 URL을 yt-dlp로 분석해 그 영상에 실제로 가용한 모든 포맷(비디오/오디오 분리, 4K/1080p/HDR, AV1/VP9/H.264, m4a/opus 등)을 표로 보여줍니다. 행을 더블클릭하거나 "이 포맷 사용" 버튼으로 특정 포맷 ID를 직접 선택 가능 (라디오 선택은 일시 비활성화 → "오버라이드 해제"로 복귀).

```
┌─ 가용 포맷 — Rick Astley - Never Gonna Give You Up ──────────────┐
│ Rick Astley - Never Gonna Give You Up                              │
│ 채널: Rick Astley   길이: 3:33   ID: dQw4w9WgXcQ   포맷 23개         │
├──┬───┬─────┬──────────┬───┬──────────┬──────────┬───────┬──────┤
│ 종류             │ ID │ EXT │ 해상도    │FPS│ VCodec   │ ACodec   │ 크기  │ 노트  │
├──┴───┴─────┴──────────┴───┴──────────┴──────────┴───────┴──────┤
│ 비디오+오디오    │ 22 │ mp4 │ 1280x720 │ 30│ avc1.640 │ mp4a.40  │ 53MB │      │
│ 비디오           │137 │ mp4 │1920x1080 │ 30│ avc1.640 │          │ 87MB │ 1080p│
│ 비디오           │248 │webm │1920x1080 │ 30│ vp09     │          │ 67MB │ 1080p│
│ 비디오           │401 │ mp4 │3840x2160 │ 30│ av01     │          │450MB │ 4K   │
│ 오디오           │140 │ m4a │     -    │ - │          │ mp4a.40  │  5MB │ 128k │
│ ...                                                                 │
├────────────────────────────────────────────────────────────────────┤
│                                          [이 포맷 사용]  [취소]      │
└────────────────────────────────────────────────────────────────────┘
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

### 3.5 단일 .exe 빌드 — uv 없이 더블클릭으로 실행

매번 `uv run ycollector-gui` 입력이 번거로우면 **단일 실행 파일**로 빌드 가능.

```powershell
# 1. 빌드 의존성 설치 (Nuitka + PyInstaller)
uv sync --extra build

# 2-A. Nuitka로 빌드 (권장 — 시작 빠름, AV 오탐 ↓)
uv run python scripts/build_exe.py

# 2-B. 또는 PyInstaller (대안)
uv run python scripts/build_exe.py --mode pyinstaller

# 3. CLI .exe도 같이 빌드하려면
uv run python scripts/build_exe.py --target both
```

| 빌드 도구 | 결과 위치 | 크기 (대략) | 시작 지연 | AV 오탐 |
|---|---|---|---|---|
| **Nuitka** | `dist/gui.dist/YCollector.exe` | 80~120 MB | 1~2초 | 낮음 |
| **PyInstaller** | `dist/YCollector/YCollector.exe` | 130~180 MB | 3~5초 | 중간 |

**바탕화면 / 시작 메뉴에 등록**:
1. `dist/gui.dist/YCollector.exe` 우클릭 → **바로 가기 만들기**
2. 만들어진 바로가기를 바탕화면 또는 `시작 메뉴 > 프로그램`으로 이동
3. (선택) 우클릭 → 작업 표시줄에 고정

> **첫 빌드 시간**: Nuitka 8~15분, PyInstaller 3~8분 (PySide6 ~80MB 분석/패킹).
> Nuitka는 첫 실행 시 C 컴파일러(MinGW64)를 자동 다운로드 — 인터넷 + 약 200MB 디스크.
> 코드 사이닝 인증서는 Phase 4에서 도입 예정 (현재는 Windows Defender / SmartScreen 경고가 한 번 뜰 수 있음).

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

### 5.4 다운로드가 멈춘 것 같음 / 너무 느림

증상: 진행률이 1분 이상 변화가 없음, 또는 속도가 KB/s 단위로 떨어짐.

**즉시 대응** — 안전하게 중단해도 됩니다. **.part 파일이 남아 다시 실행하면 자동 이어받기**:

CLI:
```powershell
# Ctrl+C → 현재 작업 안전 중단
# 같은 명령으로 다시 실행 → 자동 이어받기
uv run ycollector URL ...
```

GUI:
- "취소 (이어받기 가능)" 버튼 클릭 → subprocess 안전 종료
- 같은 URL+옵션으로 다시 다운로드 → 이어받기

**적극 대응** — 멈춤이 잦으면 stall 감지 옵션 강화:

```powershell
# 15초 동안 데이터가 안 오면 abort+retry, 100KB/s 미만이면 connection 재시작
uv run ycollector URL --socket-timeout 15 --throttled-rate 100K --retries 20
```

| 옵션 | 기본값 | 의미 |
|---|---|---|
| `--socket-timeout SEC` | 30 | N초 동안 데이터 없으면 abort+retry |
| `--retries N` | 10 | 연결 실패 재시도 횟수 |
| `--fragment-retries N` | 10 | DASH/HLS 프래그먼트 재시도 |
| `--throttled-rate RATE` | (미설정) | 이 속도 미만이면 connection 재시작 (예: `100K`) |

**근본 대응**:
1. yt-dlp 최신 확인 (`uv lock --upgrade-package yt-dlp && uv sync`)
2. 시간대 변경 (YouTube 피크 시간대 회피)
3. 인터넷 자체 속도 점검
4. (Phase 1) aria2c 다중 연결 옵션

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

### 5.10 재생목록 URL에서 멈춤 (`youtu.be/...?list=...`)

증상: `youtu.be/<id>?list=<...>` URL로 실행 시 아무 출력 없이 1~5분 정지.

원인: yt-dlp가 재생목록 전체의 메타데이터를 먼저 가져옵니다. 100개+ 영상이면 시간이 걸리고, 그 동안 진행 표시가 없습니다.

**Phase 0 Day 3부터 자동 처리**: YCollector는 `youtu.be/<id>?list=...` 패턴을 감지하면 **단일 영상으로 처리**(yt-dlp의 `--no-playlist`)합니다:

```powershell
uv run ycollector "https://youtu.be/jxCleZOPxX8?list=PLbb..."
# → [i] 단일 영상 URL + ?list= 컨텍스트 감지 — 단일 영상만 받습니다.
#     재생목록 전체를 받으려면 --yes-playlist 추가.
```

전체 재생목록을 원하면:
```powershell
uv run ycollector URL --yes-playlist
# 또는 처음 10개만:
uv run ycollector URL --yes-playlist --max-downloads 10
# 또는 1-3, 7, 10번째 이후:
uv run ycollector URL --yes-playlist --playlist-items "1-3,7,10-"
```

`settings.ini` 의 `[playlist] mode` 로 기본 동작 전환:
- `auto` (기본) — 위 자동 처리
- `expand` — 항상 재생목록 펼침 (yt-dlp 본래 동작)
- `single` — 항상 단일 영상 (모든 `list=` 무시)

순수 재생목록 URL (`youtube.com/playlist?list=...`) 은 영향받지 않고 평소대로 펼쳐집니다.

### 5.11 디스크 가득 참

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

## 8.5 설정 파일 (settings.ini)

화질, 포맷, 자막, 멈춤 대응 등 **모든 기본값을 settings.ini에 적어두면 매번 플래그를 칠 필요 없습니다**.

### 파일 위치 (우선순위 순)

1. CLI 인자 `--config PATH` (최우선)
2. 사용자 config dir
   - Windows: `%APPDATA%\YCollector\settings.ini`
   - macOS:   `~/Library/Application Support/YCollector/settings.ini`
   - Linux:   `~/.config/ycollector/settings.ini`
3. 작업 디렉토리의 `./settings.ini` (저장소에 기본 파일 제공)
4. 코드 기본값 (위 셋 다 없을 때)

### 기본 제공 내용 (저장소 `settings.ini`)

```ini
[defaults]
quality = 1080p          # 144p/240p/360p/480p/720p/1080p/1440p/2160p/best/audio
codec = auto             # auto/h264/vp9/av1
audio = best             # best/m4a/opus
container = mp4          # mp4/mkv/webm

[output]
output_dir = downloads
embed_subs = true
sub_langs = ko,en
cookies_from_browser =   # chrome/firefox/edge/brave, 빈 값 = 사용 안 함

[network]
# Phase 0 Day 3 멈춤 대응 — 적극적 기본값.
socket_timeout = 30       # 30초 무응답이면 abort+retry
retries = 10
fragment_retries = 10
throttled_rate = 100K     # 100 KB/s 미만으로 떨어지면 connection 재시작
```

→ `throttled_rate = 100K`이 기본으로 활성화되어 있어, **별도 명령 없이도** YouTube의 의도적 throttling에 자동 대응합니다.

### 우선순위

```
CLI 플래그  >  --config PATH  >  사용자 config dir  >  ./settings.ini  >  코드 기본값
```

즉 `settings.ini`의 `quality = 1440p`을 적어두고 한 번만 1080p로 받고 싶다면:

```powershell
uv run ycollector URL --quality 1080p   # 이 한 번만 1080p
```

### 개인 설정 (저장소 파일을 안 건드리고)

저장소의 `settings.ini`는 git이 추적합니다. 개인 설정은 사용자 config dir에 두는 게 깔끔합니다.

```powershell
# Windows — 한 번만 실행
New-Item -ItemType Directory -Force $env:APPDATA\YCollector | Out-Null
Copy-Item settings.ini $env:APPDATA\YCollector\settings.ini

# 이제 이쪽을 편집하면 됩니다
notepad $env:APPDATA\YCollector\settings.ini
```

```bash
# macOS / Linux
mkdir -p ~/.config/ycollector
cp settings.ini ~/.config/ycollector/settings.ini
nano ~/.config/ycollector/settings.ini
```

### 흔한 커스터마이즈

**아카이비스트 (최고 화질, 모든 자막)**:
```ini
[defaults]
quality = best
codec = auto
container = mkv

[output]
sub_langs = ko,en,ja,zh,es,fr
```

**음악만 (mp3 변환은 Phase 1)**:
```ini
[defaults]
quality = audio
audio = m4a

[output]
embed_subs = false
```

**느린 인터넷 (timeout 늘리고 throttle 감지 끔)**:
```ini
[network]
socket_timeout = 120
throttled_rate =
retries = 20
```

**더 공격적 (멈춤이 잦은 환경)**:
```ini
[network]
socket_timeout = 15
throttled_rate = 200K
retries = 30
fragment_retries = 20
```

---

## 9. 이어받기 / 취소 / 멈춤 대응

### 9.1 이어받기 (Resume)

**자동입니다 — 별도 옵션 불필요.**

yt-dlp는 다운로드 중 영상별 임시 파일을 `<제목>.<id>.fNNN.<ext>.part` 같은 이름으로 저장합니다. 다음 둘 중 어느 상황이든 동일한 명령을 다시 실행하면 **이어받기**됩니다:

| 발생 상황 | 결과 |
|---|---|
| Ctrl+C로 중단 | `.part` 남음 → 재실행 시 이어받기 |
| GUI "취소" 버튼 | `.part` 남음 → 재실행 시 이어받기 |
| 네트워크 끊김 | yt-dlp가 자동 재시도 (`--retries 10`) |
| 컴퓨터 갑작스러운 종료 | `.part` 남음 → 재실행 시 이어받기 |
| 디스크 가득 → 실패 | `.part` 남음 → 공간 확보 후 재실행 |

**중요한 조건**: 같은 **URL + 출력 폴더 + 출력 템플릿 + 포맷**일 때만 이어받기됨. 셋 중 하나라도 다르면 새로 받습니다.

### 9.2 취소

**CLI**:
- `Ctrl+C` 한 번 → 안전하게 subprocess 종료. `.part` 보존. 종료 코드 `3`.
- 친절한 안내 메시지가 표시됩니다.

**GUI**:
- "지금 다운로드" 버튼이 다운로드 중에는 빨간색 **"취소 (이어받기 가능)"** 버튼으로 바뀝니다.
- 클릭 → 현재 작업 즉시 종료 (5초 내 정리, 그 후 강제 kill).
- 다음 큐 작업도 함께 중단됩니다 (1개 → 1개씩 처리).

### 9.3 멈춤 감지 / 자동 재시작

기본값으로 다음 보호장치가 작동합니다:

| 보호장치 | 기본 |
|---|---|
| `--socket-timeout 30` | 30초 동안 데이터가 안 오면 socket abort → 자동 재시도 |
| `--retries 10` | 연결 실패 시 최대 10회 재시도 |
| `--fragment-retries 10` | DASH/HLS 프래그먼트 실패 시 최대 10회 재시도 |

**더 적극적**:
```powershell
# 짧은 timeout + throttle 감지 + 더 많은 retry
uv run ycollector URL --socket-timeout 15 --throttled-rate 100K --retries 20
```

`--throttled-rate 100K` — 다운로드 속도가 100KB/s 미만으로 떨어지면 yt-dlp가 connection을 재시작. YouTube 측 throttling 대응에 효과적.

### 9.4 자주 발생하는 문제

| 증상 | 원인 가능성 | 즉시 대응 |
|---|---|---|
| 진행률이 1분+ 정지 | TCP 연결 동결 | Ctrl+C → 재실행 (자동 이어받기) |
| 속도가 KB/s 단위 | YouTube throttling | `--throttled-rate 100K` 추가 |
| HLS 라이브 일부 누락 | 프래그먼트 실패 | `--fragment-retries 30` |
| 자정 즈음에만 빠름 | 피크 시간 throttling | 시간대 변경 |
| 항상 1080p에서 느림 | 봇 감지 (저화질로 강등) | 쿠키 임포트 (5.3) |

---

## 10. 도움 받기 / 기여

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
