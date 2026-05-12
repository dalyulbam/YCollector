"""yt-dlp subprocess wrapper with progress parsing and error classification.

Plan §4.4 (subprocess mode of the hybrid IPC) and §7.3 (error classifier).
The Python API mode (in-process metadata extraction) is added in Phase 1+.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── Error classification (plan §7.3) ────────────────────────────────────────
ERROR_PATTERNS: dict[str, str] = {
    "bot-detect":      r"Sign in to confirm you'?re not a bot",
    "private":         r"This video is private",
    "members-only":    r"members[- ]only content",
    "age-restricted":  r"is age restricted|age[- ]restricted",
    "geo-blocked":     r"not available in your country|geo[- ]restricted",
    "rate-limit":      r"HTTP Error 429",
    "stale-extractor": r"unable to extract|could not find sig function|n[- ]?sig",
    "video-removed":   r"Video unavailable|has been removed",
    "network":         r"Connection (reset|refused|aborted)|timed out",
    "disk-full":       r"No space left on device|errno 28",
}


def classify_error(stderr: str) -> str:
    """Return a category string for the error, or 'unknown'.

    Categories come from plan §7.3 — used by the future guided workflow (D3).
    """
    for category, pattern in ERROR_PATTERNS.items():
        if re.search(pattern, stderr, re.IGNORECASE):
            return category
    return "unknown"


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
        extra_args: list[str] | None = None,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> Path:
        """Download URL and return the final filepath.

        Raises ``DownloadError`` (with a classified ``category``) on failure.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        outtmpl = str(output_dir / output_template)

        cmd: list[str] = [
            str(self.ytdlp_path),
            "--newline",
            "--progress-template", _PROGRESS_TEMPLATE,
            "-f", format,
            "--merge-output-format", merge_format,
            "-o", outtmpl,
        ]
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
        )

        final_path: Path | None = None
        stderr_lines: list[str] = []

        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if line.startswith(_PROGRESS_MARKER):
                event = _parse_progress(line[len(_PROGRESS_MARKER):])
                if event is None:
                    continue
                if event.filename:
                    final_path = Path(event.filename)
                if on_progress:
                    on_progress(event)
            elif line:
                if on_log:
                    on_log(line)

        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.rstrip()
            if line:
                stderr_lines.append(line)
                if on_log:
                    on_log(line)

        proc.wait()
        if proc.returncode != 0:
            stderr = "\n".join(stderr_lines)
            last = stderr_lines[-1] if stderr_lines else f"yt-dlp exited with {proc.returncode}"
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
