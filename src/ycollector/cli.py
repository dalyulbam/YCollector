"""YCollector CLI — Phase 0 Day 1.

Supports a small subset of plan §11.5.1::

    ycollector <URL> [<URL> ...]
    ycollector --from urls.txt
    cat urls.txt | ycollector -

Future subcommands (add/queue/sync/transcribe/library/preset/daemon/doctor)
are described in the plan and will land in later phases.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ycollector import __version__
from ycollector.engine import DownloadError, ProgressEvent, YtdlpEngine


def _human_bytes(n: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:6.1f} {unit}"
        size /= 1024
    return f"{size:6.1f} PB"  # unreachable, satisfies type checker


def _make_progress_printer():
    is_tty = sys.stderr.isatty()
    state = {"painting": False}

    def printer(event: ProgressEvent) -> None:
        if event.status == "downloading":
            pct = event.percent or 0.0
            done = _human_bytes(event.downloaded_bytes)
            total = _human_bytes(event.total_bytes) if event.total_bytes else "    ?  "
            speed = _human_bytes(event.speed) + "/s" if event.speed else "      ?    "
            line = f"  {pct:5.1f}%  {done} / {total}  @ {speed}"
            if is_tty:
                sys.stderr.write("\r\033[K" + line)
                state["painting"] = True
            else:
                sys.stderr.write(line + "\n")
            sys.stderr.flush()
        elif event.status == "finished":
            if state["painting"]:
                sys.stderr.write("\n")
                state["painting"] = False
            if event.filename:
                sys.stderr.write(f"  ✓ {event.filename}\n")
            sys.stderr.flush()

    return printer


def _read_urls(args: argparse.Namespace) -> list[str]:
    urls: list[str] = []
    stdin_used = False
    for u in args.urls:
        if u == "-":
            stdin_used = True
        else:
            urls.append(u)
    if args.from_:
        text = Path(args.from_).read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    if stdin_used:
        for line in sys.stdin:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ycollector",
        description="YouTube video collector — yt-dlp wrapper (Phase 0)",
    )
    p.add_argument("urls", nargs="*", metavar="URL",
                   help="URL(s) to download. Use '-' to read additional URLs from stdin.")
    p.add_argument("--from", dest="from_", metavar="FILE",
                   help="Read URLs from a file (one per line, '#' for comments).")
    p.add_argument("-o", "--output-dir", default=Path("downloads"), type=Path,
                   help="Output directory (default: ./downloads).")
    p.add_argument("-f", "--format", default="bv*[height<=1080]+ba/b[height<=1080]",
                   help="yt-dlp format selector.")
    p.add_argument("--container", default="mp4", choices=["mp4", "mkv", "webm"],
                   help="Output container (default: mp4).")
    p.add_argument("--no-subs", action="store_true",
                   help="Skip subtitle download / embed.")
    p.add_argument("--sub-langs", default="ko,en",
                   help="Comma-separated subtitle languages (default: ko,en).")
    p.add_argument("--cookies-from-browser", metavar="BROWSER",
                   help="Import cookies from browser (chrome, firefox, edge, brave, ...).")
    p.add_argument("--version", action="version", version=f"ycollector {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    urls = _read_urls(args)
    if not urls:
        parser.error("no URL given (positional, --from FILE, or stdin via '-')")
        return 2

    try:
        engine = YtdlpEngine()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 10
    print(f"yt-dlp {engine.version()}  (ycollector {__version__})", file=sys.stderr)

    failures: list[tuple[str, DownloadError]] = []
    for i, url in enumerate(urls, start=1):
        print(f"\n[{i}/{len(urls)}] {url}", file=sys.stderr)
        try:
            path = engine.download(
                url,
                format=args.format,
                output_dir=args.output_dir,
                merge_format=args.container,
                write_subs=not args.no_subs,
                sub_langs=args.sub_langs.split(",") if args.sub_langs else (),
                cookies_from_browser=args.cookies_from_browser,
                on_progress=_make_progress_printer(),
            )
            print(f"  → {path}", file=sys.stderr)
        except DownloadError as exc:
            failures.append((url, exc))
            print(f"  ✗ {exc}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)}/{len(urls)} failed:", file=sys.stderr)
        for url, err in failures:
            print(f"  - {url}\n      [{err.category}] {err.message}", file=sys.stderr)
        return 1 if len(failures) < len(urls) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
