"""Video generation 어댑터.

Provider 추상화 — Sora 2 Pro 가 기본이지만, OpenAI Videos API 가 2026-09-24
sunset 되므로 후속 모델(Veo, Runway, Pika 등)로 교체 가능하게 설계.

사용
----
    from ycollector.generator import get_provider, VideoRequest

    provider = get_provider("sora")
    job = provider.create(VideoRequest(
        prompt="고양이가 비를 맞으며 우산을 들고 걷는 장면, 영화적 톤",
        size="1280x720",
        seconds=8,
        model="sora-2-pro",
    ))
    for status in provider.poll_until_done(job):
        print(status.progress)
    out_path = provider.download(job, Path("out.mp4"))
"""

from .base import (
    BudgetExceeded,
    JobStatus,
    Provider,
    ProviderError,
    VideoJob,
    VideoRequest,
    estimate_cost_usd,
    get_provider,
)
from .sora import SoraProvider

__all__ = [
    "BudgetExceeded",
    "JobStatus",
    "Provider",
    "ProviderError",
    "SoraProvider",
    "VideoJob",
    "VideoRequest",
    "estimate_cost_usd",
    "get_provider",
]
