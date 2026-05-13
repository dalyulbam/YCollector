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

    if "network" in parser:
        n = parser["network"]
        s.socket_timeout = _int_or_default(n.get("socket_timeout"), s.socket_timeout)
        s.retries = _int_or_default(n.get("retries"), s.retries)
        s.fragment_retries = _int_or_default(n.get("fragment_retries"), s.fragment_retries)
        tr = (n.get("throttled_rate") or "").strip()
        s.throttled_rate = tr or None

    return s, source


def write_default_config(path: Path) -> None:
    """Write a fully-commented template INI to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_TEMPLATE, encoding="utf-8")


# ── helpers ────────────────────────────────────────────────────────────────
def _resolve_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.exists() else None
    for c in (
        _user_config_dir() / _CFG_FILENAME,
        Path.cwd() / _CFG_FILENAME,
    ):
        if c.exists():
            return c
    return None


def _user_config_dir() -> Path:
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


_TEMPLATE = """# YCollector — settings.ini
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
cookies_from_browser =

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
"""
