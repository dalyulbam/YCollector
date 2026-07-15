"""산출물 렌더링 — 사람이 읽는 대본(script.md)과 요약(summary.md).

duck-typed 입력:
  * segments: ``.start`` / ``.end`` / ``.text`` 를 가진 객체들
  * summary:  ``.overview`` / ``.key_points`` / ``.sections``(각 .title/.start/.end/.summary)

화자 분리(diarization)는 현재 보류이므로 대본은 타임스탬프 단락 형태다
(추후 화자 라벨 `[화자1]` 추가 예정 — [[project_ljm_analysis]]).
"""

from __future__ import annotations

import base64
import html as _htmllib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


class _Seg(Protocol):
    start: float
    end: float
    text: str


def _hms(seconds: float) -> str:
    s = int(max(0.0, seconds))
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _dur_str(seconds: float) -> str:
    return f"{seconds / 60:.1f}분" if seconds else "?"


def render_script(
    segments: Sequence[_Seg],
    *,
    title: str,
    language: str,
    duration: float,
    speakers: Sequence[str] | None = None,
) -> str:
    has_spk = speakers is not None and len(speakers) == len(segments)
    note = (
        f"- (화자 구분: {len(set(speakers))}명 — 이름 없이 구분만)"  # type: ignore[arg-type]
        if has_spk
        else "- (화자 구분 없음 — 타임스탬프 단락)"
    )
    lines = [
        f"# 대본 — {title}",
        "",
        f"- 언어: {language}   길이: {_dur_str(duration)}   세그먼트: {len(segments)}개",
        note,
        "",
        "---",
        "",
    ]
    if has_spk:
        # 연속된 같은 화자 발화를 하나의 턴으로 묶는다.
        i = 0
        n = len(segments)
        while i < n:
            spk = speakers[i]  # type: ignore[index]
            start = segments[i].start
            buf: list[str] = []
            j = i
            while j < n and speakers[j] == spk:  # type: ignore[index]
                t = segments[j].text.strip()
                if t:
                    buf.append(t)
                j += 1
            text = " ".join(buf)
            if text:
                lines.append(f"**[{_hms(start)}] {spk}:** {text}")
                lines.append("")
            i = j
    else:
        for s in segments:
            text = s.text.strip()
            if text:
                lines.append(f"**[{_hms(s.start)}]** {text}")
                lines.append("")
    return "\n".join(lines)


def render_summary(summary, *, title: str, language: str, duration: float) -> str:  # noqa: ANN001
    lines = [
        f"# 요약 — {title}",
        "",
        f"- 언어: {language}   길이: {_dur_str(duration)}",
        "",
        "## 전체 요약",
        "",
        summary.overview.strip(),
        "",
        "## 핵심 포인트",
        "",
    ]
    for kp in summary.key_points:
        lines.append(f"- {kp.strip()}")
    lines += ["", "## 핵심 파트 (시간대별)", ""]
    for sec in summary.sections:
        lines.append(f"### [{sec.start} – {sec.end}] {sec.title.strip()}")
        lines.append("")
        lines.append(sec.summary.strip())
        lines.append("")
    return "\n".join(lines)


def write_reports(
    out_dir: Path,
    stem: str,
    segments: Sequence[_Seg],
    summary,  # noqa: ANN001 - VideoSummary | None
    *,
    title: str,
    language: str,
    duration: float,
) -> dict[str, Path]:
    """script.md (+ summary.md 가 있으면) 작성. 작성된 경로 dict 반환."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    script_path = out_dir / f"{stem}.script.md"
    script_path.write_text(
        render_script(segments, title=title, language=language, duration=duration),
        encoding="utf-8",
    )
    written["script"] = script_path

    if summary is not None:
        summary_path = out_dir / f"{stem}.summary.md"
        summary_path.write_text(
            render_summary(summary, title=title, language=language, duration=duration),
            encoding="utf-8",
        )
        written["summary"] = summary_path

    return written


# ── 전사 전문 + 화면 캡쳐 standalone HTML ────────────────────────────────────
# 전사하면 항상 <stem>.transcript.html 을 만든다. 전사 전문을 타임스탬프 태그
# (4:26 형태)와 함께 싣고, 영상이 있으면 일정 간격마다 그 시점 프레임을 ffmpeg 로
# 캡쳐해 인라인 삽입한다. 이미지는 base64 내장 → 단일 파일(어디서 열어도 그대로).
def _ts_label(sec: float, *, with_hours: bool) -> str:
    s = int(max(0.0, sec))
    if with_hours or s >= 3600:
        return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    return f"{s // 60:d}:{s % 60:02d}"


def _capture_data_uri(video: Path, t: float, tmp: Path) -> str | None:
    """ffmpeg 로 t초 프레임 1장 캡쳐 → base64 data URI. 실패 시 None."""
    from ycollector.transcribe.album import _capture  # ffmpeg 캡쳐 재사용.

    if _capture(video, t, tmp):
        try:
            return "data:image/jpeg;base64," + base64.b64encode(tmp.read_bytes()).decode("ascii")
        except OSError:
            return None
    return None


def _make_captures(
    video: Path | None, duration: float, target: int
) -> list[tuple[float, str | None]]:
    """(시각초, dataURI|None) 리스트. 영상 없거나 duration<=0 이면 빈 리스트."""
    if video is None or not Path(video).is_file() or duration <= 0:
        return []
    interval = min(90.0, max(15.0, duration / max(1, target)))  # 15~90초 사이로 clamp.
    times: list[float] = []
    t = 0.0
    while t < duration and len(times) < 160:  # 안전 상한.
        times.append(t)
        t += interval
    caps: list[tuple[float, str | None]] = []
    with tempfile.TemporaryDirectory() as td:
        for i, tt in enumerate(times):
            caps.append((tt, _capture_data_uri(Path(video), tt, Path(td) / f"c{i}.jpg")))
    return caps


def render_transcript_html(
    segments: Sequence[_Seg],
    *,
    captures: list[tuple[float, str | None]],
    title: str,
    language: str,
    duration: float,
    summary=None,  # noqa: ANN001 - VideoSummary | None
) -> str:
    wh = duration >= 3600

    def ts(sec: float) -> str:
        return _ts_label(sec, with_hours=wh)

    def esc(x: str) -> str:
        return _htmllib.escape(x or "", quote=True)

    parts: list[str] = [
        f'<header><h1>{esc(title)}</h1>',
        f'<div class="meta">언어 {esc(language)} · 길이 {duration / 60:.1f}분 · '
        f'세그먼트 {len(segments)}개 · 캡쳐 {sum(1 for _, u in captures if u)}장</div></header>',
    ]

    if summary is not None:
        parts.append('<section class="summary">')
        ov = (getattr(summary, "overview", "") or "").strip()
        if ov:
            parts.append(f'<p class="overview">{esc(ov)}</p>')
        kps = getattr(summary, "key_points", None) or []
        if kps:
            parts.append('<ul class="kp">' + "".join(f"<li>{esc(k.strip())}</li>" for k in kps) + "</ul>")
        parts.append("</section>")

    if captures:
        times = [t for t, _ in captures]
        parts.append('<div class="timeline">')
        for k, (t, uri) in enumerate(captures):
            t_next = times[k + 1] if k + 1 < len(times) else duration + 1.0
            blk = [s for s in segments if t <= s.start < t_next]
            if not blk and uri is None:
                continue
            shot = (
                f'<div class="shot"><img loading="lazy" src="{uri}" alt="{ts(t)}">'
                f'<span class="ts">{ts(t)}</span></div>'
                if uri
                else f'<div class="shot noimg"><span class="ts">{ts(t)}</span></div>'
            )
            lines = "".join(
                f'<p><span class="ts">{ts(s.start)}</span>{esc(s.text.strip())}</p>'
                for s in blk
                if s.text.strip()
            ) or '<p class="empty">(이 구간 발화 없음)</p>'
            parts.append(f'<section class="blk">{shot}<div class="lines">{lines}</div></section>')
        parts.append("</div>")
    else:
        parts.append('<div class="plain">')
        for s in segments:
            txt = s.text.strip()
            if txt:
                parts.append(f'<p><span class="ts">{ts(s.start)}</span>{esc(txt)}</p>')
        parts.append("</div>")

    return _T_PAGE.format(title=esc(title), css=_T_CSS, body="\n".join(parts))


def write_transcript_html(
    out_dir: Path,
    stem: str,
    segments: Sequence[_Seg],
    *,
    video: Path | None,
    title: str,
    language: str,
    duration: float,
    summary=None,  # noqa: ANN001
    target_captures: int = 40,
) -> Path:
    """전사 전문 + 인라인 캡쳐 standalone HTML(``<stem>.transcript.html``) 작성 후 경로 반환.

    영상이 없으면(오디오만) 캡쳐 없이 타임스탬프 전문만. 항상 파일을 만든다.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    captures = _make_captures(video, duration, target_captures)
    dest = out_dir / f"{stem}.transcript.html"
    dest.write_text(
        render_transcript_html(
            segments, captures=captures, title=title, language=language,
            duration=duration, summary=summary,
        ),
        encoding="utf-8",
    )
    return dest


_T_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;800&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body><main class="wrap">
{body}
</main></body>
</html>"""

_T_CSS = r"""
:root{--bg:#f1f5f9;--card:#fff;--fg:#1e293b;--muted:#64748b;--border:#e2e8f0;--accent:#0d9488;--radius:14px;--shadow:0 6px 24px rgb(15 23 42/.08)}
@media (prefers-color-scheme:dark){:root{--bg:#0b1120;--card:#131c2e;--fg:#e5e7eb;--muted:#9ca3af;--border:#1f2937;--shadow:0 6px 24px rgb(0 0 0/.5)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:'Noto Sans KR','Apple SD Gothic Neo','Malgun Gothic',-apple-system,sans-serif;line-height:1.7}
.wrap{max-width:1000px;margin:0 auto;padding:32px 20px 80px}
header h1{font-weight:800;font-size:1.6rem;line-height:1.35;margin:.2em 0}
.meta{color:var(--muted);font-size:.9rem}
.summary{margin:16px 0;padding:16px 18px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow)}
.summary .overview{margin:0 0 8px;white-space:pre-line}
.summary .kp{margin:0;padding-left:20px}
.summary .kp li{margin:.2em 0}
.timeline{margin-top:20px}
.blk{display:grid;grid-template-columns:340px 1fr;gap:20px;align-items:start;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);padding:16px;margin:16px 0}
.shot{position:sticky;top:16px}
.shot img{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:10px;background:#000;display:block}
.shot .ts{display:inline-block;margin-top:6px;color:#fff;background:var(--accent);border-radius:999px;padding:1px 10px;font-size:.8rem;font-weight:600;font-variant-numeric:tabular-nums}
.shot.noimg{background:var(--bg);border-radius:10px;padding:14px;text-align:center}
.lines p{margin:.15em 0;display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:baseline}
.lines .ts{color:var(--accent);font-size:.8rem;font-weight:600;font-variant-numeric:tabular-nums;cursor:default}
.lines .empty{color:var(--muted)}
.plain{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px;margin-top:20px}
.plain p{margin:.2em 0;display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:baseline}
.plain .ts{color:var(--accent);font-size:.8rem;font-weight:600;font-variant-numeric:tabular-nums}
@media (max-width:720px){.blk{grid-template-columns:1fr}.shot{position:static}}
"""


# ── 기존 산출물에서 HTML 백필(재전사 없이) ──────────────────────────────────
# 이 기능 이전에 전사돼 .md/.srt 만 있는 영상에도 캡쳐 HTML 을 만들어 준다.
# whisper 재실행 없이 기존 전사 파일(.json/.srt/.vtt, 없으면 .script.md)에서
# 세그먼트를 되살리고, .summary.md 가 있으면 상단 요약도 복원한다.
@dataclass
class _PSeg:
    start: float
    end: float
    text: str


_TS_RE = re.compile(
    r"(\d{1,3}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,3}:\d{2}[.,]\d{1,3})\s*-->\s*"
    r"(\d{1,3}:\d{2}:\d{2}[.,]\d{1,3}|\d{1,3}:\d{2}[.,]\d{1,3})"
)
# script.md 한 줄: **[H:MM:SS]** 본문  /  **[H:MM:SS] 화자1:** 본문
_SCRIPT_RE = re.compile(r"^\*\*\[(\d{1,3}:\d{2}:\d{2})\][^*]*\*\*\s*(.*)$")


def _ts_to_sec(s: str) -> float:
    parts = s.strip().replace(",", ".").split(":")
    sec = 0.0
    try:
        for p in parts:
            sec = sec * 60.0 + float(p)
    except ValueError:
        return 0.0
    return sec


def _parse_cues(text: str) -> list[_PSeg]:
    """SRT/VTT 큐 파싱 → 세그먼트. 인덱스줄·WEBVTT 헤더·NOTE·인라인 태그 무시."""
    segs: list[_PSeg] = []
    body = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    for block in re.split(r"\n[ \t]*\n", body):
        lines = block.split("\n")
        ts_i = None
        start = end = 0.0
        for i, ln in enumerate(lines):
            m = _TS_RE.search(ln)
            if m:
                ts_i, start, end = i, _ts_to_sec(m.group(1)), _ts_to_sec(m.group(2))
                break
        if ts_i is None:
            continue
        txt = " ".join(ln.strip() for ln in lines[ts_i + 1:] if ln.strip())
        txt = re.sub(r"<[^>]+>", "", txt).strip()  # VTT 인라인 태그 제거.
        if txt:
            segs.append(_PSeg(start, end, txt))
    return segs


def _parse_script_md(text: str) -> list[_PSeg]:
    """대본(script.md)에서 타임스탬프 단락 되살리기(최후 폴백 — 화자 라벨은 버림)."""
    segs: list[_PSeg] = []
    for ln in text.splitlines():
        m = _SCRIPT_RE.match(ln.strip())
        if m:
            t = _ts_to_sec(m.group(1))
            body = m.group(2).strip()
            if body:
                segs.append(_PSeg(t, t, body))
    return segs


def _load_transcript(
    analysis_dir: Path, stem: str
) -> tuple[list[_PSeg], str | None, float | None]:
    """(_analysis, stem) → (세그먼트, 언어|None, 길이초|None). json>srt>vtt>script.md 순."""
    jp = analysis_dir / f"{stem}.json"
    if jp.is_file():
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
            segs = [
                _PSeg(float(s["start"]), float(s["end"]), str(s.get("text", "")).strip())
                for s in data.get("segments", [])
                if str(s.get("text", "")).strip()
            ]
            if segs:
                dur = data.get("duration")
                return segs, data.get("language"), (float(dur) if dur else None)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    for ext in ("srt", "vtt"):
        p = analysis_dir / f"{stem}.{ext}"
        if p.is_file():
            try:
                segs = _parse_cues(p.read_text(encoding="utf-8"))
            except OSError:
                segs = []
            if segs:
                return segs, None, None
    sp = analysis_dir / f"{stem}.script.md"
    if sp.is_file():
        try:
            segs = _parse_script_md(sp.read_text(encoding="utf-8"))
        except OSError:
            segs = []
        if segs:
            return segs, None, None
    return [], None, None


def backfill_transcript_html(
    analysis_dir: Path,
    stem: str,
    *,
    video: Path | None,
    title: str | None = None,
    target_captures: int = 40,
    overwrite: bool = True,
) -> Path | None:
    """기존 전사 산출물에서 ``<stem>.transcript.html`` 을 재생성(전사 재실행 없음).

    전사물(.json/.srt/.vtt/.script.md)이 하나도 없으면 None. ``overwrite=False`` 면
    이미 HTML 이 있을 때 캡쳐를 다시 뜨지 않고 기존 경로를 그대로 돌려준다(일괄 스캔용).
    ``.summary.md`` 가 있으면 상단 요약과 언어/길이도 함께 복원한다.
    """
    dest = analysis_dir / f"{stem}.transcript.html"
    if dest.is_file() and not overwrite:
        return dest

    segments, language, duration = _load_transcript(analysis_dir, stem)
    if not segments:
        return None

    # .summary.md 가 있으면 상단 요약 + 언어/길이 폴백 복원(album 파서 재사용).
    summary = None
    smd = analysis_dir / f"{stem}.summary.md"
    if smd.is_file():
        try:
            from ycollector.transcribe.album import Book, parse_summary

            bk = Book(stem=stem)
            parse_summary(smd.read_text(encoding="utf-8"), bk)
            if language is None and bk.language and bk.language != "?":
                language = bk.language
            if (duration is None or duration <= 0) and bk.duration:
                duration = bk.duration
            if bk.overview or bk.key_points:
                summary = bk  # render 는 .overview / .key_points 만 사용.
        except (OSError, ValueError, ImportError):
            pass

    if duration is None or duration <= 0:
        duration = max((s.end for s in segments), default=0.0) or max(
            (s.start for s in segments), default=0.0
        )

    return write_transcript_html(
        analysis_dir, stem, segments, video=video, title=title or stem,
        language=language or "?", duration=duration, summary=summary,
        target_captures=target_captures,
    )
