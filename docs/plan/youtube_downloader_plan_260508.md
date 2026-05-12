# YCollector — YouTube 다운로더 설계 계획

- **문서 버전**: 1.2 (입력 UX + Transcribe + 컨피그 트리 반영, 2026-05-10)
- **작성일**: 2026-05-08 (YYMMDD: 260508), 최종 갱신 2026-05-10
- **상태**: Draft (active)
- **저자**: 초기 설계 초안
- **저장소**: `D:\26y\YCollector` (git: `main`)
- **연관 문서**: [`../motif/youtube_downloader_motif_260508.md`](../motif/youtube_downloader_motif_260508.md) — 경쟁 / 모티프 조사 (Parabolic, Stacher, Tartube, OVD, Seal, Cobalt 등 분석)

> **v1.2 변경 요약**: `§3.1.7~8` (입력 모드, Transcribe FR), `§4.4` 하이브리드 IPC(subprocess + Python API), `§6.12` Transcribe 기술 분석, `§10.5 D2/D5` typed 컨피그 명시 + `D6` Transcribe 차별화 추가, `§11.5` 입출력 워크플로우 (CLI/GUI), 새 부록 C (컨피그 트리 ↔ ydl_opts 매핑).
>
> **v1.1**: motif 조사로 `§1.1`, `§4.1`, `§4.2`, `§6.2`, `§7.1`, `§10`, `§13`, `§14` 업데이트 + 새 `§10.5 차별화 전략` 추가.

---

## 0. TL;DR (요약)

YouTube 영상을 손쉽게 다운로드하기 위한 데스크톱 프로그램(YCollector)을 만든다.

핵심 설계 원칙은 다음과 같다.

1. **추출(extract)/디시퍼링(deciphering) 로직을 직접 구현하지 않는다.** 이 영역은 YouTube가 가장 자주, 가장 공격적으로 바꾸는 부분이며, 단독으로 따라가는 것은 비현실적이다. 우리는 **`yt-dlp`** 를 코어 다운로드 엔진으로 채택하고, 그 위에 **사용자 경험(UX), 큐 관리, 인증, 자동 업데이트, 회복(recovery)** 계층을 얹는다.
2. **원치 않는 결합을 피한다.** 코어 엔진(yt-dlp)을 별도 프로세스로 격리하고, 우리는 표준화된 인터페이스(JSON, stdout 라인 프로토콜)로 통신한다. 이렇게 하면 yt-dlp 업데이트가 우리 앱을 깨뜨리지 않고, 필요하면 다른 엔진(gallery-dl, custom backend)으로 갈아끼울 수 있다.
3. **YouTube의 변경에 자동으로 적응한다.** 앱은 시작 시점과 다운로드 실패 시점에 yt-dlp를 자가-갱신하고, 핵심 회귀를 잡기 위한 카나리(canary) 테스트를 CI에서 매일 돌린다.
4. **법적/윤리적 가드레일.** 애초에 다운로드 가능한 콘텐츠(자기 영상, 공개도메인, CC, 본인 권한 보유 콘텐츠)에 초점을 두고, 사용자에게 책임 고지를 명확히 한다.

본 문서는 위 결정의 근거, 아키텍처, 위험요소, 대응 전략, 구현 로드맵을 다룬다.

---

## 1. 프로젝트 개요

### 1.1 목적 (Why)

- 일반 사용자가 **로컬에 영상을 보존**하고 싶을 때(예: 오프라인 시청, 강의 보관, 개인 백업), 명령행에 익숙하지 않은 사람도 쓸 수 있는 GUI 도구가 필요하다.
- 시장에는 도구가 많지만 각각 빈틈이 있다 (자세한 분석은 [motif 문서](../motif/youtube_downloader_motif_260508.md) 참고):
  - **Parabolic** (NickvisionApps, OSS, ★5.4k) — 모던 네이티브 UI(.NET NativeAOT)이지만 라이브러리/태그/스케줄링 부재, 크래시 이슈 빈발.
  - **Stacher** — 라이브러리/노트/태그/자막 검색 등 가장 풍부한 기능이지만 **폐쇄 소스**이며 Patreon 라이선스.
  - **Tartube** (★2.9k) — 스케줄링 / Missing Videos 감지 등 야심찬 기능이지만 **GTK3 UI가 노쇠**하고 진입장벽 높음.
  - **Open Video Downloader** (jely2002, ★8.2k) — Tauri+Vue로 작은 바이너리 검증, 그러나 **차별화 포인트가 부족**.
  - **4K Video Downloader Plus** — 상용. "Smart Mode" 등 UX 우수하지만 자유도 낮고 무료 티어 제한.
  - **ClipGrab** — 2024-11 마지막 의미있는 업데이트, 사실상 stale.
- YCollector의 포지셔닝: **Stacher 수준의 라이브러리 + Parabolic 수준의 모던 UI를, 오픈소스로** 제공한다. Windows first-class. 자세한 차별화 전략은 §10.5 참고.

### 1.2 목표 (Goals)

- G1. 단일 URL을 붙여 넣으면 화질/포맷을 골라 받을 수 있다.
- G2. 재생목록(Playlist), 채널(Channel), 검색결과 일괄 다운로드를 지원한다.
- G3. 라이브/예정 방송/Premiere/DVR 종료된 라이브에 대해 합리적인 동작을 제공한다.
- G4. 자막, 썸네일, 챕터, 메타데이터(작성자, 업로드 일자, 설명, 태그)를 함께 저장한다.
- G5. **yt-dlp 자동 업데이트** 및 회귀 시 사용자에게 명확한 안내.
- G6. 로컬 단일-바이너리 형태(또는 단일 인스톨러)로 배포한다.

### 1.3 비목표 (Non-Goals)

- N1. **DRM 보호 콘텐츠(YouTube Movies 구매, 일부 Music 구독 콘텐츠) 우회**. 명시적으로 지원하지 않는다.
- N2. **봇팜/대량 스크래핑 시나리오**. 이 도구는 개인 사용을 가정한다.
- N3. **저작권 침해 적극 조장**. 우리는 다운로드 전 사용자에게 책임 고지를 표시하고, 명백한 저작권 보호 콘텐츠에 대해서는 기본적으로 경고한다.
- N4. 자체 추출기(extractor)의 처음부터 작성. yt-dlp를 신뢰하고 그 위에 빌드한다.
- N5. 모바일 앱(iOS/Android). 데스크톱 first.

### 1.4 성공 지표 (Success Metrics)

- M1. 일반 공개 YouTube 영상에 대해 **다운로드 성공률 ≥ 99%** (CI 기준).
- M2. YouTube 측 변경 후 **24시간 이내 자동 복구**(yt-dlp 업데이트로 흡수).
- M3. 첫 사용자가 5분 안에 첫 영상을 받을 수 있다.
- M4. 1080p mp4 한 편 다운로드: 인터넷 대역폭의 **70% 이상 활용**.

---

## 2. 사용자 페르소나 & 시나리오

### 2.1 페르소나

| 페르소나 | 설명 | 핵심 니즈 |
|---|---|---|
| **A. 학습자 (Lecture Learner)** | 강의/튜토리얼을 오프라인 시청 | 재생목록 일괄, 자막 동시 다운로드, 챕터 보존 |
| **B. 크리에이터 (My-Backup)** | 자기 채널의 백업 | 메타데이터/원본 화질 보존, 정렬된 폴더 구조 |
| **C. 라이브 시청자 (Live Watcher)** | 라이브 종료 후 보관 | DVR 라이브 다운로드, 진행 중 라이브의 처음부터 받기 |
| **D. 음악 청취자 (Audio Ripper)** | 음원/팟캐스트만 추출 | 최고 비트레이트 오디오 추출, 챕터 → 분할 |
| **E. 아카이비스트 (Archivist)** | 채널 통째로 보존 | 채널 일괄, 메타/자막 포함, 중복 스킵 |

### 2.2 핵심 사용자 스토리

- **US-1**: 사용자는 URL을 입력창에 붙여넣고 "다운로드" 한 번으로 기본 옵션(1080p mp4 + 자막)을 받을 수 있다.
- **US-2**: 사용자는 화질/코덱/오디오만/포맷(mp4, webm, mkv, mp3)을 선택할 수 있다.
- **US-3**: 사용자는 재생목록 URL을 넣고 일부 영상만 체크해서 받을 수 있다.
- **US-4**: 사용자는 다운로드 큐에 여러 영상을 추가하고, 동시 다운로드 수를 조정할 수 있다.
- **US-5**: 사용자는 다운로드 실패 시 명확한 에러 메시지와 권장 조치(예: yt-dlp 업데이트, 쿠키 갱신)를 본다.
- **US-6**: 사용자는 자기 계정의 비공개/멤버십 영상을 받기 위해 쿠키 또는 OAuth로 로그인할 수 있다.
- **US-7**: 사용자는 진행 중인 라이브 방송을 "처음부터" 다운로드할 수 있다.
- **US-8**: 앱이 자동으로 백그라운드에서 yt-dlp를 갱신하고, 사용자에게는 알림만 표시한다.

---

## 3. 요구사항

### 3.1 기능 요구사항 (FR)

#### 3.1.1 다운로드
- FR-1. 단일 영상 URL → 다운로드.
- FR-2. 재생목록/채널 URL → 멤버 영상 나열 + 선택적 다운로드.
- FR-3. 화질 선택: 144p ~ 8K (가용한 경우), `best`, `bestvideo+bestaudio`, 사용자 정의 포맷 코드.
- FR-4. 컨테이너 선택: mp4, mkv, webm.
- FR-5. 오디오 전용: m4a, opus, mp3(트랜스코딩), flac(가능 시).
- FR-6. 자막: 모든 사용 가능 언어 / 선택 언어, 자동 생성 자막 포함 옵션.
- FR-7. 썸네일 다운로드 + 메타 임베딩(`-embed-thumbnail`).
- FR-8. 챕터 임베딩, 메타데이터(설명, 업로더, 일자) 임베딩.

#### 3.1.2 라이브 / 특수 콘텐츠
- FR-9. 진행 중인 라이브 방송: 현재부터 / **처음부터(`--live-from-start`)**.
- FR-10. 종료된 라이브의 DVR 다운로드.
- FR-11. Premiere(첫 공개): 시작 시간 대기 후 자동 다운로드.
- FR-12. 멤버십/연령제한 영상: 쿠키 또는 OAuth.

#### 3.1.3 큐 / 작업 관리
- FR-13. 동시 다운로드 수 설정 (1~8, 기본 2).
- FR-14. 일시정지 / 재개 / 취소.
- FR-15. 실패 시 자동 재시도 (지수 백오프).
- FR-16. 진행률 / 속도 / ETA 실시간 표시.
- FR-17. 다운로드 이력(history) 저장 및 검색.

#### 3.1.4 파일 / 저장
- FR-18. 출력 템플릿 사용자 정의: `%(uploader)s/%(playlist)s/%(title)s [%(id)s].%(ext)s` 등.
- FR-19. 중복 영상 감지 (영상 ID 기준) → 스킵 / 갱신.
- FR-20. 파일명 위생화(Windows 금지 문자, 길이 제한).

#### 3.1.5 설정 / 인증
- FR-21. 브라우저(Chrome, Edge, Firefox, Brave)에서 쿠키 임포트 (`--cookies-from-browser`).
- FR-22. 쿠키 파일 직접 임포트 (Netscape format).
- FR-23. 프록시 / VPN 지원.
- FR-24. 다국어 UI (최소: ko, en, ja).

#### 3.1.6 업데이트
- FR-25. 앱 시작 시 yt-dlp 버전 체크.
- FR-26. 자가-업데이트(앱 자체) 또는 인스톨러 안내.
- FR-27. 핫픽스 채널(stable / nightly).

#### 3.1.7 입력 모드 (자세한 워크플로우는 §11.5)
- FR-28. **단일 URL 붙여넣기** — 큰 paste 필드, Enter 또는 버튼 클릭.
- FR-29. **다중 URL 일괄 입력** — 한 줄에 하나, 빈 줄/주석(`#`) 허용. 폼 또는 multi-line textarea.
- FR-30. **파일 임포트** — `.txt` (URL 한 줄에 하나), `.csv` (URL 컬럼), `.json`/`.m3u` (선택), 드래그 앤 드롭.
- FR-31. **클립보드 감시** (옵트인) — YouTube/지원 도메인 URL 복사 시 시스템 알림 → 한 번 클릭으로 큐 추가 (D4의 일부).
- FR-32. **CLI 인자** — `ycollector URL1 URL2 ...` 또는 `ycollector --from urls.txt`.
- FR-33. **CLI stdin** — `cat urls.txt | ycollector` 또는 `ycollector -` (UNIX-style).
- FR-34. **재생목록 / 채널 URL 펼치기** — 페치 후 영상 리스트 보여주고 사용자가 선택.
- FR-35. **브라우저 확장 푸시** (Phase 3, D4) — 페이지에서 한 번 클릭으로 데스크톱 앱으로 URL 전송.

#### 3.1.8 전사 (Transcribe) — Phase 3, 자세한 기술 분석은 §6.12, 차별화는 §10.5 D6
- FR-36. **자동 자막 우선 사용** — yt-dlp가 가져온 사람 자막 / 자동 생성 자막이 있으면 그것을 사용.
- FR-37. **로컬 Whisper 전사 (옵트인)** — 자막이 없거나 사용자가 명시적으로 요청하면 `faster-whisper`로 로컬 전사.
- FR-38. **모델 선택** — tiny / base / small / medium / large-v3. 모델은 첫 사용 시 옵트인 다운로드.
- FR-39. **언어 자동 감지** + 사용자 강제 지정.
- FR-40. **GPU 가속** (CUDA/Metal/Vulkan via faster-whisper, 가능하면 자동 감지).
- FR-41. **출력 포맷**: `.srt` (기본), `.vtt`, `.txt` (단순 본문), `.json` (단어 단위 타임스탬프).
- FR-42. **임베드 옵션** — 비디오 컨테이너에 자막 트랙으로 임베드.
- FR-43. **라이브러리 통합** — 전사 본문을 SQLite FTS5 인덱스에 저장 → D1 (자막 검색)이 사람 자막 없는 영상까지 커버.
- FR-44. **클라우드 API 옵션** (선택) — OpenAI / AssemblyAI / Google STT. 기본 비활성, 명시 동의.

### 3.2 비기능 요구사항 (NFR)

| 카테고리 | 요구사항 |
|---|---|
| **성능** | 1080p 동영상 다운로드 시 인터넷 대역폭의 70% 이상 활용. 동시 4개 다운로드 시 UI 60fps 유지. |
| **안정성** | 네트워크 단절/재연결 후 자동 재개. 디스크 가득 참 등 시스템 에러를 사용자에게 명확히 표시. |
| **호환성** | Windows 10/11 (1차), macOS 12+ (2차), Linux x86_64 (3차). |
| **보안** | 코드 사이닝(Windows Authenticode, macOS Notarization). 자동 업데이트 시 서명 검증. |
| **프라이버시** | 텔레메트리는 기본 OFF, 옵트인. 쿠키/토큰은 OS 자격증명 저장소 또는 로컬 암호화. |
| **접근성** | 키보드 내비게이션, 스크린리더 호환(WCAG 2.1 AA 목표). |
| **로컬라이제이션** | UTF-8 파일명, 한글/일본어/이모지 안전 처리. |

---

## 4. 기술 스택 결정

### 4.1 다운로드 엔진 (Core)

후보:

| 엔진 | 장점 | 단점 |
|---|---|---|
| **yt-dlp** | 가장 활발한 유지보수, 1800+ 사이트, PoToken 등 최신 우회 지원, **3채널 릴리스**(stable/nightly/master) | Python 3.10+ 런타임 필요. 일부 YouTube 클라이언트는 외부 Deno JS 런타임 요구(2025-11~) |
| `youtube-dl` | (구) 안정적 | yt-dlp에 사실상 대체됨. 신규 도구에는 부적합. |
| pytube / pytubefix | API 단순, 의존성 없음 | YouTube 변경 대응 항상 yt-dlp보다 늦음. pytube 본체는 유지보수 위기. |
| Lux (Go) | 단일 바이너리, 중국 사이트(Bilibili 등) 강함 | YouTube 추출기 깨짐 빈도 ↑, 사이트 ~50개 vs yt-dlp 1800+ |
| 직접 구현 | 의존성 없음 | YouTube player JS가 자주 바뀌어 비현실적. NewPipeExtractor도 자체 구현으로 회복이 항상 늦음. |

**선택: `yt-dlp`** (subprocess). 함께 단일 바이너리로 동봉하거나 사용자 환경의 Python/시스템 yt-dlp를 활용한다.

**근거** (motif 조사 §7.1): 본 조사 통틀어 GUI 다운로더의 압도적 다수(Parabolic, Open Video Downloader, ytdlp-interface, Stacher, Video Downloader, Tartube, Seal, Persepolis 등)가 yt-dlp subprocess 모델을 사용. 자체 추출기를 채택한 모든 프로젝트(NewPipeExtractor, Lux, Cobalt)는 YouTube 변경 대응이 며칠~수주 늦음.

**미래 fallback** (Phase 6): `IExtractorBackend` 추상화로 Lux(중국 플랫폼) 또는 streamlink(라이브) 어댑터 추가 가능하도록 인터페이스 분리.

### 4.2 UI / 셸 프레임워크

| 프레임워크 | 장점 | 단점 | 검증된 경쟁자 |
|---|---|---|---|
| **Qt (PySide6)** | yt-dlp 내부 API 직접 import 가능(풍부한 에러 처리), Python 단일 스택, Persepolis가 운영 입증 | 라이선스(LGPL), 디자인 자유도 | Persepolis (★7.3k) |
| Tauri (Rust + Web) | 작은 바이너리(~25MB), 보안, 빠른 시작 | Rust 빌드 환경, WebView 차이 | Open Video Downloader (★8.2k) |
| .NET + 플랫폼별 네이티브 | 최고 폴리시(libadwaita + WinUI3), Parabolic 입증 | C#/.NET, 플랫폼별 코드 분기 | Parabolic (★5.4k) |
| Electron | 어느 환경에서도 동일하게 동작 | 큰 바이너리(150MB+), 메모리 사용 | Stacher (closed) |
| C++ + Nana | 최저 footprint(수 MB) | 디자인 자유도 ↓, Windows only | ytdlp-interface |
| Flutter Desktop | 깔끔한 디자인, 단일 코드 | 데스크톱 성숙도 ↓, 검증 사례 없음 | (없음) |

**v1 잠정 결정: PySide6**. 근거:

1. **yt-dlp가 Python으로 작성** → subprocess뿐 아니라 모듈로 import 가능. 풍부한 에러 객체 접근, 메타 추출 시 직렬화 비용 절약.
2. **개발 속도** — Python 단일 스택, 빠른 반복.
3. **검증된 경쟁자(Persepolis)가 동일 스택**으로 ★7.3k 운영 중.
4. **PyInstaller / Nuitka로 단일 바이너리**, Inno Setup / NSIS로 인스톨러 표준 패키징.

**Phase 1+ 평가 트리거**: PySide6에서 (a) 바이너리 100MB+, (b) 시작 지연 > 2초, (c) 디자인 한계 명확 — 이 중 둘 이상이면 Tauri로 마이그레이션 검토. OVD가 Tauri+Vue 모델을 운영 검증해 둔 길이 있어 위험은 낮다.

**비채택 사유**:
- **Tauri**: 좋은 옵션이지만 yt-dlp와의 결합도가 낮아 IPC 비용이 추가됨. 우리 팀에 Rust 경험이 부족할 가능성(재평가 가능).
- **.NET (Parabolic 모델)**: 결과물은 가장 우수하나 플랫폼별 UI 코드를 따로 유지해야 함 → v1 비용 과다.
- **Electron**: Stacher의 부풀린 footprint에 대한 사용자 불만 다수. 기각.
- **Flutter Desktop**: 검증된 yt-dlp 다운로더 사례가 없음. 리스크 회피.

### 4.3 보조 도구 / 의존성

- **FFmpeg**: 비디오/오디오 병합(DASH 분리 포맷), 컨테이너 변환, 트랜스코딩(필요 시), HLS muxing. 필수.
- **aria2**: 다중 연결 다운로드(선택). yt-dlp의 `--downloader aria2c`.
- **mkvtoolnix** (선택): mkv 컨테이너 조작.

### 4.4 IPC / 통합 방식 — 하이브리드 (subprocess + Python API)

yt-dlp를 임베딩(Python 모듈 import)할지, 별도 프로세스(subprocess)로 호출할지. **두 모드를 작업 종류에 따라 사용**.

| 작업 | 모드 | 이유 |
|---|---|---|
| **메타데이터 조회** (`extract_info`, 화면에 영상 정보 표시) | **Python API** (in-process) | 풍부한 dict 반환, JSON 직렬화 비용 0, 풍부한 에러 객체 |
| **실제 다운로드 + 후처리** | **subprocess** | 프로세스 격리(yt-dlp 충돌이 우리 앱을 안 죽임), yt-dlp 바이너리 핫스왑 가능 |
| **진행률 / 로그** | subprocess + stdout JSON (`--progress-template "json:%(progress)j" --newline`) | 격리 유지하면서 머신리더블 progress |
| **사용자 정의 후처리** (D5 플러그인) | Python API + `PostProcessor` 서브클래스 | yt-dlp의 PP 시스템 활용 |

**구현 메모**:
- 코어 엔진은 두 백엔드를 동일한 `IDownloadEngine` 인터페이스 뒤에 두고, 호출자가 모드를 모르도록 추상화.
- subprocess 모드는 자체 컨피그 트리(TOML) → `ydl_opts` dict → `opts_to_cli()` 직렬화 → `yt-dlp` 호출. 즉 항상 dict가 1차 진실이고 CLI는 출력 포맷.
- yt-dlp 자가 갱신 시: subprocess 모드는 바이너리 교체만; in-process 모드는 모듈 reload 또는 다음 앱 재시작에서 적용. → in-process는 갱신 빈도 낮은 메타 조회에만 사용해 마찰 ↓.

**컨피그 트리 ↔ ydl_opts 매핑**: 부록 C 참고.

---

## 5. 아키텍처

### 5.1 컴포넌트 다이어그램

```
+--------------------------------------------------------------+
|                          YCollector                           |
|                                                              |
|   +-------------+   +----------------+   +----------------+  |
|   |   UI Shell  |<->|  Core Service  |<->|  Update Mgr    |  |
|   | (React/Tauri|   |  (Rust/Python) |   |  (yt-dlp/app)  |  |
|   |  or PySide) |   |                |   +----------------+  |
|   +-------------+   |                |                       |
|                     |   +---------+  |   +----------------+  |
|                     |   | Queue   |  |<->|  Auth Vault    |  |
|                     |   | Manager |  |   |  (cookies)     |  |
|                     |   +---------+  |   +----------------+  |
|                     |       |        |                       |
|                     |       v        |   +----------------+  |
|                     |   +---------+  |<->|  Settings DB   |  |
|                     |   | Worker  |  |   |  (sqlite)      |  |
|                     |   |  Pool   |  |   +----------------+  |
|                     +---|---|---|---+                        |
|                         v   v   v                            |
|                   +---------------+                          |
|                   | yt-dlp procs  | --> [HTTP] --> YouTube   |
|                   | (subprocess)  |                          |
|                   +-------+-------+                          |
|                           |                                  |
|                           v                                  |
|                   +---------------+                          |
|                   | FFmpeg (mux,  |                          |
|                   | transcode)    |                          |
|                   +---------------+                          |
+--------------------------------------------------------------+
```

### 5.2 계층 분해

1. **UI Shell**: 화면 레이아웃, 입력 검증, 상태 표시. 비즈니스 로직 없음.
2. **Core Service**: 큐 매니저, 워커 풀, 작업 상태머신, 에러 분류, 재시도 정책.
3. **Engine Adapter**: yt-dlp 호출, stdout 파싱, 진행률 정규화. 향후 다른 엔진으로 교체 가능한 인터페이스.
4. **Auth Vault**: OS 자격증명 저장소(Windows Credential Manager, macOS Keychain, libsecret) 또는 AES-GCM 암호화 로컬 파일.
5. **Update Manager**: yt-dlp 자가-업데이트(`yt-dlp -U` 또는 별도 채널), 앱 자체 업데이트.
6. **Settings DB**: SQLite. 다운로드 이력, 큐, 사용자 설정.

### 5.3 작업 상태머신 (Job FSM)

```
   QUEUED → PREFLIGHT(메타조회) → DOWNLOADING → POSTPROCESS(병합/메타임베딩) → DONE
              |        |                |               |
              v        v                v               v
            FAILED  FAILED           FAILED          FAILED
              |
              v
        RETRY (max N) → ABANDONED
```

각 실패 상태에서 **에러 분류기**가 다음을 판단한다.

- **Retriable**: 네트워크/타임아웃 → 자동 재시도(지수 백오프).
- **Stale-extractor**: yt-dlp 시그니처/추출기 오류 → 자동 yt-dlp 업데이트 시도 후 재시도.
- **Auth-required**: 로그인/쿠키 필요 → UI에서 사용자에게 안내.
- **Geo-blocked**: 지역 제한 → 프록시 사용 안내.
- **Hard fail**: 영상 삭제, 비공개 → 사용자에게 단순 표시.

---

## 6. 핵심 도전과제 — Deep Dive

> 이 절은 본 계획의 핵심이다. YouTube를 다루는 모든 도구는 아래 문제의 어떤 조합에 부딪힌다.

### 6.1 YouTube 클라이언트의 동적 변화 (Player JS / Signature / n-sig)

**문제**:
- YouTube의 비디오 URL은 서명(signature)되며, 재생을 위해서는 player.js의 함수로 `s` 또는 `sig`를 디시퍼해야 한다. 또한 `n` 파라미터(쓰로틀링 토큰)가 있다.
- player.js는 자주 변경되며, 의도적으로 난독화되어 정적 분석으로 따라잡기 어렵다.
- 우리가 직접 디시퍼링을 구현하면, 며칠~몇 주 단위로 깨질 수 있다.

**대응**:
- **yt-dlp에 위임.** yt-dlp는 player JS를 동적으로 가져와서 함수를 추출하고 시그니처를 풀어준다. 이는 매우 활발하게 유지보수되며 PR 머지 → 릴리스가 빈번하다.
- 우리는 yt-dlp 버전을 **1주일 이상 묵히지 않는다**. 자동 업데이트 + nightly 채널 옵션.

### 6.2 봇 감지 / "Sign in to confirm you're not a bot" / PoToken

**배경**:
- 2024년 후반부터 YouTube는 익명 클라이언트에 대해 **PoToken(Proof of Origin Token)** 또는 **GVS(Google Visitor Session) PoToken**을 요구하기 시작했다. 이게 없으면 일부 화질이 차단되거나, 다운로드 자체가 거부되거나, "로그인하여 사용자가 봇이 아닌지 확인" 메시지가 뜬다.
- PoToken은 BotGuard 챌린지를 푸는 클라이언트 측 코드로 생성된다 — 즉, 헤드리스 브라우저 또는 웹뷰에서 실행되어야 한다.
- **2025-11 업데이트** ([yt-dlp PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide)): YouTube의 강화로 yt-dlp의 일부 클라이언트(특히 `web` 계열)는 이제 **외부 JS 런타임(Deno)을 요구**한다. 즉, YCollector는 Deno 또는 동등한 런타임을 옵션 의존성으로 다루어야 익명 다운로드가 안정적으로 작동한다.

**대응 옵션**(우선순위 순):

1. **쿠키 + 로그인된 세션 사용**(가장 권장): 사용자가 자신의 브라우저에서 로그인한 쿠키를 임포트하면, 대부분의 봇 감지를 우회한다. UX: "Chrome에서 로그인된 상태로 임포트" 한 번 클릭.
2. **PoToken provider 통합**: yt-dlp는 PoToken을 외부에서 주입받는 메커니즘이 있다. 별도 헬퍼 프로그램(예: `bgutil-ytdlp-pot-provider`, 또는 자체 헤드리스 Chromium)으로 PoToken을 발급한다. 별도 프로세스로 격리해 우리 앱의 보안 표면을 키우지 않는다.
3. **클라이언트 위장**: yt-dlp의 `--extractor-args "youtube:player_client=android,web,ios,tv_embedded"` 등으로 다른 클라이언트로 시도. 종종 어느 한 클라이언트는 PoToken 없이도 통과.
4. **속도 조절**: 짧은 시간 내 다수 요청은 봇 감지를 유발한다. 큐의 동시 실행을 사용자 설정과 무관하게 **요청 간 인터벌**을 강제(예: 동일 호스트당 분당 N회).

**우리 앱의 정책**:
- 기본 다운로드는 **익명 우선** + **로그인된 쿠키 권장 안내**.
- 봇 감지 에러가 감지되면 UI에서 **가이디드 워크플로우** 표시: (1) "쿠키 임포트 (브라우저 선택)", (2) "PoToken provider 활성화" (자동 Deno 감지/다운로드), (3) "클라이언트 위장 토글". 진단 메시지 + 다음 시도 명시.
- **PoToken provider는 v1에서 옵션, Phase 2~3에서 first-class 통합으로 격상** (motif §7.5: 가이디드 PoToken/쿠키 워크플로우는 시장 빈틈 중 최우선 차별화 기회).
- Deno 런타임은 사용자가 옵트인할 때만 다운로드(~30MB 추가). 시스템에 설치된 Deno 자동 감지.

### 6.3 접근 권한 — 비공개 / 멤버십 / 연령제한 / 지역제한 / Premium

| 콘텐츠 유형 | 다운로드 가능성 | 필요한 것 |
|---|---|---|
| 공개 영상 | O (대부분) | (없음) |
| 일부 차단(Limited) | O | 클라이언트 위장 |
| 연령제한 (Age-restricted) | △ | 로그인 쿠키 또는 OAuth |
| 비공개 (Private) | O (소유자/공유받은 자) | 본인 계정 쿠키 |
| 일부 공개 (Unlisted) | O (URL 알면) | 링크 |
| 멤버십 전용 | O (가입자) | 멤버 계정 쿠키 |
| 지역제한 (Geo-blocked) | △ | 해당 지역 IP / 프록시 |
| YouTube Music Premium | △ | Premium 구독 쿠키 (오디오 품질 ↑) |
| YouTube Movies 구매 | X | 시도하지 않는다 (DRM, 비목표) |
| Live (private/멤버) | △ | 멤버 쿠키 + DVR 켜진 경우 |

**구현 메모**:
- `--cookies-from-browser` 가 가장 신뢰성이 높다. 단, **브라우저가 닫혀 있어야** 한다(Chrome은 쿠키 DB를 잠근다). UI에서 이 점을 안내.
- OAuth는 Google 정책상 불안정(YouTube API 인증으로 다운로드 자체를 인증하는 것은 별개 문제). 쿠키 우선.

### 6.4 라이브 스트림 / Premiere

**라이브의 종류**:
- (a) **현재 진행 중**(LIVE NOW): HLS 매니페스트 기반.
- (b) **종료된 라이브 — DVR 있음**: 라이브 종료 후 일정 시간 동안 처음부터 끝까지 받을 수 있음.
- (c) **종료된 라이브 — DVR 없음**: 처리되지 않은 경우 일부만 또는 안 됨. 채널/콘텐츠 처리에 따라 VOD로 전환됨.
- (d) **Premiere(첫 공개)**: 정해진 시간에 공개되는 사전 업로드 영상.

**도전과제**:
- HLS의 청크는 일정 시간 후 만료. **실시간 추적 다운로드** 필요.
- `--live-from-start` 사용 시 처음부터 따라잡기 위해 빠른 다운로드가 필요. 라이브 종료까지 진행되어야 한다.
- 길이가 사전에 알려지지 않음(스트리머가 끄기 전까지). 디스크 공간 모니터링 필요.
- 끊김 / 재연결 처리.

**대응**:
- yt-dlp의 `--live-from-start --hls-use-mpegts` 조합 채택.
- UI에서 라이브 영상을 감지하면 다음 옵션을 제시:
  - 지금부터 받기 (저용량, 즉시)
  - 처음부터 받기 (시간/용량 큼, 체크박스로 경고)
  - 종료 후 자동 다운로드 (Premiere의 경우)
- 디스크 공간 가드: 사용자 설정값 미만이면 자동 일시정지.

### 6.5 영상 길이 / 대용량

**시나리오**:
- 8시간 라이브 → 4K 화질 시 50GB+
- 4K HDR → 비트레이트 50Mbps, 1시간 ≈ 22GB
- 고비트레이트 게임 라이브 → 100GB+

**대응**:
- **스트리밍 다운로드**: 메모리에 전체 보관 X, 디스크에 직접 청크 단위로.
- **재개 가능성**: yt-dlp는 partial 파일(`.part`) 지원, 실패 시 이어받기.
- **세그먼트 기반(HLS/DASH)**: 각 세그먼트를 독립적으로 다운로드, 실패한 세그먼트만 재시도.
- **디스크 모니터링**: 시작 전 예상 크기 표시(메타 조회로 알 수 있는 경우), 부족 시 경고.
- **분할 다운로드 옵션**: 매우 긴 영상(>4시간)은 챕터/시간단위 분할 다운로드 옵션 제공(yt-dlp `--download-sections "*0:00-1:00:00"`).

### 6.6 포맷 / 코덱 / 컨테이너

**현실**:
- YouTube는 **DASH** 또는 **HLS** 매니페스트로 비디오와 오디오를 **분리**해 제공. 즉 한 파일을 받는 게 아니라 V/A를 따로 받아서 **muxing** 한다.
- 비디오 코덱: **H.264 (AVC)**, **VP9**, **AV1** 혼재. AV1은 최신, 효율 좋지만 디코딩 부담 ↑.
- 오디오 코덱: **AAC (m4a)**, **Opus (webm)**.
- 컨테이너 호환성:
  - mp4: H.264 + AAC 가장 안전. AV1+Opus는 mkv 권장.
  - webm: VP9/AV1 + Opus.
  - mkv: 모든 조합 허용, 일부 플레이어/장치 호환성 ↓.

**대응**:
- 기본 포맷: **mp4 (H.264 + AAC)** — 호환성 우선.
- 사용자 옵션:
  - "최고 화질" → AV1+Opus (mkv 자동 선택)
  - "Apple 친화" → H.264+AAC (mp4)
  - "오디오만" → m4a 또는 mp3(트랜스코딩)
- FFmpeg 필수 의존성으로 번들. 사용자가 별도 설치하지 않아도 되도록.

### 6.7 속도 / 쓰로틀링 / 다중 연결

**문제**:
- YouTube가 **n-sig 토큰** 검증을 통해 의도적으로 단일 연결 속도를 떨어뜨리는 경우가 있다(과거 사례). 
- 다중 연결로 받으면 평균 속도를 끌어올릴 수 있지만, 봇 감지의 트리거.

**대응**:
- 기본은 단일 연결. yt-dlp의 n-sig 디시퍼에 의존.
- 고급 옵션: `aria2c` 다운로더로 전환 + 동시 연결 수(`-x 4`) 조정.
- 동시 영상 다운로드 수는 **2** 기본, 최대 4 권장 (그 이상은 봇 감지).

### 6.8 자막 / 다국어

**현실**:
- 자막 종류: 사람이 단 자막(quality ↑), 자동 생성 자막(YT auto-translate, quality 낮음).
- 포맷: 원본은 `vtt` 또는 `srv1/2/3` (XML), 변환해 SRT로 저장 가능.
- 자동 번역(translated) 자막은 별도 요청 — 종종 막힘.

**대응**:
- 사용자 옵션:
  - 사람 자막만 / 자동 생성 포함 / 모두
  - 언어 다중 선택 (ko, en, ja, zh, ...).
  - 임베드(mkv subtitle track) vs 별도 .srt 파일.
- yt-dlp 인자: `--write-subs --write-auto-subs --sub-langs "ko,en,ja"`.

### 6.9 메타데이터 / 챕터 / 댓글

- **메타데이터**: 제목, 업로더, 일자, 설명, 태그, 카테고리, 영상 ID. mp4/mkv tag 임베딩(`--embed-metadata`).
- **챕터**: 영상 챕터를 mkv chapter 또는 mp4 metadata에 임베딩(`--embed-chapters`).
- **썸네일**: `--embed-thumbnail` (mp4/mkv).
- **댓글**: yt-dlp `--write-comments`. 인기 영상은 수십만 개 → 매우 큼. 옵트인.
- **info.json**: 모든 메타를 사이드카 JSON으로 저장(`--write-info-json`). 아카이비스트 필수.

### 6.10 법적 / 윤리적 이슈

**관련 법규** (각국 다름, 우리는 일반적 가이드라인만 제공):
- YouTube **Terms of Service**: 다운로드는 일반적으로 금지(예외: YouTube가 다운로드 버튼을 명시적으로 제공한 경우 — Premium 다운로드는 YouTube 앱 내에서만 가능).
- 한국 **저작권법**: 사적 이용을 위한 복제는 일정 범위에서 허용되지만, 스트리밍 차단 회피는 별개 문제일 수 있음.
- 미국 **DMCA**: DRM 우회 금지. (우리는 DRM 미지원이므로 비교적 안전.)
- EU **DSM 지침**: 사용자 생성 콘텐츠 측면.

**우리의 정책**:
- 첫 실행 시 **명시적 책임 고지(EULA)**: "본 도구는 자기 콘텐츠, 공개도메인, CC 라이선스, 또는 명시적 권한이 있는 콘텐츠 다운로드를 위한 것입니다. 저작권 침해는 사용자 책임입니다."
- **저작권 콘텐츠 검출(약한 신호)**: 영상 제목/설명에 "Vevo", "Topic" (자동 생성 음악 채널), "© Sony" 등이 있을 때 추가 경고. 강제는 하지 않음.
- **우리는 우회 도구를 광고하지 않는다**: README/스토어에서 "사적 백업, 학습, 콘텐츠 크리에이터를 위한 도구"로 포지셔닝.
- 분석/텔레메트리에서 **URL 자체는 절대 수집하지 않는다**.

### 6.11 플랫폼 특이사항 (Windows)

- 파일명: `<>:"/\|?*` 금지 + 예약어(CON, PRN 등) 회피 → 위생화 함수 필수.
- 경로 길이 260자 제한 → `\\?\` 접두사 사용 또는 사용자 설정 최대 길이.
- 한글/이모지 파일명 → UTF-8 + NTFS 확장 영역. 콘솔 출력은 `chcp 65001` 또는 직접 콘솔 API.
- AV/Defender 오탐: yt-dlp 내장 PyInstaller 바이너리는 종종 PUA로 잡힘. 코드 사이닝 필수, EV 인증서 권장.

### 6.12 전사(Transcribe) — 음성 → 텍스트

**왜 필요한가**:
- D1 (라이브러리 + 자막 검색)이 사람 자막 / 자동 생성 자막이 있는 영상에서만 동작하면 시장 빈틈을 절반만 채움. **로컬 전사로 모든 영상에 자막을 만들면 D1이 라이브러리 전체에 적용됨** — Stacher가 못 하는 부분.
- 강의/세미나/팟캐스트 사용자에게는 **전사 자체가 일급 기능** (검색용이 아니라 읽기/노트 정리용).

**엔진 후보**:

| 엔진 | 라이선스 | 속도 | 품질 | 모델 크기 |
|---|---|---|---|---|
| **`faster-whisper`** | MIT (CTranslate2 기반) | 매우 빠름 (openai-whisper의 ~4×) | 동일 | tiny 39MB ~ large-v3 1.5GB |
| `openai-whisper` | MIT | 표준 | 표준 | 동일 |
| `whisper.cpp` | MIT | C++ 단일 바이너리, GGML 양자화 | 약간 ↓ | 양자화 모델 39MB ~ 1.1GB |
| `WhisperX` | BSD-4 | 단어 단위 정밀 타임스탬프 | 우수 | + 추가 정렬 모델 |
| OpenAI API | 상용 | 클라우드 | 우수 | 0 (API) |
| Google STT / AssemblyAI | 상용 | 클라우드 | 우수 (특히 한국어) | 0 |

**권장: `faster-whisper`** (1차) + 옵션으로 `whisper.cpp` (저사양 사용자) + 옵션으로 클라우드 API (속도/품질 우선 사용자).

**모델 크기 가이드**:

| 모델 | 한국어 품질 | 영어 품질 | RAM | CPU 1시간 영상 처리 시간 (대략) | GPU(CUDA) 처리 시간 |
|---|---|---|---|---|---|
| tiny  | 낮음 | 보통 | <1GB | ~10분 | ~1분 |
| base  | 보통 | 좋음 | ~1GB | ~20분 | ~2분 |
| small | 좋음 | 매우 좋음 | ~2GB | ~40분 | ~4분 |
| **medium** | **매우 좋음** | **우수** | ~5GB | ~60분 (실시간) | ~6분 |
| large-v3 | 우수 | 우수 | ~10GB | ~90~120분 | ~12분 |

→ 한국어 사용자 기본은 **medium**, 정확도 우선 사용자는 large-v3.

**도전과제**:

1. **모델 다운로드 / 캐시**: 250MB~1.5GB. 인스톨러에 번들하지 않고 첫 사용 시 다운로드(`~/.cache/ycollector/whisper/`). 사용자 동의 필수.
2. **GPU 감지**: NVIDIA(CUDA) / Apple Silicon(Metal/MPS) / AMD(ROCm/Vulkan). faster-whisper는 CTranslate2를 통해 CUDA + Metal 자동 감지. 시스템에 없으면 CPU fallback (느리지만 동작).
3. **메모리 압박**: large 모델은 RAM 10GB. 시스템 RAM 8GB 이하 사용자에게는 medium 또는 small 권장 (자동 감지).
4. **긴 영상**: 8시간 라이브의 전사는 medium CPU에서 8시간 걸림. 진행률 표시 + 백그라운드 처리 + 일시정지 필수.
5. **VAD (Voice Activity Detection)** 필요: 침묵 구간을 건너뛰면 5-10× 빨라짐. faster-whisper는 Silero VAD 통합 옵션.
6. **단어 단위 타임스탬프**: WhisperX 또는 faster-whisper의 `word_timestamps=True`. D1 검색에서 "이 단어가 영상의 02:34에 나옴" 점프에 활용.
7. **다국어 / 코드 스위칭**: 한국어 영상에 영어 단어 섞임 등. 자동 감지 + `language=None` (auto) 모드.
8. **품질 검증**: 전사 결과 신뢰도(`avg_logprob`, `no_speech_prob`)를 메타와 함께 저장. UI에서 신뢰도 낮은 구간 표시.

**대응 / 통합**:

- **파이프라인 위치**: 다운로드 → (병합/메타 임베드) → **전사(옵션)** → 라이브러리 인덱싱.
- **yt-dlp PostProcessor 서브클래스**로 구현해 PP 파이프라인에 자연스럽게 끼움 (자세한 구현은 D6 참고).
- **별도 큐**: 다운로드 큐와 전사 큐를 분리 (전사는 GPU/CPU 자원 다툼이 다른 양상). 사용자 설정으로 동시 1개 (CPU 점유) 또는 N개 (GPU 멀티 스트림).
- **"빠른 사람 자막 우선"** 정책: 사람이 단 자막이 있으면 그걸 그대로 사용, 자동 생성 자막만 있고 품질 낮다고 판단되면 옵션으로 재전사 제안.

**저장 형식**:
- `.srt` 사이드카 (표준)
- 라이브러리 DB의 `subtitles_fts` 가상 테이블에 본문 + 단어 타임스탬프 인덱싱 (SQLite FTS5)
- 옵션: `.json` (단어 단위 메타) — D1의 정밀 점프 기능에 사용
- 옵션: 비디오 컨테이너에 자막 트랙 임베드 (mkv 권장)

**라이선스 / 모델 출처**:
- Whisper 모델은 OpenAI MIT 라이선스 → 상업/비상업 모두 OK.
- faster-whisper는 CTranslate2 기반, MIT.
- 모델 다운로드는 Hugging Face mirror에서 (`Systran/faster-whisper-medium` 등).

---

## 7. YouTube 변경에 대한 적응 전략

> 이 절은 본 도구가 **장기적으로 살아남는 핵심**이다.

### 7.1 의존성 전략 — yt-dlp 우선

- **yt-dlp는 매일 ~ 며칠 단위로 릴리스된다**. 우리는 이를 **흡수**해야 한다.
- yt-dlp 자체가 **3채널** 릴리스를 제공: `stable`(월간), `nightly`(자동/일간), `master`(매 커밋). 우리는 이 모델을 그대로 사용자에게 노출한다.
- 채널 매핑 정책:

| YCollector 채널 | 번들 yt-dlp 채널 | 사용자 |
|---|---|---|
| **stable** | yt-dlp `stable`, 24시간 내 반영 | 일반 사용자 (기본값) |
| **beta** | yt-dlp `nightly` | YouTube 변경 직후 빠른 회복을 원하는 사용자 |
| **dev / canary** | yt-dlp `master` | 메인테이너, 디버깅 목적 |

- 사용자 환경에 시스템 yt-dlp가 있으면 자동 감지 + "시스템 yt-dlp 사용" 옵션 제공 (고급 사용자 친화적).

### 7.2 자가-업데이트 (Self-Update) 메커니즘

```
[App Start]
     │
     v
[yt-dlp 버전 체크 (last_check < 24h ?)]
     │
     ├── 최신 → 진행
     │
     └── 구버전 → [백그라운드 다운로드 → 다음 작업부터 적용]

[다운로드 작업 실패]
     │
     v
[에러 분류기]
     │
     ├── Stale-extractor 신호 (e.g., "unable to extract", "could not find sig function")
     │       │
     │       v
     │  [yt-dlp 강제 업데이트 → 같은 작업 1회 재시도]
     │
     ├── Bot detection 신호
     │       │
     │       v
     │  [쿠키 임포트 안내 UI]
     │
     └── 그 외 → 표준 재시도 정책
```

**구현 메모**:
- yt-dlp의 자가-업데이트(`yt-dlp -U`)는 PyInstaller 단일파일에서는 작동 어려움. 우리는 **별도 채널**로 yt-dlp 단일파일을 다운로드한다(GitHub releases).
- 서명 검증(GPG/SHA256) 후 원자적 교체.

### 7.3 에러 분류기 (Error Classifier)

yt-dlp의 stderr를 정규식 또는 토큰 매칭으로 분류:

| 신호 | 분류 | 자동 조치 |
|---|---|---|
| `Sign in to confirm you're not a bot` | bot-detect | 쿠키 안내 |
| `Video unavailable` | hard-fail | 사용자에게 표시 |
| `This video is private` | auth-required | 쿠키 안내 |
| `members-only content` | membership | 쿠키 안내 |
| `is age restricted` | age-gate | 쿠키 안내 |
| `unable to extract` / `n-sig` / `signature` | stale-extractor | yt-dlp 업데이트 → 재시도 |
| `HTTP Error 429` | rate-limit | 백오프 + 쿨다운 |
| `HTTP Error 403` | geo / blocked | 프록시 안내 |
| `Connection reset` / 타임아웃 | network | 자동 재시도 |
| `disk full` / `IOError 28` | disk | 일시정지 + 알림 |

### 7.4 카나리(Canary) 모니터링

- **CI 매일 실행 (GitHub Actions, free tier 가능)**:
  - 정해진 7~10개 영상 세트(공개 라이선스, 다양한 케이스) 다운로드.
  - 라이브, 짧은 영상, 긴 영상, 재생목록, 자막, 연령제한(만 있다면), AV1, VP9, HDR, Music, 챕터.
- 실패 → GitHub Issue 자동 생성 + 알림 → 메인테이너 개입.
- 일별 성공률 대시보드.

### 7.5 사용자 텔레메트리 (옵트인)

- 익명, **URL 미수집**.
- 수집 항목: 에러 코드 토큰, yt-dlp 버전, 앱 버전, OS, 발생 시각.
- 회귀 빠르게 감지 → 다음 yt-dlp 핫픽스 PR을 follow up.

### 7.6 다중 백엔드 (Long-term)

- 1년 후 yt-dlp가 어떤 이유로 더 이상 동작하지 않을 가능성에 대비.
- 추출기 어댑터를 인터페이스화: `IExtractorBackend` 추상화 → yt-dlp / yt-dlp(nightly) / streamlink / custom.
- Phase 6 항목.

---

## 8. 구현 로드맵

### Phase 0 — 프로토타입 (1~2주)

- [ ] `yt-dlp` 설치 검증, FFmpeg 번들 검증
- [ ] CLI 래퍼: URL → 다운로드 (mp4 1080p 기본)
- [ ] stdout 진행률 파싱 (정규식)
- [ ] 에러 분류기 v0
- [ ] **출력**: `python ycollector.py <URL>` 동작

### Phase 1 — GUI MVP (3~4주)

- [ ] PySide6 또는 Tauri 결정 (스파이크 1주)
- [ ] 메인 윈도우: URL 입력, 다운로드 버튼, 큐 리스트
- [ ] 진행률, 속도, ETA 표시
- [ ] 출력 폴더 선택
- [ ] 화질/포맷 드롭다운
- [ ] 단일 인스톨러 (Inno Setup or NSIS)

### Phase 2 — 핵심 기능 (4~6주)

- [ ] 재생목록 / 채널 다운로드 + 영상 선택 UI
- [ ] 쿠키 임포트 (브라우저 선택)
- [ ] 자막 / 썸네일 / 메타데이터 임베드
- [ ] 큐 영속화 (sqlite), 일시정지/재개/취소
- [ ] 동시 다운로드 수 설정
- [ ] 출력 템플릿 사용자화

### Phase 3 — 고급 (4~6주)

- [ ] 라이브 / Premiere 처리 (--live-from-start)
- [ ] 챕터 분할, 시간 구간 다운로드
- [ ] 다중 연결(aria2 통합) 옵션
- [ ] 자가-업데이트 (yt-dlp & 앱)
- [ ] 에러 분류기 v2 + 자동 복구

### Phase 4 — 인증 / UX (3~4주)

- [ ] PoToken provider 통합 (옵션)
- [ ] OAuth (실험적)
- [ ] 다국어 UI (ko/en/ja)
- [ ] 접근성(키보드, 스크린리더)
- [ ] 다크모드

### Phase 5 — 배포 / 유지보수 (지속)

- [ ] 코드 사이닝 (Windows, macOS notarization)
- [ ] 자동 릴리스 파이프라인 (GitHub Actions)
- [ ] CI 카나리 매일 실행
- [ ] 텔레메트리(옵트인) 백엔드
- [ ] 사용자 가이드, FAQ

### Phase 6 — 미래 (Optional)

- [ ] 다중 백엔드 추상화
- [ ] 모바일 백업 컴패니언 (선택)
- [ ] 클라우드 동기화 (선택)
- [ ] 플러그인 시스템 (사용자 후처리 스크립트)

---

## 9. 테스트 전략

### 9.1 단위 테스트

- 에러 분류기 (synthetic stderr 입력 → 분류)
- 진행률 파서
- 파일명 위생화
- 출력 템플릿 렌더링

### 9.2 통합 테스트 (CI 매일)

- "카나리 코퍼스": 다양한 케이스를 대표하는 7~10개 영상.
- 모든 빌드에서 실행 + 매일 nightly job.
- 결과: 성공률, 평균 속도, 평균 시작 지연.

### 9.3 회귀 테스트

- 기존에 동작하던 영상이 깨졌는가? Watch list.
- yt-dlp 업데이트 직후 자동 재실행.

### 9.4 수동 테스트 체크리스트

- 라이브 진행 중 다운로드 시작/중단/재개
- 4K HDR 다운로드, 오디오 싱크 확인
- 한글/이모지 제목 → 파일명/메타 확인
- 쿠키 만료 시 에러 흐름
- 디스크 가득 참 시 동작
- 인터넷 끊김 후 복귀

---

## 10. 위험 평가 (Risk Register)

| ID | 위험 | 영향 | 가능성 | 대응 |
|---|---|---|---|---|
| R1 | YouTube의 봇 감지 강화로 익명 다운로드 차단 | 高 | 中 | 쿠키 + PoToken 통합 |
| R2 | yt-dlp 프로젝트 중단 | 致命 | 低 | 다중 백엔드 추상화(Phase 6) |
| R3 | 법적 압박 (DMCA, 한국 저작권 협회) | 高 | 低~中 | 명확한 면책, 자기 콘텐츠 포지셔닝 |
| R4 | Windows Defender 오탐 | 中 | 高 | 코드 사이닝(EV 권장), Microsoft에 화이트리스트 신청 |
| R5 | 사용자가 자기 쿠키 잘못 노출 | 中 | 中 | OS 자격증명 저장소, 명확한 안내 |
| R6 | 4K/8K 다운로드 시 디스크 가득 → 시스템 영향 | 中 | 中 | 사전 디스크 가드 + 작업 일시정지 |
| R7 | YouTube가 PoToken 의무화 강화 → 헤드리스 브라우저 통합 필요 | 高 | 中 | Phase 3+ PoToken provider 통합 |
| R8 | 다국어 / 한글 메타데이터 손실 | 低 | 中 | UTF-8 강제, 임베딩 검증 테스트 |
| R9 | 의존 라이브러리(FFmpeg) 라이선스 문제 | 中 | 低 | LGPL 빌드 사용, 라이선스 명시 |
| R10 | 브라우저 확장 통합이 보안 표면 확대 | 中 | 中 | minimal permission(activeTab, clipboard), 코드 사이닝, OSS 감사 |
| R11 | OSS 출시 후 폐쇄 경쟁자(Stacher 등)가 기능 모방 + 폐쇄 유지 | 低 | 中 | 차별화는 코드가 아닌 운영 품질·커뮤니티·i18n. AGPL 검토(서버 모드 추가 시) |
| R12 | Deno 런타임 의존성으로 앱 크기 + 설치 복잡도 ↑ | 中 | 中 | 시스템 Deno 자동 감지, 옵트인 다운로드, PoToken 미사용도 fallback로 동작 |
| R13 | Parabolic식 크래시 빈발 | 中 | 中 | safe-mode 부팅(플러그인/프로파일 비활성), 자동 크래시 리포트(옵트인), CI smoke test |

---

## 10.5 차별화 전략 (Differentiation Strategy)

motif 조사(§7.5)에서 도출한 시장 빈틈 중 YCollector가 우선적으로 채울 6가지. v1~v3에 걸쳐 점진적으로 구현한다.

### D1. 라이브러리 + 태그 + 자막 텍스트 검색 (vs Stacher)
- **시장 상태**: Stacher Premium만 있음. 폐쇄 소스 + Patreon 라이선스로 r/DataHoarder 회의적.
- **YCollector 구현**: SQLite 라이브러리 테이블 + 사용자 태그 + **자막 본문 인덱싱(SQLite FTS5)** → "이 자막 본문에 'kubernetes'가 들어간 영상" 검색.
- **D6와 결합**: Whisper 전사로 사람 자막 없는 영상까지 검색 인덱스에 포함 → Stacher가 못 하는 영역.
- **Phase**: 라이브러리는 Phase 2, 자막 검색은 Phase 3.

### D2. "Smart Mode" 프리셋 (vs 4K Video Downloader)
- **시장 상태**: 상용에만 있고 OSS에서는 부재.
- **YCollector 구현**: 명명된 프리셋 ("강의 1080p+자막", "음악 320kbps", "백업 최고화질") + **채널/사이트별 오버라이드** + Seal식 명령 템플릿(고급).
- **구현 형태**: gallery-dl 영감의 **typed 컨피그 트리(TOML)**, deep-merge로 `defaults → preset → site → channel` 머지. UI는 컨피그를 편집하는 폼; CLI는 일회 오버라이드. **컨피그가 단일 진실** — yt-dlp `ydl_opts` dict는 매번 파생 (부록 C 매핑 표 참고).
- **Phase**: 기본 프리셋 Phase 1, 채널 오버라이드 + 템플릿 Phase 2.

### D3. 가이디드 PoToken / 쿠키 워크플로우
- **시장 상태**: 모든 GUI에서 가장 큰 사용자 마찰 (yt-dlp 스택트레이스 그대로 표시).
- **YCollector 구현**: 봇 감지 에러 즉시 인라인 위저드 — (1) 브라우저 선택 → 쿠키 임포트, (2) PoToken provider 활성화(Deno 자동 다운로드), (3) 클라이언트 위장 토글. 진단 + "다음 시도" 명시.
- **Phase**: v0 안내 메시지부터 시작, Phase 2까지 위저드 완성.

### D4. 클립보드 감시 + 브라우저 확장 (vs ByClick + Parabolic)
- **시장 상태**: ByClick에 클립보드만, Parabolic에 확장만. 둘을 결합한 곳 없음.
- **YCollector 구현**: 옵트인 클립보드 감시(YouTube URL 복사 시 시스템 알림 → 한 번 클릭으로 큐 추가) + 브라우저 확장(Chrome/Firefox/Edge) → 데스크톱 앱으로 URL 푸시. 모두 명시 동의.
- **Phase**: 클립보드 감시 Phase 2, 확장 Phase 3.

### D5. 채널별 스케줄 아카이빙 (vs Tartube, 모던 UI)
- **시장 상태**: Tartube가 기능은 있으나 GTK3 UI 노쇠, 신규 사용자 진입장벽.
- **YCollector 구현**: "이 채널을 매주 일요일 동기화" + Missing Videos 감지(YouTube 측 삭제 추적) + 채널별 프로파일.
- **구현 형태**: D2와 같은 **typed 컨피그 트리**의 `[channels."<channel_id>"]` 섹션. gallery-dl의 `extractor.youtube>CHANNEL_ID` 패턴 차용 (부록 C).
- **Phase**: Phase 3.

### D6. 로컬 Whisper 전사 (Local Transcription) — 신규
- **시장 상태**: yt-dlp 래퍼 GUI 어디에도 없음. Whisper UI는 별도 앱(MacWhisper, Buzz 등) 필요. **다운로더 + 전사 통합**은 시장에서 빈틈.
- **YCollector 구현**: `faster-whisper` 통합 → 다운로드 후 옵션으로 자동 전사. 모델은 옵트인 다운로드(250MB~1.5GB), GPU 자동 감지, 결과는 `.srt` + 라이브러리 FTS5 인덱싱(D1과 결합).
- **사용자 가치**:
  - 강의/팟캐스트/세미나 사용자에게 검색 가능한 노트
  - D1의 "자막 텍스트 검색"이 라이브러리 100%에 적용 (사람 자막 없는 영상도)
  - 한국어 영상의 정확한 전사 (medium 모델 이상)
- **자세한 기술 분석**: §6.12 참고.
- **Phase**: Phase 3 (D1과 동시 또는 직후).

### 비차별화 (의도적 회피)
- **편집 도구 (vs VideoProc)**: 사용자는 이미 익숙한 편집기 사용. 합치면 유지보수 비용만.
- **자체 추출기 (vs NewPipe)**: yt-dlp가 더 잘함.
- **셀프호스트 서버 모드 (vs Cobalt)**: v1 데스크톱 first; Phase 6 이후 검토.
- **DRM 우회**: 비목표(§1.3 N1).

### 차별화의 수단 vs 목적

코드/기능 차별화는 OSS 특성상 모방되기 쉽다. **장기 차별화는 (a) 신뢰할 수 있는 유지보수 케이던스, (b) 따라가기 쉬운 문서, (c) 적극적 i18n, (d) 한국어/일본어 first-class 지원**에서 온다. v1부터 이 운영 품질을 자산으로 빌드한다.

---

## 11. 운영 / 배포

### 11.1 빌드 / 패키징

- **Windows**: PyInstaller(또는 Tauri) → MSI/EXE 인스톨러 (Inno Setup).
- **macOS**: pyapp/py2app 또는 Tauri → .dmg, notarization.
- **Linux**: AppImage 또는 .deb/.rpm + flatpak.
- **번들 의존성**:
  - yt-dlp 단일 실행파일
  - FFmpeg (LGPL static 빌드)
  - aria2c (선택)

### 11.2 릴리스 채널

- **stable**: 4주 단위 릴리스, 안정성 우선.
- **beta**: 2주 단위, 신규 기능 테스트.
- **nightly**: 매일, yt-dlp nightly 동기화.

### 11.3 자동 업데이트

- 앱 자체: 표준 메커니즘(Squirrel / Sparkle / Tauri updater).
- yt-dlp: 우리가 별도로 관리(앱 안에서 yt-dlp 바이너리 교체).

### 11.4 텔레메트리 / 에러 리포팅 (옵트인)

- Sentry 자체호스팅 또는 GlitchTip → 익명 에러 트래킹.
- URL/계정 정보 절대 수집 X.
- 옵트인 시에도 사용자가 언제든 끌 수 있어야 함.

---

## 11.5 입력 / 출력 워크플로우 (UX)

요구사항 §3.1.7 (입력 모드)의 구체적 흐름. CLI와 GUI 양쪽을 1차 시민으로 설계한다.

### 11.5.1 CLI 워크플로우

**원칙**: UNIX-friendly. stdin / 인자 / 파일 모두 지원. 종료 코드 의미 있음.

```
# 1. 단일 URL
$ ycollector https://www.youtube.com/watch?v=dQw4w9WgXcQ
[1/1] Resolved: dQw4w9WgXcQ — Rick Astley - Never Gonna Give You Up
  Preset: default (mp4 1080p + ko/en subs)
  Output: D:/Videos/Rick Astley/Never Gonna Give You Up [dQw4w9WgXcQ].mp4
  ▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰▰ 100%  38.4 MB  12.3 MB/s  done in 0:03
✓ Saved.

# 2. 여러 URL을 인자로
$ ycollector URL1 URL2 URL3 --preset music

# 3. 파일에서 (한 줄에 하나; # 주석 / 빈 줄 OK)
$ ycollector --from urls.txt
Loaded 47 URLs (4 playlists expanded → 132 videos, 18 already in library — skipped)
Estimated: 24 GB, ~1h 40m at current bandwidth.
Continue? [Y/n] y

# 4. stdin 파이프
$ cat urls.txt | ycollector -
$ yt-dlp --flat-playlist -j PLAYLIST_URL | jq -r .url | ycollector -

# 5. 채널 동기화 (D5 — 새 영상만)
$ ycollector sync UC_MyFavoriteChannel
Sync: 3 new videos since last run (2026-05-08).

# 6. 재생목록 펼치기 + 인터랙티브 선택
$ ycollector --pick https://www.youtube.com/playlist?list=PL...
[ ] 1. (12:34) 첫 번째 영상
[x] 2. (08:21) 두 번째 영상
[x] 3. (15:02) 세 번째 영상
... ↑↓ 토글, Enter 확정, q 취소

# 7. 전사만 실행 (이미 로컬에 있는 파일)
$ ycollector transcribe ./video.mp4 --model medium --lang ko

# 8. 컨피그 강제 오버라이드
$ ycollector URL --format "bestaudio" --output-dir D:/Music --no-subs

# 9. dry-run (실제 다운로드 없이 계획만)
$ ycollector URL --dry-run
```

**종료 코드**:
- `0`: 모두 성공
- `1`: 부분 실패 (성공 + 실패 혼재)
- `2`: 모두 실패
- `3`: 사용자 취소
- `10+`: 시스템 에러 (디스크/네트워크/yt-dlp 미설치 등)

**서브커맨드 구조**:

| 명령 | 기능 |
|---|---|
| `ycollector <URL>` | 즉시 다운로드 (서브커맨드 생략 시 default) |
| `ycollector add <URL>` | 큐에 추가만 (다운로드 X) |
| `ycollector queue` | 현재 큐 표시 |
| `ycollector sync [<channel>]` | 등록 채널 동기화 |
| `ycollector transcribe <file>` | 로컬 파일 전사 |
| `ycollector library [search "kubernetes"]` | 라이브러리 조회/검색 |
| `ycollector preset list/save/edit` | 프리셋 관리 |
| `ycollector daemon` | 백그라운드 데몬 (클립보드 감시 등) |
| `ycollector update` | yt-dlp 강제 갱신 |
| `ycollector doctor` | 환경 진단 (yt-dlp, FFmpeg, Deno, 모델 캐시) |

### 11.5.2 GUI 워크플로우 (PySide6)

**메인 윈도우 레이아웃** (ASCII 와이어프레임):

```
┌─────────────────────────────────────────────────────────────────────┐
│  YCollector  [클립보드: ON]  [yt-dlp: 2026.05.07 ✓]    [⚙] [— □ ✕] │
├──────────┬──────────────────────────────────────────────────────────┤
│ 큐 (12)  │ ┌──────────────────────────────────────────────────────┐│
│ 라이브러리│ │ URL 붙여넣기 (한 줄에 하나, .txt 파일 드롭 가능)        ││
│ 채널 (8) │ │                                                       ││
│ 프리셋    │ │                                                       ││
│ 설정     │ └──────────────────────────────────────────────────────┘│
│          │  프리셋: [강의 1080p+자막 ▾]  폴더: [D:/Videos ▾]  [전사 □]│
│          │                              [큐에 추가]  [지금 다운로드] │
│          │ ─────────────────────────────────────────────────────── │
│          │ 큐 (3 진행 중)                                           │
│          │ ┌────────────────────────────────────────────────────┐  │
│          │ │● Title [채널] | 1080p | ▰▰▰▰▰░░░ 62% 12.3 MB/s    │  │
│          │ │● Title [채널] | 1080p | ▰░░░░░░░  8% 2.1 MB/s     │  │
│          │ │● Title [채널] | mp3   | 전사 중 (medium, GPU)      │  │
│          │ │○ Title [채널] | 1080p | 대기                       │  │
│          │ └────────────────────────────────────────────────────┘  │
│          │ ─────────────────────────────────────────────────────── │
│          │ 다운로드 합계: 24.3 MB/s  ETA 0:42                      │
│          │ 디스크 여유: 412 GB                                      │
└──────────┴──────────────────────────────────────────────────────────┘
```

**핵심 입력 흐름** (3가지):

1. **빠른 한 영상**:
   ```
   URL paste → "지금 다운로드" → (옵션 점검) → 큐에 즉시 들어가 시작
   ```
   상단 paste 박스에 한 줄 또는 다 줄. 클립보드 자동 감지 ON이면 **paste 안 해도** 토스트 알림 — "URL이 감지됨. 큐에 추가? [예 / 아니오 / 무시]".

2. **일괄 입력**:
   - 여러 URL을 paste (한 줄에 하나, `#` 주석 OK)
   - 또는 `.txt`/`.csv` 파일을 paste 박스에 **드래그 앤 드롭**
   - 또는 **재생목록 URL** 한 개 → 자동 펼침 → 영상 선택 다이얼로그 (`pick` 모드)

3. **채널 구독 (D5)**:
   - 채널 URL을 "채널" 사이드바에 드롭
   - "주기 / 프리셋" 설정 다이얼로그
   - 백그라운드에서 주기적 동기화

**라이브러리 탭** (D1 + D6 결합):

```
┌──────────────────────────────────────────────────────────────────┐
│ 검색: [ kubernetes ____________________________ ]  [태그: 강의 ▾] │
├──────────────────────────────────────────────────────────────────┤
│ ┌────┐ Kubernetes Networking Deep Dive                            │
│ │ ▶  │ TGI Kubernetes #5 | 2024-01-12 | 1:23:45                   │
│ │    │ "kubernetes" 매치 2회: 02:34, 18:12                         │
│ └────┘ [재생] [폴더 열기] [전사 보기] [태그 편집]                    │
│                                                                    │
│ ┌────┐ Production Kubernetes — KubeCon EU 2025                    │
│ │ ▶  │ KubeCon | 2025-03-15 | 0:42:18                             │
│ │    │ "kubernetes" 매치 47회: 00:15, 01:42, 03:08, ...            │
│ └────┘ [재생] [폴더 열기] [전사 보기] [태그 편집]                    │
└──────────────────────────────────────────────────────────────────┘
```

검색은 **자막 본문 + 전사 본문 + 제목/설명**을 모두 SQLite FTS5로 인덱싱. 매치 클릭 시 시스템 비디오 플레이어를 해당 타임스탬프로 점프시켜 실행 (가능한 경우 `mpv --start=02:34 file.mp4`).

### 11.5.3 출력 위치 / 파일 명명

기본 템플릿:
```
{output_dir}/{channel}/{playlist}/{title} [{id}].{ext}
```

각 부분 사용자 정의 (FR-18). gallery-dl 스타일로 `directory`(배열)와 `filename` 분리 제공:

```toml
[defaults]
output_dir = "D:/Videos"
directory = ["{uploader}", "{playlist}"]
filename = "{title} [{id}].{ext}"
```

**사이드카 파일** (옵션, 프리셋별):
- `{name}.info.json` — 모든 메타
- `{name}.{lang}.srt` — 자막 (사람/자동/Whisper 전사)
- `{name}.thumbnail.jpg` — 썸네일
- `{name}.transcript.json` — 전사 단어 단위 메타 (D1 정밀 점프용)

**중복 처리** (FR-19):
- 영상 ID 기준 라이브러리 DB 조회. 이미 있으면:
  - "스킵 / 더 좋은 화질로 갱신 / 강제 재다운로드" 다이얼로그.
- 디스크의 파일명만 중복인 경우 (다른 영상이 같은 제목): `_2`, `_3` 자동 suffix.

---

## 12. 디렉토리 / 코드 레이아웃 (제안)

```
YCollector/
├── README.md
├── LICENSE
├── docs/
│   ├── plan/
│   │   └── youtube_downloader_plan_260508.md   ← 본 문서
│   ├── motif/
│   │   └── youtube_downloader_motif_260508.md  ← 경쟁 / 모티프 조사
│   ├── user-guide/
│   └── architecture/
├── src/
│   ├── ycollector/
│   │   ├── config/                ← TOML 컨피그 + 스키마 + 머지 (D2/D5)
│   │   ├── engine/                ← yt-dlp 어댑터 (subprocess + Python API 하이브리드)
│   │   ├── queue/                 ← 작업 큐, 워커 풀, FSM
│   │   ├── classifier/            ← 에러 분류기 + 가이디드 워크플로우 (D3)
│   │   ├── auth/                  ← 쿠키/토큰 보관
│   │   ├── library/               ← SQLite 라이브러리 + FTS5 (D1)
│   │   ├── transcribe/            ← faster-whisper 통합 (D6)
│   │   ├── input/                 ← 클립보드 감시, 파일 임포트, 브라우저 확장 endpoint (D4)
│   │   ├── schedule/              ← 채널 동기화 스케줄러 (D5)
│   │   ├── settings/              ← 설정 / 사용자 데이터
│   │   └── update/                ← yt-dlp 자가-업데이트
│   ├── ycollector_cli/            ← CLI 진입점 (§11.5.1)
│   ├── ycollector_gui/            ← PySide6 GUI 진입점 (§11.5.2)
│   ├── services/                  ← PoToken provider, 외부 헬퍼
│   └── tests/
├── canary/                        ← CI 카나리 영상 목록 + 스크립트
├── installers/
│   ├── windows/   (Inno Setup)
│   ├── macos/     (DMG)
│   └── linux/     (AppImage, deb)
└── .github/
    └── workflows/
        ├── ci.yml
        ├── nightly.yml             ← 매일 카나리
        └── release.yml
```

---

## 13. 열린 질문 (Open Questions)

OQ-1. ~~**UI 프레임워크 최종 선택**~~ → **잠정 결정: PySide6 (v1)**. 근거 §4.2 참고. Phase 1 스파이크에서 (a) 바이너리 크기, (b) 시작 지연, (c) 디자인 자유도 측정 후 재평가. Tauri 대안은 OVD(★8.2k)가 검증해 둔 길.

OQ-2. **Python 런타임 동봉 방식**: PyInstaller 단일파일은 시작이 느리고 AV 오탐 가능성. 대안: Nuitka 컴파일. → Phase 0/1에 두 방식 모두 빌드해 시작 지연 / 바이너리 크기 / Defender 오탐 측정.

OQ-3. ~~**PoToken provider**~~ → **잠정 결정: 외부 헬퍼(`bgutil-ytdlp-pot-provider` 또는 동등) + Deno 옵트인 다운로드**. 근거 §6.2 + motif §7.5(가이디드 워크플로우는 우선 차별화 기회 — D3). 자체 헤드리스 Chromium은 앱 크기 / 유지보수 비용 과다.

OQ-4. **OAuth 지원 여부**: 쿠키 기반이 사실상 모든 케이스를 커버하지만, OAuth는 더 깔끔한 UX. Google 정책상 토큰 발급 / 검증이 까다로움.

OQ-5. **YouTube Music 특화 기능**: 플레이리스트 → 챕터 분할 → ID3 태그 자동 채움. Phase 4 후보.

OQ-6. **모바일**: Phase 6에서 검토, 또는 NewPipe 같은 기존 솔루션과의 컴패니언 동기화.

OQ-7. **법적 자문**: 정식 배포 전 한국 변호사 1회 자문 권장 (특히 텔레메트리 / 약관 / 면책).

OQ-8. **상업화**: 본 프로젝트는 OSS 무료인가, Pro 기능(클라우드 동기화 등)을 제공할 것인가? 라이선스 결정과 연결.

OQ-9. **컨피그 포맷 — TOML vs JSON vs JSONC**: TOML은 사용자 편집/주석 친화적, JSON은 도구 호환성 ↑, JSONC는 절충. 잠정 **TOML**(주석 + 단순 syntax). UI 편집은 항상 폼이라 직접 편집은 부수적. (부록 C 매핑 표 참고)

OQ-10. **Whisper 모델 다운로드 호스트**: Hugging Face가 표준이지만 일부 사용자가 차단된 환경. 미러 또는 자체 CDN 검토. 모델 무결성은 SHA256 검증.

OQ-11. **클라우드 STT 통합 우선순위**: D6의 옵션 단계. OpenAI(보편)/AssemblyAI(품질)/Google STT(한국어 우수). v1.0에서는 로컬 Whisper만, v1.1+에서 클라우드 옵션. API 키는 OS 자격증명에.

OQ-12. **클립보드 감시의 시스템 리소스**: 매 초 폴링 vs 시스템 클립보드 이벤트 hook. PySide6는 `QClipboard.dataChanged` 시그널 제공 → 폴링 불필요. macOS는 별도 hook 필요할 수 있음. 측정 후 결정.

---

## 14. 참고

### 핵심 의존성
- **yt-dlp**: https://github.com/yt-dlp/yt-dlp — 본 프로젝트의 코어 엔진, 활발한 유지보수.
- **yt-dlp PO Token Guide**: https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide — Deno 의존성 등 PoToken 처리.
- **FFmpeg**: https://ffmpeg.org/ — muxing, 트랜스코딩 표준.
- **aria2**: https://aria2.github.io/ — 다중 연결 다운로더 옵션.
- **bgutil-ytdlp-pot-provider**: PoToken 헬퍼 (Phase 2/3 통합).
- **Deno**: https://deno.land/ — PoToken 관련 일부 yt-dlp 클라이언트의 JS 런타임 의존성.

### 전사 (D6)
- **faster-whisper**: https://github.com/SYSTRAN/faster-whisper — CTranslate2 기반, openai-whisper 4× 빠름. v1 1차 후보.
- **openai-whisper**: https://github.com/openai/whisper — 표준 구현, 폴백.
- **whisper.cpp**: https://github.com/ggml-org/whisper.cpp — C++ 단일 바이너리, 양자화. 저사양 옵션.
- **WhisperX**: https://github.com/m-bain/whisperX — 단어 단위 정밀 정렬. D1 정밀 점프에 활용 검토.
- **Silero VAD**: https://github.com/snakers4/silero-vad — Voice Activity Detection, 침묵 구간 스킵.

### 컨피그 디자인 영감
- **gallery-dl**: https://github.com/mikf/gallery-dl — JSON 컨피그 트리, namespace 추출기. D2/D5 모티프.
- **rclone**: https://rclone.org/ — `~/.config/rclone/rclone.conf`의 multi-remote 패턴 참고.

### 경쟁 / 모티프 (자세한 분석은 [motif 문서](../motif/youtube_downloader_motif_260508.md))
- **Parabolic** (NickvisionApps): https://github.com/NickvisionApps/Parabolic — 모던 OSS 벤치마크 (.NET NativeAOT, libadwaita+WinUI3).
- **Open Video Downloader** (jely2002): https://github.com/jely2002/youtube-dl-gui — Tauri+Vue 검증 사례.
- **Stacher**: https://stacher.io — 라이브러리/태깅/자막검색 모티프 (closed-source).
- **Tartube**: https://github.com/axcore/tartube — 스케줄링 / Missing Videos 모티프.
- **Persepolis**: https://github.com/persepolisdm/persepolis — PySide6 운영 검증 사례.
- **Seal**: https://github.com/JunkFood02/Seal — UI / 명령 템플릿 디자인 모티프.
- **Cobalt**: https://github.com/imputnet/cobalt — 미니멀 UI 모티프, 오디오 언어 픽커.
- **NewPipeExtractor**: https://github.com/TeamNewPipe/NewPipeExtractor — 플러그인 SDK 참조.
- **streamlink**: https://github.com/streamlink/streamlink — 플러그인 컨트랙트 모범 사례.

---

## 15. 부록 A — 핵심 yt-dlp 인자 치트시트

```bash
# 기본 1080p mp4
yt-dlp -f "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]" \
       --merge-output-format mp4 URL

# 최고 화질, 포맷 자동
yt-dlp -f "bv*+ba/b" --merge-output-format mkv URL

# 오디오 추출 (m4a)
yt-dlp -f "ba[ext=m4a]" URL

# 자막 + 썸네일 + 메타 + 챕터 풀패키지
yt-dlp --write-subs --sub-langs "ko,en" --embed-subs \
       --embed-thumbnail --embed-metadata --embed-chapters \
       --convert-thumbnails png URL

# 라이브 처음부터
yt-dlp --live-from-start --hls-use-mpegts URL

# 쿠키 (브라우저)
yt-dlp --cookies-from-browser chrome URL

# 진행률 머신리더블
yt-dlp --newline --progress-template "download:%(progress.downloaded_bytes)s/%(progress.total_bytes)s" URL

# 재생목록 일부만
yt-dlp -I "1-10" PLAYLIST_URL

# 시간 구간만
yt-dlp --download-sections "*0:00-10:00" URL

# 다중 연결
yt-dlp --downloader aria2c --downloader-args "aria2c:-x 4 -k 1M" URL

# JSON으로 메타만 (UI 미리보기용)
yt-dlp --dump-single-json --no-playlist URL
```

## 16. 부록 B — 진행률 stdout 파싱 (예시)

`--newline --progress-template "...."` 를 사용하면 한 줄에 머신리더블한 키-값을 보낼 수 있다.

```
download:23.5%/512.34MiB at 12.3MiB/s ETA 00:42
```

또는 더 안전하게 JSON-on-stderr 방식:

```bash
yt-dlp --no-progress \
       --progress-template "json:%(progress)j" \
       --newline URL
```

→ `{"downloaded_bytes": 102400, "total_bytes": 524288000, "speed": 12345678, "eta": 42, ...}`

각 라인을 JSON 파싱해서 UI 상태에 매핑한다.

---

## 17. 부록 C — 컨피그 트리 ↔ ydl_opts 매핑

YCollector의 typed 컨피그 (TOML/JSON, gallery-dl 영감)는 yt-dlp의 `ydl_opts` dict로 1:1 또는 1:N 매핑된다. 이 표는 핵심 키만 — 시간이 지나면서 확장한다.

### 17.1 다운로드 / 포맷

| YCollector 키 | yt-dlp `ydl_opts` 키 | yt-dlp CLI 플래그 | 메모 |
|---|---|---|---|
| `format` | `format` | `-f / --format` | 그대로 통과 |
| `container` | `merge_output_format` | `--merge-output-format` | mp4/mkv/webm |
| `output_template` | `outtmpl` | `-o / --output` | placeholder 호환 |
| `directory` (배열) | `outtmpl` 일부로 join | (synthesized) | gallery-dl 스타일 |
| `filename` | `outtmpl` 일부로 join | (synthesized) | gallery-dl 스타일 |
| `audio_only` | `format='bestaudio'` + `extract_audio` PP | `-x` | 단축 |
| `audio_format` | `FFmpegExtractAudio` PP에 `preferredcodec` | `--audio-format` | mp3/m4a/opus |
| `audio_quality` | `FFmpegExtractAudio`의 `preferredquality` | `--audio-quality` | "best" or 번호 |

### 17.2 자막 / 메타 / 임베드

| YCollector 키 | yt-dlp `ydl_opts` 키 | yt-dlp CLI 플래그 | 메모 |
|---|---|---|---|
| `subtitles` (배열) | `writesubtitles=True`, `subtitleslangs=[...]` | `--write-subs --sub-langs` | 빈 배열 = false |
| `auto_subtitles` | `writeautomaticsub` | `--write-auto-subs` | bool |
| `embed.subs` | `FFmpegEmbedSubtitle` PP | `--embed-subs` | embed 배열의 멤버 |
| `embed.thumbnail` | `EmbedThumbnail` PP | `--embed-thumbnail` | |
| `embed.metadata` | `FFmpegMetadata` PP | `--embed-metadata` | |
| `embed.chapters` | `FFmpegMetadata(add_chapters=True)` | `--embed-chapters` | |
| `write_info_json` | `writeinfojson` | `--write-info-json` | bool |
| `write_thumbnail` | `writethumbnail` | `--write-thumbnail` | bool |
| `write_comments` | `getcomments` | `--write-comments` | bool, 옵트인 |

### 17.3 인증 / 네트워크

| YCollector 키 | yt-dlp `ydl_opts` 키 | yt-dlp CLI 플래그 | 메모 |
|---|---|---|---|
| `cookies_from_browser` | `cookiesfrombrowser=(name,)` | `--cookies-from-browser` | tuple, profile/keyring 추가 가능 |
| `cookies_file` | `cookiefile` | `--cookies` | Netscape 포맷 경로 |
| `proxy` | `proxy` | `--proxy` | URL 형식 |
| `username` / `password` | `username`, `password` | `-u / -p` | 가능하면 키체인 사용 |
| `extractor_args` (dict) | `extractor_args` (dict) | `--extractor-args` | 그대로 — gallery-dl namespace와 완전 매칭 |

### 17.4 라이브 / 다운로드 동작

| YCollector 키 | yt-dlp `ydl_opts` 키 | yt-dlp CLI 플래그 | 메모 |
|---|---|---|---|
| `live_from_start` | `live_from_start` | `--live-from-start` | bool |
| `hls_use_mpegts` | `hls_use_mpegts` | `--hls-use-mpegts` | 라이브 안정성 |
| `download_sections` | `download_ranges` (callback) | `--download-sections` | 시간 구간 |
| `concurrent_fragments` | `concurrent_fragment_downloads` | `-N` | DASH 세그먼트 병렬 |
| `external_downloader` | `external_downloader` | `--downloader` | aria2c/axel/curl |
| `external_downloader_args` | `external_downloader_args` | `--downloader-args` | dict, 다운로더별 |
| `retries` | `retries` | `--retries` | 정수 또는 "infinite" |

### 17.5 후처리 (D6 Whisper, D2 사용자 PP)

YCollector 컨피그의 `postprocessors` 배열은 yt-dlp의 `postprocessors` 리스트에 직접 매핑되지만, **YCollector 전용 PP** (Whisper, NAS 업로드 등)는 우리 어댑터가 추가:

```toml
[presets.lecture]
postprocessors = [
    { type = "embed_subs" },                              # → FFmpegEmbedSubtitle
    { type = "embed_metadata" },                          # → FFmpegMetadata
    { type = "transcribe", model = "medium", lang = "ko" },  # → YCollector 전용 (D6)
    { type = "set_metadata", fields = { genre = "Lecture" } },
    { type = "upload_nas", target = "\\\\NAS\\Lectures" }, # → 사용자 플러그인 (D5 SDK)
]
```

매핑 함수 (`adapter.py` 의 `to_ydl_opts`):

```python
PP_MAP = {
    "embed_subs":     lambda c: {"key": "FFmpegEmbedSubtitle"},
    "embed_metadata": lambda c: {"key": "FFmpegMetadata"},
    "embed_chapters": lambda c: {"key": "FFmpegMetadata", "add_chapters": True},
    "embed_thumbnail":lambda c: {"key": "EmbedThumbnail"},
    "extract_audio":  lambda c: {"key": "FFmpegExtractAudio",
                                  "preferredcodec": c.get("format", "m4a"),
                                  "preferredquality": c.get("quality", "best")},
}
YCOLLECTOR_PP = {
    "transcribe":   "ycollector.transcribe.WhisperPP",
    "set_metadata": "ycollector.engine.pp.SetMetadataPP",
    "upload_nas":   "ycollector.engine.pp.UploadNASPP",
    # ... 사용자 플러그인 (entry_points "ycollector.postprocessors")
}
```

### 17.6 머지 시맨틱 (gallery-dl 영감)

```
defaults
   ▼  deep-merge
sites.<host>
   ▼  deep-merge
presets.<active_preset>
   ▼  deep-merge
channels.<channel_id>
   ▼  deep-merge
CLI / UI 일회 오버라이드
   ▼
Effective Config
   ▼  to_ydl_opts()
ydl_opts (yt-dlp가 이해하는 dict)
```

머지 규칙:
- 객체 키: 깊은 머지 (`{a:1, b:2}` + `{b:3, c:4}` = `{a:1, b:3, c:4}`)
- 배열: **교체** (병합 아님). 명시적 머지 원하면 `{ extend = [...] }` 같은 syntax 도입 검토.
- 스칼라: 마지막 값이 이김.
- `None` / 명시적 unset: 부모의 키를 제거.

### 17.7 검증

스키마는 `pydantic` 또는 `jsonschema` 로 정의. `formaat = "..."` 같은 오타는 yt-dlp에 도달하기 전에 잡힘. CLI에서 `ycollector doctor` 가 컨피그 검증 + 마이그레이션 안내.

---

**(끝)**
