"""``compose_format_spec`` / ``compose_format_sort`` 계약 테스트.

핵심 회귀 방지 대상: 세로(포트레이트) 영상의 화질 상한.
예전엔 ``-f bv*[height<=1080]`` 로 상한을 걸었는데, 1080x1920 쇼츠는 *height*
가 1920 이라 네이티브 화질이 필터에서 탈락하고 608x1080 이 선택됐다(픽셀 3.2배
손실, 종료코드 0, 경고 없음). 지금은 상한을 ``-S res:N`` 이 담당한다 —
yt-dlp 의 ``res`` 는 ``min(width, height)`` 라 가로/세로 모두 짧은 변 기준이다.

여기서는 네트워크를 타지 않는 순수 함수만 검증한다. "res:1080 이 실제로
1080x1920 을 고른다"는 yt-dlp 쪽 동작은 실제 실행으로 따로 확인했다.
"""

from __future__ import annotations

import pytest

from ycollector.engine import (
    AudioPref,
    CodecPref,
    Container,
    FormatChoice,
    Quality,
    compose_format_sort,
    compose_format_spec,
)


def test_format_spec_carries_no_height_filter() -> None:
    """-f 에 height 필터가 다시 들어오면 세로 영상이 또 깎인다."""
    spec = compose_format_spec(FormatChoice(quality=Quality.P1080))
    assert "height" not in spec
    assert spec == "bv*+ba/b"


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        (Quality.P144, "res:144"),
        (Quality.P360, "res:360"),
        (Quality.P720, "res:720"),
        (Quality.P1080, "res:1080"),
        (Quality.P1440, "res:1440"),
        (Quality.P2160, "res:2160"),
    ],
)
def test_format_sort_caps_short_edge(quality: Quality, expected: str) -> None:
    assert compose_format_sort(FormatChoice(quality=quality)) == expected


@pytest.mark.parametrize("quality", [Quality.BEST, Quality.AUDIO])
def test_format_sort_none_when_uncapped(quality: Quality) -> None:
    """'best' 와 'audio' 는 상한이 없다 — -S 를 붙이면 안 된다."""
    assert compose_format_sort(FormatChoice(quality=quality)) is None


def test_audio_only_unaffected() -> None:
    choice = FormatChoice(quality=Quality.AUDIO, audio=AudioPref.M4A)
    assert compose_format_spec(choice) == "bestaudio[ext=m4a]/bestaudio"


def test_codec_and_audio_filters_still_in_spec() -> None:
    """코덱/오디오 선호는 여전히 -f 쪽 책임 — -S 로 옮기지 않았다."""
    spec = compose_format_spec(
        FormatChoice(
            quality=Quality.P2160,
            container=Container.WEBM,
            codec=CodecPref.AV1,
            audio=AudioPref.OPUS,
        )
    )
    assert spec == "bv*[vcodec^=av01]+ba[ext=webm]/b"


def test_every_capped_quality_has_a_sort() -> None:
    """새 화질이 추가돼도 상한이 조용히 사라지지 않도록."""
    for q in Quality:
        sort = compose_format_sort(FormatChoice(quality=q))
        if q in (Quality.BEST, Quality.AUDIO):
            assert sort is None
        else:
            assert sort == f"res:{q.height}"
