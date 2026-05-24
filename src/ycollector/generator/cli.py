"""`ycollector-generate` — CLI 진입점.

기본 사용:
    uv run ycollector-generate "프롬프트 본문" \\
        --references "./images/cat.jpg" \\
        --references "https://www.youtube.com/watch?v=..." \\
        --references "https://..." \\
        --model sora-2-pro --size 1280x720 --seconds 8 \\
        --out generated/cat.mp4 --budget-usd 5

references 는 로컬 이미지 파일 경로 / 이미지 URL / YouTube URL 모두 가능.
env: .env 의 OPENAI_API_KEY 자동 로드(python-dotenv 가 있으면).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .base import (
    BudgetExceeded,
    ProviderError,
    VideoJob,
    VideoRequest,
    estimate_cost_usd,
    get_provider,
)


# ── .env 자동 로드 (있으면) ────────────────────────────────────────────────
def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # extras 없으면 조용히 패스.
    # 현재 cwd 와 그 상위로 .env 탐색.
    here = Path.cwd()
    for candidate in [here / ".env", *(p / ".env" for p in here.parents)]:
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return


# ── 진행률 표시 ───────────────────────────────────────────────────────────
_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _print_progress(idx: int, total: int, job: VideoJob, status: str, progress: float, t0: float) -> None:
    pct = progress * 100
    elapsed = time.monotonic() - t0
    ch = _SPINNER[int(elapsed * 10) % len(_SPINNER)]
    is_tty = sys.stderr.isatty()
    prefix = "\r\033[K" if is_tty else ""
    suffix = "" if is_tty else "\n"
    sys.stderr.write(
        f"{prefix}  {ch} [{idx}/{total}] {job.id[:10]}…  {status:13s}  {pct:5.1f}%  ({elapsed:.0f}s){suffix}"
    )
    sys.stderr.flush()


# ── 라이브러리 manifest append (Tauri UI 의 LibraryTab 과 같은 저장소) ─────
def _library_manifest_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
        return base / "YCollector" / "library.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "YCollector" / "library.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "ycollector" / "library.json"


def _library_append(item: dict[str, object]) -> None:
    p = _library_manifest_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            doc = {"version": 1, "items": []}
    else:
        doc = {"version": 1, "items": []}
    doc.setdefault("items", []).insert(0, item)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


# ── main ──────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except (AttributeError, OSError):
                pass
    _load_dotenv()

    p = argparse.ArgumentParser(
        prog="ycollector-generate",
        description="Sora 2 Pro (OpenAI Videos API) 로 영상 생성. .env 의 OPENAI_API_KEY 자동 로드.",
    )
    p.add_argument("prompt", help="생성할 영상의 프롬프트 (한국어/영어 OK).")
    p.add_argument("--references", action="append", default=[], metavar="REF",
                   help="참조 이미지/영상. 로컬 이미지 파일 경로(예: ./cat.jpg, C:\\img\\cat.png) "
                        "또는 이미지 URL, 또는 YouTube URL(thumbnail 자동 추출). 여러 번 줄 수 "
                        "있고 reference 1개당 1잡 → 다중이면 N개 영상 생성.")
    p.add_argument("--model", default="sora-2-pro", choices=["sora-2", "sora-2-pro"])
    p.add_argument("--size", default="1280x720",
                   help="WxH (sora-2: 1280x720 only. sora-2-pro: 1280x720|1024x1792|1792x1024|1920x1080)")
    p.add_argument("--seconds", type=int, default=8, choices=[8, 12, 16, 20])
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--provider", default="sora", help="provider 어댑터 (지금은 sora 만).")
    p.add_argument("--out", type=Path, default=None,
                   help="출력 파일 경로(.mp4). 미지정 시 generated/<id>.mp4. "
                        "reference 가 N개면 stem 뒤에 -0, -1, ... 자동 부여.")
    p.add_argument("--budget-usd", type=float, default=10.0,
                   help="잡 1개당 최대 비용(USD). 초과 시 실행 거부. 기본 10.")
    p.add_argument("--dry-run", action="store_true",
                   help="실제 API 호출 없이 견적·계획만 출력.")
    args = p.parse_args(argv)

    refs: list[str | None] = list(args.references) if args.references else [None]  # type: ignore[list-item]
    n = len(refs)

    # 견적
    base_req = VideoRequest(
        prompt=args.prompt, size=args.size, seconds=args.seconds,
        model=args.model, seed=args.seed,
    )
    per_job = estimate_cost_usd(base_req)
    total_est = per_job * n
    print(f"plan: {n}개 잡 × ${per_job:.2f}/잡 = ${total_est:.2f} (예상)", file=sys.stderr)
    print(f"  model:    {args.model}", file=sys.stderr)
    print(f"  size:     {args.size}", file=sys.stderr)
    print(f"  seconds:  {args.seconds}", file=sys.stderr)
    print(f"  refs:     {args.references or '(none)'}", file=sys.stderr)
    if per_job > args.budget_usd:
        print(
            f"\n  ✗ 잡 1개 견적 ${per_job:.2f} > 한도 ${args.budget_usd:.2f}.\n"
            f"    --budget-usd 를 올리거나 --seconds/--size 를 줄이세요.",
            file=sys.stderr,
        )
        return 4

    if args.dry_run:
        print("\n(--dry-run) 실제 API 호출 안 함.", file=sys.stderr)
        return 0

    try:
        provider = get_provider(args.provider)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_base = args.out or Path("generated") / "video.mp4"
    out_base.parent.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    for i, ref in enumerate(refs, start=1):
        req = VideoRequest(
            prompt=args.prompt, size=args.size, seconds=args.seconds,
            model=args.model, seed=args.seed,
            references=[ref] if ref else [],
            output_dir=out_base.parent,
        )
        out_path = out_base if n == 1 else out_base.with_stem(f"{out_base.stem}-{i:02d}")
        print(f"\n[{i}/{n}] reference: {ref or '(none)'}", file=sys.stderr)
        print(f"       output:    {out_path}", file=sys.stderr)
        try:
            job = provider.create(req)
        except (ProviderError, BudgetExceeded) as exc:
            print(f"  ✗ create 실패: [{exc.category}] {exc}", file=sys.stderr)
            failures.append(str(exc))
            continue

        t0 = time.monotonic()
        last_status = "queued"
        try:
            for st in provider.poll_until_done(job, interval_sec=4.0, timeout_sec=1800.0):
                last_status = st.status
                _print_progress(i, n, job, st.status, st.progress, t0)
                if st.status in ("failed", "cancelled"):
                    print(
                        f"\n  ✗ 잡 실패: [{st.error_category or 'unknown'}] {st.message}",
                        file=sys.stderr,
                    )
                    failures.append(st.message)
                    break
        except ProviderError as exc:
            print(f"\n  ✗ poll 실패: [{exc.category}] {exc}", file=sys.stderr)
            failures.append(str(exc))
            continue

        if last_status != "completed":
            continue

        try:
            saved = provider.download(job, out_path)
        except ProviderError as exc:
            print(f"\n  ✗ 다운로드 실패: [{exc.category}] {exc}", file=sys.stderr)
            failures.append(str(exc))
            continue

        job.output_path = saved
        job.cost_usd = per_job
        print(f"\n  ✓ 저장: {saved}  (${per_job:.2f})", file=sys.stderr)

        _library_append({
            "job_id": job.id,
            "url": ref or "",
            "title": (args.prompt[:80] + "…") if len(args.prompt) > 80 else args.prompt,
            "channel": f"(generated · {args.model})",
            "duration": f"0:{args.seconds:02d}",
            "video_id": job.id,
            "thumbnail": "",
            "filepath": str(saved),
            "finished_at": int(time.time() * 1000),
            "source": "generated",
            "cost_usd": per_job,
        })

    if failures:
        print(f"\n{len(failures)}/{n} 실패.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
