"""Provider ABC + 공통 데이터클래스 + 비용 견적.

Sora 외에도 Veo/Runway/Pika 등을 같은 인터페이스로 끼울 수 있도록
provider name → 클래스 매핑을 가운데서 관리한다.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Literal


# ── 데이터 ──────────────────────────────────────────────────────────────────
@dataclass
class VideoRequest:
    """텍스트→비디오 생성 요청.

    references: yt-dlp 로 받아 thumbnail 추출 가능한 영상 URL, 혹은 직접
                이미지 URL. provider 가 input_reference 슬롯이 1개뿐인 경우
                (Sora 등) 본 어댑터는 reference 1개당 1개의 잡으로 분할.
    """

    prompt: str
    size: str = "1280x720"        # "1280x720" | "1024x1792" | "1792x1024" | "1920x1080"
    seconds: int = 8              # Sora: 8 | 12 | 16 | 20 (provider 마다 다름)
    model: str = "sora-2-pro"     # "sora-2" | "sora-2-pro"
    references: list[str] = field(default_factory=list)
    seed: int | None = None
    output_dir: Path = field(default_factory=lambda: Path("generated"))


JobStatusValue = Literal["queued", "in_progress", "completed", "failed", "cancelled"]


@dataclass
class JobStatus:
    status: JobStatusValue
    progress: float = 0.0          # 0.0 ~ 1.0
    message: str = ""
    error_category: str | None = None  # provider-mapped (rate-limit / content-policy / network / …)


@dataclass
class VideoJob:
    """provider 가 발급하는 잡 핸들. 라이브러리 manifest 에 그대로 들어가도 안전한 dict 화."""

    id: str
    provider: str
    model: str
    request: VideoRequest
    reference_local: Path | None = None  # 다운로드된 reference image 경로
    output_path: Path | None = None       # 완료 후 저장 위치
    cost_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)  # provider raw response

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "prompt": self.request.prompt,
            "size": self.request.size,
            "seconds": self.request.seconds,
            "references": list(self.request.references),
            "output_path": str(self.output_path) if self.output_path else None,
            "cost_usd": self.cost_usd,
        }


# ── Provider ABC ────────────────────────────────────────────────────────────
class ProviderError(Exception):
    """provider-mapped 분류 카테고리 동반."""

    def __init__(self, message: str, *, category: str = "unknown") -> None:
        super().__init__(message)
        self.category = category


class BudgetExceeded(ProviderError):
    """create() 직전 비용 견적이 한도 초과."""

    def __init__(self, est: float, limit: float) -> None:
        super().__init__(
            f"예상 비용 ${est:.2f} 가 한도 ${limit:.2f} 초과. "
            "--budget-usd 를 올리거나 --seconds / --size 를 줄이세요.",
            category="budget",
        )
        self.est = est
        self.limit = limit


class Provider(abc.ABC):
    """video 생성 provider 인터페이스."""

    name: str = "base"

    @abc.abstractmethod
    def create(self, req: VideoRequest) -> VideoJob:
        """잡 생성(API 호출). reference 가 여러 개면 호출자가 분할 — 이 메서드는 1잡."""

    @abc.abstractmethod
    def poll(self, job: VideoJob) -> JobStatus:
        """현재 상태 1회 조회."""

    @abc.abstractmethod
    def download(self, job: VideoJob, out_path: Path) -> Path:
        """완료된 잡의 영상 파일을 out_path 로 저장 → 실제 경로 반환."""

    def cancel(self, job: VideoJob) -> bool:
        """provider 가 지원하면 override. 기본은 unsupported."""
        return False

    def poll_until_done(
        self,
        job: VideoJob,
        *,
        interval_sec: float = 4.0,
        timeout_sec: float = 1800.0,
    ) -> Iterator[JobStatus]:
        """완료/실패까지 상태를 yield. 사용자는 progress 표시.

        Provider 별로 적절한 interval 을 override 할 수 있다.
        """
        import time
        t0 = time.monotonic()
        while True:
            status = self.poll(job)
            yield status
            if status.status in ("completed", "failed", "cancelled"):
                return
            if time.monotonic() - t0 > timeout_sec:
                raise ProviderError(
                    f"잡이 {timeout_sec:.0f}s 안에 끝나지 않음 (id={job.id})",
                    category="timeout",
                )
            time.sleep(interval_sec)


# ── 비용 견적 ─────────────────────────────────────────────────────────────
# 2026-05 기준 OpenAI 공식 가격. https://platform.openai.com/docs/pricing
# 단위: $ / second of generated video.
_SORA_PRICING: dict[tuple[str, str], float] = {
    ("sora-2", "720p"):    0.10,
    ("sora-2", "1080p"):   0.20,  # sora-2 는 1080p 미지원이지만 가격표 호환.
    ("sora-2-pro", "720p"):   0.30,
    ("sora-2-pro", "1024p"):  0.50,
    ("sora-2-pro", "1080p"):  0.70,
}


def _resolution_bucket(size: str) -> str:
    """size 문자열을 가격 bucket(720p/1024p/1080p) 으로 정규화."""
    try:
        _w, h = size.lower().split("x")
        h_i = int(h)
    except (ValueError, AttributeError):
        return "720p"
    if h_i >= 1080:
        return "1080p"
    if h_i >= 1024:
        return "1024p"
    return "720p"


def estimate_cost_usd(req: VideoRequest) -> float:
    """잡 1개의 예상 비용(USD). 견적은 모델·해상도·길이 곱."""
    bucket = _resolution_bucket(req.size)
    per_sec = _SORA_PRICING.get((req.model, bucket))
    if per_sec is None:
        # 모르는 조합 — 보수적 견적: 가장 비싼 단가.
        per_sec = max(_SORA_PRICING.values())
    return per_sec * float(req.seconds)


# ── 레지스트리 ────────────────────────────────────────────────────────────
def get_provider(name: str = "sora") -> Provider:
    """provider 이름 → 인스턴스. 지금은 sora 만."""
    name = name.lower()
    if name in ("sora", "sora2", "sora-2", "openai"):
        from .sora import SoraProvider
        return SoraProvider()
    raise ValueError(f"unknown provider: {name!r}")
