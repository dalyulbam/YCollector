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

# 2. CLI — 단일 URL
uv run ycollector https://www.youtube.com/watch?v=dQw4w9WgXcQ

# 3. CLI — 여러 URL
uv run ycollector URL1 URL2 URL3 -o D:/Videos --container mkv

# 4. CLI — 파일에서 (한 줄에 하나, '#' 주석)
uv run ycollector --from urls.txt

# 5. CLI — stdin 파이프
Get-Content urls.txt | uv run ycollector -

# 6. GUI
uv run ycollector-gui
```

기본 동작: `bv*[height<=1080]+ba/b[height<=1080]` (1080p mp4) + ko/en 자막 임베드 → `./downloads/`.

플래그 일부:
- `-f / --format` — yt-dlp 포맷 셀렉터
- `-o / --output-dir` — 출력 폴더 (기본 `./downloads`)
- `--container {mp4,mkv,webm}` — 머지 컨테이너
- `--no-subs` — 자막 다운로드 스킵
- `--sub-langs ko,en,ja` — 자막 언어
- `--cookies-from-browser chrome` — 비공개/연령제한/멤버십 (브라우저는 닫혀 있어야 함)

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
