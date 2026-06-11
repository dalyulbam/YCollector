"""faster-whisper(CTranslate2) 기반 전사 엔진 — in-process.

원본 ``D:\\26y\\transcriber\\transcribe.py`` 는 시스템 ``whisper`` CLI(openai-whisper)
를 ``subprocess`` 로 호출했지만, 여기서는 **faster-whisper 라이브러리를 직접** 사용한다:

  * 외부 ``whisper.exe`` 설치 불필요 (in-process)
  * CPU ``int8`` / CUDA ``float16`` 자동 선택
  * 세그먼트를 스트리밍 받아 진행률 콜백 제공

이 모듈은 ``faster_whisper`` / ``ctranslate2`` 를 import 하므로 **무겁다**. CLI 는
실제 전사 직전에만 지연 import 한다(:mod:`ycollector.transcribe.cli` 참고).

TLS 가로채기 환경
-----------------
이 PC 는 백신/프록시가 TLS 를 가로채므로, 모델 최초 다운로드(HuggingFace Hub)
시 ``certifi`` 가 발급자를 모른다며(UnknownIssuer) 실패할 수 있다. ``truststore``
가 설치돼 있으면 OS 신뢰 저장소를 ssl 에 주입해 회피한다(video-gen 어댑터와 동일).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import OUTPUT_FORMATS, TranscribeConfig

# faster-whisper 는 optional extra(`uv sync --extra transcribe`). 미설치여도 이 모듈
# import 자체는 깨지지 않게 하고, 엔진 생성 시점에 친절한 안내를 던진다.
try:
    from faster_whisper import WhisperModel

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - 환경 의존
    WhisperModel = None  # type: ignore[assignment,misc]
    _IMPORT_ERROR = exc


class TranscribeError(RuntimeError):
    """전사 실패. ``category`` 로 호출부가 안내 메시지를 분기."""

    def __init__(self, message: str, *, category: str = "unknown") -> None:
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class TranscriptResult:
    language: str
    language_probability: float
    duration: float
    segments: list[Segment]

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments).strip()


# ── 환경 준비 ────────────────────────────────────────────────────────────────
def _inject_truststore() -> None:
    """OS 신뢰 저장소를 ssl 에 주입(설치돼 있으면). 모델 다운로드 TLS 오류 회피."""
    try:
        import truststore

        truststore.inject_into_ssl()
    except Exception:
        pass  # 없으면 조용히 패스 — 시스템 기본 동작.


def _add_cuda_dll_dirs() -> None:
    """pip 로 설치한 nvidia-* wheel 의 DLL 디렉토리를 Windows 검색 경로에 등록.

    CTranslate2(CUDA) 는 ``cublas64_12.dll`` / ``cudnn_*64_9.dll`` 을 필요로 하는데,
    ``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` wheel 은 이를
    ``site-packages/nvidia/<lib>/bin`` 에 두지만 PATH 에는 올리지 않는다.
    """
    try:
        import nvidia

        base = Path(nvidia.__path__[0])  # type: ignore[attr-defined]
    except Exception:
        return
    bins = [str(sub / "bin") for sub in base.iterdir() if (sub / "bin").is_dir()]
    if not bins:
        return
    # PATH prepend 가 핵심 — CTranslate2 의 동적 로더가 cublas/cudnn 을 찾을 때
    # add_dll_directory(보안 검색 목록)가 아니라 PATH 를 참조한다(진단으로 확인).
    os.environ["PATH"] = os.pathsep.join(bins) + os.pathsep + os.environ.get("PATH", "")
    if hasattr(os, "add_dll_directory"):  # 보조 — Windows 전용.
        for b in bins:
            try:
                os.add_dll_directory(b)
            except OSError:
                pass


def _is_cuda_lib_error(exc: Exception) -> bool:
    low = str(exc).lower()
    return any(k in low for k in ("cublas", "cudnn", "cuda", "libcu", "cudart"))


def _auto_device_and_compute(device: str, compute_type: str) -> tuple[str, str]:
    """``auto`` 를 실제 (device, compute_type) 로 해석.

    CUDA 가용 여부는 torch 없이 ctranslate2 로 본다.
    """
    dev = (device or "auto").lower()
    if dev == "auto":
        dev = "cpu"
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                dev = "cuda"
        except Exception:
            dev = "cpu"

    ct = (compute_type or "auto").lower()
    if ct == "auto":
        ct = "float16" if dev == "cuda" else "int8"
    return dev, ct


def _friendly_model_error(exc: Exception, model: str) -> str:
    text = str(exc)
    low = text.lower()
    if any(k in low for k in ("certificate", "ssl", "unknownissuer", "self-signed", "self signed")):
        return (
            f"모델 '{model}' 다운로드 중 TLS 인증서 오류.\n"
            "  이 PC 는 백신/프록시 TLS 가로채기 환경입니다.\n"
            "  truststore 설치 확인:  uv sync --extra transcribe --native-tls\n"
            f"  (원본 오류: {text})"
        )
    if any(k in low for k in ("not a valid", "no such file", "repository not found", "not found", "404")):
        return (
            f"모델 '{model}' 을(를) 찾을 수 없습니다.\n"
            "  지원 별칭 예: tiny, base, small, medium, large-v3, large-v3-turbo, turbo\n"
            "  또는 HuggingFace CT2 repo id / 로컬 디렉토리 경로.\n"
            f"  (원본 오류: {text})"
        )
    return f"모델 로드 실패: {text}"


# ── 엔진 ────────────────────────────────────────────────────────────────────
class TranscribeEngine:
    """모델을 한 번 로드하고 여러 파일을 전사한다(배치 재사용)."""

    def __init__(
        self,
        cfg: TranscribeConfig,
        *,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        if WhisperModel is None:
            raise TranscribeError(
                "faster-whisper 가 설치돼 있지 않습니다.\n"
                "  설치:  uv sync --extra transcribe --native-tls\n"
                "  실행:  uv run --extra transcribe ycollector transcribe ...\n"
                f"  (import 오류: {_IMPORT_ERROR})",
                category="not-installed",
            )
        self.cfg = cfg
        self._log = on_log or (lambda _m: None)

        _inject_truststore()
        self.device, self.compute_type = _auto_device_and_compute(cfg.device, cfg.compute_type)
        if self.device == "cuda":
            _add_cuda_dll_dirs()
        self._log(f"모델 로드: {cfg.model}  (device={self.device}, compute={self.compute_type})")
        try:
            self.model = WhisperModel(
                cfg.model, device=self.device, compute_type=self.compute_type
            )
        except Exception as exc:
            # CUDA 런타임(cuBLAS/cuDNN) 미설치 등 → CPU(int8)로 자동 폴백.
            if self.device == "cuda" and _is_cuda_lib_error(exc):
                self._log(
                    f"CUDA 라이브러리 로드 실패({exc}) → CPU(int8) 폴백. "
                    "GPU 사용:  uv sync --extra transcribe-cuda --native-tls"
                )
                self.device, self.compute_type = "cpu", "int8"
                try:
                    self.model = WhisperModel(cfg.model, device="cpu", compute_type="int8")
                except Exception as exc2:  # noqa: BLE001
                    raise TranscribeError(
                        _friendly_model_error(exc2, cfg.model), category="model-load"
                    ) from exc2
            else:  # 다운로드/로드 실패 → 친절 메시지로 변환.
                raise TranscribeError(
                    _friendly_model_error(exc, cfg.model), category="model-load"
                ) from exc

    def transcribe(
        self,
        src: Path,
        *,
        on_segment: Callable[[Segment, float], None] | None = None,
    ) -> TranscriptResult:
        """``src`` 한 파일을 전사. 세그먼트가 나올 때마다 ``on_segment`` 호출.

        faster-whisper 의 segments 는 **지연 평가 제너레이터** — 실제 추론은 이
        루프를 돌 때 진행되므로, 여기서 콜백으로 진행률을 노출할 수 있다.
        """
        if not src.is_file():
            raise TranscribeError(f"파일을 찾을 수 없습니다: {src}", category="no-input")
        try:
            seg_iter, info = self.model.transcribe(
                str(src),
                language=self.cfg.language,  # None = 자동 감지
                task=self.cfg.task,
                beam_size=self.cfg.beam_size,
                vad_filter=self.cfg.vad_filter,
            )
            collected: list[Segment] = []
            for seg in seg_iter:
                s = Segment(start=float(seg.start), end=float(seg.end), text=seg.text)
                collected.append(s)
                if on_segment is not None:
                    on_segment(s, float(info.duration or 0.0))
        except TranscribeError:
            raise
        except Exception as exc:
            raise TranscribeError(f"전사 실패 ({src.name}): {exc}", category="runtime") from exc

        return TranscriptResult(
            language=info.language,
            language_probability=float(info.language_probability or 0.0),
            duration=float(info.duration or 0.0),
            segments=collected,
        )


# ── 출력 ────────────────────────────────────────────────────────────────────
def _fmt_ts(seconds: float, *, sep: str) -> str:
    """초 → ``HH:MM:SS<sep>mmm`` (SRT 는 ',', VTT 는 '.')."""
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _to_srt(segments: list[Segment]) -> str:
    out: list[str] = []
    for i, seg in enumerate(segments, start=1):
        out.append(str(i))
        out.append(f"{_fmt_ts(seg.start, sep=',')} --> {_fmt_ts(seg.end, sep=',')}")
        out.append(seg.text.strip())
        out.append("")
    return "\n".join(out)


def _to_vtt(segments: list[Segment]) -> str:
    out: list[str] = ["WEBVTT", ""]
    for seg in segments:
        out.append(f"{_fmt_ts(seg.start, sep='.')} --> {_fmt_ts(seg.end, sep='.')}")
        out.append(seg.text.strip())
        out.append("")
    return "\n".join(out)


def write_transcript(result: TranscriptResult, out_dir: Path, stem: str, fmt: str) -> Path:
    """전사 결과를 ``out_dir/<stem>.<fmt>`` 로 저장하고 경로 반환."""
    fmt = fmt.lower()
    if fmt not in OUTPUT_FORMATS:
        raise TranscribeError(
            f"알 수 없는 output_format: {fmt} (지원: {', '.join(OUTPUT_FORMATS)})",
            category="bad-format",
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{stem}.{fmt}"
    if fmt == "txt":
        dest.write_text(result.text + "\n", encoding="utf-8")
    elif fmt == "srt":
        dest.write_text(_to_srt(result.segments), encoding="utf-8")
    elif fmt == "vtt":
        dest.write_text(_to_vtt(result.segments), encoding="utf-8")
    elif fmt == "json":
        payload = {
            "language": result.language,
            "language_probability": round(result.language_probability, 4),
            "duration": round(result.duration, 3),
            "text": result.text,
            "segments": [
                {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
                for s in result.segments
            ],
        }
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest
