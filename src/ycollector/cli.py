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
import re
import sys
import threading
import time
from pathlib import Path

from ycollector import __version__
from ycollector.config import Settings, load_settings
from ycollector.engine import (
    AudioPref,
    CodecPref,
    Container,
    DownloadError,
    FormatChoice,
    MetaInfo,
    ProgressEvent,
    Quality,
    YtdlpEngine,
    compose_format_spec,
)
from ycollector.engine.ytdlp import _find_deno_dir, is_ambiguous_playlist_url


# yt-dlp 가 stderr/stdout 에 찍는 단계 라벨. 다운로드 직전까지 무엇을 하고 있는지
# 보여주려고 spinner 라벨로 노출한다 ("준비 중" 만으로는 사용자가 멈춤을 의심).
_ACTIVITY_RE = re.compile(r"^\[(?P<src>\w+)\]\s+(?P<msg>.+?)$")
# 11자 YouTube video-ID 프리픽스. `[youtube]`/`[info]` 메시지에서만 떼어낸다 —
# `[download] Destination:` 의 "Destination" 도 11자 영문이라 무조건 떼면 오인.
_VID_ID_PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]{11}:\s+")


def _format_activity_msg(line: str) -> str | None:
    """yt-dlp 의 ``[src] message`` 라인에서 사용자에게 보여줄 메시지를 추출."""
    m = _ACTIVITY_RE.match(line)
    if not m:
        return None
    src = m.group("src")
    msg = m.group("msg")
    if src in ("youtube", "info"):
        msg = _VID_ID_PREFIX_RE.sub("", msg)
    return msg


def _human_bytes(n: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(n)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:6.1f} {unit}"
        size /= 1024
    return f"{size:6.1f} PB"  # unreachable, satisfies type checker


_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


class _StatusLine:
    """단일 stderr 라인을 백그라운드 스레드가 상시 페인팅.

    yt-dlp 가 메타데이터 추출(시작 직후)·머지/자막 임베드(다운로드 직후)
    같은 진행률 hook 이 없는 구간에 머무를 때, "준비 중 / 후처리 중"
    스피너와 경과 시간을 100ms 주기로 갱신해서 사용자가 멈춤/대기를
    구분할 수 있게 한다. ProgressEvent 가 들어오면 같은 라인을 진행률
    숫자로 바꿔 페인팅 — 두 출력원이 한 라인을 공유하지 않도록 본 클래스가
    유일한 페인터다.
    """

    # 진행률 이벤트조차 없는 초기 대기가 매우 짧을 땐 스피너를 띄우지 말자.
    # 300ms 이내에 끝나면 깜빡임 없이 통과.
    _PAINT_DELAY = 0.3

    # TTY 가 아니면(파일 리다이렉트 등) 매 frame 출력은 spam — 2초마다 한 줄.
    # 추가로 상태 전환(preparing→downloading→postprocessing) 시점엔 즉시 한 줄.
    _NONTTY_LOG_INTERVAL = 2.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._state = "preparing"   # preparing | downloading | postprocessing
        self._event: ProgressEvent | None = None
        self._meta: MetaInfo | None = None
        self._activity: str = ""  # 마지막 yt-dlp "[src] message" 라인
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._is_tty = sys.stderr.isatty()
        self._frame_idx = 0
        self._last_log_t = 0.0

    def on_meta(self, meta: MetaInfo) -> None:
        """yt-dlp pre_process — 다운로드 시작 전 제목/채널/길이가 알려진 시점."""
        with self._lock:
            self._meta = meta
        # spinner 라인을 일시적으로 지우고 굵게 한 줄 출력 → spinner 가 그 아래에 다시 그림.
        if self._is_tty:
            sys.stderr.write("\r\033[K")
        sys.stderr.write(f"  ▶ {meta.title}\n")
        sys.stderr.write(f"     채널: {meta.channel}   길이: {meta.duration}   ID: {meta.video_id}\n")
        sys.stderr.flush()

    def on_log(self, line: str) -> None:
        """yt-dlp 의 모든 stdout/stderr 라인을 받아 spinner 활동 라벨을 갱신."""
        msg = _format_activity_msg(line)
        if msg is not None:
            if len(msg) > 70:
                msg = msg[:67] + "…"
            with self._lock:
                self._activity = msg

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def on_progress(self, event: ProgressEvent) -> None:
        with self._lock:
            prev = self._state
            if event.status == "downloading":
                self._state = "downloading"
                self._event = event
            elif event.status == "finished":
                # 비디오/오디오 프래그먼트가 끝나면 잠시 postprocessing 으로.
                # 다음 fragment 가 시작되면 다시 downloading 으로 전환됨.
                self._state = "postprocessing"
                self._event = None
            transitioned = prev != self._state
        # 상태 전환 시점엔 비-TTY 모드에서도 보이도록 즉시 한 줄 페인팅.
        if transitioned:
            self._paint(force=True)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        if self._is_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

    def _loop(self) -> None:
        # 너무 빨리 끝나는 작업에선 스피너를 표시하지 않는다.
        if self._stop.wait(self._PAINT_DELAY):
            return
        self._paint()
        while not self._stop.wait(0.1):
            self._paint()

    def _paint(self, *, force: bool = False) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._t0
            self._frame_idx = (self._frame_idx + 1) % len(_SPINNER_FRAMES)
            ch = _SPINNER_FRAMES[self._frame_idx]

            if self._state == "downloading" and self._event:
                e = self._event
                pct = e.percent or 0.0
                done = _human_bytes(e.downloaded_bytes)
                total = _human_bytes(e.total_bytes) if e.total_bytes else "    ?  "
                speed = _human_bytes(e.speed) + "/s" if e.speed else "      ?    "
                eta_str = f"  ETA {e.eta}s" if e.eta else ""
                line = (
                    f"  {ch} {pct:5.1f}%  {done} / {total}  @ {speed}"
                    f"{eta_str}  ({elapsed:.0f}s)"
                )
            else:
                label = "준비 중" if self._state == "preparing" else "후처리 중"
                # 활동 라벨이 있으면 같이 — 이게 핵심: "준비 중 — Downloading js player..."
                activity = f"  —  {self._activity}" if self._activity else ""
                line = f"  {ch} {label}... {elapsed:.1f}s{activity}"

            if self._is_tty:
                sys.stderr.write("\r\033[K" + line)
                sys.stderr.flush()
            elif force or now - self._last_log_t >= self._NONTTY_LOG_INTERVAL:
                sys.stderr.write(line + "\n")
                sys.stderr.flush()
                self._last_log_t = now


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
                   help="Import cookies from browser (chrome, firefox, edge, brave, ...). "
                        "주의: 해당 브라우저는 실행 중이면 안 됨. 메인 Chrome 켜둔 채로 "
                        "쓰려면 --cookies 또는 `ycollector-login` 사용.")
    p.add_argument("--cookies", metavar="FILE", type=Path, default=None,
                   help="Netscape HTTP Cookie File 경로 (yt-dlp 의 --cookies 패스스루). "
                        "지정 안 하면 settings.ini 의 cookies_file 또는 기본 위치 "
                        "(%%APPDATA%%\\YCollector\\cookies.txt) 자동 탐지. "
                        "생성: `uv run ycollector-login`.")
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
    # stdin도 UTF-8 — Tauri sidecar 모드에서 한글 settings/URL이 들어와도 안전.
    try:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

    # NDJSON sidecar 모드: 다른 모든 인자/설정을 무시하고 본격 진입.
    # Tauri 프런트가 `ycollector --json` 으로 spawn한다.
    raw_argv = sys.argv[1:] if argv is None else argv
    if "--json" in raw_argv:
        from ycollector.sidecar import run as sidecar_run
        return sidecar_run()

    # `ycollector transcribe ...` — 로컬 음성/영상 전사 서브커맨드.
    # 본 다운로드 파서는 positional 이 URL 이므로, 서브커맨드는 파서 진입 전에
    # 선분기한다(위 `--json` sidecar 분기와 동일한 패턴).
    if raw_argv and raw_argv[0] == "transcribe":
        from ycollector.transcribe.cli import main as transcribe_main
        return transcribe_main(raw_argv[1:])

    # `ycollector analyze ...` — 전사 + 대본 + 요약 파이프라인(LilysAI 류).
    if raw_argv and raw_argv[0] == "analyze":
        from ycollector.transcribe.analyze import main as analyze_main
        return analyze_main(raw_argv[1:])

    # `ycollector album ...` — 장면 캡쳐 + 대화 HTML 앨범북.
    if raw_argv and raw_argv[0] == "album":
        from ycollector.transcribe.album import main as album_main
        return album_main(raw_argv[1:])

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
    # winget 으로 설치한 deno 가 PATH 에 없을 때, 발견된 fallback 경로를 알린다.
    deno_dir = _find_deno_dir()
    if deno_dir is not None:
        print(f"[i] deno (JS runtime) 발견 — PATH 추가: {deno_dir}", file=sys.stderr)

    # 쿠키 우선순위:
    #   1) CLI --cookies
    #   2) settings.ini cookies_file
    #   3) 기본 위치(%APPDATA%\YCollector\cookies.txt) 자동 탐지
    # file 이 있으면 cookies_from_browser 는 무시(engine 에서도 동일).
    from ycollector.cookies import default_cookies_path, is_cookies_present
    cookies_file: Path | None = args.cookies
    if cookies_file is None and settings.cookies_file:
        cookies_file = Path(settings.cookies_file)
    if cookies_file is None:
        auto = default_cookies_path()
        if is_cookies_present(auto):
            cookies_file = auto
            print(f"[i] cookies.txt 자동 감지: {auto}", file=sys.stderr)
    if cookies_file is not None and not cookies_file.is_file():
        print(
            f"[!] --cookies 경로가 파일이 아님: {cookies_file}\n"
            f"     무시하고 진행. 생성: uv run ycollector-login",
            file=sys.stderr,
        )
        cookies_file = None
    if cookies_file is not None:
        print(f"cookies: {cookies_file}", file=sys.stderr)

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

            status = _StatusLine()
            status.start()
            try:
                path = engine.download(
                    url,
                    format=args.format,
                    output_dir=args.output_dir,
                    merge_format=args.container,
                    write_subs=not args.no_subs,
                    sub_langs=args.sub_langs.split(",") if args.sub_langs else (),
                    cookies_from_browser=args.cookies_from_browser,
                    cookies_file=cookies_file,
                    socket_timeout=args.socket_timeout,
                    retries=args.retries,
                    fragment_retries=args.fragment_retries,
                    throttled_rate=args.throttled_rate,
                    no_playlist=no_pl,
                    yes_playlist=yes_pl,
                    max_downloads=args.max_downloads,
                    playlist_items=args.playlist_items,
                    on_progress=status.on_progress,
                    on_log=status.on_log,
                    on_meta=status.on_meta,
                )
            except DownloadError as exc:
                failures.append((url, exc))
                print(f"  ✗ {exc}", file=sys.stderr)
            else:
                print(f"  → {path}", file=sys.stderr)
            finally:
                # KeyboardInterrupt 가 download() 한가운데서 발생해도
                # 페인터 스레드가 단정하게 멈춘다.
                status.stop()
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
        # 카테고리별 한 줄 가이드 (한 번씩만).
        hinted: set[str] = set()
        for _, err in failures:
            if err.category == "js-runtime" and "js-runtime" not in hinted:
                print(
                    "\n  💡 yt-dlp 가 외부 JavaScript 런타임(deno 등)을 요구합니다.\n"
                    "     재생목록 / n-sig 풀이 등 일부 추출 경로에 필요합니다.\n"
                    "     Windows:  winget install DenoLand.Deno\n"
                    "     macOS:    brew install deno\n"
                    "     설치 후 새 터미널에서 다시 실행하세요.",
                    file=sys.stderr,
                )
                hinted.add("js-runtime")
            elif err.category == "cookies-locked" and "cookies-locked" not in hinted:
                print(
                    "\n  💡 브라우저가 실행 중이라 쿠키 DB 에 락이 걸려있습니다(이슈 #7271).\n"
                    "     해결 셋 중 하나:\n"
                    "       1) 해당 브라우저를 시스템 트레이까지 완전 종료 후 재시도.\n"
                    "       2) 다른 브라우저로 전환:\n"
                    "          --cookies-from-browser firefox  (또는 brave)\n"
                    "       3) 가상 Chromium 으로 한 번 로그인 → cookies.txt 영구 사용:\n"
                    "          uv sync --extra cookies-headed\n"
                    "          uv run playwright install chromium    # 1회\n"
                    "          uv run ycollector-login               # 한 번 로그인\n"
                    "          uv run ycollector --yes-playlist \"<URL>\"   # 이후 자동",
                    file=sys.stderr,
                )
                hinted.add("cookies-locked")
        return 1 if len(failures) < len(urls) else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
