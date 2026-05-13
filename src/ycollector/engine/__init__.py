"""Engine adapter — yt-dlp subprocess + (장차) Python API 하이브리드.

Plan §4.4 참고.
"""

from .format_spec import (
    AudioPref,
    CodecPref,
    Container,
    FormatChoice,
    Quality,
    compose_format_spec,
    spec_for_format_id,
)
from .ytdlp import (
    DownloadError,
    MetaInfo,
    ProgressEvent,
    YtdlpEngine,
    classify_error,
)

__all__ = [
    "AudioPref",
    "CodecPref",
    "Container",
    "DownloadError",
    "FormatChoice",
    "MetaInfo",
    "ProgressEvent",
    "Quality",
    "YtdlpEngine",
    "classify_error",
    "compose_format_spec",
    "spec_for_format_id",
]
