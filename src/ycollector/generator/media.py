"""ffmpeg/ffprobe + Pillow 기반 미디어 유틸 (chain 모드용).

- 비디오 reference 의 첫 프레임 추출 (→ Sora input_reference 앵커 이미지)
- reference 이미지를 출력 해상도에 정확히 맞춤 (cover + center-crop)
- 생성된 클립들을 하나로 concat

ffmpeg/ffprobe 는 PATH 에서 찾는다(yt-dlp 와 동일 가정). 임포트/탐색은 lazy.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".gif"}


class MediaError(Exception):
    """ffmpeg 부재/실행 실패 등."""


# winget/choco 설치 시 PATH 누락이 잦아 흔한 위치 보조 탐색.
_WIN_HINTS = (Path(r"C:\ffmpeg\bin"),)


def _find(tool: str) -> str:
    exe = shutil.which(tool)
    if exe:
        return exe
    if sys.platform == "win32":
        for d in _WIN_HINTS:
            cand = d / f"{tool}.exe"
            if cand.is_file():
                return str(cand)
    raise MediaError(
        f"{tool} 를 찾지 못했습니다. FFmpeg 를 설치하고 PATH 에 추가하세요 "
        "(예: winget install Gyan.FFmpeg)."
    )


def find_ffmpeg() -> str:
    return _find("ffmpeg")


def find_ffprobe() -> str:
    return _find("ffprobe")


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def probe_duration(path: Path) -> float:
    """영상 길이(초). 실패 시 -1.0."""
    try:
        proc = subprocess.run(
            [find_ffprobe(), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True,
        )
        out = (proc.stdout or "").strip()
        return float(out) if out else -1.0
    except Exception:
        return -1.0


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        name = Path(cmd[0]).name
        raise MediaError(f"{name} 실패 (rc={proc.returncode}): {(proc.stderr or '').strip()[:400]}")


def extract_first_frame(video: Path, out: Path) -> Path:
    """비디오의 첫 프레임을 이미지로 저장 → 그 경로 반환."""
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([find_ffmpeg(), "-y", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(out)])
    if not out.is_file():
        raise MediaError(f"첫 프레임 추출 실패: {video}")
    return out


def extract_last_frame(video: Path, out: Path) -> Path:
    """비디오의 마지막 프레임을 이미지로 저장 (last-frame chaining 용).

    -sseof -1 로 끝 1초 구간만 디코드, -update 1 로 같은 파일에 매 프레임 덮어써
    최종적으로 마지막 프레임이 남는다.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    _run([find_ffmpeg(), "-y", "-sseof", "-1", "-i", str(video),
          "-update", "1", "-q:v", "2", str(out)])
    if not out.is_file():
        raise MediaError(f"마지막 프레임 추출 실패: {video}")
    return out


def resize_cover(src: Path, size: str, out_dir: Path) -> Path:
    """이미지를 'WxH' 에 정확히 맞춤(cover + center-crop). Pillow 없으면 원본 반환.

    OpenAI Videos API 는 input_reference 가 출력 size 와 일치할 것을 요구한다.
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return src
    try:
        w_s, h_s = size.lower().split("x")
        w, h = int(w_s), int(h_s)
    except (ValueError, AttributeError):
        return src
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:  # Pillow < 9.1
        resample = Image.LANCZOS  # type: ignore[attr-defined]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{src.stem}_{w}x{h}.jpg"
    with Image.open(src) as im:
        fitted = ImageOps.fit(im.convert("RGB"), (w, h), method=resample)
        fitted.save(out, format="JPEG", quality=92)
    return out


def concat_videos(clips: list[Path], out: Path) -> Path:
    """여러 mp4 를 하드컷으로 이어붙임. 독립 인코딩 클립이라 안전하게 재인코딩."""
    clips = [c for c in clips if c.is_file()]
    if not clips:
        raise MediaError("concat 할 클립이 없습니다.")
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) == 1:
        shutil.copyfile(clips[0], out)
        return out
    list_file = out.with_suffix(".concat.txt")
    lines = []
    for c in clips:
        p = str(c.resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{p}'")
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        _run([
            find_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-movflags", "+faststart", str(out),
        ])
    finally:
        try:
            list_file.unlink()
        except OSError:
            pass
    if not out.is_file():
        raise MediaError("concat 결과 파일이 없습니다.")
    return out
