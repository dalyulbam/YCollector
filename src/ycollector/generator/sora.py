"""OpenAI Sora 2 Pro provider.

Videos API (2026-05 기준):
    POST   /v1/videos                 → 잡 생성 (multipart 또는 JSON)
    GET    /v1/videos/{id}            → 상태 폴링
    GET    /v1/videos/{id}/content    → 완성 MP4 다운로드

⚠ 2026-09-24 sunset 예정. 본 어댑터는 Provider ABC 구현이라 후속 모델로 교체 용이.

reference 처리
--------------
사용자가 YouTube URL 등 영상을 reference 로 주면 (a) yt-dlp 로 thumbnail 만
받아 (b) 그 이미지를 input_reference 로 Sora 에 업로드한다. yt-dlp 가
이미 PATH 에 있으니 추가 의존성 0. 직접 이미지 URL 을 주면 그대로 fetch.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .base import (
    JobStatus,
    Provider,
    ProviderError,
    VideoJob,
    VideoRequest,
)

_API_BASE = "https://api.openai.com/v1"

# 어떤 이미지든 일단 받아두는 임시 폴더 (라이브러리 manifest 가 가리킬 수도).
_REF_CACHE_DIR = Path(tempfile.gettempdir()) / "ycollector-refs"


# ── TLS 신뢰저장소 ───────────────────────────────────────────────────────
_tls_injected = False


def _ensure_native_tls() -> None:
    """백신/프록시가 TLS 를 가로채는 환경(주로 Windows)에서 certifi 만으로는
    인증서 검증이 실패한다(UnknownIssuer). OS 인증서 저장소를 쓰도록 truststore 를
    1회 주입. truststore 가 없으면 조용히 통과(기존 certifi 경로 유지).

    참고: uv 설치 자체도 같은 이유로 막히면 `uv pip install --native-tls ...`
    또는 환경변수 UV_NATIVE_TLS=1 을 쓴다.
    """
    global _tls_injected
    if _tls_injected:
        return
    _tls_injected = True
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass


# ── 에러 분류 ─────────────────────────────────────────────────────────────
_SORA_ERROR_PATTERNS: dict[str, str] = {
    "content-policy":   r"content policy|moderation|unsafe|disallowed",
    "rate-limit":       r"rate limit|too many requests|HTTP 429",
    "quota":            r"insufficient[_ ]quota|billing",
    "invalid-input":    r"invalid|bad request|HTTP 400",
    "auth":             r"authentication|invalid api key|HTTP 401|HTTP 403",
    "server":           r"HTTP 5[0-9][0-9]|server error",
    "network":          r"connection|timeout|getaddrinfo",
}


def _classify(msg: str) -> str:
    for cat, pat in _SORA_ERROR_PATTERNS.items():
        if re.search(pat, msg, re.IGNORECASE):
            return cat
    return "unknown"


# ── reference 처리 ────────────────────────────────────────────────────────
# input_reference 로 허용하는 이미지 확장자 → MIME.
_IMAGE_EXTS: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _guess_image_mime(path: Path) -> str:
    return _IMAGE_EXTS.get(path.suffix.lower(), "image/jpeg")


def _validate_local_image(path: Path) -> Path:
    """로컬 파일이 실재하고 이미지 확장자인지 확인 후 그대로 반환."""
    if not path.is_file():
        raise ProviderError(f"로컬 이미지 파일이 없습니다: {path}", category="invalid-input")
    if path.suffix.lower() not in _IMAGE_EXTS:
        raise ProviderError(
            f"지원하지 않는 이미지 형식입니다: {path.name} "
            f"(허용: {', '.join(sorted(_IMAGE_EXTS))})",
            category="invalid-input",
        )
    return path


def _resolve_local(path: Path, out_dir: Path) -> Path:
    """로컬 파일 → 앵커 이미지 경로. 비디오면 첫 프레임 추출, 이미지면 검증."""
    if not path.is_file():
        raise ProviderError(f"로컬 reference 파일이 없습니다: {path}", category="invalid-input")
    from . import media
    if media.is_video(path):
        out_dir.mkdir(parents=True, exist_ok=True)
        frame = out_dir / f"{path.stem}_frame.jpg"
        try:
            return media.extract_first_frame(path, frame)
        except media.MediaError as exc:
            raise ProviderError(
                f"비디오 reference 의 프레임 추출 실패: {exc}", category="invalid-input"
            ) from exc
    return _validate_local_image(path)


def _file_uri_to_path(uri: str) -> Path:
    """file:// URI → 로컬 경로. Windows 드라이브레터/UNC 보정."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    p = unquote(parsed.path)
    if sys.platform == "win32":
        if re.match(r"^/[A-Za-z]:", p):
            p = p[1:]                        # /C:/x → C:/x
        elif parsed.netloc:
            p = f"//{parsed.netloc}{p}"      # file://server/share → //server/share
    return Path(p)


def _fetch_reference(url: str, out_dir: Path) -> Path:
    """reference(로컬 파일 또는 URL) → 로컬 이미지 파일 경로.

    우선순위:
      1. file:// URI          → 로컬 경로로 변환
      2. 존재하는 로컬 파일     → 그대로 사용 (예: C:\\...\\cat.jpg, ./cat.png, ~/cat.jpg)
      3. youtube/youtu.be URL  → yt-dlp 로 thumbnail 추출
      4. 그 외 http(s) URL     → httpx GET, content-type image/* 검증
    """
    _ensure_native_tls()
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = url.strip()
    low = raw.lower()

    if low.startswith("file://"):
        return _resolve_local(_file_uri_to_path(raw), out_dir)

    if not low.startswith(("http://", "https://")):
        # http(s) 가 아니면 로컬 파일 경로(이미지 또는 비디오)로 간주.
        p = Path(raw).expanduser()
        if p.is_file():
            return _resolve_local(p, out_dir)
        raise ProviderError(
            f"reference 를 해석할 수 없습니다: {raw!r}\n"
            "  존재하는 로컬 이미지/비디오 파일 경로이거나 http(s):// URL 이어야 합니다.",
            category="invalid-input",
        )

    if "youtube.com" in low or "youtu.be" in low:
        return _fetch_yt_thumbnail(raw, out_dir)
    return _fetch_image_url(raw, out_dir)


def _fetch_yt_thumbnail(url: str, out_dir: Path) -> Path:
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        raise ProviderError("yt-dlp 가 PATH 에 없습니다.", category="invalid-input")
    # %(id)s.jpg 로 저장.
    template = str(out_dir / "%(id)s.%(ext)s")
    cmd = [
        ytdlp, "--skip-download", "--write-thumbnail", "--no-warnings",
        "--convert-thumbnails", "jpg",
        "-o", template, url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise ProviderError(
            f"yt-dlp thumbnail 받기 실패: {proc.stderr.strip()[:200]}",
            category=_classify(proc.stderr),
        )
    # out_dir 에서 가장 최근 .jpg 찾기 (id 를 stdout 에서 파싱하기보다 단순).
    jpgs = sorted(out_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not jpgs:
        raise ProviderError("thumbnail 다운로드 완료됐는데 .jpg 가 안 보임.", category="unknown")
    return jpgs[0]


def _fetch_image_url(url: str, out_dir: Path) -> Path:
    try:
        import httpx
    except ImportError as exc:
        raise ProviderError(
            "httpx 가 없습니다. `uv sync --extra video-gen` 으로 설치하세요.",
            category="invalid-input",
        ) from exc
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        r = client.get(url)
        if r.status_code != 200:
            raise ProviderError(
                f"reference URL fetch 실패 HTTP {r.status_code}: {url}",
                category="network",
            )
        ct = r.headers.get("content-type", "")
        if not ct.startswith("image/"):
            raise ProviderError(
                f"이미지가 아닌 응답: content-type={ct!r}. URL: {url}",
                category="invalid-input",
            )
        ext = ct.split("/")[-1].split(";")[0] or "jpg"
        out_path = out_dir / f"ref_{abs(hash(url))}.{ext}"
        out_path.write_bytes(r.content)
        return out_path


def _dump(v: Any) -> dict[str, Any]:
    try:
        return v.model_dump()
    except Exception:
        return {}


# ── SoraProvider (OpenAI SDK 기반) ──────────────────────────────────────────
class SoraProvider(Provider):
    name = "sora"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")

    def _client(self):  # type: ignore[no-untyped-def]
        _ensure_native_tls()
        if not self.api_key:
            raise ProviderError(
                "OPENAI_API_KEY 가 설정되지 않았습니다. "
                ".env 또는 환경변수에 OPENAI_API_KEY=sk-... 를 추가하세요.",
                category="auth",
            )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError(
                "openai SDK 가 없습니다. `uv sync --extra video-gen`.",
                category="invalid-input",
            ) from exc
        return OpenAI(api_key=self.api_key)

    def _prepare_reference(self, req: VideoRequest) -> Path | None:
        """references[0] → 앵커 이미지(비디오면 첫 프레임) → 출력 size 로 리사이즈."""
        if not req.references:
            return None
        ref = _fetch_reference(req.references[0], _REF_CACHE_DIR)
        from . import media
        return media.resize_cover(ref, req.size, _REF_CACHE_DIR)

    # ── 잡 생성 ──────────────────────────────────────────────────────────
    def create(self, req: VideoRequest) -> VideoJob:
        client = self._client()
        ref_local = self._prepare_reference(req)
        kwargs: dict[str, Any] = {
            "model": req.model,
            "prompt": req.prompt,
            "seconds": str(req.seconds),  # API: '4'|'8'|'12' (문자열)
            "size": req.size,
        }
        try:
            if ref_local is not None and ref_local.is_file():
                with ref_local.open("rb") as f:
                    v = client.videos.create(
                        input_reference=(ref_local.name, f, _guess_image_mime(ref_local)),
                        **kwargs,
                    )
            else:
                v = client.videos.create(**kwargs)
        except Exception as exc:
            raise self._map_exc(exc, "create") from exc
        return VideoJob(
            id=v.id, provider=self.name, model=req.model, request=req,
            reference_local=ref_local, raw=_dump(v),
        )

    # ── 확장(extend): 완성된 영상 파일을 이어 연장 ────────────────────────
    def extend(self, prev_video: Path, prompt: str, seconds: int, model: str) -> VideoJob:
        client = self._client()
        prev = Path(prev_video)
        try:
            with prev.open("rb") as f:
                v = client.videos.extend(
                    video=(prev.name, f, "video/mp4"),
                    prompt=prompt, seconds=str(seconds),
                )
        except Exception as exc:
            raise self._map_exc(exc, "extend") from exc
        req = VideoRequest(prompt=prompt, seconds=seconds, model=model)
        return VideoJob(id=v.id, provider=self.name, model=model, request=req, raw=_dump(v))

    # ── 폴링 ────────────────────────────────────────────────────────────
    def poll(self, job: VideoJob) -> JobStatus:
        client = self._client()
        try:
            v = client.videos.retrieve(job.id)
        except Exception as exc:
            import openai
            if isinstance(exc, openai.APIConnectionError):
                # 일시적 네트워크 — 재시도하도록 in_progress 로 흘림.
                return JobStatus("in_progress", 0.0, f"poll 네트워크 재시도: {exc}")
            raise self._map_exc(exc, "poll") from exc
        status_map = {"queued": "queued", "in_progress": "in_progress",
                      "completed": "completed", "failed": "failed"}
        mapped = status_map.get(str(v.status), "in_progress")
        progress = float(v.progress or 0) / 100.0
        err_msg = ""
        err_cat = None
        if mapped == "failed":
            err = v.error
            err_msg = (getattr(err, "message", None) or "unknown failure") if err else "unknown failure"
            err_cat = _classify(err_msg)
        job.raw = _dump(v)
        return JobStatus(
            status=mapped,  # type: ignore[arg-type]
            progress=max(0.0, min(1.0, progress)),
            message=err_msg,
            error_category=err_cat,
        )

    # ── 다운로드 ────────────────────────────────────────────────────────
    def download(self, job: VideoJob, out_path: Path) -> Path:
        client = self._client()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = client.videos.download_content(job.id)
            content.write_to_file(str(out_path))
        except Exception as exc:
            raise self._map_exc(exc, "download") from exc
        job.output_path = out_path
        return out_path

    # ── openai 예외 → ProviderError (실제 코드/메시지 노출) ────────────────
    def _map_exc(self, exc: Exception, where: str) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        import openai
        if isinstance(exc, openai.APIStatusError):
            code = getattr(exc, "status_code", 0)
            msg = ""
            try:
                body = exc.response.json()
                err = body.get("error") if isinstance(body, dict) else None
                msg = (err.get("message") if isinstance(err, dict) else None) or str(body)
            except Exception:
                msg = getattr(exc, "message", "") or str(exc)
            if code == 429:
                cat = "rate-limit"
            elif code in (401, 403):
                cat = "auth"
            elif code >= 500:
                cat = "server"
            else:
                cat = _classify(f"{msg} HTTP {code}")
            return ProviderError(f"[{where}] HTTP {code}: {msg}", category=cat)
        if isinstance(exc, openai.APIConnectionError):
            return ProviderError(f"[{where}] 네트워크 오류: {exc}", category="network")
        return ProviderError(f"[{where}] {exc}", category=_classify(str(exc)))


# 모듈 단독 실행 시 — 환경/키 확인 진단.
def _self_check() -> None:
    print(f"OPENAI_API_KEY: {'set' if os.environ.get('OPENAI_API_KEY') else 'NOT SET'}",
          file=sys.stderr)


if __name__ == "__main__":
    _self_check()
