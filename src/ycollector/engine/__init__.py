"""Engine adapter — yt-dlp subprocess + (장차) Python API 하이브리드.

Plan §4.4 참고.
"""

from .ytdlp import (
    DownloadError,
    ProgressEvent,
    YtdlpEngine,
    classify_error,
)

__all__ = [
    "DownloadError",
    "ProgressEvent",
    "YtdlpEngine",
    "classify_error",
]
