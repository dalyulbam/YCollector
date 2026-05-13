"""yt-dlp subprocess wrapper with progress parsing and error classification.

Plan §4.4 (subprocess mode of the hybrid IPC) and §7.3 (error classifier).
The Python API mode (in-process metadata extraction) is added in Phase 1+.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── Playlist URL detection ──────────────────────────────────────────────────
# A "single video URL with a playlist context" — `youtu.be/<id>?list=...`
# or `youtube.com/watch?v=<id>...&list=...`. yt-dlp defaults to expanding
# the playlist for these, which surprises users who clicked one video.
_AMBIGUOUS_PLAYLIST_PATTERNS = (
    re.compile(r"youtu\.be/[A-Za-z0-9_-]+\?.*list=", re.IGNORECASE),
    re.compile(r"youtube\.com/watch\?.*v=[A-Za-z0-9_-]+.*list=", re.IGNORECASE),
    re.compile(r"youtube\.com/watch\?.*list=.*v=[A-Za-z0-9_-]+", re.IGNORECASE),
)


def is_ambiguous_playlist_url(url: str) -> bool:
    """True iff the URL is a single video + ?list= context (most users want
    just the single video). Pure playlist URLs (``/playlist?list=...``)
    are NOT considered ambiguous — those should expand normally."""
    return any(p.search(url) for p in _AMBIGUOUS_PLAYLIST_PATTERNS)


# ── Error classification (plan §7.3) ────────────────────────────────────────
ERROR_PATTERNS: dict[str, str] = {
    "bot-detect":      r"Sign in to confirm you'?re not a bot",
    "private":         r"This video is private",
    "members-only":    r"members[- ]only content",
    "age-restricted":  r"is age restricted|age[- ]restricted",
    "geo-blocked":     r"not available in your country|geo[- ]restricted",
    "rate-limit":      r"HTTP Error 429",
    # yt-dlp 의 EJS(Extractor JS) 의존성. 재생목록 등 일부 추출 경로가 deno/node 같은
    # 외부 JS 런타임을 필요로 함. 사용자 가이드: `winget install DenoLand.Deno`.
    "js-runtime":      r"No supported JavaScript runtime|n challenge solving failed",
    "stale-extractor": r"unable to extract|could not find sig function|n[- ]?sig",
    "video-removed":   r"Video unavailable|has been removed",
    "network":         r"Connection (reset|refused|aborted)|timed out",
    "disk-full":       r"No space left on device|errno 28",
    "throttled":       r"throttled|throttling|slow",
    "cancelled":       r"interrupted|cancelled|user aborted",
}


def classify_error(stderr: str) -> str:
    """Return a category string for the error, or 'unknown'.

    Categories come from plan §7.3 — used by the future guided workflow (D3).
    """
    for category, pattern in ERROR_PATTERNS.items():
        if re.search(pattern, stderr, re.IGNORECASE):
            return category
    return "unknown"


# ── JS runtime discovery ───────────────────────────────────────────────────
# 최근 yt-dlp 는 YouTube 추출(특히 재생목록)에서 외부 JS 런타임(deno) 을 요구.
# winget 으로 deno 를 설치해도 winget Links 폴더에 shim 이 생성 안 될 때가 있어
# PATH 에 안 잡힌다. yt-dlp 가 무한 재시도하며 멈춰 보이는 가장 흔한 원인.
# 여기서 알려진 설치 위치를 직접 뒤져, 찾은 디렉터리를 yt-dlp 의 env PATH 에
# prepend 해서 yt-dlp 의 자동 감지 로직이 그대로 동작하게 한다.
_DENO_FALLBACK_DIR: Path | None = None
_DENO_PROBED = False


def _find_deno_dir() -> Path | None:
    """deno.exe 를 담은 디렉터리를 찾는다. PATH 에 이미 있으면 None.

    캐싱: 한 프로세스 안에서 한 번만 디스크를 뒤진다.
    """
    global _DENO_FALLBACK_DIR, _DENO_PROBED
    if _DENO_PROBED:
        return _DENO_FALLBACK_DIR
    _DENO_PROBED = True

    if shutil.which("deno"):
        # 이미 PATH 에 있음 → 별도 prepend 불필요.
        return None

    if sys.platform != "win32":
        return None

    local = os.environ.get("LOCALAPPDATA") or ""
    home = os.environ.get("USERPROFILE") or ""
    candidates = [
        # winget 기본 설치 위치 (Links shim 누락 시 여기서만 발견됨)
        Path(local) / "Microsoft" / "WinGet" / "Packages"
            / "DenoLand.Deno_Microsoft.Winget.Source_8wekyb3d8bbwe",
        # 공식 install 스크립트 기본 위치
        Path(home) / ".deno" / "bin",
        # scoop
        Path(home) / "scoop" / "apps" / "deno" / "current",
        # chocolatey
        Path("C:/ProgramData/chocolatey/lib/deno/tools"),
    ]
    for c in candidates:
        if (c / "deno.exe").is_file():
            _DENO_FALLBACK_DIR = c
            return c
    return None


def _subprocess_env() -> dict[str, str]:
    """yt-dlp 자식 프로세스용 env. PATH 보정 + UTF-8 출력 강제.

    부모 프로세스의 ``os.environ`` 은 건드리지 않는다.

    PYTHONIOENCODING / PYTHONUTF8:
        Korean Windows 의 기본 콘솔 인코딩은 cp949. 이걸 그대로 두면
        yt-dlp 가 한글 제목/채널을 cp949 로 stdout 에 쏘는데, 우리는
        ``Popen(..., encoding="utf-8")`` 로 디코드하므로 한글이 ``�``
        replacement char 로 깨진다 ("채널: ������2" 현상).
        자식 Python 인터프리터에 UTF-8 출력을 강제해 양쪽이 같은
        인코딩으로 만난다. yt-dlp 가 PyInstaller / Nuitka 로 패킹된
        경우에도 내부 Python 이 환경변수를 존중하므로 동일하게 동작.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    deno_dir = _find_deno_dir()
    if deno_dir is not None:
        env["PATH"] = f"{deno_dir}{os.pathsep}{env.get('PATH', '')}"
    return env


# ── Progress / errors ───────────────────────────────────────────────────────
@dataclass
class ProgressEvent:
    """One frame of download progress (parsed from yt-dlp --progress-template json)."""

    status: str
    downloaded_bytes: int
    total_bytes: int | None
    speed: float | None
    eta: int | None
    filename: str | None

    @property
    def percent(self) -> float | None:
        if self.total_bytes:
            return self.downloaded_bytes / self.total_bytes * 100
        return None


@dataclass
class MetaInfo:
    """Video metadata surfaced *before* download bytes flow.

    yt-dlp emits this via our dedicated ``--print pre_process:`` template, so
    the UI can show "▶ <title>" while the slow "extract player.js → solve
    n-sig → fetch formats" phase is still going on (no progress events yet).
    """

    title: str
    channel: str
    duration: str
    video_id: str


def _parse_meta(payload: str) -> MetaInfo | None:
    parts = payload.split("\t", 3)
    while len(parts) < 4:
        parts.append("")
    cleaned = [("" if p in ("NA", "N/A") else p) for p in parts]
    title, channel, duration, vid_id = cleaned
    if not (title or channel or duration or vid_id):
        return None
    return MetaInfo(
        title=title or "(제목 불명)",
        channel=channel or "?",
        duration=duration or "?",
        video_id=vid_id or "?",
    )


class DownloadError(Exception):
    """Categorised download failure carrying classified category + raw stderr."""

    def __init__(self, *, category: str, message: str, raw_stderr: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.raw_stderr = raw_stderr

    def __str__(self) -> str:
        return f"[{self.category}] {self.message}"


# ── Engine ─────────────────────────────────────────────────────────────────
# Sentinel prefix for our own progress lines. yt-dlp's --progress-template
# allows an optional phase prefix ("download:") followed by the literal output
# template; we emit "YCPROG:<json>" so the parser can distinguish progress
# events from arbitrary log lines.
_PROGRESS_TEMPLATE = "download:YCPROG:%(progress)j"
_PROGRESS_MARKER = "YCPROG:"

# Sentinel for the *final* on-disk filepath after all post-processing
# (merge bv+ba, embed subs, move to outtmpl). progress events only report
# intermediate fragment filenames (e.g. ``.f140.mp4``) and in recent yt-dlp
# may omit ``filename`` entirely, so we ask yt-dlp directly via ``--print``.
# Emitted once per finished video — for playlists, the last line wins.
_FINAL_PATH_TEMPLATE = "after_move:YCFINAL:%(filepath)s"
_FINAL_PATH_MARKER = "YCFINAL:"

# Video metadata (title / channel / duration / id) emitted right after
# extraction succeeds, *before* download bytes start flowing. yt-dlp's own
# default output buries this in "[youtube] xxx: Downloading webpage" noise;
# we route a dedicated --print so the UI can show "▶ <Title>" prominently
# and the user knows which video is being prepared. Fires once per video.
_META_TEMPLATE = (
    "pre_process:YCMETA\t"
    "%(title)s\t%(channel,uploader)s\t%(duration_string,duration)s\t%(id)s"
)
_META_MARKER = "YCMETA\t"

# 이미 받은 파일이면 yt-dlp 가 다운로드를 건너뛰어 --print after_move 도 안 쏘기에
# 로그 라인에서 직접 경로를 캡처해 둔다. 예:
#   [download] downloads\foo\bar [id].mp4 has already been downloaded
_ALREADY_DOWNLOADED_RE = re.compile(r"^\[download\]\s+(.+?)\s+has already been downloaded$")

# yt-dlp 가 `--max-downloads N` 에 도달했을 때 사용하는 정상 종료 코드.
# 정확히 N 개를 받은 뒤 깨끗하게 멈췄다는 신호로 실패가 아니다.
_MAX_DOWNLOADS_REACHED_EXIT = 101


class YtdlpEngine:
    """Subprocess-based yt-dlp adapter (plan §4.4)."""

    def __init__(self, ytdlp_path: str | Path | None = None) -> None:
        self.ytdlp_path = Path(ytdlp_path) if ytdlp_path else self._discover()

    @staticmethod
    def _discover() -> Path:
        path = shutil.which("yt-dlp")
        if not path:
            raise FileNotFoundError(
                "yt-dlp not found in PATH. Install it (e.g. `uv add yt-dlp`)."
            )
        return Path(path)

    def version(self) -> str:
        result = subprocess.run(
            [str(self.ytdlp_path), "--version"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def list_formats(self, url: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return ``(info, formats)`` for ``url``.

        ``info`` carries top-level metadata (title, channel, duration, ...);
        ``formats`` is the per-format list yt-dlp surfaces under ``-F``.
        Raises :class:`DownloadError` on failure with classified category.
        """
        result = subprocess.run(
            [
                str(self.ytdlp_path),
                "--dump-single-json",
                "--no-playlist",
                "--no-warnings",
                "--no-progress",
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            env=_subprocess_env(),
        )
        if result.returncode != 0:
            stderr = result.stderr or ""
            last = stderr.strip().splitlines()[-1] if stderr.strip() else "yt-dlp failed"
            raise DownloadError(
                category=classify_error(stderr),
                message=last,
                raw_stderr=stderr,
            )
        try:
            info: dict[str, Any] = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DownloadError(
                category="unknown",
                message=f"Failed to parse yt-dlp JSON output: {exc}",
                raw_stderr=result.stderr or "",
            ) from exc
        return info, list(info.get("formats", []))

    def download(
        self,
        url: str,
        *,
        format: str = "bv*[height<=1080]+ba/b[height<=1080]",
        output_dir: Path = Path("downloads"),
        output_template: str = "%(uploader)s/%(title)s [%(id)s].%(ext)s",
        merge_format: str = "mp4",
        write_subs: bool = True,
        sub_langs: Iterable[str] = ("ko", "en"),
        cookies_from_browser: str | None = None,
        socket_timeout: int | None = 30,
        retries: int = 10,
        fragment_retries: int = 10,
        throttled_rate: str | None = None,
        no_playlist: bool = False,
        yes_playlist: bool = False,
        max_downloads: int | None = None,
        playlist_items: str | None = None,
        extra_args: list[str] | None = None,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        on_process: Callable[[subprocess.Popen[str]], None] | None = None,
        on_meta: Callable[[MetaInfo], None] | None = None,
    ) -> Path:
        """Download URL and return the final filepath.

        Raises :class:`DownloadError` (with a classified ``category``) on
        failure. Re-running the same command will automatically resume from
        the partial ``.part`` file (yt-dlp's default behaviour).

        Stall mitigation:
          - ``socket_timeout`` (sec): abort socket if no data arrives within N
            seconds and retry. Default 30 — yt-dlp's own default is ~600s.
          - ``retries`` / ``fragment_retries``: how many times to retry a
            failed connection / DASH-HLS fragment.
          - ``throttled_rate`` (e.g. ``"100K"``): if download rate falls below
            this for too long, restart the connection. Useful against
            YouTube throttling. Disabled by default.

        Cancellation:
          - ``on_process(proc)`` callback receives the live ``Popen`` so that
            another thread (e.g. the Qt worker) can call ``proc.terminate()``
            to cancel cooperatively. The terminated subprocess raises a
            ``DownloadError(category="cancelled", ...)`` here.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(output_dir / output_template)

        cmd: list[str] = [
            str(self.ytdlp_path),
            "--newline",
            "--progress-template", _PROGRESS_TEMPLATE,
            # --print 가 yt-dlp 의 --quiet 를 묵시적으로 켜서 진행률 출력이 사라지므로
            # --no-quiet 로 명시적 해제 — 진행률 + 최종 경로 둘 다 받기 위해 필수.
            "--no-quiet",
            "--print", _META_TEMPLATE,
            "--print", _FINAL_PATH_TEMPLATE,
            # YouTube n-sig 풀이용 EJS(Extractor JS) 챌린지 솔버 자동 수신.
            # deno/node 런타임만으론 부족하고 yt-dlp 가 권장하는 ejs:github 가
            # 있어야 재생목록·일부 화질 추출이 동작. 최초 1회만 GitHub 에서
            # 받아 캐시 — 이후엔 오프라인에서도 동작.
            "--remote-components", "ejs:github",
            # 재생목록 내 일부 영상(삭제·비공개·연령제한·지역차단) 에서 에러가 나도
            # 전체를 중단하지 않고 다음 항목으로. 단일 영상에선 영향 없음.
            "--ignore-errors",
            "-f", format,
            "--merge-output-format", merge_format,
            "-o", outtmpl,
            "--retries", str(retries),
            "--fragment-retries", str(fragment_retries),
        ]
        if socket_timeout:
            cmd += ["--socket-timeout", str(socket_timeout)]
        if throttled_rate:
            cmd += ["--throttled-rate", throttled_rate]
        # Playlist handling. `no_playlist` wins if both are set.
        if no_playlist:
            cmd.append("--no-playlist")
        elif yes_playlist:
            cmd.append("--yes-playlist")
        if max_downloads is not None:
            cmd += ["--max-downloads", str(max_downloads)]
        if playlist_items:
            cmd += ["--playlist-items", playlist_items]
        if write_subs and sub_langs:
            cmd += [
                "--write-subs",
                "--sub-langs", ",".join(sub_langs),
                "--embed-subs",
            ]
        if cookies_from_browser:
            cmd += ["--cookies-from-browser", cookies_from_browser]
        if extra_args:
            cmd += extra_args
        cmd.append(url)

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=_subprocess_env(),
        )
        if on_process:
            on_process(proc)

        final_path: Path | None = None
        stderr_lines: list[str] = []

        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip()
                if line.startswith(_FINAL_PATH_MARKER):
                    # Authoritative final path after merge + post-process + move.
                    # For playlists this fires per video; the last one wins.
                    payload = line[len(_FINAL_PATH_MARKER):].strip()
                    if payload:
                        final_path = Path(payload)
                elif line.startswith(_META_MARKER):
                    meta = _parse_meta(line[len(_META_MARKER):])
                    if meta is not None and on_meta:
                        on_meta(meta)
                elif line.startswith(_PROGRESS_MARKER):
                    event = _parse_progress(line[len(_PROGRESS_MARKER):])
                    if event is None:
                        continue
                    # Progress filename points at intermediate fragments
                    # (e.g. .f140.mp4) — keep only as a fallback if --print
                    # never fires (e.g. audio-only with no post-processing).
                    if event.filename and final_path is None:
                        final_path = Path(event.filename)
                    if on_progress:
                        on_progress(event)
                elif line:
                    if on_log:
                        on_log(line)
                    # 이미 받은 파일 — yt-dlp 는 다운로드/머지/move 를 건너뛰므로
                    # progress 도 --print 도 발사 안 됨. 로그 라인에서 직접 캡처.
                    m = _ALREADY_DOWNLOADED_RE.match(line)
                    if m:
                        final_path = Path(m.group(1))

            assert proc.stderr is not None
            for raw in proc.stderr:
                line = raw.rstrip()
                if line:
                    stderr_lines.append(line)
                    if on_log:
                        on_log(line)
        finally:
            # Make sure subprocess never outlives this function.
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()

        # exit 101 = --max-downloads 도달. yt-dlp 가 의도적으로 일찍 멈춘
        # 신호이므로 success 와 동등 취급 (final_path 는 이미 마지막 영상에서 set).
        if proc.returncode not in (0, _MAX_DOWNLOADS_REACHED_EXIT):
            stderr = "\n".join(stderr_lines)
            # `terminate()` / Ctrl+C usually surfaces as a negative return
            # code on POSIX or a non-zero on Windows.  Treat any non-success
            # while we have no real stderr as cancellation.
            if not stderr_lines:
                raise DownloadError(
                    category="cancelled",
                    message=f"yt-dlp interrupted (exit {proc.returncode})",
                    raw_stderr="",
                )
            # 재생목록에서 일부 영상이 실패했지만 (--ignore-errors 로 진행) 적어도
            # 한 개는 받았다면 partial success — 마지막 final_path 반환. 단일 영상
            # 실패 (final_path is None) 만 진짜 DownloadError 로 올린다.
            if final_path is None:
                last = stderr_lines[-1]
                raise DownloadError(
                    category=classify_error(stderr),
                    message=last,
                    raw_stderr=stderr,
                )

        if final_path is None:
            raise DownloadError(
                category="unknown",
                message="Download completed but no filename reported",
                raw_stderr="\n".join(stderr_lines),
            )
        return final_path


# ── helpers ────────────────────────────────────────────────────────────────
def _parse_progress(payload: str) -> ProgressEvent | None:
    try:
        data: dict[str, Any] = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return ProgressEvent(
        status=str(data.get("status", "unknown")),
        downloaded_bytes=_int_or_zero(data.get("downloaded_bytes")),
        total_bytes=_int_or_none(data.get("total_bytes") or data.get("total_bytes_estimate")),
        speed=_float_or_none(data.get("speed")),
        eta=_int_or_none(data.get("eta")),
        filename=_str_or_none(data.get("filename") or data.get("info_dict", {}).get("filename")),
    )


def _int_or_none(v: Any) -> int | None:
    if v in (None, "NA", "N/A"):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _int_or_zero(v: Any) -> int:
    return _int_or_none(v) or 0


def _float_or_none(v: Any) -> float | None:
    if v in (None, "NA", "N/A"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _str_or_none(v: Any) -> str | None:
    if v in (None, "", "NA", "N/A"):
        return None
    return str(v)
