"""화자 분리(diarization) — 토큰 불필요 (speechbrain ECAPA + 클러스터링).

pyannote.audio 는 게이트 모델이라 HuggingFace 토큰+약관 동의가 필요하다. 그 마찰을
피하기 위해, 공개(비게이트) **speechbrain ECAPA-TDNN** 화자 임베딩 + 응집형
클러스터링으로 '이름은 모르지만 화자만 구분'한다.

흐름
----
1. ffmpeg 로 원본(webm 등) → 16kHz mono wav 추출
2. whisper 세그먼트마다 해당 구간 파형을 잘라 ECAPA 임베딩
3. 코사인 거리 응집 클러스터링(또는 화자 수 지정) → 세그먼트별 클러스터
4. 시간 순 첫 등장 순서로 화자1, 화자2, … 라벨링

이 모듈은 무겁다(torch/speechbrain). ``analyze`` 와 별개의 **후처리**로 동작:
이미 생성된 ``_analysis/*.json`` 전사록을 읽어 원본 오디오로 화자를 붙이고
``<stem>.script.md`` 를 화자 라벨 포함해 다시 쓴다.

설치:  uv sync --extra diarize --native-tls
실행:  uv run --extra diarize --native-tls ycollector-diarize
       (또는)  .venv\\Scripts\\python.exe -m ycollector.transcribe.diarize
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

try:
    import numpy as np
    import soundfile as sf
    import torch
    from sklearn.cluster import AgglomerativeClustering
    from speechbrain.inference.speaker import EncoderClassifier

    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover
    _IMPORT_ERROR = exc

_SR = 16000
_MEDIA_EXTS = (".webm", ".mp4", ".mkv", ".mov", ".m4a", ".mp3", ".wav", ".flac", ".aac", ".opus", ".ogg")


class DiarizeError(RuntimeError):
    def __init__(self, message: str, *, category: str = "unknown") -> None:
        super().__init__(message)
        self.category = category
        self.message = message


@dataclass
class _JSeg:
    start: float
    end: float
    text: str


def _extract_wav(src: Path, dst: Path) -> None:
    """ffmpeg 로 16kHz mono wav 추출. ffmpeg 가 PATH 에 있어야 함."""
    cmd = ["ffmpeg", "-y", "-i", str(src), "-ar", str(_SR), "-ac", "1", "-vn", "-f", "wav", str(dst)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError as exc:
        raise DiarizeError("ffmpeg 를 찾을 수 없습니다(오디오 추출 필요).", category="no-ffmpeg") from exc
    except subprocess.CalledProcessError as exc:
        raise DiarizeError(
            f"ffmpeg 오디오 추출 실패: {exc.stderr.decode('utf-8', 'replace')[:200]}",
            category="ffmpeg",
        ) from exc


def _relabel_by_first_appearance(cluster_ids: list[int]) -> list[str]:
    """클러스터 id 들을 시간 순 첫 등장 순서로 화자1, 화자2, … 로 변환."""
    mapping: dict[int, str] = {}
    out: list[str] = []
    nxt = 1
    for cid in cluster_ids:
        if cid not in mapping:
            mapping[cid] = f"화자{nxt}"
            nxt += 1
        out.append(mapping[cid])
    return out


class Diarizer:
    """ECAPA 임베딩 모델을 한 번 로드하고 여러 파일을 화자 분리(배치 재사용)."""

    def __init__(self, *, device: str | None = None, on_log=None) -> None:  # noqa: ANN001
        if _IMPORT_ERROR is not None:
            raise DiarizeError(
                "화자분리 의존성이 없습니다.\n"
                "  설치:  uv sync --extra diarize --native-tls\n"
                f"  (import 오류: {_IMPORT_ERROR})",
                category="not-installed",
            )
        self._log = on_log or (lambda _m: None)
        try:
            import truststore

            truststore.inject_into_ssl()
        except Exception:
            pass
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._log(f"화자 임베딩 모델 로드: ECAPA-TDNN (device={self.device})")
        try:
            self.encoder = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                run_opts={"device": self.device},
            )
        except Exception as exc:
            raise DiarizeError(f"ECAPA 모델 로드 실패: {exc}", category="model-load") from exc

    def _embed(self, wav, start: float, end: float):  # noqa: ANN001
        a = max(0, int(start * _SR))
        b = min(len(wav), int(end * _SR))
        clip = wav[a:b]
        min_len = int(_SR * 0.4)
        if len(clip) < min_len:
            clip = np.pad(clip, (0, min_len - len(clip)))
        t = torch.tensor(clip, dtype=torch.float32, device=self.device).unsqueeze(0)
        with torch.no_grad():
            emb = self.encoder.encode_batch(t).squeeze().detach().cpu().numpy()
        return emb.astype("float32")

    def _embed_segments(self, audio_src: Path, segments: list[_JSeg], valid_idx: list[int]):  # noqa: ANN001
        with tempfile.TemporaryDirectory() as td:
            wavp = Path(td) / "audio.wav"
            _extract_wav(audio_src, wavp)
            wav, _ = sf.read(str(wavp), dtype="float32")
        if getattr(wav, "ndim", 1) > 1:
            wav = wav.mean(axis=1)
        embs = []
        for k, i in enumerate(valid_idx):
            s = segments[i]
            embs.append(self._embed(wav, s.start, s.end))
            if (k + 1) % 100 == 0:
                self._log(f"  임베딩 {k + 1}/{len(valid_idx)}")
        return np.stack(embs).astype("float32") if embs else np.empty((0, 192), dtype="float32")

    def diarize(
        self,
        audio_src: Path,
        segments: list[_JSeg],
        *,
        num_speakers: int | None = None,
        threshold: float = 0.9,
        cache_path: Path | None = None,
    ) -> list[str]:
        """세그먼트별 화자 라벨(화자1…) 리스트 반환.

        ``cache_path`` 가 주어지면 임베딩을 npz로 캐시 — threshold/num_speakers 만
        바꿔 재실행할 때 재임베딩(느린 CPU 연산)을 건너뛴다.
        """
        if not segments:
            return []
        # 너무 짧은 세그먼트는 임베딩 신뢰도가 낮아 제외(직전 화자 상속).
        valid_idx = [i for i, s in enumerate(segments) if (s.end - s.start) >= 0.2]
        if not valid_idx:
            return ["화자1"] * len(segments)

        X = None
        if cache_path is not None and Path(cache_path).is_file():
            try:
                cached = np.load(cache_path)
                if list(int(x) for x in cached["valid_idx"]) == valid_idx:
                    X = cached["X"].astype("float32")
                    self._log(f"  임베딩 캐시 재사용 ({len(X)})")
            except Exception:
                X = None
        if X is None:
            X = self._embed_segments(audio_src, segments, valid_idx)
            if cache_path is not None and len(X):
                try:
                    np.savez(cache_path, X=X, valid_idx=np.array(valid_idx, dtype="int64"))
                except Exception:
                    pass
        if len(X) == 0:
            return ["화자1"] * len(segments)

        Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-8)  # 코사인용 정규화
        if len(Xn) == 1:
            clusters = [0]
        elif num_speakers:
            clusters = AgglomerativeClustering(
                n_clusters=min(num_speakers, len(Xn)), metric="cosine", linkage="average"
            ).fit_predict(Xn).tolist()
        else:
            clusters = AgglomerativeClustering(
                n_clusters=None, distance_threshold=threshold, metric="cosine", linkage="average"
            ).fit_predict(Xn).tolist()

        # 세그먼트별 cluster id (짧아서 임베딩 제외된 건 직전 값 상속).
        seg_cluster: list[int] = [-1] * len(segments)
        for idx, c in zip(valid_idx, clusters):
            seg_cluster[idx] = c
        last = clusters[0]
        for i in range(len(segments)):
            if seg_cluster[i] == -1:
                seg_cluster[i] = last
            else:
                last = seg_cluster[i]
        return _relabel_by_first_appearance(seg_cluster)


# ── 후처리: _analysis/*.json|*.srt → 화자 라벨 script.md 재작성 ──────────────
def _load_segments(json_path: Path) -> tuple[list[_JSeg], str, float]:
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    segs = [_JSeg(float(s["start"]), float(s["end"]), s.get("text", "")) for s in doc.get("segments", [])]
    return segs, doc.get("language", "?"), float(doc.get("duration", 0.0))


_SRT_TS_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)


def _parse_srt(text: str) -> list[_JSeg]:
    """SRT/VTT 텍스트 → 세그먼트. analyze 산출물(.srt)에서 화자분리용 타임코드 복원."""
    segs: list[_JSeg] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        ts_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ts_idx is None:
            continue
        m = _SRT_TS_RE.search(lines[ts_idx])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        txt = " ".join(lines[ts_idx + 1:]).strip()
        segs.append(_JSeg(start, end, txt))
    return segs


def _load_transcript(kind: str, path: Path) -> tuple[list[_JSeg], str, float]:
    """전사 산출물을 세그먼트로 로드. json=메타 포함, srt=타임코드만(언어 '?')."""
    if kind == "json":
        return _load_segments(path)
    segs = _parse_srt(path.read_text(encoding="utf-8", errors="replace"))
    duration = max((s.end for s in segs), default=0.0)
    return segs, "?", duration


def _find_audio(stem: str, audio_dir: Path) -> Path | None:
    for ext in _MEDIA_EXTS:
        cand = audio_dir / f"{stem}{ext}"
        if cand.is_file():
            return cand
    return None


def diarize_folder(
    analysis_dir: Path,
    audio_dir: Path | None = None,
    *,
    num_speakers: int | None = None,
    threshold: float = 0.9,
    device: str | None = None,
    on_log: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, object]:
    """폴더의 전사 산출물(_analysis)에 화자 구분을 붙여 ``<stem>.script.md`` 재작성.

    전사 파일은 ``*.json``(메타 포함) 우선, 없으면 ``*.srt`` 를 읽는다. 각 stem 의
    원본 오디오를 ``audio_dir``(기본: analysis_dir 의 상위)에서 찾아 ECAPA 임베딩
    클러스터링으로 화자를 구분한다. ``Diarizer`` 생성이 실패하면(의존성/모델)
    :class:`DiarizeError` 를 그대로 올린다(호출부가 설치 안내). 반환: 처리 통계.
    """
    from .report import render_script

    analysis_dir = Path(analysis_dir)
    audio_dir = Path(audio_dir) if audio_dir else analysis_dir.parent
    log = on_log or (lambda _m: None)

    trans: dict[str, tuple[str, Path]] = {}
    for j in sorted(analysis_dir.glob("*.json")):
        trans[j.stem] = ("json", j)
    for s in sorted(analysis_dir.glob("*.srt")):
        trans.setdefault(s.stem, ("srt", s))
    stems = sorted(trans)
    if not stems:
        return {"total": 0, "ok": 0, "failed": 0, "speakers": {}}

    diarizer = Diarizer(device=device, on_log=log)  # 미설치/로드 실패 시 DiarizeError.

    ok = 0
    failed = 0
    speakers_out: dict[str, int] = {}
    total = len(stems)
    for i, stem in enumerate(stems):
        if on_progress is not None:
            on_progress(i, total, stem)
        kind, tpath = trans[stem]
        audio = _find_audio(stem, audio_dir)
        if audio is None:
            failed += 1
            log(f"[{stem}] 원본 오디오를 못 찾음 — 건너뜀")
            continue
        try:
            segs, language, duration = _load_transcript(kind, tpath)
            if not segs:
                failed += 1
                log(f"[{stem}] 전사 세그먼트가 비어 있음 — 건너뜀")
                continue
            spk = diarizer.diarize(
                audio, segs, num_speakers=num_speakers, threshold=threshold,
                cache_path=analysis_dir / f"{stem}.emb.npz",
            )
        except DiarizeError:
            raise
        except Exception as exc:  # noqa: BLE001 - 한 파일 실패가 폴더 전체를 막지 않게.
            failed += 1
            log(f"[{stem}] 화자분리 실패: {exc}")
            continue
        n_spk = len(set(spk))
        out = analysis_dir / f"{stem}.script.md"
        tmp = analysis_dir / f"{stem}.script.md.tmp"
        tmp.write_text(
            render_script(segs, title=stem, language=language, duration=duration, speakers=spk),
            encoding="utf-8",
        )
        tmp.replace(out)  # 원자적 교체 — 앨범 레인이 동시에 읽어도 깨진 파일을 안 보게.
        speakers_out[stem] = n_spk
        ok += 1
        log(f"[{stem}] 화자 {n_spk}명 구분 → {out.name}")
    return {"total": total, "ok": ok, "failed": failed, "speakers": speakers_out}


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except (AttributeError, OSError):
                pass

    p = argparse.ArgumentParser(
        prog="ycollector-diarize",
        description="기존 전사 JSON 에 화자 구분을 붙여 화자 라벨 대본(script.md)을 재작성.",
    )
    p.add_argument("--analysis-dir", type=Path, required=True,
                   help="analyze 산출물 폴더(*.json 전사록이 있는 곳, 예: .../LJM/_analysis).")
    p.add_argument("--audio-dir", type=Path, default=None,
                   help="원본 오디오/영상 폴더(기본: analysis-dir 의 상위).")
    p.add_argument("--num-speakers", type=int, default=None,
                   help="화자 수를 알면 지정(미지정 시 자동 추정).")
    p.add_argument("--threshold", type=float, default=0.9,
                   help="자동 추정 시 코사인 거리 임계값(작을수록 화자 많아짐, 기본 0.9). "
                        "보정: 0.75=과분할, 0.9=적정, 1.0+=과병합.")
    args = p.parse_args(argv)

    analysis_dir: Path = args.analysis_dir
    audio_dir: Path = args.audio_dir or analysis_dir.parent

    def _progress(i: int, n: int, stem: str) -> None:
        print(f"\n[{i + 1}/{n}] {stem}", file=sys.stderr)

    try:
        res = diarize_folder(
            analysis_dir, audio_dir,
            num_speakers=args.num_speakers, threshold=args.threshold,
            on_log=lambda m: print(f"[i] {m}", file=sys.stderr),
            on_progress=_progress,
        )
    except DiarizeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 10

    total = int(res.get("total", 0))  # type: ignore[arg-type]
    ok = int(res.get("ok", 0))  # type: ignore[arg-type]
    if not total:
        print(f"[안내] {analysis_dir} 에 *.json|*.srt 전사록이 없습니다.", file=sys.stderr)
        return 0
    print(f"\n[완료] {ok}/{total} 화자분리 완료.", file=sys.stderr)
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
