"""``ycollector transcribe`` / ``ycollector-transcribe`` — 로컬 음성·영상 전사.

faster-whisper(in-process)로 파일을 텍스트(txt/srt/vtt/json)로 전사한다.
원본 ``D:\\26y\\transcriber`` 의 UX 계승:

  * 인자 없이 실행 → 파일 선택창(tkinter) 다중 선택
  * 파일/폴더 경로를 인자로 주면 그대로 전사 (폴더는 최상위 미디어 파일 펼침)

기본값은 ``settings.ini`` 의 ``[transcribe]`` 에서 로드(없으면 코드 기본값).

사용 예:
    uv run --extra transcribe ycollector transcribe a.mp3 b.mp4 --format srt
    uv run --extra transcribe ycollector transcribe ./clips --language ko -o out
    uv run --extra transcribe ycollector transcribe            # 파일 선택창
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .config import (
    MEDIA_EXTENSIONS,
    OUTPUT_FORMATS,
    TranscribeConfig,
    load_transcribe_config,
)

_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _mmss(seconds: float) -> str:
    seconds = int(max(0.0, seconds))
    return f"{seconds // 60:d}:{seconds % 60:02d}"


def _preparse_config(argv: list[str] | None) -> Path | None:
    """본 파서 빌드 전에 ``--config PATH`` 만 미리 떼어낸다(설정을 기본값으로 쓰므로)."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path)
    try:
        known, _ = pre.parse_known_args(argv)
    except SystemExit:
        return None
    return known.config


def _pick_files() -> list[Path]:
    """tkinter 다중 파일 선택창. 헤드리스/미설치면 빈 리스트 + 안내."""
    try:
        from tkinter import Tk, filedialog
    except Exception as exc:  # pragma: no cover - 환경 의존
        print(
            f"[안내] 파일 선택창을 열 수 없습니다({exc}).\n"
            "       전사할 파일/폴더 경로를 인자로 주세요.",
            file=sys.stderr,
        )
        return []
    patterns = " ".join(f"*{e}" for e in MEDIA_EXTENSIONS)
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        files = filedialog.askopenfilenames(
            parent=root,
            title="전사할 파일 선택 (여러 개 가능)",
            filetypes=[("음성/영상 파일", patterns), ("모든 파일", "*.*")],
        )
    finally:
        root.destroy()
    return [Path(f) for f in files]


def _expand_inputs(raw: list[str]) -> list[Path]:
    """파일/폴더 인자를 실제 파일 목록으로. 폴더는 최상위 미디어 파일만."""
    files: list[Path] = []
    for r in raw:
        p = Path(r)
        if p.is_dir():
            kids = [
                c for c in sorted(p.iterdir())
                if c.is_file() and c.suffix.lower() in MEDIA_EXTENSIONS
            ]
            if not kids:
                print(f"[!] 폴더에 미디어 파일 없음, 건너뜀: {p}", file=sys.stderr)
            files.extend(kids)
        elif p.is_file():
            files.append(p)
        else:
            print(f"[!] 경로 없음, 건너뜀: {r}", file=sys.stderr)
    return files


def _transcribe_one(engine, src: Path, idx: int, total: int):  # noqa: ANN001 - 지연 타입
    """진행률 라인을 그리며 한 파일 전사. :class:`TranscriptResult` 반환."""
    t0 = time.monotonic()
    is_tty = sys.stderr.isatty()
    last = [0.0]

    def cb(seg, duration: float) -> None:  # noqa: ANN001
        now = time.monotonic()
        elapsed = now - t0
        pct = (seg.end / duration * 100.0) if duration else 0.0
        if is_tty:
            ch = _SPINNER[int(elapsed * 10) % len(_SPINNER)]
            sys.stderr.write(
                f"\r\033[K  {ch} [{idx}/{total}] {pct:5.1f}%  "
                f"{_mmss(seg.end)}/{_mmss(duration)}  ({elapsed:.0f}s)"
            )
            sys.stderr.flush()
        elif now - last[0] >= 2.0:
            sys.stderr.write(
                f"  [{idx}/{total}] {pct:5.1f}%  {_mmss(seg.end)}/{_mmss(duration)}\n"
            )
            sys.stderr.flush()
            last[0] = now

    result = engine.transcribe(src, on_segment=cb)
    if is_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()
    return result


def main(argv: list[str] | None = None) -> int:
    # Windows cp949 콘솔에서 한글/기호가 깨지지 않도록 UTF-8 재설정(다른 CLI 와 동일).
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except (AttributeError, OSError):
                pass

    cfg, cfg_path = load_transcribe_config(_preparse_config(argv))

    p = argparse.ArgumentParser(
        prog="ycollector transcribe",
        description="로컬 음성/영상 파일을 faster-whisper 로 전사(txt/srt/vtt/json).",
    )
    p.add_argument("files", nargs="*", metavar="PATH",
                   help="전사할 파일/폴더. 생략하면 파일 선택창을 엽니다.")
    p.add_argument("--config", metavar="PATH", type=Path,
                   help="이 settings.ini 의 [transcribe] 를 기본값으로 사용.")
    p.add_argument("--model", default=cfg.model,
                   help=f"모델 별칭/경로 (기본: {cfg.model}). 예: tiny, small, "
                        f"large-v3, large-v3-turbo, turbo.")
    p.add_argument("--language", default=cfg.language or "auto",
                   help="언어 코드(ko|en|fr|ja …) 또는 'auto'(자동 감지). "
                        f"기본: {cfg.language or 'auto'}.")
    p.add_argument("--task", choices=["transcribe", "translate"], default=cfg.task,
                   help=f"transcribe=원어 그대로, translate=영어 번역 (기본: {cfg.task}).")
    p.add_argument("--format", "--output-format", dest="output_format",
                   choices=list(OUTPUT_FORMATS), default=cfg.output_format,
                   help=f"출력 포맷 (기본: {cfg.output_format}).")
    p.add_argument("-o", "--output-dir", type=Path, default=Path(cfg.output_dir),
                   help=f"결과 저장 폴더 (기본: {cfg.output_dir}).")
    p.add_argument("--device", choices=["auto", "cpu", "cuda"], default=cfg.device,
                   help=f"추론 장치 (기본: {cfg.device}).")
    p.add_argument("--compute-type", default=cfg.compute_type,
                   help=f"양자화: auto|int8|int8_float16|float16|float32 (기본: {cfg.compute_type}).")
    p.add_argument("--beam-size", type=int, default=cfg.beam_size,
                   help=f"디코딩 beam size (기본: {cfg.beam_size}).")
    p.add_argument("--no-vad", action="store_true",
                   help="VAD(무음 제거) 필터 끄기.")
    p.add_argument("--pick", action="store_true",
                   help="인자가 있어도 파일 선택창을 추가로 엽니다.")
    args = p.parse_args(argv)

    print(f"settings: {cfg_path if cfg_path else '(none — using code defaults)'}", file=sys.stderr)

    # ── 입력 수집 ───────────────────────────────────────────────────────────
    files = _expand_inputs(args.files)
    if args.pick or not files:
        files.extend(_pick_files())
    # 중복 제거(순서 보존).
    seen: set[str] = set()
    uniq: list[Path] = []
    for f in files:
        key = str(f.resolve()).lower()
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    files = uniq

    if not files:
        print("[안내] 전사할 파일이 없습니다. 종료합니다.", file=sys.stderr)
        return 0

    # ── 설정 구성 ───────────────────────────────────────────────────────────
    lang = (args.language or "").strip()
    run_cfg = TranscribeConfig(
        model=args.model,
        language=None if lang.lower() in ("", "auto") else lang,
        task=args.task,
        output_format=args.output_format,
        output_dir=str(args.output_dir),
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
    )

    # ── 엔진 로드(여기서 처음 faster-whisper/ctranslate2 import) ─────────────
    from .whisper import TranscribeEngine, TranscribeError, write_transcript

    try:
        engine = TranscribeEngine(run_cfg, on_log=lambda m: print(f"[i] {m}", file=sys.stderr))
    except TranscribeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 10 if exc.category == "not-installed" else 11

    out_dir = Path(run_cfg.output_dir)
    print(
        f"[설정] model={run_cfg.model}  language={run_cfg.language or 'auto'}  "
        f"task={run_cfg.task}  format={run_cfg.output_format}",
        file=sys.stderr,
    )
    print(f"[설정] 출력 디렉토리: {out_dir.resolve()}", file=sys.stderr)
    print(f"\n[안내] {len(files)}개 파일 전사 시작", file=sys.stderr)

    # ── 전사 루프 ───────────────────────────────────────────────────────────
    failed: list[tuple[Path, str]] = []
    for i, src in enumerate(files, start=1):
        print(f"\n{'=' * 70}\n[{i}/{len(files)}] {src}\n{'=' * 70}", file=sys.stderr)
        try:
            result = _transcribe_one(engine, src, i, len(files))
            dest = write_transcript(result, out_dir, src.stem, run_cfg.output_format)
        except TranscribeError as exc:
            print(f"  ✗ [{exc.category}] {exc}", file=sys.stderr)
            failed.append((src, exc.message))
            continue
        except KeyboardInterrupt:
            print("\n중단됨 (Ctrl+C).", file=sys.stderr)
            return 3
        mins = f"{result.duration / 60:.1f}분" if result.duration else "?"
        print(
            f"  ✓ {dest}  (언어 {result.language} {result.language_probability:.0%}, {mins})",
            file=sys.stderr,
        )

    # ── 요약 ───────────────────────────────────────────────────────────────
    ok = len(files) - len(failed)
    print(f"\n{'=' * 70}", file=sys.stderr)
    print(f"[완료] 성공 {ok}/{len(files)}   출력: {out_dir.resolve()}", file=sys.stderr)
    if failed:
        print("[실패]", file=sys.stderr)
        for src, msg in failed:
            print(f"  - {src.name}: {msg}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
