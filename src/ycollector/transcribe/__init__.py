"""로컬 음성·영상 전사 (faster-whisper, in-process).

원본 ``D:\\26y\\transcriber`` 의 openai-whisper CLI 런처를 YCollector 에 편입하며
faster-whisper 라이브러리 직접 호출로 전환한 모듈. plan §6.12 / §10.5 D6.

진입점
------
    ycollector transcribe ...        (cli.py 가 본 다운로드 파서 진입 전에 선분기)
    ycollector-transcribe ...        (console_script)

가벼운 설정 심볼(:class:`TranscribeConfig` 등)은 즉시 import 가능하지만, 엔진
(:class:`TranscribeEngine`)은 ``faster_whisper``/``ctranslate2`` 를 끌어오므로
``__getattr__`` 로 **지연 노출**한다 — ``--help`` 등에서 무거운 런타임을 피한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import (
    MEDIA_EXTENSIONS,
    OUTPUT_FORMATS,
    TranscribeConfig,
    load_transcribe_config,
)

if TYPE_CHECKING:  # 타입 체커에는 실제 심볼 노출, 런타임엔 지연.
    from .whisper import (
        Segment,
        TranscribeEngine,
        TranscribeError,
        TranscriptResult,
        write_transcript,
    )

__all__ = [
    "MEDIA_EXTENSIONS",
    "OUTPUT_FORMATS",
    "Segment",
    "TranscribeConfig",
    "TranscribeEngine",
    "TranscribeError",
    "TranscriptResult",
    "load_transcribe_config",
    "write_transcript",
]

_LAZY = {"Segment", "TranscribeEngine", "TranscribeError", "TranscriptResult", "write_transcript"}


def __getattr__(name: str):  # PEP 562 — 엔진 심볼은 실제 사용 시점에 import.
    if name in _LAZY:
        from . import whisper

        return getattr(whisper, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
