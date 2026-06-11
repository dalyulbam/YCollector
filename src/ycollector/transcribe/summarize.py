"""전사 텍스트 → 구조화 요약 (Anthropic Claude API).

LilysAI 류 산출물의 핵심: 타임스탬프가 붙은 전사를 받아
  * 전체 요약(overview)
  * 핵심 포인트(key_points)
  * 핵심 파트(sections — 각 파트의 시작/끝 시간 + 요약)
를 **구조화 출력**으로 생성한다.

설계 포인트
-----------
* 모델: 기본 ``claude-opus-4-8``(설정/CLI로 변경 가능). claude-api 스킬 권장 기본값.
* 구조화 출력: ``client.messages.parse(output_format=Pydantic)`` — 스키마 검증 자동.
* 프롬프트 캐싱: 안정적인 system 프롬프트에 ``cache_control``. (주의: Opus 계열은
  캐시 최소 prefix 4096 토큰이라 짧은 system 은 실제 캐시가 안 될 수 있음 — 무해.)
* 맵-리듀스: 매우 긴 전사(최대 ~4시간)는 청크로 나눠 부분 요약(map) 후 통합(reduce).
* 비용 상한: 호출 전 예상 비용으로 예산 초과를 차단, 호출 후 실제 usage 로 누적.
* TLS 가로채기 환경: truststore 주입(설치돼 있으면).

이 모듈은 ``faster_whisper`` 를 import 하지 않는다 — 세그먼트는 (start,end,text)
속성만 있으면 되는 duck-typed 입력으로 받는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

try:
    import anthropic
    from pydantic import BaseModel, Field

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    anthropic = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment,misc]
    _IMPORT_ERROR = exc

DEFAULT_SUMMARY_MODEL = "claude-opus-4-8"

# USD / 1M 토큰 (input, output) — claude-api 스킬 가격표(2026-05 기준).
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# 청크 1개의 목표 문자 수(타임스탬프 포함). 한국어 기준 ~2.5자/토큰 → 약 32k 토큰.
# Opus 컨텍스트는 1M 이라 대부분 단일 호출이지만, 출력 품질·집중도·비용 가드를 위해 분할.
_CHUNK_CHARS = 80_000
_MAX_OUTPUT_TOKENS = 8_000


class _Seg(Protocol):
    start: float
    end: float
    text: str


# ── 구조화 출력 스키마 ───────────────────────────────────────────────────────
if anthropic is not None:

    class KeySection(BaseModel):
        title: str = Field(description="핵심 파트 제목(간결하게)")
        start: str = Field(description="시작 시각 HH:MM:SS")
        end: str = Field(description="끝 시각 HH:MM:SS")
        summary: str = Field(description="이 파트에서 다룬 내용 요약")

    class VideoSummary(BaseModel):
        overview: str = Field(description="영상 전체 요약(3~6문장)")
        key_points: list[str] = Field(description="핵심 포인트 불릿 5~10개")
        sections: list[KeySection] = Field(description="시간 순 핵심 파트 목록")
else:  # 타입 자리만.
    class KeySection:  # type: ignore[no-redef]
        ...

    class VideoSummary:  # type: ignore[no-redef]
        ...


class SummarizeError(RuntimeError):
    def __init__(self, message: str, *, category: str = "unknown") -> None:
        super().__init__(message)
        self.category = category
        self.message = message


class BudgetExceeded(SummarizeError):
    def __init__(self, message: str) -> None:
        super().__init__(message, category="budget")


@dataclass
class Budget:
    cap_usd: float
    model: str
    spent_usd: float = 0.0

    def _rate(self) -> tuple[float, float]:
        return _PRICES.get(self.model, (5.0, 25.0))

    def precheck(self, est_in_tokens: int, max_out_tokens: int) -> None:
        ri, ro = self._rate()
        projected = self.spent_usd + (est_in_tokens * ri + max_out_tokens * ro) / 1e6
        if projected > self.cap_usd:
            raise BudgetExceeded(
                f"예산 초과 예상: 누적 ${self.spent_usd:.2f} + 이번 호출 예상 "
                f"~${projected - self.spent_usd:.2f} > 한도 ${self.cap_usd:.2f}. "
                f"--budget-usd 를 올리거나 더 저렴한 --summary-model 을 쓰세요."
            )

    def record(self, usage) -> None:  # noqa: ANN001 - anthropic Usage
        ri, ro = self._rate()
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        # 캐시 쓰기 ~1.25x, 읽기 ~0.1x.
        eff_in = (usage.input_tokens or 0) + cache_create * 1.25 + cache_read * 0.1
        self.spent_usd += (eff_in * ri + (usage.output_tokens or 0) * ro) / 1e6


# ── 환경 준비 ────────────────────────────────────────────────────────────────
def _inject_truststore() -> None:
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass


def _load_dotenv() -> None:
    try:
        from pathlib import Path

        from dotenv import load_dotenv
    except ImportError:
        return
    from pathlib import Path

    here = Path.cwd()
    for cand in [here / ".env", *(p / ".env" for p in here.parents)]:
        if cand.is_file():
            load_dotenv(cand, override=False)
            return


def _hms(seconds: float) -> str:
    s = int(max(0.0, seconds))
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _segments_to_text(segs: Sequence[_Seg]) -> str:
    return "\n".join(f"[{_hms(s.start)}] {s.text.strip()}" for s in segs)


def _chunk_segments(segs: Sequence[_Seg], max_chars: int) -> list[list[_Seg]]:
    chunks: list[list[_Seg]] = []
    cur: list[_Seg] = []
    n = 0
    for s in segs:
        ln = len(s.text) + 14  # 타임스탬프 프리픽스 보정
        if cur and n + ln > max_chars:
            chunks.append(cur)
            cur, n = [], 0
        cur.append(s)
        n += ln
    if cur:
        chunks.append(cur)
    return chunks


# ── 프롬프트 ────────────────────────────────────────────────────────────────
_SYSTEM = """당신은 한국어 회의·간담회·타운홀 영상의 전사록을 분석해 구조화된 요약을 만드는 전문가입니다.

입력은 `[H:MM:SS] 발화` 형식의 타임스탬프 전사록입니다. 화자 이름은 주어지지 않습니다.

다음 원칙을 지키세요:
- overview: 영상 전체의 핵심을 3~6문장으로. 무슨 자리였고, 누가(직책/집단 수준) 무엇을 논의했는지.
- key_points: 가장 중요한 논점·발언·결정·수치를 불릿 5~10개로. 구체적으로.
- sections: 시간 순서대로 주제가 바뀌는 지점을 기준으로 핵심 파트를 나눕니다.
  각 파트에 title(간결한 제목), start/end(전사록의 타임스탬프에서 가져온 H:MM:SS), summary를 채웁니다.
  start/end는 반드시 입력 전사록에 실제로 등장한 시간 범위 안에서 고르세요(지어내지 말 것).
- 모든 출력은 한국어로. 추측·과장 없이 전사록 내용에만 근거하세요. 들리지 않은 내용을 만들지 마세요."""

_MAP_INSTR = """다음은 한 영상의 전사록입니다(긴 영상의 일부일 수 있음). 위 원칙대로 overview, key_points, sections를 작성하세요. sections의 start/end는 아래 전사록에 등장한 타임스탬프 범위 안에서 정하세요.

전사록:
"""

_REDUCE_INSTR = """다음은 한 영상을 여러 구간으로 나눠 각각 요약한 '부분 요약'들입니다(시간 순). 이를 하나로 통합해 영상 전체의 overview, key_points, sections를 작성하세요.
- overview는 영상 전체를 아우르도록 다시 쓰세요(부분 요약 단순 나열 금지).
- key_points는 중복을 제거하고 가장 중요한 것만 남기세요.
- sections는 부분 요약의 파트들을 시간 순으로 통합하되, 인접한 같은 주제는 합치고 start/end 시간 범위는 보존하세요.

부분 요약들:
"""


# ── 요약기 ──────────────────────────────────────────────────────────────────
class Summarizer:
    """Claude 클라이언트를 한 번 만들고 여러 영상을 요약(배치 재사용·예산 누적)."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_SUMMARY_MODEL,
        budget_usd: float = 5.0,
        on_log=None,  # noqa: ANN001
    ) -> None:
        if anthropic is None:
            raise SummarizeError(
                "anthropic SDK 가 설치돼 있지 않습니다.\n"
                "  설치:  uv sync --extra summarize --native-tls\n"
                f"  (import 오류: {_IMPORT_ERROR})",
                category="not-installed",
            )
        self.model = model
        self.budget = Budget(cap_usd=budget_usd, model=model)
        self._log = on_log or (lambda _m: None)
        _inject_truststore()
        _load_dotenv()
        import os

        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise SummarizeError(
                "ANTHROPIC_API_KEY 가 없습니다. .env 또는 환경변수에 설정하세요.",
                category="no-key",
            )
        self.client = anthropic.Anthropic()

    def _call(self, instruction: str, body: str) -> "VideoSummary":
        # 로컬 토큰 추정(한국어 ~2.5자/토큰) 후 예산 사전 점검.
        est_in = int(len(_SYSTEM + instruction + body) / 2.5) + 64
        self.budget.precheck(est_in, _MAX_OUTPUT_TOKENS)
        try:
            resp = self.client.messages.parse(
                model=self.model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": instruction + body}],
                output_format=VideoSummary,
            )
        except anthropic.APIStatusError as exc:  # type: ignore[union-attr]
            raise SummarizeError(f"Claude API 오류: {exc}", category="api") from exc
        self.budget.record(resp.usage)
        out = resp.parsed_output
        if out is None:
            raise SummarizeError("구조화 출력 파싱 실패(거부 또는 토큰 한도).", category="parse")
        return out

    def summarize(self, segments: Sequence[_Seg]) -> "VideoSummary":
        if not segments:
            raise SummarizeError("요약할 세그먼트가 없습니다.", category="no-input")
        chunks = _chunk_segments(segments, _CHUNK_CHARS)
        if len(chunks) == 1:
            self._log("요약 1/1 (단일 청크)")
            return self._call(_MAP_INSTR, _segments_to_text(chunks[0]))

        # map
        partials: list[VideoSummary] = []
        for i, ch in enumerate(chunks, 1):
            self._log(f"요약 map {i}/{len(chunks)}  (누적 ${self.budget.spent_usd:.2f})")
            partials.append(self._call(_MAP_INSTR, _segments_to_text(ch)))

        # reduce
        self._log(f"요약 reduce ({len(partials)}개 부분 통합)")
        body = "\n\n".join(_partial_to_text(p, i) for i, p in enumerate(partials, 1))
        return self._call(_REDUCE_INSTR, body)


def _partial_to_text(p: "VideoSummary", idx: int) -> str:
    lines = [f"## 부분 {idx}", f"개요: {p.overview}", "핵심 포인트:"]
    lines += [f"- {kp}" for kp in p.key_points]
    lines.append("파트:")
    lines += [f"- [{s.start}–{s.end}] {s.title}: {s.summary}" for s in p.sections]
    return "\n".join(lines)
