"""Compose yt-dlp ``--format`` strings from structured UI selections.

Pure functions — easy to unit test, reusable in CLI/GUI.
Plan §10.5 D2 (Smart Mode 프리셋의 컨피그-우선 형태)에 부합.

References:
    https://github.com/yt-dlp/yt-dlp#format-selection
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Quality(str, Enum):
    P144 = "144p"
    P240 = "240p"
    P360 = "360p"
    P480 = "480p"
    P720 = "720p"
    P1080 = "1080p"
    P1440 = "1440p"
    P2160 = "2160p"
    BEST = "best"
    AUDIO = "audio"

    @property
    def label(self) -> str:
        return {
            Quality.P2160: "4K (2160p)",
            Quality.P1440: "1440p (QHD)",
            Quality.BEST: "최고 (제한 없음)",
            Quality.AUDIO: "오디오만",
        }.get(self, self.value)

    @property
    def height(self) -> int | None:
        if self in (Quality.BEST, Quality.AUDIO):
            return None
        return int(self.value.rstrip("p"))


class Container(str, Enum):
    MP4 = "mp4"
    MKV = "mkv"
    WEBM = "webm"


class CodecPref(str, Enum):
    AUTO = "auto"
    H264 = "h264"
    VP9 = "vp9"
    AV1 = "av1"

    @property
    def label(self) -> str:
        return {
            CodecPref.AUTO: "자동",
            CodecPref.H264: "H.264 (호환성)",
            CodecPref.VP9: "VP9",
            CodecPref.AV1: "AV1 (효율)",
        }[self]


class AudioPref(str, Enum):
    BEST = "best"
    M4A = "m4a"
    OPUS = "opus"

    @property
    def label(self) -> str:
        return {
            AudioPref.BEST: "최고",
            AudioPref.M4A: "m4a (AAC)",
            AudioPref.OPUS: "opus",
        }[self]


_VCODEC_FILTER = {
    CodecPref.H264: "[vcodec^=avc1]",
    CodecPref.VP9: "[vcodec^=vp09]",
    CodecPref.AV1: "[vcodec^=av01]",
}

_AUDIO_FILTER = {
    AudioPref.M4A: "[ext=m4a]",
    AudioPref.OPUS: "[ext=webm]",
}


@dataclass(frozen=True)
class FormatChoice:
    """User-facing format selection — translates to a yt-dlp ``-f`` spec."""

    quality: Quality = Quality.P1080
    container: Container = Container.MP4
    codec: CodecPref = CodecPref.AUTO
    audio: AudioPref = AudioPref.BEST


def compose_format_spec(choice: FormatChoice) -> str:
    """Translate a :class:`FormatChoice` into a yt-dlp ``-f`` spec.

    Examples
    --------
    Default (1080p mp4 auto-codec best-audio)::

        bv*[height<=1080]+ba/b[height<=1080]

    4K + AV1 + opus::

        bv*[height<=2160][vcodec^=av01]+ba[ext=webm]/b[height<=2160]

    Audio only + m4a::

        bestaudio[ext=m4a]/bestaudio
    """
    if choice.quality == Quality.AUDIO:
        f = _AUDIO_FILTER.get(choice.audio, "")
        return f"bestaudio{f}/bestaudio" if f else "bestaudio/best"

    height = choice.quality.height
    height_filter = f"[height<={height}]" if height is not None else ""
    codec_filter = _VCODEC_FILTER.get(choice.codec, "")
    audio_filter = _AUDIO_FILTER.get(choice.audio, "")

    video = f"bv*{height_filter}{codec_filter}"
    audio = f"ba{audio_filter}" if audio_filter else "ba"
    fallback = f"b{height_filter}"

    return f"{video}+{audio}/{fallback}"


def spec_for_format_id(fmt: dict) -> str:
    """Compose a ``-f`` spec for a single browsed format row.

    Combined formats (with both video and audio) are used as-is.
    Video-only formats are paired with ``bestaudio``.
    Audio-only formats are used as-is.
    """
    fid = str(fmt.get("format_id", ""))
    if not fid:
        return "best"
    has_video = (fmt.get("vcodec") or "none") != "none"
    has_audio = (fmt.get("acodec") or "none") != "none"
    if has_video and has_audio:
        return fid
    if has_video:
        return f"{fid}+bestaudio/best"
    return fid
