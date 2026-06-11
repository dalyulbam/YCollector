"""HTML 앨범북 렌더 — 장면 캡쳐 + 대화.

``analyze`` / ``diarize`` 산출물(`_analysis/<영상>.summary.md`, `.script.md`)과
원본 영상을 묶어, **핵심 파트(장면)마다 영상 프레임을 캡쳐**하고 그 장면 시간대의
**화자별 대화**를 함께 싣는 2단 레이아웃 HTML 앨범을 만든다.

흐름
----
1. summary.md 파싱 → 메타 + 전체요약 + 핵심포인트 + 섹션[(start,end,제목,요약)]
2. script.md  파싱 → 화자 대화 턴[(start,화자,텍스트)]
3. 섹션마다 ffmpeg 빠른 seek 으로 N장 캡쳐(720px JPEG) → bookNN/images/
4. 2단 카드(좌: 메인+썸네일 / 우: 제목·시간·요약·대화) HTML 생성
5. 9권을 잇는 표지 갤러리 _album/index.html

출력: ``_album/bookNN/index.html`` + ``images/`` , ``_album/index.html``.
폴더/이미지명은 ASCII(bookNN)로 — 한글 제목은 HTML 본문에 표시.

사용:
    uv run --native-tls ycollector album --analysis-dir "D:/.../LJM/_analysis"
    (또는)  .venv\\Scripts\\python.exe -m ycollector.transcribe.album --analysis-dir ...
"""

from __future__ import annotations

import argparse
import base64
import html
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_VIDEO_EXTS = (".webm", ".mp4", ".mkv", ".mov", ".m4a", ".mp3", ".wav", ".flac", ".aac", ".opus", ".ogg")
_SECTION_RE = re.compile(r"^###\s*\[(\d+:\d{2}:\d{2})\s*[–\-~]\s*(\d+:\d{2}:\d{2})\]\s*(.+?)\s*$")
_TURN_RE = re.compile(r"^\*\*\[(\d+:\d{2}:\d{2})\]\s*(.+?):\*\*\s*(.+)$")
_LANG_RE = re.compile(r"언어:\s*([^\s]+)")
_DUR_RE = re.compile(r"길이:\s*([\d.]+)\s*분")

# 화자 칩 색상 팔레트(12색 순환).
_PALETTE = [
    "#4f46e5", "#0d9488", "#ea580c", "#db2777", "#2563eb", "#16a34a",
    "#9333ea", "#dc2626", "#0891b2", "#ca8a04", "#7c3aed", "#65a30d",
]


def _to_sec(hms: str) -> float:
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def _hms(sec: float) -> str:
    s = int(max(0.0, sec))
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


@dataclass
class Section:
    start: float
    end: float
    title: str
    summary: str = ""


@dataclass
class Turn:
    start: float
    speaker: str
    text: str


@dataclass
class Book:
    stem: str
    language: str = "?"
    duration: float = 0.0
    overview: str = ""
    key_points: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)


# ── 파싱 ────────────────────────────────────────────────────────────────────
def parse_summary(md: str, book: Book) -> None:
    lang = _LANG_RE.search(md)
    dur = _DUR_RE.search(md)
    if lang:
        book.language = lang.group(1)
    if dur:
        book.duration = float(dur.group(1)) * 60.0

    lines = md.splitlines()
    section_idx = [i for i, ln in enumerate(lines) if ln.startswith("## ")]

    def block(header_kw: str) -> list[str]:
        for k, i in enumerate(section_idx):
            if header_kw in lines[i]:
                end = section_idx[k + 1] if k + 1 < len(section_idx) else len(lines)
                return lines[i + 1:end]
        return []

    book.overview = "\n".join(block("전체 요약")).strip()
    book.key_points = [
        ln[1:].strip() for ln in block("핵심 포인트") if ln.strip().startswith("-")
    ]

    # 핵심 파트: ### [s – e] 제목 + 이후 요약 단락.
    part_lines = block("핵심 파트")
    cur: Section | None = None
    buf: list[str] = []
    for ln in part_lines:
        m = _SECTION_RE.match(ln)
        if m:
            if cur is not None:
                cur.summary = "\n".join(buf).strip()
                book.sections.append(cur)
            cur = Section(_to_sec(m.group(1)), _to_sec(m.group(2)), m.group(3))
            buf = []
        elif cur is not None:
            buf.append(ln)
    if cur is not None:
        cur.summary = "\n".join(buf).strip()
        book.sections.append(cur)


def parse_script(md: str, book: Book) -> None:
    for ln in md.splitlines():
        m = _TURN_RE.match(ln)
        if m:
            book.turns.append(Turn(_to_sec(m.group(1)), m.group(2).strip(), m.group(3).strip()))


# ── 프레임 캡쳐 ──────────────────────────────────────────────────────────────
def _capture(video: Path, t: float, out: Path) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-nostdin", "-ss", f"{max(0.0, t):.2f}", "-i", str(video),
        "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3", "-y", str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return out.is_file() and out.stat().st_size > 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _frame_times(sec: Section, n: int) -> list[float]:
    span = max(0.0, sec.end - sec.start)
    if n <= 1 or span < 2:
        return [sec.start + span / 2]
    # 시작/끝 경계의 전환·암전 프레임을 피해 안쪽으로.
    pad = min(1.5, span * 0.1)
    lo, hi = sec.start + pad, sec.end - pad
    return [lo + (hi - lo) * k / (n - 1) for k in range(n)]


# ── HTML ────────────────────────────────────────────────────────────────────
def _img_src(path: Path, book_dir: Path, embed: bool) -> str:
    if embed:
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:image/jpeg;base64,{data}"
    return path.relative_to(book_dir).as_posix()


def _esc(s: str) -> str:
    return html.escape(s, quote=True)


# 썸네일 클릭 → 같은 .frames 안의 hero 교체(전역 함수/ id 불필요 → 합본에서도 충돌 없음).
_SWAP = "this.closest('.frames').querySelector('.hero').src=this.src"


def _book_body(
    book: Book,
    scene_images: list[list[Path]],
    book_dir: Path | None,
    *,
    embed: bool,
    top_link: str = "",
    anchor: str = "",
) -> str:
    """한 권의 본문(헤더 + 장면 카드들) HTML. _PAGE 래핑 없음."""
    spk_order: dict[str, int] = {}
    for t in book.turns:
        spk_order.setdefault(t.speaker, len(spk_order))

    def spk_color(name: str) -> str:
        return _PALETTE[spk_order.get(name, 0) % len(_PALETTE)]

    aid = f' id="{anchor}"' if anchor else ""
    parts: list[str] = [f'<header class="book-head"{aid}>']
    if top_link:
        parts.append(f"  {top_link}")
    parts.append(f"  <h1>{_esc(book.stem)}</h1>")
    parts.append(
        f'  <div class="meta">언어 {_esc(book.language)} · 길이 {book.duration/60:.1f}분 · '
        f'화자 {len(spk_order)}명 · 장면 {len(book.sections)}개</div>'
    )
    if book.overview:
        parts.append(f'  <p class="overview">{_esc(book.overview)}</p>')
    if book.key_points:
        parts.append('  <ul class="keypoints">')
        parts += [f"    <li>{_esc(kp)}</li>" for kp in book.key_points]
        parts.append("  </ul>")
    parts.append("</header>")

    for idx, sec in enumerate(book.sections):
        imgs = scene_images[idx] if idx < len(scene_images) else []
        turns = [t for t in book.turns if sec.start <= t.start < sec.end] or \
                [t for t in book.turns if sec.start - 2 <= t.start <= sec.end + 2]
        hero = imgs[len(imgs) // 2] if imgs else None
        hero_src = _img_src(hero, book_dir, embed) if hero else ""
        thumbs = "".join(
            f'<img class="thumb" src="{_img_src(p, book_dir, embed)}" onclick="{_SWAP}" alt="">'
            for p in imgs
        )
        dialogue = "".join(
            f'<div class="turn"><span class="spk" style="background:{spk_color(t.speaker)}">{_esc(t.speaker)}</span>'
            f'<span class="ts">{_hms(t.start)}</span><p>{_esc(t.text)}</p></div>'
            for t in turns
        ) or '<div class="turn empty">(이 장면 구간의 대화 없음)</div>'
        hero_html = (
            f'<img class="hero" src="{hero_src}" alt="장면 {idx+1}">'
            if hero else '<div class="hero noimg">캡쳐 없음</div>'
        )
        parts.append(f"""<section class="scene">
  <div class="frames">
    {hero_html}
    <div class="thumbs">{thumbs}</div>
  </div>
  <div class="content">
    <div class="scene-time">장면 {idx+1} · {_hms(sec.start)}–{_hms(sec.end)}</div>
    <h2>{_esc(sec.title)}</h2>
    <p class="scene-summary">{_esc(sec.summary)}</p>
    <div class="dialogue">{dialogue}</div>
  </div>
</section>""")
    return "\n".join(parts)


def render_book_html(book: Book, scene_images: list[list[Path]], book_dir: Path, *, embed: bool) -> str:
    top = '<a class="back" href="../index.html">← 앨범 목록</a>'
    body = _book_body(book, scene_images, book_dir, embed=embed, top_link=top)
    return _PAGE.format(title=_esc(book.stem), css=_CSS, body=body)


def render_standalone(entries: list[tuple[Book, list[list[Path]]]], *, title: str) -> str:
    """모든 권을 이미지 내장(base64)으로 한 파일에 합치고 상단에 목차(TOC)."""
    toc = [
        '<header class="index-head" id="top">',
        f"  <h1>{_esc(title)}</h1>",
        f'  <div class="meta">{len(entries)}권 · 장면 캡쳐 + 화자별 대화 · 단일 파일(이미지 내장)</div>',
        '  <ol class="toc">',
    ]
    for i, (book, _imgs) in enumerate(entries, 1):
        n_spk = len({t.speaker for t in book.turns})
        toc.append(
            f'    <li><a href="#book{i}">{_esc(book.stem)}</a> '
            f'<span class="meta">· {book.duration/60:.1f}분 · 장면 {len(book.sections)}개 · 화자 {n_spk}명</span></li>'
        )
    toc += ["  </ol>", "</header>"]

    bodies = []
    for i, (book, scene_images) in enumerate(entries, 1):
        top = '<a class="back" href="#top">↑ 목차</a>'
        body = _book_body(book, scene_images, None, embed=True, top_link=top, anchor=f"book{i}")
        bodies.append(f'<article class="book">\n{body}\n</article>')
    return _PAGE.format(title=_esc(title), css=_CSS, body="\n".join(toc) + "\n" + "\n".join(bodies))


def _build_standalone(
    summaries: list[Path], analysis_dir: Path, video_dir: Path, album_dir: Path,
    out_path: Path, frames: int,
) -> int:
    """기존 _album 이미지를 재사용(없으면 캡쳐)해 단일 합본 HTML 작성."""
    entries: list[tuple[Book, list[list[Path]]]] = []
    for i, sm in enumerate(summaries, 1):
        stem = sm.name[: -len(".summary.md")]
        book = Book(stem=stem)
        parse_summary(sm.read_text(encoding="utf-8"), book)
        sp = analysis_dir / f"{stem}.script.md"
        if sp.is_file():
            parse_script(sp.read_text(encoding="utf-8"), book)
        if not book.sections:
            print(f"  [{i}] 섹션 없음 — 건너뜀", file=sys.stderr)
            continue
        img_dir = album_dir / f"book{i:02d}" / "images"
        video = next((video_dir / f"{stem}{e}" for e in _VIDEO_EXTS if (video_dir / f"{stem}{e}").is_file()), None)
        scene_images: list[list[Path]] = []
        for sidx, sec in enumerate(book.sections):
            imgs = sorted(img_dir.glob(f"scene{sidx+1:02d}_*.jpg"))
            if not imgs and video is not None:  # 캐시 없으면 즉석 캡쳐.
                for k, t in enumerate(_frame_times(sec, frames)):
                    op = img_dir / f"scene{sidx+1:02d}_{k+1}.jpg"
                    if _capture(video, t, op):
                        imgs.append(op)
            scene_images.append(imgs)
        n = sum(len(x) for x in scene_images)
        print(f"  [{i}/{len(summaries)}] {stem[:42]} — 장면 {len(book.sections)} · 이미지 {n}", file=sys.stderr)
        entries.append((book, scene_images))

    html_text = render_standalone(entries, title="이재명 대통령 간담회·타운홀 — 장면 앨범북")
    out_path.write_text(html_text, encoding="utf-8")
    print(f"\n[완료] 단일 파일 {out_path}  ({out_path.stat().st_size/1e6:.1f} MB)", file=sys.stderr)
    return 0


def render_index_html(books: list[tuple[Book, str, Path | None]], out_dir: Path, *, embed: bool) -> str:
    cards = []
    for book, slug, cover in books:
        cover_src = ""
        if cover is not None:
            cover_src = (
                f'data:image/jpeg;base64,{base64.b64encode(cover.read_bytes()).decode("ascii")}'
                if embed else f"{slug}/{cover.relative_to(out_dir / slug).as_posix()}"
            )
        cover_html = (
            f'<img src="{cover_src}" alt="">' if cover_src else '<div class="noimg">표지 없음</div>'
        )
        cards.append(f"""<a class="cover" href="{slug}/index.html">
  <div class="cover-img">{cover_html}</div>
  <div class="cover-cap">
    <h3>{_esc(book.stem)}</h3>
    <div class="meta">{book.duration/60:.1f}분 · 장면 {len(book.sections)}개</div>
  </div>
</a>""")
    body = f"""<header class="index-head">
  <h1>이재명 대통령 간담회·타운홀 — 장면 앨범북</h1>
  <div class="meta">{len(books)}권 · 영상별 장면 캡쳐 + 화자별 대화</div>
</header>
<div class="gallery">
{''.join(cards)}
</div>"""
    return _PAGE.format(title="장면 앨범북", css=_CSS, body=body)


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
            except (AttributeError, OSError):
                pass

    p = argparse.ArgumentParser(
        prog="ycollector album",
        description="analyze/diarize 산출물 + 원본 영상 → 장면 캡쳐 HTML 앨범북.",
    )
    p.add_argument("--analysis-dir", type=Path, required=True,
                   help="*.summary.md / *.script.md 가 있는 폴더(예: .../LJM/_analysis).")
    p.add_argument("--video-dir", type=Path, default=None,
                   help="원본 영상 폴더(기본: analysis-dir 의 상위).")
    p.add_argument("-o", "--output-dir", type=Path, default=None,
                   help="앨범 출력 폴더(기본: video-dir/_album).")
    p.add_argument("--frames", type=int, default=3, help="장면당 캡쳐 수(기본 3).")
    p.add_argument("--embed", action="store_true",
                   help="이미지를 HTML에 base64로 내장(권별 단일 파일, 용량 큼).")
    p.add_argument("--standalone", type=Path, default=None,
                   help="9권 전체를 이미지 내장(base64)한 '단 하나의' HTML로 이 경로에 출력.")
    p.add_argument("--album-dir", type=Path, default=None,
                   help="단일 합본 시 재사용할 기존 앨범 폴더(기본: 출력 폴더). 이미지 없으면 캡쳐.")
    args = p.parse_args(argv)

    analysis_dir: Path = args.analysis_dir
    video_dir: Path = args.video_dir or analysis_dir.parent
    out_dir: Path = args.output_dir or (video_dir / "_album")
    out_dir.mkdir(parents=True, exist_ok=True)

    summaries = sorted(analysis_dir.glob("*.summary.md"))
    if not summaries:
        print(f"[안내] {analysis_dir} 에 *.summary.md 가 없습니다.", file=sys.stderr)
        return 0

    # 단일 합본 모드: 9권 전체를 이미지 내장(base64)한 '한 파일'로.
    if args.standalone:
        album_dir = args.album_dir or out_dir
        return _build_standalone(
            summaries, analysis_dir, video_dir, album_dir, args.standalone, args.frames
        )

    index_entries: list[tuple[Book, str, Path | None]] = []
    for i, sm in enumerate(summaries, 1):
        stem = sm.name[: -len(".summary.md")]
        script = analysis_dir / f"{stem}.script.md"
        video = next((video_dir / f"{stem}{e}" for e in _VIDEO_EXTS if (video_dir / f"{stem}{e}").is_file()), None)
        print(f"\n[{i}/{len(summaries)}] {stem[:48]}", file=sys.stderr)
        if video is None:
            print(f"  ✗ 원본 영상 없음(video-dir={video_dir}) — 건너뜀", file=sys.stderr)
            continue

        book = Book(stem=stem)
        parse_summary(sm.read_text(encoding="utf-8"), book)
        if script.is_file():
            parse_script(script.read_text(encoding="utf-8"), book)
        if not book.sections:
            print("  ✗ 섹션 파싱 실패 — 건너뜀", file=sys.stderr)
            continue

        slug = f"book{i:02d}"
        book_dir = out_dir / slug
        img_dir = book_dir / "images"
        scene_images: list[list[Path]] = []
        for sidx, sec in enumerate(book.sections):
            imgs: list[Path] = []
            for k, t in enumerate(_frame_times(sec, args.frames)):
                op = img_dir / f"scene{sidx+1:02d}_{k+1}.jpg"
                if _capture(video, t, op):
                    imgs.append(op)
            scene_images.append(imgs)
        n_imgs = sum(len(x) for x in scene_images)
        print(f"  ✓ 장면 {len(book.sections)}개 · 캡쳐 {n_imgs}장 · 화자 {len({t.speaker for t in book.turns})}명", file=sys.stderr)

        html_text = render_book_html(book, scene_images, book_dir, embed=args.embed)
        (book_dir / "index.html").write_text(html_text, encoding="utf-8")
        cover = scene_images[0][0] if scene_images and scene_images[0] else None
        index_entries.append((book, slug, cover))

    (out_dir / "index.html").write_text(
        render_index_html(index_entries, out_dir, embed=args.embed), encoding="utf-8"
    )
    print(f"\n[완료] {len(index_entries)}권 → {out_dir.resolve()}\\index.html", file=sys.stderr)
    return 0


# ── 스타일/템플릿 (docs 테마 차용: 다크모드·한국어 폰트) ──────────────────────
_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;600;800&display=swap" rel="stylesheet">
<style>{css}</style>
</head>
<body>
<main class="wrap">
{body}
</main>
</body>
</html>"""

_CSS = r"""
:root{--bg:#f1f5f9;--card:#fff;--fg:#1e293b;--muted:#64748b;--border:#e2e8f0;--accent:#4f46e5;--radius:14px;--shadow:0 6px 24px rgb(15 23 42/.08)}
@media (prefers-color-scheme:dark){:root{--bg:#0b1120;--card:#131c2e;--fg:#e5e7eb;--muted:#9ca3af;--border:#1f2937;--shadow:0 6px 24px rgb(0 0 0/.5)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:'Noto Sans KR','Apple SD Gothic Neo','Malgun Gothic',-apple-system,sans-serif;line-height:1.7}
.wrap{max-width:1080px;margin:0 auto;padding:32px 20px 80px}
a{color:inherit}
.back{display:inline-block;color:var(--muted);text-decoration:none;font-size:.9rem;margin-bottom:12px}
.book-head h1,.index-head h1{font-weight:800;font-size:1.6rem;line-height:1.35;margin:.2em 0}
.meta{color:var(--muted);font-size:.9rem}
.book-head .overview{margin:14px 0;padding:16px 18px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow)}
.keypoints{margin:0 0 8px;padding-left:20px}
.keypoints li{margin:.2em 0}
/* 장면 카드 — 2단 */
.scene{display:grid;grid-template-columns:380px 1fr;gap:22px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px;margin:22px 0}
.frames .hero{width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:10px;background:#000}
.frames .hero.noimg,.cover .noimg{display:flex;align-items:center;justify-content:center;color:var(--muted);background:var(--bg);aspect-ratio:16/9;border-radius:10px}
.thumbs{display:flex;gap:8px;margin-top:8px}
.thumb{width:33%;aspect-ratio:16/9;object-fit:cover;border-radius:6px;cursor:pointer;opacity:.8;transition:opacity .15s,outline .15s;outline:2px solid transparent}
.thumb:hover{opacity:1;outline:2px solid var(--accent)}
.scene-time{color:var(--accent);font-weight:600;font-size:.85rem}
.content h2{font-size:1.15rem;margin:.2em 0 .4em}
.scene-summary{color:var(--fg);margin:.2em 0 1em;white-space:pre-line}
.dialogue{display:flex;flex-direction:column;gap:8px;max-height:340px;overflow:auto;padding-right:6px}
.turn{display:grid;grid-template-columns:auto auto 1fr;gap:8px;align-items:start;font-size:.92rem}
.turn .spk{color:#fff;border-radius:999px;padding:1px 9px;font-size:.78rem;font-weight:600;white-space:nowrap}
.turn .ts{color:var(--muted);font-size:.78rem;font-variant-numeric:tabular-nums;padding-top:1px}
.turn p{margin:0}
.turn.empty{color:var(--muted)}
/* 표지 갤러리 */
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:18px;margin-top:22px}
.cover{display:block;text-decoration:none;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden;box-shadow:var(--shadow);transition:transform .15s}
.cover:hover{transform:translateY(-3px)}
.cover-img img,.cover-img .noimg{width:100%;aspect-ratio:16/9;object-fit:cover;display:block}
.cover-cap{padding:12px 14px}
.cover-cap h3{margin:0 0 4px;font-size:.98rem;font-weight:600;line-height:1.4}
/* 단일 합본: 목차 + 권 구분 */
.toc{margin:14px 0 0;padding-left:22px}
.toc li{margin:.35em 0}
.toc a{font-weight:600;text-decoration:none}
.toc a:hover{text-decoration:underline}
.book{border-top:3px solid var(--accent);margin-top:48px;padding-top:6px}
.book:first-of-type{border-top:none;margin-top:28px}
@media (max-width:720px){.scene{grid-template-columns:1fr}}
"""


if __name__ == "__main__":
    sys.exit(main())
