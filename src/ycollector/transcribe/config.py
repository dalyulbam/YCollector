"""전사 설정 — ``settings.ini`` 의 ``[transcribe]`` 섹션.

YCollector 의 다운로드 설정(:mod:`ycollector.config`)과 **같은 파일·같은 탐색
규칙**을 쓰되, 전사 전용 키만 별도 dataclass 로 본다. 다운로드 UI(React/Tauri)
로 흘러가는 중앙 :class:`~ycollector.config.Settings` 를 전사 키로 오염시키지
않으려는 분리다.

이 모듈은 의도적으로 **가볍다** — ``faster_whisper`` / ``ctranslate2`` 를 import
하지 않으므로 ``ycollector transcribe --help`` 가 무거운 추론 런타임을 로드하지
않는다. 실제 엔진은 :mod:`ycollector.transcribe.whisper` 에 있고 전사 직전에만
지연 import 한다.
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from ycollector.config import resolve_config_path

# 전사 대상 미디어 확장자 (원본 transcriber 의 MEDIA_EXTENSIONS 계승).
MEDIA_EXTENSIONS: tuple[str, ...] = (
    ".mp3", ".mp4", ".wav", ".m4a", ".mov", ".mkv",
    ".flac", ".ogg", ".webm", ".aac", ".wma", ".opus",
)
# 출력 포맷.
OUTPUT_FORMATS: tuple[str, ...] = ("txt", "srt", "vtt", "json")

_SECTION = "transcribe"


@dataclass
class TranscribeConfig:
    """``[transcribe]`` 섹션의 타입드 뷰.

    필드 기본값이 곧 파일이 없을 때의 값 — 리포 루트 ``settings.ini`` 및
    :data:`ycollector.config._TEMPLATE` 의 ``[transcribe]`` 와 동기 유지.
    """

    # faster-whisper 모델 별칭(tiny|base|small|medium|large-v3|large-v3-turbo …)
    # 또는 HuggingFace CT2 repo / 로컬 경로.
    model: str = "large-v3-turbo"
    # 언어 코드(ko|en|fr|ja …). None/빈 값 = 자동 감지.
    language: str | None = None
    # transcribe(원어 그대로) | translate(영어로 번역).
    task: str = "transcribe"
    # 출력 포맷: txt | srt | vtt | json.
    output_format: str = "txt"
    # 전사 결과 저장 폴더(상대/절대).
    output_dir: str = "transcripts"
    # 추론 장치: auto(가능하면 CUDA) | cpu | cuda.
    device: str = "auto"
    # 양자화: auto(cpu→int8, cuda→float16) | int8 | int8_float16 | float16 | float32.
    compute_type: str = "auto"
    # 디코딩 beam size.
    beam_size: int = 5
    # VAD(무음 구간 제거) 필터.
    vad_filter: bool = True


def load_transcribe_config(explicit: Path | None = None) -> tuple[TranscribeConfig, Path | None]:
    """:class:`TranscribeConfig` 를 ``settings.ini`` 의 ``[transcribe]`` 에서 로드.

    탐색 규칙은 :func:`ycollector.config.resolve_config_path` 와 동일
    (``--config`` > 사용자 config dir > CWD). 파일/섹션이 없으면 코드 기본값.
    Returns ``(cfg, source_path or None)``.
    """
    cfg = TranscribeConfig()
    path = resolve_config_path(explicit)
    if path is None:
        return cfg, None

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError):
        return cfg, path

    if _SECTION not in parser:
        return cfg, path

    t = parser[_SECTION]
    cfg.model = (t.get("model") or cfg.model).strip()
    lang = (t.get("language") or "").strip()
    cfg.language = None if lang.lower() in ("", "auto") else lang
    cfg.task = (t.get("task") or cfg.task).strip()
    cfg.output_format = (t.get("output_format") or cfg.output_format).strip().lower()
    cfg.output_dir = (t.get("output_dir") or cfg.output_dir).strip()
    cfg.device = (t.get("device") or cfg.device).strip().lower()
    cfg.compute_type = (t.get("compute_type") or cfg.compute_type).strip().lower()
    try:
        cfg.beam_size = t.getint("beam_size", cfg.beam_size)
    except ValueError:
        pass
    try:
        cfg.vad_filter = t.getboolean("vad_filter", cfg.vad_filter)
    except ValueError:
        pass
    return cfg, path
