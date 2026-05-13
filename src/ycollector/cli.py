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
from ycollector.config import Settings, load_settings
from ycollector.engine import (
    AudioPref,
    CodecPref,
    Container,
    DownloadError,
    FormatChoice,
    ProgressEvent,
    Quality,
    YtdlpEngine,
    compose_format_spec,
)
from ycollector.engine.ytdlp import is_ambiguous_playlist_url


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


def _build_parser(s: Settings) -> argparse.ArgumentParser:
    """Build the CLI parser using ``s`` (from settings.ini) as defaults."""
    p = argparse.ArgumentParser(
        prog="ycollector",
        description="YouTube video collector — yt-dlp wrapper (Phase 0)",
    )
    p.add_argument("urls", nargs="*", metavar="URL",
                   help="URL(s) to download. Use '-' to read additional URLs from stdin.")
    p.add_argument("--from", dest="from_", metavar="FILE",
                   help="Read URLs from a file (one per line, '#' for comments).")
    p.add_argument("--config", metavar="PATH", type=Path,
                   help="Use this settings.ini instead of the default search "
                        "(./settings.ini, %%APPDATA%%\\YCollector\\settings.ini, ...).")
    p.add_argument("-o", "--output-dir", type=Path, default=Path(s.output_dir),
                   help=f"Output directory (default from settings.ini: {s.output_dir}).")
    p.add_argument("-f", "--format", default=None,
                   help="Raw yt-dlp format selector (overrides --quality / --codec / --audio).")
    p.add_argument("--quality", default=s.quality,
                   choices=[q.value for q in Quality],
                   help=f"Quality preset (default from settings.ini: {s.quality}).")
    p.add_argument("--codec", default=s.codec,
                   choices=[c.value for c in CodecPref],
                   help=f"Video codec preference (default from settings.ini: {s.codec}).")
    p.add_argument("--audio", default=s.audio,
                   choices=[a.value for a in AudioPref],
                   help=f"Audio preference (default from settings.ini: {s.audio}).")
    p.add_argument("--container", default=s.container, choices=["mp4", "mkv", "webm"],
                   help=f"Output container (default from settings.ini: {s.container}).")
    p.add_argument("--no-subs", action="store_true",
                   help="Skip subtitle download / embed.")
    p.add_argument("--sub-langs", default=",".join(s.sub_langs),
                   help=f"Comma-separated subtitle languages "
                        f"(default from settings.ini: {','.join(s.sub_langs)}).")
    p.add_argument("--cookies-from-browser", metavar="BROWSER",
                   default=s.cookies_from_browser,
                   help="Import cookies from browser (chrome, firefox, edge, brave, ...).")
    # ── playlist handling ──────────────────────────────────────────────────
    pl_grp = p.add_mutually_exclusive_group()
    pl_grp.add_argument("--no-playlist", action="store_true",
                        help="Treat URL as a single video even if it has ?list=PLAYLIST_ID.")
    pl_grp.add_argument("--yes-playlist", action="store_true",
                        help="Force-expand the playlist (overrides settings.ini and auto-detect).")
    p.add_argument("--max-downloads", type=int, default=s.max_downloads, metavar="N",
                   help=f"Stop after N successful downloads (default from settings.ini: "
                        f"{s.max_downloads or 'unlimited'}).")
    p.add_argument("--playlist-items", default=s.playlist_items, metavar="SPEC",
                   help="Which items of a playlist to download (e.g. '1-3,7,10-').")
    # ── stall mitigation (defaults from settings.ini) ──────────────────────
    p.add_argument("--socket-timeout", type=int, default=s.socket_timeout, metavar="SEC",
                   help=f"Abort hung sockets after N seconds and retry "
                        f"(default from settings.ini: {s.socket_timeout}).")
    p.add_argument("--retries", type=int, default=s.retries, metavar="N",
                   help=f"Retries for failed connections (default from settings.ini: {s.retries}).")
    p.add_argument("--fragment-retries", type=int, default=s.fragment_retries, metavar="N",
                   help=f"Retries for failed DASH/HLS fragments "
                        f"(default from settings.ini: {s.fragment_retries}).")
    p.add_argument("--throttled-rate", metavar="RATE", default=s.throttled_rate,
                   help=f"If download rate falls below RATE (e.g. '100K'), restart "
                        f"the connection (default from settings.ini: {s.throttled_rate or 'off'}).")
    p.add_argument("--version", action="version", version=f"ycollector {__version__}")
    return p


def _preparse_config(argv: list[str] | None) -> Path | None:
    """Sniff just ``--config PATH`` from argv so we can load settings before
    building the full parser (which uses settings as defaults)."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path)
    try:
        known, _ = pre.parse_known_args(argv)
    except SystemExit:
        return None
    return known.config


def main(argv: list[str] | None = None) -> int:
    # Windows 한국어 시스템의 cp949 콘솔에서 ✓ / ✗ / 한글 등이 깨지지 않도록.
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except (AttributeError, OSError):
                pass

    settings, settings_path = load_settings(_preparse_config(argv))
    parser = _build_parser(settings)
    args = parser.parse_args(argv)
    if settings_path is not None:
        print(f"settings: {settings_path}", file=sys.stderr)
    else:
        print("settings: (none — using code defaults)", file=sys.stderr)

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

    if args.format is None:
        choice = FormatChoice(
            quality=Quality(args.quality),
            container=Container(args.container),
            codec=CodecPref(args.codec),
            audio=AudioPref(args.audio),
        )
        args.format = compose_format_spec(choice)
        print(f"format spec: {args.format}", file=sys.stderr)

    failures: list[tuple[str, DownloadError]] = []
    interrupted_at: int | None = None
    try:
        for i, url in enumerate(urls, start=1):
            print(f"\n[{i}/{len(urls)}] {url}", file=sys.stderr)

            # Decide playlist behaviour. Precedence:
            #   1) explicit --yes-playlist / --no-playlist
            #   2) settings.ini [playlist] mode
            #   3) auto: ambiguous video?list= URLs become single-video
            if args.yes_playlist:
                no_pl, yes_pl = False, True
            elif args.no_playlist:
                no_pl, yes_pl = True, False
            elif settings.playlist_mode == "single":
                no_pl, yes_pl = True, False
                print("  [i] playlist mode = single → 단일 영상으로 처리", file=sys.stderr)
            elif settings.playlist_mode == "expand":
                no_pl, yes_pl = False, True
            else:  # auto
                if is_ambiguous_playlist_url(url):
                    no_pl, yes_pl = True, False
                    print(
                        "  [i] 단일 영상 URL + ?list= 컨텍스트 감지 — 단일 영상만 받습니다.\n"
                        "      재생목록 전체를 받으려면 --yes-playlist 추가.",
                        file=sys.stderr,
                    )
                else:
                    no_pl, yes_pl = False, False  # let yt-dlp default

            try:
                path = engine.download(
                    url,
                    format=args.format,
                    output_dir=args.output_dir,
                    merge_format=args.container,
                    write_subs=not args.no_subs,
                    sub_langs=args.sub_langs.split(",") if args.sub_langs else (),
                    cookies_from_browser=args.cookies_from_browser,
                    socket_timeout=args.socket_timeout,
                    retries=args.retries,
                    fragment_retries=args.fragment_retries,
                    throttled_rate=args.throttled_rate,
                    no_playlist=no_pl,
                    yes_playlist=yes_pl,
                    max_downloads=args.max_downloads,
                    playlist_items=args.playlist_items,
                    on_progress=_make_progress_printer(),
                )
                print(f"  → {path}", file=sys.stderr)
            except DownloadError as exc:
                failures.append((url, exc))
                print(f"  ✗ {exc}", file=sys.stderr)
    except KeyboardInterrupt:
        interrupted_at = i  # noqa: F821 - bound by `for` above when this runs
        print(
            "\n\n중단됨 (Ctrl+C). 부분 다운로드(.part)가 남아 있어, "
            "동일한 명령으로 다시 실행하면 yt-dlp가 자동으로 이어받기를 합니다.\n"
            "  → 멈춤이 잦으면: --socket-timeout 15 --throttled-rate 100K",
            file=sys.stderr,
        )

    if interrupted_at is not None:
        print(f"  진행: {interrupted_at - 1}/{len(urls)} 완료, 1개 중단", file=sys.stderr)
        return 3

    if failures:
        print(f"\n{len(failures)}/{len(urls)} failed:", file=sys.stderr)
        for url, err in failures:
            print(f"  - {url}\n      [{err.category}] {err.message}", file=sys.stderr)
        return 1 if len(failures) < len(urls) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
