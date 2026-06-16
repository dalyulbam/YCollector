"""`ycollector-server` — 로컬 브라우저 UI.

```
uv sync --extra web --extra video-gen
uv run ycollector-server
# → http://127.0.0.1:8765/
```

엔드포인트:
    GET  /                       → webui/index.html
    GET  /webui/{path}           → webui/ 정적 (app.js, styles.css)
    GET  /api/health             → 키 보유 여부 등
    GET  /api/settings           → 현재 설정 (JSON)
    POST /api/settings           → 설정 저장
    POST /api/download           → {"url", "settings"?: {...}} → job_id
    POST /api/generate           → {"prompt", "size", "seconds", "model", "references": [...]} → job_id (또는 [job_id, ...])
    GET  /api/jobs/{job_id}      → 현재 상태
    POST /api/jobs/{job_id}/cancel → 취소
    GET  /api/jobs/{job_id}/events → SSE 진행률 스트림
    GET  /api/jobs               → 전체 작업 목록
    GET  /api/library            → manifest 전체
    GET  /api/analysis/overview  → 다운로드 루트 스캔(미디어 + _analysis/_album 산출물)
    POST /api/analyze            → 파일 모드 {"root","files":[...]} 또는
                                    URL 모드 {"url","playlist_all"?} (+language/summarize/budget) → job_id
    POST /api/album              → {"root", "dir", "frames"?} → job_id (장면 앨범북 생성)
    GET  /files/{root}/{path}    → 다운로드 루트 안의 파일 서빙(요약 md · 앨범 html · 영상)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# `from __future__ import annotations` 로 라우트 어노테이션이 문자열이라,
# FastAPI(get_type_hints)가 모듈 전역에서 'UploadFile' 을 해석할 수 있어야 한다.
# fastapi 부재 시엔 _build_app() 가 더 친절한 설치 안내를 먼저 낸다.
try:
    from fastapi import UploadFile
except ImportError:
    UploadFile = object  # type: ignore[assignment,misc]

# ── 지연 import 들 ────────────────────────────────────────────────────────
# 메인 모듈 로드 시 OpenAI/Sora 의존성을 강제하지 않도록 함수 내부에서 import.


# ── job registry ──────────────────────────────────────────────────────────
class _JobRow:
    __slots__ = ("id", "kind", "url_or_prompt", "status", "progress", "message",
                 "out_path", "error", "thumb", "title", "channel", "duration", "cost_usd",
                 "created_at", "events")

    def __init__(self, id_: str, kind: str, url_or_prompt: str) -> None:
        self.id = id_
        self.kind = kind  # "download" | "generate" | "analyze" | "album"
        self.url_or_prompt = url_or_prompt
        self.status: str = "queued"
        self.progress: float = 0.0
        self.message: str = ""
        self.out_path: str | None = None
        self.error: dict[str, str] | None = None
        self.thumb: str = ""
        self.title: str = ""
        self.channel: str = ""
        self.duration: str = ""
        self.cost_usd: float = 0.0
        self.created_at = time.time()
        # asyncio queues 로 listener 에 SSE 푸시.
        self.events: list[asyncio.Queue[dict[str, Any]]] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "input": self.url_or_prompt,
            "status": self.status, "progress": self.progress, "message": self.message,
            "out_path": self.out_path, "error": self.error,
            "thumb": self.thumb, "title": self.title, "channel": self.channel,
            "duration": self.duration, "cost_usd": self.cost_usd,
            "created_at": self.created_at,
        }

    async def emit(self, event: str, **payload: Any) -> None:
        # JSON-serializable 한 게 payload 라고 가정.
        msg = {"event": event, **payload}
        for q in list(self.events):
            try:
                q.put_nowait(msg)
            except Exception:
                pass

    def emit_sync(self, event: str, **payload: Any) -> None:
        """워커 스레드(블로킹 yt-dlp/sora poll) 에서 호출. 메인 loop 에 안전하게 푸시."""
        loop = _state.loop
        if loop is None:
            return
        msg = {"event": event, **payload}
        for q in list(self.events):
            loop.call_soon_threadsafe(q.put_nowait, msg)


class _ServerState:
    """프로세스 전역 상태."""

    def __init__(self) -> None:
        self.jobs: dict[str, _JobRow] = {}
        self.loop: asyncio.AbstractEventLoop | None = None


_state = _ServerState()

# 메모리 무한 증가 방지 — 초과 시 가장 오래된 '종료' job 부터 비운다(영속 큐는
# queue.json 에 따로 남으므로 in-memory job 카드만 정리되는 것).
_MAX_JOBS = 300


def _remember_job(jid: str, row: "_JobRow") -> None:
    jobs = _state.jobs
    jobs[jid] = row
    if len(jobs) <= _MAX_JOBS:
        return
    terminal = {"done", "failed", "cancelled"}
    removable = sorted((r for r in jobs.values() if r.status in terminal),
                       key=lambda r: r.created_at)
    for r in removable[: len(jobs) - _MAX_JOBS]:
        jobs.pop(r.id, None)


# ── library manifest ──────────────────────────────────────────────────────
def _library_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home())
        return base / "YCollector" / "library.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "YCollector" / "library.json"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "ycollector" / "library.json"


def _library_append(item: dict[str, Any]) -> None:
    p = _library_path()
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


def _library_read() -> dict[str, Any]:
    p = _library_path()
    if not p.is_file():
        return {"version": 1, "items": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "items": []}


# ── download 워커 (yt-dlp via existing engine) ──────────────────────────
def _run_download(job: _JobRow, url: str, settings_override: dict[str, Any] | None = None) -> None:
    from ycollector.config import load_settings, settings_from_dict, user_config_dir
    from ycollector.cookies import default_cookies_path, is_cookies_present
    from ycollector.engine import (
        AudioPref, CodecPref, Container, FormatChoice, Quality,
        YtdlpEngine, compose_format_spec, DownloadError,
    )
    from ycollector.engine.ytdlp import is_ambiguous_playlist_url

    base, _ = load_settings(None)
    if settings_override:
        from ycollector.config import settings_to_dict
        merged = {**settings_to_dict(base), **{k: v for k, v in settings_override.items() if v is not None}}
        s = settings_from_dict(merged)
    else:
        s = base

    try:
        engine = YtdlpEngine()
    except FileNotFoundError as exc:
        job.status = "failed"
        job.error = {"category": "not-installed", "message": f"yt-dlp 미설치: {exc}"}
        job.emit_sync("error", category="not-installed",
                      message=f"yt-dlp 를 찾을 수 없습니다 — 설치 후 다시 시도하세요. ({exc})")
        return
    fmt = compose_format_spec(FormatChoice(
        quality=Quality(s.quality), container=Container(s.container),
        codec=CodecPref(s.codec), audio=AudioPref(s.audio),
    ))
    cookies_file = None
    if s.cookies_file:
        cookies_file = Path(s.cookies_file)
    elif is_cookies_present():
        cookies_file = default_cookies_path()
    if cookies_file is not None and not cookies_file.is_file():
        cookies_file = None

    if s.playlist_mode == "single":
        no_pl, yes_pl = True, False
    elif s.playlist_mode == "expand":
        no_pl, yes_pl = False, True
    elif is_ambiguous_playlist_url(url):
        no_pl, yes_pl = True, False
    else:
        no_pl, yes_pl = False, False

    def on_progress(ev: Any) -> None:
        job.progress = (ev.percent or 0.0) / 100.0
        job.status = "downloading" if ev.status == "downloading" else (
            "postprocess" if ev.status == "finished" else job.status
        )
        job.emit_sync("progress", progress=job.progress, status=job.status,
                      speed=ev.speed, eta=ev.eta)

    def on_meta(meta: Any) -> None:
        job.title = meta.title
        job.channel = meta.channel
        job.duration = meta.duration
        job.emit_sync("meta", title=meta.title, channel=meta.channel, duration=meta.duration)

    job.status = "preparing"
    job.emit_sync("status", status="preparing")
    try:
        archive_path = user_config_dir() / "archive.txt"
        path = engine.download(
            url, format=fmt, output_dir=Path(s.output_dir),
            merge_format=s.container, write_subs=s.embed_subs,
            sub_langs=s.sub_langs,
            cookies_from_browser=s.cookies_from_browser,
            cookies_file=cookies_file,
            socket_timeout=s.socket_timeout, retries=s.retries,
            fragment_retries=s.fragment_retries, throttled_rate=s.throttled_rate,
            no_playlist=no_pl, yes_playlist=yes_pl,
            max_downloads=s.max_downloads, playlist_items=s.playlist_items,
            download_archive=archive_path,
            on_progress=on_progress, on_meta=on_meta,
        )
    except DownloadError as exc:
        job.status = "failed"
        job.error = {"category": exc.category, "message": exc.message}
        job.emit_sync("error", category=exc.category, message=exc.message)
        return
    except Exception as exc:
        job.status = "failed"
        job.error = {"category": "internal", "message": str(exc)}
        job.emit_sync("error", category="internal", message=str(exc))
        return

    job.status = "done"
    job.progress = 1.0
    job.out_path = str(path)
    job.emit_sync("done", out_path=str(path))
    _library_append({
        "job_id": job.id, "url": url, "title": job.title or url,
        "channel": job.channel, "duration": job.duration,
        "video_id": "", "thumbnail": "",
        "filepath": str(path), "finished_at": int(time.time() * 1000),
        "source": "downloaded",
    })


# ── generate 워커 (Sora) ────────────────────────────────────────────────
def _run_generate(job: _JobRow, prompt: str, references: list[str],
                  model: str, size: str, seconds: int, seed: int | None) -> None:
    from ycollector.generator import (
        BudgetExceeded, ProviderError, VideoRequest, estimate_cost_usd, get_provider,
    )

    provider = get_provider("sora")
    out_dir = Path("generated")
    out_dir.mkdir(parents=True, exist_ok=True)
    ref = references[0] if references else None
    req = VideoRequest(
        prompt=prompt, size=size, seconds=seconds, model=model, seed=seed,
        references=[ref] if ref else [], output_dir=out_dir,
    )
    cost = estimate_cost_usd(req)
    job.cost_usd = cost
    job.emit_sync("estimate", cost_usd=cost)

    job.status = "creating"
    job.emit_sync("status", status="creating")
    try:
        v_job = provider.create(req)
    except (ProviderError, BudgetExceeded) as exc:
        job.status = "failed"
        job.error = {"category": exc.category, "message": str(exc)}
        job.emit_sync("error", category=exc.category, message=str(exc))
        return

    try:
        for st in provider.poll_until_done(v_job, interval_sec=4.0, timeout_sec=1800.0):
            job.status = st.status if st.status != "in_progress" else "generating"
            job.progress = st.progress
            job.emit_sync("progress", progress=st.progress, status=job.status,
                          message=st.message)
            if st.status in ("failed", "cancelled"):
                job.error = {"category": st.error_category or "unknown",
                             "message": st.message}
                job.emit_sync("error", category=job.error["category"],
                              message=job.error["message"])
                return
    except ProviderError as exc:
        job.status = "failed"
        job.error = {"category": exc.category, "message": str(exc)}
        job.emit_sync("error", category=exc.category, message=str(exc))
        return

    out_path = out_dir / f"{v_job.id}.mp4"
    try:
        provider.download(v_job, out_path)
    except ProviderError as exc:
        job.status = "failed"
        job.error = {"category": exc.category, "message": str(exc)}
        job.emit_sync("error", category=exc.category, message=str(exc))
        return

    job.status = "done"
    job.progress = 1.0
    job.out_path = str(out_path)
    job.emit_sync("done", out_path=str(out_path), cost_usd=cost)
    _library_append({
        "job_id": v_job.id, "url": ref or "",
        "title": (prompt[:80] + "…") if len(prompt) > 80 else prompt,
        "channel": f"(generated · {model})", "duration": f"0:{seconds:02d}",
        "video_id": v_job.id, "thumbnail": "",
        "filepath": str(out_path), "finished_at": int(time.time() * 1000),
        "source": "generated", "cost_usd": cost,
    })


# ── 연속형(A) 워커: last-frame chaining + concat ────────────────────────────
# extend API 는 현재 SDK/서버 간 파라미터 불일치(HTTP 400)로 사용 불가하여,
# 검증된 create 만으로 연속성을 만든다: 각 세그먼트는 직전 세그먼트의 '마지막
# 프레임'을 input_reference 로 받아 그 지점에서 이어 시작 → concat.
def _run_generate_continuous(job: _JobRow, prompts: list[str], references: list[str],
                             model: str, size: str, seconds: int, seed: int | None) -> None:
    from ycollector.generator import (
        ProviderError, VideoRequest, estimate_cost_usd, get_provider, media,
    )

    provider = get_provider("sora")
    out_dir = Path("generated")
    work = out_dir / f"_chain_{job.id}"
    work.mkdir(parents=True, exist_ok=True)

    combined = " ".join(prompts)
    # 세그먼트: 프롬프트 2개 이상이면 프롬프트별 장면, 아니면 references 수만큼 공통 프롬프트.
    seg_prompts = prompts if len(prompts) >= 2 else [combined] * max(1, len(references))
    n = len(seg_prompts)
    anchor = references[0] if references else None

    per = estimate_cost_usd(VideoRequest(prompt=combined, size=size, seconds=seconds, model=model))
    total_cost = per * n
    job.cost_usd = total_cost
    job.emit_sync("estimate", cost_usd=total_cost)

    clips: list[Path] = []
    prev_lastframe: Path | None = None
    for i, sp in enumerate(seg_prompts):
        shot = i + 1
        job.status = "generating"
        job.emit_sync("status", status="generating", message=f"segment {shot}/{n} 생성 시작")
        # 1번 세그먼트는 사용자 reference, 이후는 직전 클립의 마지막 프레임을 앵커로(연속성).
        seg_ref = anchor if i == 0 else (str(prev_lastframe) if prev_lastframe else None)
        req = VideoRequest(
            prompt=sp, size=size, seconds=seconds, model=model, seed=seed,
            references=[seg_ref] if seg_ref else [], output_dir=work,
        )
        try:
            v_job = provider.create(req)
        except ProviderError as exc:
            job.status = "failed"
            job.error = {"category": exc.category, "message": str(exc)}
            job.emit_sync("error", category=exc.category, message=f"segment {shot}/{n}: {exc}")
            return

        try:
            for st in provider.poll_until_done(v_job, interval_sec=4.0, timeout_sec=1800.0):
                job.status = "generating"
                job.progress = (i + st.progress) / n * 0.92
                job.emit_sync("progress", progress=job.progress, status="generating",
                              message=f"segment {shot}/{n} · {int(st.progress * 100)}%")
                if st.status in ("failed", "cancelled"):
                    job.status = "failed"
                    job.error = {"category": st.error_category or "unknown", "message": st.message}
                    job.emit_sync("error", category=job.error["category"],
                                  message=f"segment {shot}/{n}: {st.message}")
                    return
        except ProviderError as exc:
            job.status = "failed"
            job.error = {"category": exc.category, "message": str(exc)}
            job.emit_sync("error", category=exc.category, message=f"segment {shot}/{n}: {exc}")
            return

        clip = work / f"seg{shot:02d}.mp4"
        try:
            provider.download(v_job, clip)
        except ProviderError as exc:
            job.status = "failed"
            job.error = {"category": exc.category, "message": str(exc)}
            job.emit_sync("error", category=exc.category, message=f"segment {shot}/{n} 다운로드: {exc}")
            return
        clips.append(clip)
        if shot < n:  # 다음 세그먼트 앵커용 마지막 프레임
            try:
                prev_lastframe = media.extract_last_frame(clip, work / f"lf{shot:02d}.jpg")
            except media.MediaError:
                prev_lastframe = None  # 추출 실패 시 다음 세그먼트는 텍스트만으로

    job.status = "stitching"
    job.emit_sync("status", status="stitching", message=f"{n}개 세그먼트 이어붙이는 중")
    final = out_dir / f"chain_{job.id}.mp4"
    try:
        media.concat_videos(clips, final)
    except media.MediaError as exc:
        job.status = "failed"
        job.error = {"category": "ffmpeg", "message": str(exc)}
        job.emit_sync("error", category="ffmpeg", message=str(exc))
        return

    tot = seconds * n
    job.status = "done"
    job.progress = 1.0
    job.out_path = str(final)
    job.emit_sync("done", out_path=str(final), cost_usd=total_cost)
    _library_append({
        "job_id": job.id, "url": "",
        "title": (combined[:80] + "…") if len(combined) > 80 else combined,
        "channel": f"(generated · {model} · 연속 ×{n})",
        "duration": f"{tot // 60}:{tot % 60:02d}",
        "video_id": job.id, "thumbnail": "",
        "filepath": str(final), "finished_at": int(time.time() * 1000),
        "source": "generated", "cost_usd": total_cost,
    })


# ── 분석(전사·요약·앨범) — transcribe 파이프라인의 웹 연동 ──────────────────
# CLI(ycollector-analyze / ycollector-album)와 같은 모듈을 재사용한다.
# 산출물 규약(analyze CLI 와 동일): 영상 폴더 바로 아래
#   _analysis/<stem>.srt / .script.md / .summary.md
#   _album/index.html + bookNN/
_ANALYSIS_DIRNAME = "_analysis"
_ALBUM_DIRNAME = "_album"

# /files/{root}/{path} 서빙 시 텍스트 파일의 charset 명시(브라우저 한글 깨짐 방지).
_TEXT_MEDIA_TYPES = {
    ".md": "text/plain; charset=utf-8",
    ".srt": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".vtt": "text/vtt; charset=utf-8",
}

# /files 가 서빙하는 확장자 화이트리스트 — 미디어 + 분석/앨범 산출물만.
# (CORS * 환경에서 다운로드 루트에 섞여 있을 수 있는 무관한 파일 노출 방지.)
_SERVE_EXTRA_EXTENSIONS = (
    ".html", ".htm", ".md", ".srt", ".vtt", ".txt", ".json",
    ".jpg", ".jpeg", ".png", ".webp", ".gif",
)


# 전사·요약용으로 받는 영상은 일반 다운로드(downloads/)와 섞이지 않게 전용
# 폴더에 둔다. URL 전사 모드가 여기로 받고, 분석 보드/스캔도 이 폴더를 본다.
_READ_DIRNAME = "download2read"


def _read_root() -> Path:
    return Path.cwd() / _READ_DIRNAME


def _media_roots() -> list[tuple[str, Path]]:
    """분석 보드가 스캔/서빙할 수 있는 루트 폴더 목록 ``[(키, 절대경로)]``.

    키는 URL(``/files/{키}/...``)과 API payload 의 ``root`` 로 쓰인다:
      * ``read`` — 서버 cwd 의 ``download2read`` (전사·요약 전용 다운로드)
      * ``out`` — settings.ini ``[output] output_dir`` (일반 다운로드)
      * ``dl`` / ``dls`` — 서버 cwd 의 ``download`` / ``downloads``
        (과거 산출물 폴더 호환 — 예: 리포의 download/LJM)
    존재하는 폴더만, 같은 경로면 앞의 키 하나만 남긴다.
    """
    from ycollector.config import load_settings

    s, _ = load_settings(None)
    cands: list[tuple[str, Path]] = [
        ("read", _read_root()),
        ("out", Path(s.output_dir)),
        ("dl", Path.cwd() / "download"),
        ("dls", Path.cwd() / "downloads"),
    ]
    roots: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for key, p in cands:
        try:
            r = p.resolve()
            norm = str(r).lower()
            if r.is_dir() and norm not in seen:
                seen.add(norm)
                roots.append((key, r))
        except OSError:
            continue
    return roots


def _root_by_key(key: str) -> Path | None:
    for k, r in _media_roots():
        if k == key:
            return r
    return None


def _safe_join(root: Path, rel: str) -> Path | None:
    """``root`` 바깥으로 나가는 경로(.. / 절대경로 / 드라이브·ADS 콜론)를 차단."""
    if not rel or rel.startswith(("/", "\\")) or ":" in rel:
        return None
    try:
        target = (root / rel).resolve()
        if not target.is_relative_to(root):
            return None
    except (OSError, ValueError):
        return None
    return target


def _scan_overview() -> dict[str, Any]:
    """루트별로 미디어 파일과 분석 산출물(_analysis/_album) 현황을 모은다.

    보드 표시는 루트 직속 + 1단계 하위 폴더까지만 본다(LJM 같은 채널/주제 폴더 구조).
    """
    from ycollector.transcribe.config import MEDIA_EXTENSIONS

    roots_out: list[dict[str, Any]] = []
    for key, root in _media_roots():
        try:
            subdirs = [root, *sorted(
                (p for p in root.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))),
                key=lambda p: p.name.lower(),
            )]
        except OSError:
            continue
        folders: list[dict[str, Any]] = []
        for d in subdirs:
            try:
                children = sorted(d.iterdir())
            except OSError:
                continue
            analysis_dir = d / _ANALYSIS_DIRNAME
            items: list[dict[str, Any]] = []
            for f in children:
                if not (f.is_file() and f.suffix.lower() in MEDIA_EXTENSIONS):
                    continue
                try:
                    size = f.stat().st_size
                except OSError:
                    continue  # 스캔 중 삭제/권한 변경 — 이 파일만 건너뜀.
                summary = analysis_dir / f"{f.stem}.summary.md"
                script = analysis_dir / f"{f.stem}.script.md"
                items.append({
                    "name": f.name,
                    "rel": f.relative_to(root).as_posix(),
                    "bytes": size,
                    "summary": summary.relative_to(root).as_posix() if summary.is_file() else None,
                    "script": script.relative_to(root).as_posix() if script.is_file() else None,
                })
            album_index = d / _ALBUM_DIRNAME / "index.html"
            standalones = [
                p.relative_to(root).as_posix()
                for p in children
                if p.is_file() and p.name.lower().endswith("_standalone.html")
            ]
            if not items and not album_index.is_file() and not standalones:
                continue  # 보드에 보여줄 게 없는 폴더.
            folders.append({
                "name": d.name if d != root else "(루트)",
                "rel": "" if d == root else d.relative_to(root).as_posix(),
                "items": items,
                # analyzed = 산출물이 하나라도 있는 파일 수(전사만 한 것 포함).
                # summarized = summary.md 가 있는 파일 수 — 앨범 생성 가능 조건.
                "analyzed": sum(1 for it in items if it["summary"] or it["script"]),
                "summarized": sum(1 for it in items if it["summary"]),
                "album": album_index.relative_to(root).as_posix() if album_index.is_file() else None,
                "standalones": standalones,
            })
        roots_out.append({"key": key, "path": str(root), "folders": folders})
    return {"roots": roots_out}


# ── analyze 워커 (faster-whisper 전사 → Claude 요약 → 리포트) ──────────────
def _run_analyze(job: _JobRow, files: list[Path], *, language: str | None,
                 do_summarize: bool, summary_model: str | None, budget_usd: float,
                 whisper_model: str | None, skip_existing: bool = False,
                 make_album: bool = False) -> None:
    # skip_existing: 이미 산출물이 있는 파일은 건너뛴다(큐 재시도/폴더 스캔 시).
    # 전사(.script.md)가 있고, 요약을 안 하거나 요약(.summary.md)까지 있으면 완료로 본다.
    if skip_existing:
        kept: list[Path] = []
        for f in files:
            ad = f.parent / _ANALYSIS_DIRNAME
            has_script = (ad / f"{f.stem}.script.md").is_file()
            has_summary = (ad / f"{f.stem}.summary.md").is_file()
            if has_script and (has_summary or not do_summarize):
                continue
            kept.append(f)
        if not kept:
            job.status = "done"
            job.progress = 1.0
            job.message = "이미 전사·요약됨 (건너뜀)"
            job.emit_sync("done", message=job.message)
            return
        files = kept

    from ycollector.transcribe.config import TranscribeConfig, load_transcribe_config

    base_cfg, _src = load_transcribe_config(None)
    run_cfg = TranscribeConfig(
        model=whisper_model or base_cfg.model,
        language=language,
        task=base_cfg.task,
        output_format="srt",  # analyze CLI 와 동일 — 타임스탬프 보존.
        output_dir=str(files[0].parent / _ANALYSIS_DIRNAME),
        device=base_cfg.device,
        compute_type=base_cfg.compute_type,
        beam_size=base_cfg.beam_size,
        vad_filter=base_cfg.vad_filter,
    )

    job.status = "loading-model"
    job.emit_sync("status", status="loading-model",
                  message=f"Whisper 모델 로드 중 ({run_cfg.model}) — 최초 1회는 다운로드로 오래 걸림")
    from ycollector.transcribe.whisper import TranscribeEngine, TranscribeError, write_transcript

    try:
        engine = TranscribeEngine(run_cfg, on_log=lambda m: job.emit_sync("progress", message=m))
    except TranscribeError as exc:
        job.status = "failed"
        job.error = {"category": exc.category, "message": exc.message}
        job.emit_sync("error", category=exc.category, message=exc.message)
        return

    summarizer = None
    if do_summarize:
        from ycollector.transcribe.summarize import (
            DEFAULT_SUMMARY_MODEL,
            SummarizeError,
            Summarizer,
        )

        try:
            summarizer = Summarizer(
                model=summary_model or DEFAULT_SUMMARY_MODEL,
                budget_usd=budget_usd,
                on_log=lambda m: job.emit_sync("progress", message=m),
            )
        except SummarizeError as exc:
            job.emit_sync("progress",
                          message=f"요약 비활성 — {exc.message.splitlines()[0]} (전사만 진행)")

    from ycollector.transcribe.report import write_reports

    n = len(files)
    # 파일 1개 안에서의 비중: 요약을 켰으면 전사 85% + 요약 15% (요약이 훨씬 짧다).
    t_share = 0.85 if summarizer is not None else 1.0
    failed: list[str] = []
    summarized = 0
    for i, src in enumerate(files):
        out_dir = src.parent / _ANALYSIS_DIRNAME  # 파일이 속한 폴더 기준(여러 폴더 혼합 대비).
        job.status = "transcribing"
        job.emit_sync("status", status="transcribing", message=f"[{i + 1}/{n}] {src.name}")
        last_emit = [0.0]

        def on_seg(seg, duration, *, _i=i, _name=src.name, _last=last_emit):
            now = time.monotonic()
            if now - _last[0] < 1.0:  # SSE 폭주 방지 — 초당 1회만.
                return
            _last[0] = now
            frac = min(1.0, seg.end / duration) if duration else 0.0
            job.progress = (_i + frac * t_share) / n
            job.emit_sync("progress", progress=job.progress, status="transcribing",
                          message=f"[{_i + 1}/{n}] 전사 {frac:.0%} · {_name[:48]}")

        try:
            result = engine.transcribe(src, on_segment=on_seg)
            write_transcript(result, out_dir, src.stem, run_cfg.output_format)
        except TranscribeError as exc:
            failed.append(f"{src.name}: {exc.message}")
            job.emit_sync("progress", message=f"✗ 전사 실패 — {src.name}: {exc.message}")
            continue

        summary = None
        if summarizer is not None:
            job.status = "summarizing"
            job.progress = (i + t_share) / n
            job.emit_sync("progress", progress=job.progress, status="summarizing",
                          message=f"[{i + 1}/{n}] Claude 요약 중 · {src.name[:48]}")
            try:
                summary = summarizer.summarize(result.segments)
                summarized += 1
                job.cost_usd = summarizer.budget.spent_usd
                job.emit_sync("estimate", cost_usd=job.cost_usd)
            except Exception as exc:
                from ycollector.transcribe.summarize import BudgetExceeded

                job.emit_sync("progress", message=f"✗ 요약 실패 — {src.name}: {exc}")
                if isinstance(exc, BudgetExceeded):
                    summarizer = None  # 예산 소진 — 이후 파일은 전사만.

        write_reports(out_dir, src.stem, result.segments, summary,
                      title=src.stem, language=result.language, duration=result.duration)
        job.progress = (i + 1) / n
        job.emit_sync("progress", progress=job.progress,
                      message=f"[{i + 1}/{n}] 완료 · {src.name[:48]}")

    if failed and len(failed) == n:
        job.status = "failed"
        job.error = {"category": "transcribe", "message": " / ".join(failed)[:500]}
        job.emit_sync("error", category="transcribe", message=job.error["message"])
        return

    out_dirs = sorted({str(src.parent / _ANALYSIS_DIRNAME) for src in files})
    job.status = "done"
    job.progress = 1.0
    job.out_path = out_dirs[0] if len(out_dirs) == 1 else f"{len(out_dirs)}개 폴더"
    job.message = f"전사 {n - len(failed)}/{n} · 요약 {summarized}/{n}"
    # 일부 파일이 실패했으면 큐가 '실패'로 표시해 재시도할 수 있게 detail 을 남긴다
    # (재시도 시 skip_existing 이면 성공분은 건너뛰고 실패분만 다시 시도).
    if failed:
        job.error = {"category": "partial",
                     "message": f"부분 실패 {len(failed)}/{n}: " + " / ".join(failed)[:300]}
    # 정방향: 전사·요약이 끝난 폴더마다 앨범 작업을 큐(앨범 레인)에 넣는다.
    # 앨범은 ffmpeg(CPU)라 전사(GPU)와 별도 레인에서 동시 진행된다.
    if make_album:
        done_folders = sorted(
            {src.parent for src in files
             if (src.parent / _ANALYSIS_DIRNAME / f"{src.stem}.summary.md").is_file()},
            key=str,
        )
        for folder in done_folders:
            try:
                _enqueue_album(folder)
            except Exception:  # 앨범 enqueue 실패가 전사 완료를 막지 않게.
                pass
    job.emit_sync("done", out_path=job.out_path, message=job.message, cost_usd=job.cost_usd)


# ── URL 전사 워커 (영상 다운로드 → 전사·요약) ─────────────────────────────
# 분석 보드의 두 번째 입력 모드: 폴더 파일 대신 URL 을 받아 해당 영상(또는
# 재생목록 전체)을 다운로드한 뒤 위 _run_analyze 로 그대로 넘긴다.
def _download_collect(job: _JobRow, url: str, *, playlist_all: bool) -> list[Path]:
    """URL(또는 재생목록)을 다운로드하고 받은 미디어 파일 경로 전부를 모은다.

    `_run_download` 과 같은 엔진·설정을 쓰되 (1) 재생목록의 모든 파일을
    on_file 로 수집하고 (2) library 등록/done emit 은 하지 않는다(전사 단계가
    이어서 done 을 쏜다). 미디어 확장자 + 실재 파일만 중복 제거 후 반환.
    """
    from ycollector.config import load_settings
    from ycollector.cookies import default_cookies_path, is_cookies_present
    from ycollector.engine import (
        AudioPref, CodecPref, Container, DownloadError, FormatChoice, Quality,
        YtdlpEngine, compose_format_spec,
    )
    from ycollector.transcribe.config import MEDIA_EXTENSIONS

    s, _ = load_settings(None)
    engine = YtdlpEngine()
    fmt = compose_format_spec(FormatChoice(
        quality=Quality(s.quality), container=Container(s.container),
        codec=CodecPref(s.codec), audio=AudioPref(s.audio),
    ))
    cookies_file: Path | None = None
    if s.cookies_file:
        cookies_file = Path(s.cookies_file)
    elif is_cookies_present():
        cookies_file = default_cookies_path()
    if cookies_file is not None and not cookies_file.is_file():
        cookies_file = None

    # 사용자가 '전체' 면 재생목록 펼침, 아니면 단일(모호 URL 도 단일 취급).
    no_pl, yes_pl = (False, True) if playlist_all else (True, False)

    collected: list[Path] = []
    seen: set[str] = set()

    def on_file(p: Path) -> None:
        key = str(p).lower()
        if key in seen:
            return
        seen.add(key)
        collected.append(p)
        job.emit_sync("progress", status="downloading",
                      message=f"받음 {len(collected)}개 · {p.name[:48]}")

    def on_progress(ev: Any) -> None:
        # 진행바는 전사 단계(이후 _run_analyze)가 0→100 으로 단조 증가시킨다.
        # 다운로드 중에는 바를 건드리지 않고 속도/ETA/개수만 메시지로 보여
        # 전사 시작 시 바가 거꾸로 튀지 않게 한다.
        pct = f"{ev.percent:.0f}%" if ev.percent is not None else "…"
        job.emit_sync("progress", status="downloading", speed=ev.speed, eta=ev.eta,
                      message=f"다운로드 중 {pct} ({len(collected)}개 완료)")

    def on_meta(meta: Any) -> None:
        job.title = meta.title
        job.emit_sync("meta", title=meta.title, channel=meta.channel, duration=meta.duration)

    job.status = "downloading"
    job.emit_sync("status", status="downloading",
                  message="영상 다운로드 시작 — 재생목록이면 항목 수만큼 걸립니다")
    try:
        engine.download(
            # 전사용 다운로드는 일반 다운로드(downloads/)와 섞이지 않게 전용 폴더로.
            url, format=fmt, output_dir=_read_root(),
            merge_format=s.container, write_subs=s.embed_subs, sub_langs=s.sub_langs,
            cookies_from_browser=s.cookies_from_browser, cookies_file=cookies_file,
            socket_timeout=s.socket_timeout, retries=s.retries,
            fragment_retries=s.fragment_retries, throttled_rate=s.throttled_rate,
            no_playlist=no_pl, yes_playlist=yes_pl,
            max_downloads=s.max_downloads, playlist_items=s.playlist_items,
            # 아카이브 미사용 — 전사 목적이라 이미 받은 URL 도 파일 경로를 확보해야
            # 한다. 다운로드 패널과 archive.txt 를 공유하면 yt-dlp 가 archive-skip
            # 하면서 경로를 안 알려줘(로그 형식이 달라 on_file 미발사) '영상 없음'
            # 으로 오인된다. 디스크에 이미 있으면 yt-dlp 가 곧장 인식해 경로를 준다.
            on_progress=on_progress, on_meta=on_meta, on_file=on_file,
        )
    except DownloadError as exc:
        # 일부라도 받았으면 그것만 전사, 전부 실패면 위로 전달.
        if not collected:
            raise
        job.emit_sync("progress",
                      message=f"일부 다운로드 실패({exc.message[:80]}) — 받은 것만 전사")

    out: list[Path] = []
    for p in collected:
        try:
            if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS:
                out.append(p)
        except OSError:
            continue
    return out


def _run_url_analyze(job: _JobRow, url: str, *, playlist_all: bool,
                     language: str | None, do_summarize: bool,
                     summary_model: str | None, budget_usd: float,
                     whisper_model: str | None, make_album: bool = False) -> None:
    from ycollector.engine import DownloadError

    try:
        files = _download_collect(job, url, playlist_all=playlist_all)
    except DownloadError as exc:
        job.status = "failed"
        job.error = {"category": exc.category, "message": exc.message}
        job.emit_sync("error", category=exc.category, message=exc.message)
        return
    except Exception as exc:
        job.status = "failed"
        job.error = {"category": "internal", "message": str(exc)}
        job.emit_sync("error", category="internal", message=str(exc))
        return

    if not files:
        job.status = "failed"
        job.error = {"category": "download",
                     "message": "다운로드된 영상이 없습니다 (URL/재생목록을 확인하세요)."}
        job.emit_sync("error", category="download", message=job.error["message"])
        return

    job.emit_sync("progress", message=f"다운로드 완료 {len(files)}개 — 전사 시작")
    # 이어서 전사+요약 — 완료 시 _run_analyze 가 done 을 쏘고 앨범을 enqueue 한다.
    _run_analyze(job, files, language=language, do_summarize=do_summarize,
                 summary_model=summary_model, budget_usd=budget_usd,
                 whisper_model=whisper_model, make_album=make_album)


# ── album 워커 (ffmpeg 장면 캡쳐 + HTML 앨범북) ─────────────────────────────
def _generate_album(folder: Path, frames: int) -> tuple[int, Path]:
    """``_album/index.html`` (+ bookNN) 와 단일 합본 ``*_album_standalone.html`` 생성.

    album CLI 의 ``--standalone`` 은 '단독 모드'(index 대신 합본만)라, LJM 처럼 둘 다
    얻으려면 (1) 일반 앨범 빌드 후 (2) 캡쳐 이미지를 재사용해 합본을 한 번 더 만든다.
    (rc, index_path) 반환. 합본 실패는 무시(메인 앨범이 핵심).
    """
    from ycollector.transcribe import album as album_mod

    out_dir = folder / _ALBUM_DIRNAME
    index = out_dir / "index.html"
    base = ["--analysis-dir", str(folder / _ANALYSIS_DIRNAME),
            "--video-dir", str(folder), "--frames", str(frames)]

    def _run(extra: list[str]) -> int:
        try:
            rc = album_mod.main(base + extra)
        except SystemExit as exc:  # argparse 방어.
            rc = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        return rc if isinstance(rc, int) else 1

    rc = _run(["--output-dir", str(out_dir)])
    if rc == 0 and index.is_file():
        standalone = folder / f"{folder.name}_album_standalone.html"
        _run(["--standalone", str(standalone), "--album-dir", str(out_dir)])
    return rc, index


def _run_album(job: _JobRow, folder: Path, frames: int) -> None:
    job.status = "rendering"
    job.emit_sync("status", status="rendering",
                  message="ffmpeg 장면 캡쳐 + 앨범 HTML 생성 중 (몇 분 걸릴 수 있음)")
    try:
        rc, index = _generate_album(folder, frames)
    except Exception as exc:
        job.status = "failed"
        job.error = {"category": "internal", "message": str(exc)}
        job.emit_sync("error", category="internal", message=str(exc))
        return

    if rc == 0 and index.is_file():
        job.status = "done"
        job.progress = 1.0
        job.out_path = str(index)
        job.message = "앨범 생성 완료"
        job.emit_sync("done", out_path=str(index), message=job.message)
    else:
        job.status = "failed"
        job.error = {"category": "album",
                     "message": f"앨범 생성 실패 (exit {rc}) — 서버 콘솔 로그를 확인하세요. "
                                "ffmpeg 미설치면 캡쳐 없이 생성됩니다."}
        job.emit_sync("error", category="album", message=job.error["message"])


def _run_album_task(task: dict[str, Any]) -> None:
    """큐의 album 작업 — spec {root, dir, frames} 폴더에 앨범 생성."""
    spec = task.get("spec") or {}
    jid = task.get("job_id") or uuid.uuid4().hex[:12]
    row = _state.jobs.get(jid) or _JobRow(jid, "album", task.get("title") or jid)
    _remember_job(jid, row)
    if task.get("job_id") != jid:
        _queue_update(task["id"], job_id=jid)
    root = _root_by_key(str(spec.get("root") or ""))
    folder = None
    if root is not None:
        rel = str(spec.get("dir") or "").strip()
        folder = root if rel in ("", ".") else _safe_join(root, rel)
    if folder is None or not folder.is_dir():
        row.status = "failed"
        row.error = {"category": "album", "message": "대상 폴더 없음(이동·삭제됨)."}
        row.emit_sync("error", category="album", message=row.error["message"])
    else:
        try:
            _run_album(row, folder, int(spec.get("frames") or 3))
        except Exception as exc:
            row.status = "failed"
            row.error = {"category": "internal", "message": str(exc)}
            row.emit_sync("error", category="internal", message=str(exc))
    _queue_outcome(task["id"], row)


def _root_ref_for(path: Path) -> tuple[str, str] | None:
    """절대 폴더 경로 → (루트키, 루트기준 rel). 앨범 작업 enqueue 용."""
    try:
        rp = path.resolve()
    except OSError:
        return None
    for key, root in _media_roots():
        try:
            if rp == root or rp.is_relative_to(root):
                return key, ("" if rp == root else rp.relative_to(root).as_posix())
        except (OSError, ValueError):
            continue
    return None


def _enqueue_album(folder: Path, frames: int = 3) -> str | None:
    """폴더에 summary.md 가 있으면 앨범 작업을 큐(album 레인)에 추가하고 job_id 반환.

    같은 폴더(root,dir)의 앨범 작업이 이미 pending/running 이면 새로 만들지 않고 그
    job_id 를 돌려준다(중복 방지). summary 없거나 루트 밖이면 None.
    """
    if not any((folder / _ANALYSIS_DIRNAME).glob("*.summary.md")):
        return None
    ref = _root_ref_for(folder)
    if ref is None:
        return None
    key, rel = ref
    with _QUEUE_LOCK:  # 검사+추가를 한 잠금 안에서(중복 enqueue TOCTOU 방지).
        tasks = _queue_read()
        for t in tasks:
            spec = t.get("spec") or {}
            if (t.get("kind") == "album" and t.get("status") in ("pending", "running")
                    and spec.get("root") == key and spec.get("dir") == rel):
                return t.get("job_id")  # 이미 대기/실행 중 — 그걸 추적.
        jid = uuid.uuid4().hex[:12]
        row = _JobRow(jid, "album", folder.name or key)
        row.status = "queued"
        _remember_job(jid, row)
        tasks.append(_new_task("album", f"앨범 — {folder.name or key}",
                               {"root": key, "dir": rel, "frames": frames},
                               status="pending", job_id=jid))
        _queue_write(tasks)
    _album_lane_wake()
    return jid


# ── 영속 작업 큐 (다운로드/전사 — 중단되면 리스트로 관리·재시도) ────────────
# queue.json 에 작업을 영속 저장한다. 다운로드는 즉시 실행하되 추적하고(실패·
# 중단 시 리스트에 남아 재시도 가능), 전사(analyze)는 CPU 가 무거워 단일 레인에서
# 하나씩 순차 처리한다. 서버가 죽었다 떠도 'running' 이던 작업을 'interrupted' 로
# 표시해 재시도할 수 있다.
#
# task: {id, kind: download|analyze-url|analyze-files, title, spec, status, error,
#        attempts, job_id, created_at, updated_at}
#   status: pending(레인 대기) | running | done | failed | interrupted
_QUEUE_LOCK = threading.RLock()
_ANALYZE_CV = threading.Condition()  # 전사 레인 wake 신호.
_ALBUM_CV = threading.Condition()    # 앨범 레인 wake 신호(ffmpeg=CPU, 전사와 동시 진행).
_QUEUE_STATUSES = ("pending", "running", "done", "failed", "interrupted")


def _queue_path() -> Path:
    from ycollector.config import user_config_dir
    return user_config_dir() / "queue.json"


def _queue_read() -> list[dict[str, Any]]:
    p = _queue_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data.get("tasks", []) if isinstance(data, dict) else []


def _queue_write(tasks: list[dict[str, Any]]) -> None:
    p = _queue_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(p)  # 원자적 교체 — 중간에 죽어도 파일이 깨지지 않게.


def _new_task(kind: str, title: str, spec: dict[str, Any], status: str,
              job_id: str | None = None) -> dict[str, Any]:
    now = int(time.time() * 1000)
    return {"id": uuid.uuid4().hex[:12], "kind": kind, "title": title, "spec": spec,
            "status": status, "error": None, "attempts": 0, "job_id": job_id,
            "created_at": now, "updated_at": now}


def _queue_add(task: dict[str, Any]) -> None:
    with _QUEUE_LOCK:
        tasks = _queue_read()
        tasks.append(task)
        _queue_write(tasks)


def _queue_update(task_id: str, **changes: Any) -> dict[str, Any] | None:
    with _QUEUE_LOCK:
        tasks = _queue_read()
        found = None
        for t in tasks:
            if t.get("id") == task_id:
                t.update(changes)
                t["updated_at"] = int(time.time() * 1000)
                found = t
                break
        if found is not None:
            _queue_write(tasks)
        return found


def _queue_outcome(task_id: str, row: _JobRow) -> None:
    """워커가 끝난 뒤 job 상태로 큐 상태를 갱신.

    완전 실패뿐 아니라 '부분 실패'(status=done 이지만 row.error 가 설정된 경우)도
    큐에선 failed 로 남겨 재시도 대상이 되게 한다.
    """
    err = (row.error or {}).get("message") if row.error else None
    if row.status == "failed" or err:
        _queue_update(task_id, status="failed", error=err or "실패")
    else:
        _queue_update(task_id, status="done", error=None)


def _run_download_task(row: _JobRow, url: str, settings_override: dict[str, Any] | None,
                       task_id: str) -> None:
    _queue_update(task_id, status="running", job_id=row.id, error=None)
    try:
        _run_download(row, url, settings_override)
    except Exception as exc:  # _run_download 는 내부에서 잡지만 방어적으로.
        row.status = "failed"
        row.error = {"category": "internal", "message": str(exc)}
        row.emit_sync("error", category="internal", message=str(exc))
    _queue_outcome(task_id, row)


def _run_analyze_task(task: dict[str, Any]) -> None:
    spec = task.get("spec") or {}
    jid = task.get("job_id") or uuid.uuid4().hex[:12]
    row = _state.jobs.get(jid) or _JobRow(jid, "analyze", task.get("title") or jid)
    _remember_job(jid, row)
    # 상태/attempts 는 레인이 이미 'running' 으로 원자적 claim 했다 — 여기선 job_id 만 보강.
    if task.get("job_id") != jid:
        _queue_update(task["id"], job_id=jid)
    try:
        if task.get("kind") == "analyze-url":
            _run_url_analyze(
                row, spec["url"], playlist_all=bool(spec.get("playlist_all")),
                language=spec.get("language"), do_summarize=bool(spec.get("summarize", True)),
                summary_model=None, budget_usd=float(spec.get("budget_usd") or 5.0),
                whisper_model=None, make_album=bool(spec.get("make_album", True)),
            )
        else:  # analyze-files
            root = _root_by_key(str(spec.get("root") or ""))
            files: list[Path] = []
            if root is not None:
                for rel in spec.get("files") or []:
                    p = _safe_join(root, str(rel))
                    if p is not None and p.is_file():
                        files.append(p)
            if not files:
                raise RuntimeError("대상 파일이 없습니다 (이동·삭제됐거나 root 변경).")
            _run_analyze(
                row, files, language=spec.get("language"),
                do_summarize=bool(spec.get("summarize", True)), summary_model=None,
                budget_usd=float(spec.get("budget_usd") or 5.0), whisper_model=None,
                skip_existing=bool(spec.get("skip_existing")),
                make_album=bool(spec.get("make_album", True)),
            )
    except Exception as exc:
        row.status = "failed"
        row.error = {"category": "internal", "message": str(exc)}
        row.emit_sync("error", category="internal", message=str(exc))
    _queue_outcome(task["id"], row)


def _lane_claim(match: "Callable[[dict[str, Any]], bool]") -> dict[str, Any] | None:
    """match(t) 이고 pending 인 첫 작업을 '잠금 안에서' 원자적으로 running 으로 claim.

    claim 직후 들어온 재시도 요청이 (이미 running 이라) 거부돼 같은 작업이 두 job_id
    로 갈라지지 않는다(TOCTOU 방지). claim 한 작업의 스냅샷(dict) 반환, 없으면 None.
    """
    with _QUEUE_LOCK:
        tasks = _queue_read()
        for t in tasks:
            if match(t) and t.get("status") == "pending":
                t["status"] = "running"
                t["attempts"] = int(t.get("attempts") or 0) + 1
                t["error"] = None
                t["updated_at"] = int(time.time() * 1000)
                _queue_write(tasks)
                return dict(t)
    return None


def _lane_loop(match: "Callable[[dict[str, Any]], bool]", cv: threading.Condition,
               runner: "Callable[[dict[str, Any]], None]") -> None:
    """한 종류의 작업을 하나씩 순차 처리하는 단일 워커. (전사/앨범 각각 1개)"""
    while True:
        claimed = _lane_claim(match)
        if claimed is None:
            with cv:
                cv.wait(timeout=5.0)
            continue
        try:
            runner(claimed)
        except Exception as exc:  # 레인은 절대 죽지 않게.
            _queue_update(claimed["id"], status="failed", error=str(exc))


def _is_analyze(t: dict[str, Any]) -> bool:
    return str(t.get("kind", "")).startswith("analyze")


def _is_album(t: dict[str, Any]) -> bool:
    return t.get("kind") == "album"


def _analyze_lane_wake() -> None:
    with _ANALYZE_CV:
        _ANALYZE_CV.notify_all()


def _album_lane_wake() -> None:
    with _ALBUM_CV:
        _ALBUM_CV.notify_all()


def _queue_recover() -> None:
    """서버 시작 시: 직전 프로세스에서 'running' 이던 작업을 'interrupted' 로."""
    with _QUEUE_LOCK:
        tasks = _queue_read()
        changed = False
        for t in tasks:
            if t.get("status") == "running":
                t["status"] = "interrupted"
                t["updated_at"] = int(time.time() * 1000)
                changed = True
        if changed:
            _queue_write(tasks)


def _queue_requeue(task_id: str) -> str | None:
    """interrupted/failed 작업을 다시 실행. 다운로드는 즉시, 전사는 레인에 pending.

    상태 확인 + 전이를 '잠금 안에서' 원자적으로 처리해, 동시 재시도(두 탭·더블클릭)
    나 레인 claim 직전 끼어드는 재시도에도 작업이 두 번 돌지 않게 한다. 이미
    running/pending 이거나 없는 작업이면 ``None`` 을 돌려준다(호출부가 거부).
    새 live job 의 id 를 돌려줘 UI 가 그걸 추적한다(이전 job_id 폐기).
    """
    jid = uuid.uuid4().hex[:12]
    with _QUEUE_LOCK:
        tasks = _queue_read()
        t = next((x for x in tasks if x.get("id") == task_id), None)
        if t is None or t.get("status") in ("running", "pending"):
            return None
        kind = t.get("kind")
        title = t.get("title") or jid
        spec = dict(t.get("spec") or {})
        t["job_id"] = jid
        t["error"] = None
        t["attempts"] = int(t.get("attempts") or 0) + 1
        t["status"] = "running" if kind == "download" else "pending"
        t["updated_at"] = int(time.time() * 1000)
        _queue_write(tasks)

    if kind == "download":
        row = _JobRow(jid, "download", title)
        _remember_job(jid, row)
        threading.Thread(target=_run_download_task,
                         args=(row, spec.get("url") or "", spec.get("settings"), task_id),
                         daemon=True).start()
    elif kind == "album":
        row = _JobRow(jid, "album", title)
        row.status = "queued"
        _remember_job(jid, row)
        _album_lane_wake()
    else:  # analyze-url / analyze-files
        row = _JobRow(jid, "analyze", title)
        row.status = "queued"
        _remember_job(jid, row)
        _analyze_lane_wake()
    return jid


# ── FastAPI app ───────────────────────────────────────────────────────────
def _build_app():  # type: ignore[no-untyped-def]
    try:
        from fastapi import FastAPI, File, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as exc:
        raise SystemExit(
            "fastapi 가 없습니다. 다음을 실행하세요:\n"
            "  uv sync --extra web --extra video-gen\n"
            f"원인: {exc}"
        ) from exc

    # python-dotenv 가 있으면 .env 자동 로드.
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
        for cand in [Path.cwd() / ".env", *(p / ".env" for p in Path.cwd().parents)]:
            if cand.is_file():
                load_dotenv(cand, override=False)
                break
    except ImportError:
        pass

    @asynccontextmanager
    async def lifespan(_app):  # type: ignore[no-untyped-def]
        _state.loop = asyncio.get_running_loop()
        # 직전 프로세스의 미완료 작업 정리 + 레인 1회 기동(전사 1 · 앨범 1, 동시).
        _queue_recover()
        if not getattr(_state, "_lanes_started", False):
            _state._lanes_started = True  # type: ignore[attr-defined]
            threading.Thread(target=lambda: _lane_loop(_is_analyze, _ANALYZE_CV, _run_analyze_task),
                             daemon=True, name="analyze-lane").start()
            threading.Thread(target=lambda: _lane_loop(_is_album, _ALBUM_CV, _run_album_task),
                             daemon=True, name="album-lane").start()
        yield
        _state.loop = None

    app = FastAPI(title="YCollector", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    webui_dir = Path(__file__).parent / "webui"

    @app.get("/")
    def index() -> Any:
        return FileResponse(webui_dir / "index.html")

    @app.get("/README.html")
    def readme_html() -> Any:
        # 서버 가동 cwd 의 README.html (저장소 루트 기준).
        for cand in (Path("README.html"), Path(__file__).parent.parent.parent.parent / "README.html"):
            if cand.is_file():
                return FileResponse(cand)
        raise HTTPException(404, "README.html 을 찾지 못함")

    app.mount("/webui", StaticFiles(directory=str(webui_dir)), name="webui")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        from ycollector.cookies import default_cookies_path, is_cookies_present
        return {
            "ok": True,
            "openai_api_key": bool(os.environ.get("OPENAI_API_KEY")),
            "cookies_present": is_cookies_present(),
            "cookies_path": str(default_cookies_path()),
            "library": str(_library_path()),
        }

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        from ycollector.config import load_settings, settings_to_dict
        s, src = load_settings(None)
        d = settings_to_dict(s)
        d["_source"] = str(src) if src else None
        return d

    @app.post("/api/settings")
    def post_settings(payload: dict[str, Any]) -> dict[str, Any]:
        from ycollector.config import load_settings, save_settings, settings_from_dict, settings_to_dict
        base, _ = load_settings(None)
        merged = {**settings_to_dict(base), **{k: v for k, v in payload.items() if k != "_source"}}
        s = settings_from_dict(merged)
        dest = save_settings(s)
        return {"ok": True, "path": str(dest)}

    @app.post("/api/download")
    def start_download(payload: dict[str, Any]) -> dict[str, Any]:
        url = (payload.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url 비어있음")
        settings_dict = payload.get("settings") or {}
        if payload.get("playlist_mode"):
            settings_dict.setdefault("playlist_mode", payload["playlist_mode"])
        jid = uuid.uuid4().hex[:12]
        row = _JobRow(jid, "download", url)
        _remember_job(jid, row)
        # 다운로드는 즉시 실행하되 큐에 기록 — 실패·중단되면 리스트에서 재시도 가능.
        task = _new_task("download", url, {"url": url, "settings": settings_dict or None},
                         status="running", job_id=jid)
        _queue_add(task)
        threading.Thread(
            target=_run_download_task,
            args=(row, url, settings_dict or None, task["id"]),
            daemon=True,
        ).start()
        return {"job_id": jid}

    @app.post("/api/upload")
    async def upload_reference(file: UploadFile = File(...)) -> dict[str, Any]:
        """이미지/영상 reference 파일을 받아 temp 에 저장 → 서버 경로 반환.

        반환 path 를 그대로 /api/generate 의 references 에 넣으면 된다.
        """
        from ycollector.generator import media
        uploads = Path(tempfile.gettempdir()) / "ycollector-uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "ref")[-64:] or "ref"
        dest = uploads / f"{uuid.uuid4().hex[:8]}_{safe}"
        size_bytes = 0
        with dest.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                size_bytes += len(chunk)
        kind = "video" if media.is_video(dest) else "image"
        return {"path": str(dest), "name": file.filename, "kind": kind, "bytes": size_bytes}

    @app.post("/api/generate")
    def start_generate(payload: dict[str, Any]) -> dict[str, Any]:
        prompts = payload.get("prompts") or []
        if not isinstance(prompts, list):
            raise HTTPException(400, "prompts 는 리스트여야 함")
        prompts_clean = [str(x).strip() for x in prompts if str(x).strip()]
        if not prompts_clean:
            raise HTTPException(400, "프롬프트를 1개 이상 넣으세요")
        combined = " ".join(prompts_clean)
        references = payload.get("references") or []
        if not isinstance(references, list):
            raise HTTPException(400, "references 는 리스트여야 함")
        refs: list[str] = [str(x).strip() for x in references if str(x).strip()]
        model = str(payload.get("model") or "sora-2-pro")
        size = str(payload.get("size") or "1280x720")
        seconds = int(payload.get("seconds") or 8)
        seed = payload.get("seed")
        seed_i = int(seed) if seed is not None else None

        # 연속형(chain): 프롬프트 2개 이상(장면별) 또는 reference 2개 이상이면
        # last-frame chaining 으로 세그먼트를 이어 1개 영상 생성.
        if len(prompts_clean) >= 2 or len(refs) >= 2:
            jid = uuid.uuid4().hex[:12]
            row = _JobRow(jid, "generate", combined)
            _remember_job(jid, row)
            threading.Thread(
                target=_run_generate_continuous,
                args=(row, prompts_clean, refs, model, size, seconds, seed_i),
                daemon=True,
            ).start()
            return {"job_ids": [jid], "chain": True}

        # 단일 생성(프롬프트 1개 + reference 0~1개).
        jid = uuid.uuid4().hex[:12]
        row = _JobRow(jid, "generate", combined)
        _remember_job(jid, row)
        ref0 = refs[0] if refs else None
        threading.Thread(
            target=_run_generate,
            args=(row, combined, [ref0] if ref0 else [], model, size, seconds, seed_i),
            daemon=True,
        ).start()
        return {"job_ids": [jid], "chain": False}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        row = _state.jobs.get(job_id)
        if not row:
            raise HTTPException(404, "unknown job")
        return row.to_dict()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        row = _state.jobs.get(job_id)
        if not row:
            raise HTTPException(404, "unknown job")
        # 다운로드 취소는 별 hook 필요(현재 미구현) — 표지만 갱신.
        row.status = "cancelled"
        row.emit_sync("cancelled")
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/events")
    async def events(job_id: str) -> StreamingResponse:
        row = _state.jobs.get(job_id)
        if not row:
            raise HTTPException(404, "unknown job")
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        row.events.append(q)

        async def gen():  # type: ignore[no-untyped-def]
            # 즉시 현재 상태 한 번 보냄.
            yield _sse({"event": "snapshot", **row.to_dict()})
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield _sse(msg)
                        if msg.get("event") in ("done", "error", "cancelled"):
                            break
                    except asyncio.TimeoutError:
                        # heartbeat (SSE 일부 프록시가 idle 60s 끊음)
                        yield ": keep-alive\n\n"
            finally:
                try:
                    row.events.remove(q)
                except ValueError:
                    pass

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ── 전사 · 요약 분석 (transcribe → summarize → album) ──────────────
    @app.get("/api/analysis/overview")
    def analysis_overview() -> dict[str, Any]:
        """다운로드 루트들을 스캔해 미디어 + 분석 산출물 현황을 돌려준다."""
        return _scan_overview()

    @app.post("/api/analyze")
    def start_analyze(payload: dict[str, Any]) -> dict[str, Any]:
        """전사·요약 시작. 두 입력 모드:
          * 파일 모드 — {"root", "files": [...]}: 폴더에서 고른 파일 전사.
          * URL  모드 — {"url", "playlist_all"?}: 영상(또는 재생목록 전체)을
            다운로드한 뒤 전사. 공통 옵션: language / summarize / budget_usd.
        """
        from ycollector.transcribe.config import MEDIA_EXTENSIONS

        def clean_model(v: Any) -> str | None:
            s = str(v or "").strip()
            if not s:
                return None
            if len(s) > 120 or not re.fullmatch(r"[A-Za-z0-9._/\-]+", s):
                raise HTTPException(400, "model 이름 형식이 올바르지 않음")
            return s

        lang = str(payload.get("language") or "auto").strip().lower()
        do_sum = bool(payload.get("summarize", True))
        try:
            budget = float(payload.get("budget_usd") or 5.0)
        except (TypeError, ValueError):
            budget = 5.0
        kwargs: dict[str, Any] = {
            "language": None if lang in ("", "auto") else lang,
            "do_summarize": do_sum,
            "summary_model": clean_model(payload.get("summary_model")),
            "budget_usd": budget,
            "whisper_model": clean_model(payload.get("whisper_model")),
        }

        # 전사는 CPU 가 무거워 큐의 단일 레인에서 순차 처리한다. 여기선 작업을
        # 'pending' 으로 큐에 넣고 즉시 job_id 를 돌려준다(UI 는 'queued' 로 표시).
        # ── URL 모드: 영상(들) 다운로드 → 전사 ──
        url = str(payload.get("url") or "").strip()
        if url:
            label = f"URL 전사 — {url[:60]}"
            jid = uuid.uuid4().hex[:12]
            row = _JobRow(jid, "analyze", label)
            row.status = "queued"
            _remember_job(jid, row)
            spec = {"url": url, "playlist_all": bool(payload.get("playlist_all")),
                    "language": kwargs["language"], "summarize": kwargs["do_summarize"],
                    "budget_usd": kwargs["budget_usd"]}
            _queue_add(_new_task("analyze-url", label, spec, status="pending", job_id=jid))
            _analyze_lane_wake()
            return {"job_id": jid}

        # ── 파일 모드: 폴더에서 고른 파일 전사 ──
        root = _root_by_key(str(payload.get("root") or ""))
        if root is None:
            raise HTTPException(400, "root 가 올바르지 않음 (overview 의 key 를 쓰거나 url 을 주세요)")
        rels = payload.get("files") or []
        if not isinstance(rels, list) or not rels:
            raise HTTPException(400, "files 가 비어 있음 (또는 url 을 주세요)")
        for rel in rels:
            p = _safe_join(root, str(rel))
            if p is None or not p.is_file():
                raise HTTPException(400, f"파일 없음: {rel}")
            if p.suffix.lower() not in MEDIA_EXTENSIONS:
                raise HTTPException(400, f"미디어 파일이 아님: {rel}")

        first = Path(str(rels[0])).name
        label = first if len(rels) == 1 else f"{Path(str(rels[0])).parent.name} — {len(rels)}개 파일"
        jid = uuid.uuid4().hex[:12]
        row = _JobRow(jid, "analyze", label)
        row.status = "queued"
        _remember_job(jid, row)
        spec = {"root": str(payload.get("root")), "files": list(rels),
                "language": kwargs["language"], "summarize": kwargs["do_summarize"],
                "budget_usd": kwargs["budget_usd"]}
        _queue_add(_new_task("analyze-files", label, spec, status="pending", job_id=jid))
        _analyze_lane_wake()
        return {"job_id": jid}

    @app.post("/api/album")
    def start_album(payload: dict[str, Any]) -> dict[str, Any]:
        root = _root_by_key(str(payload.get("root") or ""))
        if root is None:
            raise HTTPException(400, "root 가 올바르지 않음")
        rel = str(payload.get("dir") or "").strip()
        folder = root if rel in ("", ".") else _safe_join(root, rel)
        if folder is None or not folder.is_dir():
            raise HTTPException(400, f"폴더 없음: {rel}")
        if not any((folder / _ANALYSIS_DIRNAME).glob("*.summary.md")):
            raise HTTPException(400, "_analysis/*.summary.md 가 없습니다. 먼저 분석을 실행하세요.")
        try:
            frames = max(1, min(6, int(payload.get("frames") or 3)))
        except (TypeError, ValueError):
            frames = 3

        # 앨범 레인으로 라우팅 — 같은 폴더에서 자동앨범과 직접요청이 동시에 돌아
        # _album 출력이 깨지는 것을 막는다(단일 레인이 직렬화). 이미 대기/실행 중이면
        # 그 job_id 를 돌려준다.
        jid = _enqueue_album(folder, frames)
        if jid is None:
            raise HTTPException(400, "앨범 작업 추가 실패 (요약/루트 확인)")
        return {"job_id": jid}

    @app.get("/files/{root_key}/{rel:path}")
    def serve_root_file(root_key: str, rel: str) -> Any:
        """다운로드 루트 안의 파일 서빙 — 앨범 html 의 상대 링크가 그대로 동작한다."""
        from ycollector.transcribe.config import MEDIA_EXTENSIONS

        root = _root_by_key(root_key)
        if root is None:
            raise HTTPException(404, "unknown root")
        target = _safe_join(root, rel)
        if target is None or not target.is_file():
            raise HTTPException(404, "file not found")
        suffix = target.suffix.lower()
        if suffix not in MEDIA_EXTENSIONS and suffix not in _SERVE_EXTRA_EXTENSIONS:
            raise HTTPException(404, "file not found")  # 화이트리스트 외 — 존재 여부도 숨김.
        media_type = _TEXT_MEDIA_TYPES.get(suffix)
        return FileResponse(target, media_type=media_type) if media_type else FileResponse(target)

    @app.get("/api/library")
    def library() -> dict[str, Any]:
        return _library_read()

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, Any]:
        return {
            "items": [r.to_dict() for r in
                      sorted(_state.jobs.values(), key=lambda r: -r.created_at)]
        }

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, Any]:
        """작업 카드(in-memory job)를 목록에서 제거. 실행 중이어도 카드만 치운다
        (잡 자체는 계속 진행). 새로고침 시 /api/jobs 복원에서도 빠진다."""
        _state.jobs.pop(job_id, None)
        return {"ok": True}

    # ── 작업 큐 (다운로드/전사 — 중단 시 관리·재시도) ──────────────────────
    @app.get("/api/queue")
    def queue_list() -> dict[str, Any]:
        tasks = sorted(_queue_read(), key=lambda t: -int(t.get("updated_at") or 0))
        counts: dict[str, int] = {s: 0 for s in _QUEUE_STATUSES}
        # 대기 작업이 '몇 번째'인지(전사 레인은 1개씩 순차) + 실행 중 작업의 라이브
        # 진행률/메시지를 붙여 UI 가 '지금 뭐가 도는지/왜 대기인지'를 보여주게 한다.
        wait_pos = 0
        for t in tasks:
            st = t.get("status", "")
            counts[st] = counts.get(st, 0) + 1
            job = _state.jobs.get(t.get("job_id") or "")
            if job is not None:
                t["live"] = {
                    "status": job.status, "progress": round(job.progress, 4),
                    "message": getattr(job, "message", "") or "",
                }
            if str(t.get("kind", "")).startswith("analyze") and st == "pending":
                wait_pos += 1
                t["wait_pos"] = wait_pos  # 전사 레인에서 몇 번째로 대기 중인지.
        lane_running = next((t for t in tasks if str(t.get("kind", "")).startswith("analyze")
                             and t.get("status") == "running"), None)
        lane = {
            "busy": lane_running is not None,
            "current": (lane_running or {}).get("title"),
            "current_msg": ((lane_running or {}).get("live") or {}).get("message"),
            "waiting": wait_pos,
        }
        return {"tasks": tasks, "counts": counts, "lane": lane}

    @app.post("/api/queue/{task_id}/retry")
    def queue_retry(task_id: str) -> dict[str, Any]:
        jid = _queue_requeue(task_id)  # 원자적 — 없거나 이미 대기·실행 중이면 None.
        if jid is None:
            raise HTTPException(409, "재시도 불가 (없는 작업이거나 이미 대기·실행 중)")
        return {"ok": True, "job_id": jid}

    @app.post("/api/queue/retry-all")
    def queue_retry_all() -> dict[str, Any]:
        with _QUEUE_LOCK:
            targets = [(t["id"], t.get("kind", ""), t.get("title", ""))
                       for t in _queue_read()
                       if t.get("status") in ("interrupted", "failed")]
        jobs: list[dict[str, str]] = []
        for tid, kind, title in targets:
            jid = _queue_requeue(tid)
            if jid:
                jobs.append({"task_id": tid, "job_id": jid, "kind": kind, "title": title})
        return {"ok": True, "retried": len(jobs), "jobs": jobs}

    @app.delete("/api/queue/{task_id}")
    def queue_delete(task_id: str) -> dict[str, Any]:
        with _QUEUE_LOCK:
            tasks = _queue_read()
            keep = [t for t in tasks if t.get("id") != task_id]
            if len(keep) == len(tasks):
                raise HTTPException(404, "작업 없음")
            # 실행 중인 건 큐에서 빼되 잡 자체는 그대로 둔다(목록 정리 용도).
            _queue_write(keep)
        return {"ok": True}

    @app.post("/api/queue/clear")
    def queue_clear() -> dict[str, Any]:
        with _QUEUE_LOCK:
            tasks = _queue_read()
            keep = [t for t in tasks if t.get("status") != "done"]
            _queue_write(keep)
        return {"ok": True, "removed": len(tasks) - len(keep)}

    @app.post("/api/queue/scan")
    def queue_scan(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """다운로드 루트를 스캔해 '전사 안 된' 미디어를 폴더별로 큐에 추가."""
        p = payload or {}
        do_sum = bool(p.get("summarize", True))
        lang = str(p.get("language") or "ko").strip().lower()
        lang_arg = None if lang in ("", "auto") else lang
        try:
            budget = float(p.get("budget_usd") or 5.0)
        except (TypeError, ValueError):
            budget = 5.0

        added = 0
        files_total = 0
        for root in _scan_overview().get("roots", []):
            for folder in root.get("folders", []):
                missing = [it["rel"] for it in folder.get("items", []) if not it.get("script")]
                if not missing:
                    continue
                label = f"{folder.get('name') or root['key']} — 미전사 {len(missing)}개"
                jid = uuid.uuid4().hex[:12]
                row = _JobRow(jid, "analyze", label)
                row.status = "queued"
                _remember_job(jid, row)
                spec = {"root": root["key"], "files": missing, "language": lang_arg,
                        "summarize": do_sum, "budget_usd": budget, "skip_existing": True}
                _queue_add(_new_task("analyze-files", label, spec, status="pending", job_id=jid))
                added += 1
                files_total += len(missing)
        if added:
            _analyze_lane_wake()
        return {"enqueued": added, "files": files_total}

    @app.post("/api/queue/scan-albums")
    def queue_scan_albums(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """전사·요약은 됐지만 앨범(_album/index.html)이 없는 폴더를 앨범 큐에 추가.
        이미 앨범이 있는 폴더(LJM 등)는 자동으로 건너뛴다."""
        try:
            frames = max(1, min(6, int((payload or {}).get("frames") or 3)))
        except (TypeError, ValueError):
            frames = 3
        added = 0
        for root in _scan_overview().get("roots", []):
            base = _root_by_key(root["key"])
            if base is None:
                continue
            for folder in root.get("folders", []):
                if folder.get("summarized", 0) <= 0 or folder.get("album"):
                    continue  # 요약 없음 → 앨범 불가 / 이미 앨범 있음 → 건너뜀.
                rel = folder.get("rel") or ""
                abs_folder = base if rel in ("", ".") else (base / rel)
                if _enqueue_album(abs_folder, frames):
                    added += 1
        return {"enqueued": added}

    return app


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _pick_free_port(preferred: int = 8765) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", preferred))
        s.close()
        return preferred
    except OSError:
        # OS 가 골라줌
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.bind(("127.0.0.1", 0))
        port = s2.getsockname()[1]
        s2.close()
        return port


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except (AttributeError, OSError):
                pass

    p = argparse.ArgumentParser(
        prog="ycollector-server",
        description="브라우저 UI 로컬 서버 (YouTube 다운로드 + Sora 2 Pro 영상 생성).",
    )
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true",
                   help="자동 브라우저 띄우기 비활성.")
    args = p.parse_args(argv)

    try:
        import uvicorn
    except ImportError as exc:
        print(
            "uvicorn 이 없습니다. `uv sync --extra web` 으로 설치하세요.\n"
            f"원인: {exc}",
            file=sys.stderr,
        )
        return 10

    app = _build_app()
    port = args.port if args.port != 0 else _pick_free_port(8765)
    url = f"http://{args.host}:{port}/"
    print(f"\n  ▶ YCollector server: {url}\n", file=sys.stderr)

    # HTTP 파서 선택: 기본은 빠른 httptools(있으면). 단 TLS 가로채기 환경에서
    # httptools 휠이 잘려 받아지면 import 는 되는데 HttpRequestParser 가 없어
    # 매 요청마다 죽는다. 그 경우 순수 파이썬 h11 로 폴백해 무조건 뜨게 한다.
    http_impl = "auto"
    try:
        import httptools  # noqa: F401
        if not hasattr(httptools, "HttpRequestParser"):
            raise ImportError("httptools 부분 설치(HttpRequestParser 없음)")
    except Exception as exc:
        http_impl = "h11"
        print(f"  ⓘ httptools 사용 불가({exc}) → h11 로 구동. "
              "복구:  uv pip install --reinstall --no-cache --native-tls httptools\n",
              file=sys.stderr)

    if not args.no_browser:
        # 서버 가동 직후 잠깐 뒤 브라우저 — 별 스레드에서 sleep.
        threading.Thread(
            target=lambda: (time.sleep(0.8), webbrowser.open(url)),
            daemon=True,
        ).start()

    uvicorn.run(app, host=args.host, port=port, http=http_impl, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
