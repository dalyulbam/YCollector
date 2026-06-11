"""산출물 렌더링 — 사람이 읽는 대본(script.md)과 요약(summary.md).

duck-typed 입력:
  * segments: ``.start`` / ``.end`` / ``.text`` 를 가진 객체들
  * summary:  ``.overview`` / ``.key_points`` / ``.sections``(각 .title/.start/.end/.summary)

화자 분리(diarization)는 현재 보류이므로 대본은 타임스탬프 단락 형태다
(추후 화자 라벨 `[화자1]` 추가 예정 — [[project_ljm_analysis]]).
"""

from __future__ import annotations

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
