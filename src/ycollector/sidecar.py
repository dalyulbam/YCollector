"""NDJSON sidecar mode — Tauri 프런트가 spawn하는 모드.

`ycollector --json` 으로 실행하면, stdin에서 NDJSON 명령을 받고 stdout으로
NDJSON 이벤트를 흘려보낸다. stderr는 사람 가독 로그 그대로.

프로토콜 (한 줄 = 한 메시지):

  Rust → Python (stdin):
    {"op":"probe","job_id":"j_…","url":"…"}
    {"op":"download","job_id":"j_…","url":"…","settings":{…}}
    {"op":"cancel","job_id":"j_…"}
    {"op":"shutdown"}

  Python → Rust (stdout):
    {"event":"ready","yt_dlp":"2026.02.0","version":"0.1.0"}
    {"event":"meta","job_id":"j_…","title":"…","channel":"…","duration":"…",
     "video_id":"…","thumbnail":"…"}
    {"event":"progress","job_id":"j_…","phase":"download","percent":42.1,
     "downloaded":…,"total":…,"speed":…,"eta":…}
    {"event":"progress","job_id":"j_…","phase":"postprocess"}
    {"event":"done","job_id":"j_…","filepath":"D:\\…\\video.mp4"}
    {"event":"error","job_id":"j_…","category":"…","message":"…"}
    {"event":"log","job_id":"j_…","level":"info","line":"…"}

한 sidecar 프로세스가 작업을 **직렬**로 처리한다 (현재 CLI/GUI 거동과 동일).
취소는 활성 yt-dlp Popen.terminate(). 동시 다중 작업은 Phase 1 큐에서.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from queue import Queue
from typing import Any

from ycollector import __version__
from ycollector.config import load_settings
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


# ── stdout 직렬화 락 (NDJSON 라인이 절대 깨지지 않도록) ───────────────────
_STDOUT_LOCK = threading.Lock()


def _emit(event: dict[str, Any]) -> None:
    """단일 이벤트를 NDJSON 한 줄로 stdout에 쓴다.

    ensure_ascii=False → 한글이 \\uXXXX로 escape 되지 않음 (Rust 측에서 그대로 UTF-8 디코드).
    """
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    with _STDOUT_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _log(level: str, line: str, job_id: str | None = None) -> None:
    ev: dict[str, Any] = {"event": "log", "level": level, "line": line}
    if job_id:
        ev["job_id"] = job_id
    _emit(ev)


# ── 활성 작업 레지스트리 (취소용) ────────────────────────────────────────
class _JobRegistry:
    """job_id → live Popen 매핑. cancel op가 해당 Popen.terminate()."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._procs: dict[str, subprocess.Popen[str]] = {}
        self._cancelled: set[str] = set()

    def attach(self, job_id: str, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._procs[job_id] = proc
            # 이미 cancel 이 먼저 도착했다면 즉시 terminate.
            if job_id in self._cancelled:
                proc.terminate()

    def detach(self, job_id: str) -> None:
        with self._lock:
            self._procs.pop(job_id, None)
            self._cancelled.discard(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            self._cancelled.add(job_id)
            proc = self._procs.get(job_id)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    return False
                return True
            return False


# ── 워커: 한 번에 하나씩 op 처리 ──────────────────────────────────────────
class _Worker(threading.Thread):
    def __init__(self, engine: YtdlpEngine, registry: _JobRegistry) -> None:
        super().__init__(daemon=True)
        self.engine = engine
        self.registry = registry
        self.q: Queue[dict[str, Any] | None] = Queue()
        self._stop = threading.Event()

    def submit(self, op: dict[str, Any]) -> None:
        self.q.put(op)

    def shutdown(self) -> None:
        self._stop.set()
        self.q.put(None)

    def run(self) -> None:  # noqa: D401
        while not self._stop.is_set():
            op = self.q.get()
            if op is None:
                return
            try:
                kind = op.get("op")
                if kind == "probe":
                    self._handle_probe(op)
                elif kind == "download":
                    self._handle_download(op)
                # cancel은 메인 스레드가 즉시 처리(워커 큐를 거치지 않음).
            except Exception as exc:  # 워커 자체가 죽지 않도록 최후의 방어선.
                _emit({
                    "event": "error",
                    "job_id": op.get("job_id"),
                    "category": "internal",
                    "message": f"{type(exc).__name__}: {exc}",
                })

    # ── op handlers ──────────────────────────────────────────────────────
    def _handle_probe(self, op: dict[str, Any]) -> None:
        job_id = op.get("job_id") or ""
        url = op.get("url") or ""
        try:
            info, _formats = self.engine.list_formats(url)
        except DownloadError as exc:
            _emit({
                "event": "error",
                "job_id": job_id,
                "category": exc.category,
                "message": exc.message,
            })
            return
        _emit({
            "event": "meta",
            "job_id": job_id,
            "title": info.get("title") or "",
            "channel": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration_string") or _fmt_duration(info.get("duration")),
            "video_id": info.get("id") or "",
            "thumbnail": info.get("thumbnail") or "",
        })

    def _handle_download(self, op: dict[str, Any]) -> None:
        job_id = op.get("job_id") or ""
        url = op.get("url") or ""
        s = op.get("settings") or {}

        # settings dict → engine 인자.
        try:
            quality = Quality(s.get("quality", "1080p"))
            codec = CodecPref(s.get("codec", "auto"))
            audio = AudioPref(s.get("audio", "best"))
            container = Container(s.get("container", "mp4"))
            format_spec = s.get("format") or compose_format_spec(
                FormatChoice(quality=quality, container=container, codec=codec, audio=audio)
            )
        except Exception as exc:
            _emit({
                "event": "error", "job_id": job_id,
                "category": "bad-settings",
                "message": f"settings 파싱 실패: {exc}",
            })
            return

        output_dir = Path(s.get("output_dir") or "downloads")
        sub_langs_raw = s.get("sub_langs") or "ko,en"
        if isinstance(sub_langs_raw, list):
            sub_langs = [x for x in sub_langs_raw if x]
        else:
            sub_langs = [x.strip() for x in str(sub_langs_raw).split(",") if x.strip()]
        write_subs = bool(s.get("embed_subs", True))
        cookies = (s.get("cookies_from_browser") or "").strip() or None
        # cookies_file (Tauri UI 가 명시) 또는 자동 탐지.
        from ycollector.cookies import default_cookies_path, is_cookies_present
        cookies_file_raw = (s.get("cookies_file") or "").strip()
        cookies_file: Path | None = Path(cookies_file_raw) if cookies_file_raw else None
        if cookies_file is None and is_cookies_present():
            cookies_file = default_cookies_path()
        if cookies_file is not None and not cookies_file.is_file():
            cookies_file = None

        playlist_mode = (s.get("playlist_mode") or "auto").strip().lower()
        if playlist_mode == "single":
            no_pl, yes_pl = True, False
        elif playlist_mode == "expand":
            no_pl, yes_pl = False, True
        else:  # auto
            if is_ambiguous_playlist_url(url):
                no_pl, yes_pl = True, False
            else:
                no_pl, yes_pl = False, False

        # Optional ints
        def _opt_int(k: str) -> int | None:
            v = s.get(k)
            if v in (None, "", "null"):
                return None
            try:
                return int(v)
            except (TypeError, ValueError):
                return None

        max_downloads = _opt_int("max_downloads")
        items = (s.get("playlist_items") or "").strip() or None
        socket_timeout = _opt_int("socket_timeout") or 30
        retries = _opt_int("retries") or 10
        fragment_retries = _opt_int("fragment_retries") or 10
        throttled_rate = (s.get("throttled_rate") or "").strip() or None

        # 진행률·메타·로그 콜백 → NDJSON 이벤트.
        def on_progress(ev: ProgressEvent) -> None:
            phase = "download" if ev.status == "downloading" else (
                "postprocess" if ev.status == "finished" else ev.status
            )
            _emit({
                "event": "progress",
                "job_id": job_id,
                "phase": phase,
                "percent": ev.percent,
                "downloaded": ev.downloaded_bytes,
                "total": ev.total_bytes,
                "speed": ev.speed,
                "eta": ev.eta,
                "filename": ev.filename,
            })

        def on_meta(meta: MetaInfo) -> None:
            _emit({
                "event": "meta",
                "job_id": job_id,
                "title": meta.title,
                "channel": meta.channel,
                "duration": meta.duration,
                "video_id": meta.video_id,
                "thumbnail": "",  # download 경로엔 yt-dlp가 thumbnail 안 쏨 (probe에서 받음).
            })

        def on_log(line: str) -> None:
            # 로그는 자주 발생 → debug 레벨로. UI 측에서 노이즈 필터링.
            _log("info", line, job_id=job_id)

        def on_process(proc: subprocess.Popen[str]) -> None:
            self.registry.attach(job_id, proc)

        try:
            path = self.engine.download(
                url,
                format=format_spec,
                output_dir=output_dir,
                merge_format=container.value,
                write_subs=write_subs,
                sub_langs=sub_langs,
                cookies_from_browser=cookies,
                cookies_file=cookies_file,
                socket_timeout=socket_timeout,
                retries=retries,
                fragment_retries=fragment_retries,
                throttled_rate=throttled_rate,
                no_playlist=no_pl,
                yes_playlist=yes_pl,
                max_downloads=max_downloads,
                playlist_items=items,
                on_progress=on_progress,
                on_log=on_log,
                on_process=on_process,
                on_meta=on_meta,
            )
        except DownloadError as exc:
            _emit({
                "event": "error",
                "job_id": job_id,
                "category": exc.category,
                "message": exc.message,
            })
            return
        finally:
            self.registry.detach(job_id)

        _emit({
            "event": "done",
            "job_id": job_id,
            "filepath": str(path),
        })


# ── main loop ──────────────────────────────────────────────────────────────
def run() -> int:
    """`ycollector --json` 진입점.

    Windows에선 cli.main()에서 이미 stdout/stderr를 UTF-8로 reconfigure 한 뒤 호출된다.
    """
    settings, _settings_path = load_settings(None)
    try:
        engine = YtdlpEngine()
    except FileNotFoundError as exc:
        _emit({
            "event": "fatal",
            "category": "no-ytdlp",
            "message": str(exc),
        })
        return 10

    # 시작 인사 — Rust 측이 sidecar 가동을 확인하는 데 사용.
    deno_dir = _find_deno_dir()
    _emit({
        "event": "ready",
        "yt_dlp": engine.version(),
        "version": __version__,
        "deno_dir": str(deno_dir) if deno_dir else None,
        "defaults": {
            "quality": settings.quality,
            "codec": settings.codec,
            "audio": settings.audio,
            "container": settings.container,
            "output_dir": settings.output_dir,
            "embed_subs": settings.embed_subs,
            "sub_langs": ",".join(settings.sub_langs),
            "cookies_from_browser": settings.cookies_from_browser or "",
            "playlist_mode": settings.playlist_mode,
            "max_downloads": settings.max_downloads or "",
            "playlist_items": settings.playlist_items or "",
            "socket_timeout": settings.socket_timeout,
            "retries": settings.retries,
            "fragment_retries": settings.fragment_retries,
            "throttled_rate": settings.throttled_rate or "",
        },
    })

    registry = _JobRegistry()
    worker = _Worker(engine, registry)
    worker.start()

    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                op = json.loads(line)
            except json.JSONDecodeError as exc:
                _log("error", f"bad NDJSON: {exc} (line: {line[:80]!r})")
                continue
            kind = op.get("op")
            if kind == "shutdown":
                break
            if kind == "cancel":
                cancelled = registry.cancel(op.get("job_id") or "")
                _emit({
                    "event": "cancel-ack",
                    "job_id": op.get("job_id"),
                    "cancelled": cancelled,
                })
                continue
            if kind in ("probe", "download"):
                worker.submit(op)
                continue
            _log("warn", f"unknown op: {kind!r}")
    except KeyboardInterrupt:
        pass
    finally:
        worker.shutdown()
        worker.join(timeout=1.0)

    _emit({"event": "bye"})
    return 0


def _fmt_duration(seconds: Any) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    if s < 0:
        return ""
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


# silence "imported but unused" for asdict (kept for future use).
_ = asdict
