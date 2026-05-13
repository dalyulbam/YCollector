# YCollector

YouTube 영상을 손쉽게 다운로드하기 위한 데스크톱 도구. **yt-dlp** 코어 + **PySide6** UI.

> **Phase 0 — Day 1**: 단일 URL CLI/GUI가 동작하는 최소 스캐폴딩.
> 자세한 설계는 [`docs/`](docs/) 또는 [`docs/index.html`](docs/index.html) 참고.

---

## 빠른 시작

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

# 7. GUI (radio 버튼 + "가용 포맷 보기" 다이얼로그)
uv run ycollector-gui
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
└── src/ycollector/
    ├── __init__.py
    ├── __main__.py            ← `python -m ycollector` 진입
    ├── cli.py                 ← CLI 진입 (argparse)
    ├── gui.py                 ← PySide6 메인 윈도우
    └── engine/
        ├── __init__.py
        └── ytdlp.py           ← subprocess 래퍼 + 진행률 파서 + 에러 분류
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
