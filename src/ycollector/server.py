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
        self.kind = kind  # "download" | "generate"
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

    engine = YtdlpEngine()
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
        _state.jobs[jid] = row
        threading.Thread(
            target=_run_download,
            args=(row, url, settings_dict or None),
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
            _state.jobs[jid] = row
            threading.Thread(
                target=_run_generate_continuous,
                args=(row, prompts_clean, refs, model, size, seconds, seed_i),
                daemon=True,
            ).start()
            return {"job_ids": [jid], "chain": True}

        # 단일 생성(프롬프트 1개 + reference 0~1개).
        jid = uuid.uuid4().hex[:12]
        row = _JobRow(jid, "generate", combined)
        _state.jobs[jid] = row
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

    @app.get("/api/library")
    def library() -> dict[str, Any]:
        return _library_read()

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, Any]:
        return {
            "items": [r.to_dict() for r in
                      sorted(_state.jobs.values(), key=lambda r: -r.created_at)]
        }

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

    if not args.no_browser:
        # 서버 가동 직후 잠깐 뒤 브라우저 — 별 스레드에서 sleep.
        threading.Thread(
            target=lambda: (time.sleep(0.8), webbrowser.open(url)),
            daemon=True,
        ).start()

    uvicorn.run(app, host=args.host, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
