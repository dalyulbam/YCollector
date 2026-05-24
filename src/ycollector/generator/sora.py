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
        return _validate_local_image(_file_uri_to_path(raw))

    if not low.startswith(("http://", "https://")):
        # http(s) 가 아니면 로컬 파일 경로로 간주.
        p = Path(raw).expanduser()
        if p.is_file():
            return _validate_local_image(p)
        raise ProviderError(
            f"reference 를 해석할 수 없습니다: {raw!r}\n"
            "  존재하는 로컬 이미지 파일 경로이거나 http(s):// URL 이어야 합니다.",
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


# ── SoraProvider ──────────────────────────────────────────────────────────
class SoraProvider(Provider):
    name = "sora"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            # 지연 — 실제 호출 직전에 다시 확인.
            pass

    # ── 공통 헤더 ────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        if not self.api_key:
            raise ProviderError(
                "OPENAI_API_KEY 가 설정되지 않았습니다. "
                ".env 또는 환경변수에 OPENAI_API_KEY=sk-... 를 추가하세요.",
                category="auth",
            )
        return {"Authorization": f"Bearer {self.api_key}"}

    def _client(self):  # type: ignore[no-untyped-def]
        _ensure_native_tls()
        try:
            import httpx
        except ImportError as exc:
            raise ProviderError(
                "httpx 가 없습니다. `uv sync --extra video-gen`.",
                category="invalid-input",
            ) from exc
        return httpx.Client(base_url=_API_BASE, headers=self._headers(), timeout=60.0)

    # ── 잡 생성 ──────────────────────────────────────────────────────────
    def create(self, req: VideoRequest) -> VideoJob:
        # reference 1개만 사용 (Sora input_reference 슬롯 1개). 여러 개를 받았다면
        # 호출자(cli/server)가 reference 당 1잡으로 분할해야 함 — 여기선 단일.
        ref_local: Path | None = None
        if req.references:
            ref_local = _fetch_reference(req.references[0], _REF_CACHE_DIR)

        # request body. Videos API 는 multipart/form-data 권장 (input_reference 가
        # 파일 업로드면). JSON 으로도 가능하나 input_reference 가 있을 땐 multipart.
        data = {
            "model": req.model,
            "prompt": req.prompt,
            "size": req.size,
            "seconds": str(req.seconds),
        }
        if req.seed is not None:
            data["seed"] = str(req.seed)

        with self._client() as client:
            try:
                if ref_local is not None and ref_local.is_file():
                    with ref_local.open("rb") as f:
                        mime = _guess_image_mime(ref_local)
                        files = {"input_reference": (ref_local.name, f, mime)}
                        r = client.post("/videos", data=data, files=files)
                else:
                    r = client.post("/videos", json=data)
            except Exception as exc:
                raise ProviderError(f"Sora create 네트워크 오류: {exc}", category="network") from exc

        if r.status_code >= 400:
            raise self._raise_for(r)
        body = r.json()
        job_id = body.get("id")
        if not job_id:
            raise ProviderError(f"Sora 응답에 id 없음: {body}", category="server")

        return VideoJob(
            id=job_id,
            provider=self.name,
            model=req.model,
            request=req,
            reference_local=ref_local,
            raw=body,
        )

    # ── 폴링 ────────────────────────────────────────────────────────────
    def poll(self, job: VideoJob) -> JobStatus:
        with self._client() as client:
            try:
                r = client.get(f"/videos/{job.id}")
            except Exception as exc:
                # 일시적 네트워크 오류 — 호출자가 재시도하도록 in_progress 로 흘림.
                return JobStatus(status="in_progress", progress=0.0,
                                 message=f"poll 네트워크 오류: {exc}")
        if r.status_code >= 400:
            raise self._raise_for(r)
        body = r.json()
        status_raw = (body.get("status") or "queued").lower()
        status_map = {
            "queued": "queued",
            "in_progress": "in_progress",
            "processing": "in_progress",
            "completed": "completed",
            "failed": "failed",
            "canceled": "cancelled",
            "cancelled": "cancelled",
        }
        mapped = status_map.get(status_raw, "in_progress")
        progress_raw = body.get("progress")
        if isinstance(progress_raw, (int, float)):
            progress = float(progress_raw) / 100.0 if progress_raw > 1 else float(progress_raw)
        else:
            progress = 0.0 if mapped == "queued" else (1.0 if mapped == "completed" else 0.5)
        err_msg = ""
        err_cat = None
        if mapped == "failed":
            err = body.get("error") or {}
            err_msg = err.get("message") or "unknown failure"
            err_cat = _classify(err_msg)
        # raw 업데이트(다음 호출자가 cost/eta 등 보고 싶을 수 있음).
        job.raw = body
        return JobStatus(
            status=mapped,  # type: ignore[arg-type]
            progress=max(0.0, min(1.0, progress)),
            message=err_msg or body.get("status_message", ""),
            error_category=err_cat,
        )

    # ── 다운로드 ────────────────────────────────────────────────────────
    def download(self, job: VideoJob, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with self._client() as client:
            try:
                with client.stream("GET", f"/videos/{job.id}/content") as r:
                    if r.status_code >= 400:
                        # body 가 small 이므로 read 후 raise.
                        r.read()
                        raise self._raise_for(r)
                    with out_path.open("wb") as f:
                        for chunk in r.iter_bytes(chunk_size=1024 * 64):
                            f.write(chunk)
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderError(f"download 네트워크 오류: {exc}", category="network") from exc
        job.output_path = out_path
        return out_path

    # ── 취소 ────────────────────────────────────────────────────────────
    def cancel(self, job: VideoJob) -> bool:
        with self._client() as client:
            try:
                r = client.post(f"/videos/{job.id}/cancel")
            except Exception:
                return False
        return r.status_code < 400

    # ── 에러 매핑 헬퍼 ──────────────────────────────────────────────────
    def _raise_for(self, r: Any) -> ProviderError:
        try:
            body = r.json()
        except Exception:
            body = {"raw_text": r.text[:400]}
        msg = ""
        if isinstance(body, dict):
            err = body.get("error") or {}
            if isinstance(err, dict):
                msg = err.get("message") or str(err)
            else:
                msg = str(err)
        if not msg:
            msg = f"HTTP {r.status_code}"
        return ProviderError(msg, category=_classify(msg + f" HTTP {r.status_code}"))


# 모듈 단독 실행 시 — 환경/키 확인 진단.
def _self_check() -> None:
    print(f"OPENAI_API_KEY: {'set' if os.environ.get('OPENAI_API_KEY') else 'NOT SET'}",
          file=sys.stderr)


if __name__ == "__main__":
    _self_check()
