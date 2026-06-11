"""``ycollector analyze`` / ``ycollector-analyze`` — LilysAI 류 분석 파이프라인.

음성/영상 파일(또는 폴더)을 받아 영상마다:
  1. 전사 (faster-whisper, 타임스탬프)        → ``<stem>.srt``
  2. 사람이 읽는 대본                          → ``<stem>.script.md``
  3. 전체 요약 + 핵심 파트(시간대) (Claude)    → ``<stem>.summary.md``

화자 분리(diarization)는 현재 보류 — 추후 추가([[project_ljm_analysis]]).

사용 예:
    uv run --extra transcribe --extra transcribe-cuda --extra summarize --native-tls \\
        ycollector analyze "D:/.../download/LJM" --language ko --budget-usd 10

    # 요약 없이 전사+대본만:
    ycollector analyze ./clips --no-summarize
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .cli import _expand_inputs, _pick_files, _preparse_config, _transcribe_one
from .config import OUTPUT_FORMATS, TranscribeConfig, load_transcribe_config
from .summarize import DEFAULT_SUMMARY_MODEL


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except (AttributeError, OSError):
                pass

    cfg, cfg_path = load_transcribe_config(_preparse_config(argv))

    p = argparse.ArgumentParser(
        prog="ycollector analyze",
        description="음성/영상 → 전사 + 대본 + 요약(핵심 파트·시간대). faster-whisper + Claude.",
    )
    p.add_argument("files", nargs="*", metavar="PATH",
                   help="분석할 파일/폴더. 생략하면 파일 선택창을 엽니다.")
    p.add_argument("--config", metavar="PATH", type=Path)
    # 전사 옵션
    p.add_argument("--model", default=cfg.model, help=f"Whisper 모델 (기본: {cfg.model}).")
    p.add_argument("--language", default=cfg.language or "auto",
                   help="언어 코드 또는 'auto' (기본: auto).")
    p.add_argument("--task", choices=["transcribe", "translate"], default=cfg.task)
    p.add_argument("--transcript-format", choices=list(OUTPUT_FORMATS), default="srt",
                   help="전사 저장 포맷 (기본: srt).")
    p.add_argument("--device", choices=["auto", "cpu", "cuda"], default=cfg.device)
    p.add_argument("--compute-type", default=cfg.compute_type)
    p.add_argument("--beam-size", type=int, default=cfg.beam_size)
    p.add_argument("--no-vad", action="store_true")
    # 요약 옵션
    p.add_argument("--no-summarize", action="store_true", help="요약 없이 전사+대본만.")
    p.add_argument("--summary-model", default=DEFAULT_SUMMARY_MODEL,
                   help=f"요약 LLM (기본: {DEFAULT_SUMMARY_MODEL}).")
    p.add_argument("--budget-usd", type=float, default=5.0,
                   help="요약 전체 비용 상한 USD (기본: 5).")
    p.add_argument("-o", "--output-dir", type=Path, default=None,
                   help="산출물 폴더 (기본: 입력 폴더의 _analysis/ 하위).")
    args = p.parse_args(argv)

    print(f"settings: {cfg_path if cfg_path else '(none)'}", file=sys.stderr)

    files = _expand_inputs(args.files)
    if not files:
        files = _pick_files()
    # 중복 제거.
    seen: set[str] = set()
    uniq = []
    for f in files:
        k = str(f.resolve()).lower()
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    files = uniq
    if not files:
        print("[안내] 분석할 파일이 없습니다.", file=sys.stderr)
        return 0

    out_dir = args.output_dir or (files[0].parent / "_analysis")

    lang = (args.language or "").strip()
    run_cfg = TranscribeConfig(
        model=args.model,
        language=None if lang.lower() in ("", "auto") else lang,
        task=args.task,
        output_format=args.transcript_format,
        output_dir=str(out_dir),
        device=args.device,
        compute_type=args.compute_type,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
    )

    # ── 엔진/요약기 로드 (지연 import) ──────────────────────────────────────
    from .whisper import TranscribeEngine, TranscribeError, write_transcript

    try:
        engine = TranscribeEngine(run_cfg, on_log=lambda m: print(f"[i] {m}", file=sys.stderr))
    except TranscribeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 10

    summarizer = None
    if not args.no_summarize:
        from .summarize import SummarizeError, Summarizer

        try:
            summarizer = Summarizer(
                model=args.summary_model,
                budget_usd=args.budget_usd,
                on_log=lambda m: print(f"[i] {m}", file=sys.stderr),
            )
        except SummarizeError as exc:
            print(
                f"[!] 요약기 비활성화: {exc}\n    전사+대본만 진행합니다.",
                file=sys.stderr,
            )
            summarizer = None

    from .report import write_reports

    print(f"[설정] whisper={run_cfg.model} ({engine.device})  요약={'off' if summarizer is None else args.summary_model}",
          file=sys.stderr)
    print(f"[설정] 출력: {out_dir.resolve()}", file=sys.stderr)
    print(f"\n[안내] {len(files)}개 파일 분석 시작\n", file=sys.stderr)

    failed: list[tuple[str, str]] = []
    summarized = 0
    for i, src in enumerate(files, 1):
        print(f"{'=' * 70}\n[{i}/{len(files)}] {src.name}\n{'=' * 70}", file=sys.stderr)
        # 1) 전사
        try:
            result = _transcribe_one(engine, src, i, len(files))
            tpath = write_transcript(result, out_dir, src.stem, run_cfg.output_format)
        except TranscribeError as exc:
            print(f"  ✗ 전사 실패 [{exc.category}] {exc}", file=sys.stderr)
            failed.append((src.name, f"전사: {exc.message}"))
            continue
        except KeyboardInterrupt:
            print("\n중단됨 (Ctrl+C).", file=sys.stderr)
            return 3
        print(f"  ✓ 전사: {tpath.name}  (언어 {result.language}, {result.duration/60:.1f}분)",
              file=sys.stderr)

        # 2) 요약 (가능하면)
        summary = None
        if summarizer is not None:
            try:
                t0 = time.monotonic()
                summary = summarizer.summarize(result.segments)
                summarized += 1
                print(f"  ✓ 요약: 파트 {len(summary.sections)}개  "
                      f"(누적 ${summarizer.budget.spent_usd:.2f}, {time.monotonic()-t0:.0f}s)",
                      file=sys.stderr)
            except Exception as exc:  # BudgetExceeded 포함 — 전사물은 유지.
                from .summarize import BudgetExceeded

                print(f"  ✗ 요약 실패: {exc}", file=sys.stderr)
                if isinstance(exc, BudgetExceeded):
                    print("    예산 소진 — 이후 영상은 요약 건너뜀.", file=sys.stderr)
                    summarizer = None  # 이후 영상은 전사만.

        # 3) 리포트
        written = write_reports(
            out_dir, src.stem, result.segments, summary,
            title=src.stem, language=result.language, duration=result.duration,
        )
        for kind, pth in written.items():
            print(f"  ✓ {kind}: {pth.name}", file=sys.stderr)
        print("", file=sys.stderr)

    # ── 요약 ───────────────────────────────────────────────────────────────
    ok = len(files) - len(failed)
    print(f"{'=' * 70}", file=sys.stderr)
    print(f"[완료] 전사 성공 {ok}/{len(files)}   요약 {summarized}/{len(files)}", file=sys.stderr)
    print(f"[완료] 출력: {out_dir.resolve()}", file=sys.stderr)
    if failed:
        print("[실패]", file=sys.stderr)
        for name, msg in failed:
            print(f"  - {name}: {msg}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
