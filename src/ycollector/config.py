"""User-editable defaults loaded from ``settings.ini``.

Precedence (highest first):
    CLI flags  >  ``--config PATH``  >  user config dir  >  CWD settings.ini
                                                         >  code defaults

User config dir:
    Windows:  %APPDATA%\\YCollector\\settings.ini
    macOS:    ~/Library/Application Support/YCollector/settings.ini
    Linux:    ${XDG_CONFIG_HOME:-~/.config}/ycollector/settings.ini

Phase 0 ships flat INI (one section per concern). Plan §10.5 D2 calls for a
TOML tree (presets + channel overrides) in Phase 1+ — this module will then
migrate while keeping the same Settings dataclass surface.
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_CFG_FILENAME = "settings.ini"


@dataclass
class Settings:
    """Typed view over ``settings.ini``.

    Field defaults double as the values used when the file is absent — keep
    them in sync with the shipped ``settings.ini`` in the repo root.
    """

    # [defaults]
    quality: str = "1080p"
    codec: str = "auto"
    audio: str = "best"
    container: str = "mp4"

    # [output]
    output_dir: str = "downloads"
    embed_subs: bool = True
    sub_langs: list[str] = field(default_factory=lambda: ["ko", "en"])
    cookies_from_browser: str | None = None
    # Netscape cookies.txt 경로. None 이면 default_cookies_path() 자동 탐지.
    # 우선순위: cookies_file > cookies_from_browser.
    cookies_file: str | None = None

    # [playlist] — playlist handling
    playlist_mode: str = "auto"  # auto | expand | single
    max_downloads: int | None = None
    playlist_items: str | None = None

    # [network] — stall mitigation defaults (Phase 0 Day 3 recommendation)
    socket_timeout: int = 30
    retries: int = 10
    fragment_retries: int = 10
    throttled_rate: str | None = "100K"


# ── loader ─────────────────────────────────────────────────────────────────
def load_settings(explicit: Path | None = None) -> tuple[Settings, Path | None]:
    """Load :class:`Settings` from the first INI file found.

    Returns ``(settings, source_path or None)``. When no file is found, the
    code defaults are returned with ``source = None``.
    """
    source = _resolve_path(explicit)
    if source is None:
        return Settings(), None

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(source, encoding="utf-8")
    except (configparser.Error, OSError):
        return Settings(), None

    s = Settings()

    if "defaults" in parser:
        d = parser["defaults"]
        s.quality = d.get("quality", s.quality).strip()
        s.codec = d.get("codec", s.codec).strip()
        s.audio = d.get("audio", s.audio).strip()
        s.container = d.get("container", s.container).strip()

    if "output" in parser:
        o = parser["output"]
        s.output_dir = o.get("output_dir", s.output_dir).strip()
        try:
            s.embed_subs = o.getboolean("embed_subs", s.embed_subs)
        except ValueError:
            pass
        if "sub_langs" in o:
            s.sub_langs = [x.strip() for x in o["sub_langs"].split(",") if x.strip()]
        cb = (o.get("cookies_from_browser") or "").strip()
        s.cookies_from_browser = cb or None
        cf = (o.get("cookies_file") or "").strip()
        # 환경변수 expand — %APPDATA% 등.
        s.cookies_file = os.path.expandvars(cf) if cf else None

    if "playlist" in parser:
        pl = parser["playlist"]
        mode = (pl.get("mode") or s.playlist_mode).strip().lower()
        if mode in ("auto", "expand", "single"):
            s.playlist_mode = mode
        md = (pl.get("max_downloads") or "").strip()
        s.max_downloads = _int_or_default(md, s.max_downloads or 0) or None
        items = (pl.get("items") or "").strip()
        s.playlist_items = items or None

    if "network" in parser:
        n = parser["network"]
        s.socket_timeout = _int_or_default(n.get("socket_timeout"), s.socket_timeout)
        s.retries = _int_or_default(n.get("retries"), s.retries)
        s.fragment_retries = _int_or_default(n.get("fragment_retries"), s.fragment_retries)
        tr = (n.get("throttled_rate") or "").strip()
        s.throttled_rate = tr or None

    return s, source


def save_settings(s: Settings, path: Path | None = None) -> Path:
    """Persist *s* to INI.  Returns the path written."""
    dest = path or (user_config_dir() / _CFG_FILENAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    parser = configparser.ConfigParser(interpolation=None)
    parser["defaults"] = {
        "quality": s.quality, "codec": s.codec,
        "audio": s.audio, "container": s.container,
    }
    parser["output"] = {
        "output_dir": s.output_dir,
        "embed_subs": str(s.embed_subs).lower(),
        "sub_langs": ",".join(s.sub_langs),
        "cookies_from_browser": s.cookies_from_browser or "",
        "cookies_file": s.cookies_file or "",
    }
    parser["playlist"] = {
        "mode": s.playlist_mode,
        "max_downloads": str(s.max_downloads) if s.max_downloads else "",
        "items": s.playlist_items or "",
    }
    parser["network"] = {
        "socket_timeout": str(s.socket_timeout),
        "retries": str(s.retries),
        "fragment_retries": str(s.fragment_retries),
        "throttled_rate": s.throttled_rate or "",
    }
    with open(dest, "w", encoding="utf-8") as f:
        parser.write(f)
    return dest


def settings_to_dict(s: Settings) -> dict[str, object]:
    """Settings → JSON-friendly dict (React/web UI 와 호환)."""
    return {
        "quality": s.quality, "codec": s.codec, "audio": s.audio,
        "container": s.container, "output_dir": s.output_dir,
        "embed_subs": s.embed_subs, "sub_langs": ",".join(s.sub_langs),
        "cookies_from_browser": s.cookies_from_browser or "",
        "playlist_mode": s.playlist_mode,
        "max_downloads": str(s.max_downloads) if s.max_downloads else "",
        "playlist_items": s.playlist_items or "",
        "socket_timeout": s.socket_timeout, "retries": s.retries,
        "fragment_retries": s.fragment_retries,
        "throttled_rate": s.throttled_rate or "",
    }


def settings_from_dict(d: dict[str, object]) -> Settings:
    """JSON dict → Settings dataclass."""
    s = Settings()
    if "quality" in d:
        s.quality = str(d["quality"])
    if "codec" in d:
        s.codec = str(d["codec"])
    if "audio" in d:
        s.audio = str(d["audio"])
    if "container" in d:
        s.container = str(d["container"])
    if "output_dir" in d:
        s.output_dir = str(d["output_dir"])
    if "embed_subs" in d:
        v = d["embed_subs"]
        s.embed_subs = v if isinstance(v, bool) else str(v).lower() in ("true", "1", "yes")
    if "sub_langs" in d:
        raw = d["sub_langs"]
        if isinstance(raw, list):
            s.sub_langs = [x for x in raw if x]
        else:
            s.sub_langs = [x.strip() for x in str(raw).split(",") if x.strip()]
    if "cookies_from_browser" in d:
        cb = str(d["cookies_from_browser"]).strip()
        s.cookies_from_browser = cb or None
    if "playlist_mode" in d:
        s.playlist_mode = str(d["playlist_mode"])
    if "max_downloads" in d:
        v = d["max_downloads"]
        if v in (None, "", "null"):
            s.max_downloads = None
        else:
            try:
                s.max_downloads = int(v)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                pass
    if "playlist_items" in d:
        pi = str(d.get("playlist_items") or "").strip()
        s.playlist_items = pi or None
    if "socket_timeout" in d:
        s.socket_timeout = _int_or_default(str(d["socket_timeout"]), s.socket_timeout)
    if "retries" in d:
        s.retries = _int_or_default(str(d["retries"]), s.retries)
    if "fragment_retries" in d:
        s.fragment_retries = _int_or_default(str(d["fragment_retries"]), s.fragment_retries)
    if "throttled_rate" in d:
        tr = str(d["throttled_rate"]).strip()
        s.throttled_rate = tr or None
    return s


def write_default_config(path: Path) -> None:
    """Write a fully-commented template INI to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_TEMPLATE, encoding="utf-8")


# ── helpers ────────────────────────────────────────────────────────────────
def _resolve_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.exists() else None
    for c in (
        user_config_dir() / _CFG_FILENAME,
        Path.cwd() / _CFG_FILENAME,
    ):
        if c.exists():
            return c
    return None


def resolve_config_path(explicit: Path | None = None) -> Path | None:
    """``settings.ini`` 탐색 규칙의 공개 wrapper.

    전사(:mod:`ycollector.transcribe.config`) 등 다른 모듈이 동일한 파일·우선순위
    (``--config`` > 사용자 config dir > CWD)를 재사용하기 위한 진입점.
    """
    return _resolve_path(explicit)


def user_config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "YCollector"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "YCollector"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "ycollector"


def _int_or_default(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


_TEMPLATE = r"""# YCollector — settings.ini
# 모든 항목은 선택입니다. 빠진 키는 코드 기본값을 사용합니다.
# 우선순위:  CLI 인자  >  --config PATH  >  사용자 config dir  >  이 파일

[defaults]
# 화질: 144p | 240p | 360p | 480p | 720p | 1080p | 1440p | 2160p | best | audio
quality = 1080p
# 비디오 코덱: auto | h264 | vp9 | av1
codec = auto
# 오디오: best | m4a | opus
audio = best
# 컨테이너: mp4 | mkv | webm
container = mp4

[output]
# 출력 폴더 (상대 또는 절대 경로)
output_dir = downloads
# 자막 임베드 + 언어 (쉼표 구분)
embed_subs = true
sub_langs = ko,en
# 쿠키 임포트 브라우저 (chrome|firefox|edge|brave, 빈 값 = 사용 안 함)
# 주의: 해당 브라우저는 yt-dlp 실행 중 완전 종료돼있어야 함(파일 락 충돌).
# 메인 Chrome 을 켜둔 채로 쓰고 싶다면 아래 cookies_file 을 권장.
cookies_from_browser =

# Netscape HTTP Cookie File 경로 (예: %APPDATA%\YCollector\cookies.txt).
# 빈 값이면 ycollector 가 기본 위치를 자동 탐지. cookies_from_browser 보다 우선.
# 생성:  uv run ycollector-login   (별 Chromium 으로 로그인 → 자동 저장)
cookies_file =

[playlist]
# 재생목록 처리 모드.
#   auto    — "단일 영상 URL + ?list=..."는 단일 영상으로 처리. (권장)
#   expand  — 항상 재생목록 펼침.
#   single  — 항상 단일 영상.
mode = auto
# 펼침 시 N개까지만
max_downloads =
# 펼침 시 특정 항목만 (예: 1-10)
items =

[network]
# Phase 0 Day 3 멈춤 대응 기본값.
# socket_timeout: N초 동안 데이터 없으면 connection abort + retry
socket_timeout = 30
# retries: 연결 실패 재시도 횟수
retries = 10
# fragment_retries: DASH / HLS 프래그먼트 재시도
fragment_retries = 10
# throttled_rate: 다운로드 속도가 이 미만이면 connection 재시작.
# YouTube의 의도적 단일-연결 throttling 대응에 효과적.
# 빈 값 = 비활성. 예: 100K (= 100 KB/s), 1M (= 1 MB/s)
throttled_rate = 100K

[transcribe]
# 로컬 음성/영상 전사 (faster-whisper). 사용:
#   uv sync --extra transcribe --native-tls          # 1회 설치
#   uv run --extra transcribe ycollector transcribe <파일/폴더>
# 모델 별칭: tiny | base | small | medium | large-v3 | large-v3-turbo | turbo
# (또는 HuggingFace CT2 repo / 로컬 경로). 최초 1회 자동 다운로드.
model = large-v3-turbo
# 언어 코드(ko|en|fr|ja …) 또는 비움/auto = 자동 감지
language =
# transcribe = 원어 그대로, translate = 영어로 번역
task = transcribe
# 출력 포맷: txt | srt | vtt | json
output_format = txt
# 결과 저장 폴더(상대/절대)
output_dir = transcripts
# 추론 장치: auto(가능하면 CUDA) | cpu | cuda
device = auto
# 양자화: auto(cpu→int8, cuda→float16) | int8 | int8_float16 | float16 | float32
compute_type = auto
# 디코딩 beam size
beam_size = 5
# VAD(무음 구간 제거) 필터
vad_filter = true
"""
