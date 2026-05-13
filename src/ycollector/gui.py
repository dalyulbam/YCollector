"""YCollector GUI — Phase 0 Day 2 (PySide6).

Improvements over Day 1:
  - Replaced raw QLineEdit format spec with structured controls
    (radio groups for quality/container/codec/audio).
  - Added "가용 포맷 보기" dialog that calls ``yt-dlp -J URL`` in a worker
    thread and lets the user pick a specific format from a sortable table.
  - Output settings (folder, subtitles) split into their own panel.

Coming in later phases (per plan §11.5.2):
  - Persistent settings (Phase 1)
  - Queue / library views (Phase 1~2)
  - Preset profiles + channel overrides (D2)
  - Guided PoToken/cookie wizard (D3)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ycollector import __version__
from ycollector.config import Settings, load_settings
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
    spec_for_format_id,
)
from ycollector.engine.ytdlp import is_ambiguous_playlist_url


# ────────────────────────────────────────────────────────────────────────────
# Worker threads
# ────────────────────────────────────────────────────────────────────────────
class DownloadWorker(QObject):
    """Sequentially download URLs using ``YtdlpEngine``.

    Supports cooperative cancellation via :meth:`cancel`: the engine hands
    us the live ``Popen`` so we can ``terminate()`` it from the UI thread.
    Partial ``.part`` files survive the cancellation, and re-running the
    same job resumes from where it stopped (yt-dlp built-in behaviour).
    """

    progress = Signal(object)            # ProgressEvent
    log = Signal(str)
    item_started = Signal(int, int, str) # index, total, url — for status bar
    item_meta = Signal(object)           # MetaInfo — title/channel/duration before download
    item_done = Signal(str)              # filepath
    item_failed = Signal(str, str, str)  # url, category, message
    all_done = Signal()

    def __init__(
        self,
        urls: list[str],
        output_dir: Path,
        format_spec: str,
        container: str,
        write_subs: bool,
        sub_langs: list[str],
        cookies_from_browser: str | None,
        *,
        socket_timeout: int = 30,
        retries: int = 10,
        fragment_retries: int = 10,
        throttled_rate: str | None = None,
        playlist_mode: str = "auto",
        max_downloads: int | None = None,
        playlist_items: str | None = None,
    ) -> None:
        super().__init__()
        self._urls = urls
        self._output_dir = output_dir
        self._format = format_spec
        self._container = container
        self._write_subs = write_subs
        self._sub_langs = sub_langs
        self._cookies_from_browser = cookies_from_browser
        self._socket_timeout = socket_timeout
        self._retries = retries
        self._fragment_retries = fragment_retries
        self._throttled_rate = throttled_rate
        self._playlist_mode = playlist_mode
        self._max_downloads = max_downloads
        self._playlist_items = playlist_items
        self._cancelled = False
        self._proc: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        """Request graceful cancellation. Safe to call from another thread."""
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def _capture_proc(self, proc: subprocess.Popen[str]) -> None:
        self._proc = proc

    def _decide_playlist(self, url: str) -> tuple[bool, bool]:
        """Return ``(no_playlist, yes_playlist)`` for this URL."""
        mode = self._playlist_mode
        if mode == "single":
            return True, False
        if mode == "expand":
            return False, True
        # auto
        if is_ambiguous_playlist_url(url):
            self.log.emit(
                "  [i] 단일 영상 URL + ?list= 컨텍스트 → 단일 영상만 받습니다 "
                "(설정에서 playlist.mode=expand로 변경 가능)."
            )
            return True, False
        return False, False

    def run(self) -> None:
        try:
            engine = YtdlpEngine()
        except FileNotFoundError as exc:
            self.log.emit(f"[!] {exc}")
            self.all_done.emit()
            return
        self.log.emit(f"yt-dlp {engine.version()}  (ycollector {__version__})")

        for i, url in enumerate(self._urls, start=1):
            if self._cancelled:
                self.log.emit("\n[!] 사용자 취소 — 남은 작업 중단.")
                break
            self.log.emit(f"\n[{i}/{len(self._urls)}] {url}")
            self.item_started.emit(i, len(self._urls), url)

            # Decide playlist behaviour from settings + URL pattern.
            no_pl, yes_pl = self._decide_playlist(url)

            try:
                path = engine.download(
                    url,
                    format=self._format,
                    output_dir=self._output_dir,
                    merge_format=self._container,
                    write_subs=self._write_subs,
                    sub_langs=self._sub_langs,
                    cookies_from_browser=self._cookies_from_browser,
                    socket_timeout=self._socket_timeout,
                    retries=self._retries,
                    fragment_retries=self._fragment_retries,
                    throttled_rate=self._throttled_rate,
                    no_playlist=no_pl,
                    yes_playlist=yes_pl,
                    max_downloads=self._max_downloads,
                    playlist_items=self._playlist_items,
                    on_progress=self.progress.emit,
                    on_log=self.log.emit,
                    on_process=self._capture_proc,
                    on_meta=self.item_meta.emit,
                )
                self.item_done.emit(str(path))
            except DownloadError as exc:
                if exc.category == "cancelled" or self._cancelled:
                    self.log.emit(
                        f"  ✗ 취소됨. .part 파일이 남아 있어 같은 옵션으로 "
                        f"다시 시작하면 자동 이어받기됩니다."
                    )
                    break
                self.item_failed.emit(url, exc.category, exc.message)
            finally:
                self._proc = None
        self.all_done.emit()


class FormatFetchWorker(QObject):
    done = Signal(dict, list)   # info, formats
    failed = Signal(str, str)   # category, message

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url

    def run(self) -> None:
        try:
            engine = YtdlpEngine()
        except FileNotFoundError as exc:
            self.failed.emit("missing", str(exc))
            return
        try:
            info, formats = engine.list_formats(self._url)
            self.done.emit(info, formats)
        except DownloadError as exc:
            self.failed.emit(exc.category, exc.message)


# ────────────────────────────────────────────────────────────────────────────
# Format panel — radio groups + spec preview + "가용 포맷 보기" button
# ────────────────────────────────────────────────────────────────────────────
class _RadioRow(QWidget):
    """A horizontal row of radio buttons backed by a QButtonGroup."""

    selected = Signal(str)  # the value of the chosen radio (Enum.value)

    def __init__(self, options: list[tuple[str, str]], default_value: str) -> None:
        super().__init__()
        self._group = QButtonGroup(self)
        self._buttons: dict[str, QRadioButton] = {}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for value, label in options:
            btn = QRadioButton(label)
            btn.setProperty("value", value)
            self._group.addButton(btn)
            self._buttons[value] = btn
            layout.addWidget(btn)
            if value == default_value:
                btn.setChecked(True)
        layout.addStretch(1)
        self._group.buttonClicked.connect(
            lambda b: self.selected.emit(str(b.property("value")))
        )

    def value(self) -> str:
        for value, btn in self._buttons.items():
            if btn.isChecked():
                return value
        return ""

    def set_value(self, value: str) -> None:
        if value in self._buttons:
            self._buttons[value].setChecked(True)

    def set_enabled_all(self, enabled: bool) -> None:
        for btn in self._buttons.values():
            btn.setEnabled(enabled)


class FormatPanel(QGroupBox):
    """Structured format selector — produces a yt-dlp ``-f`` spec."""

    changed = Signal()             # emitted whenever selection or override changes
    browse_requested = Signal()    # user clicked "가용 포맷 보기"

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__("포맷")
        self._override: str | None = None
        self._build_ui()
        if settings is not None:
            self.apply_settings(settings)

    def apply_settings(self, s: Settings) -> None:
        self.quality.set_value(s.quality)
        self.container.set_value(s.container)
        self.codec.set_value(s.codec)
        self.audio.set_value(s.audio)
        self._refresh()

    def _build_ui(self) -> None:
        self.quality = _RadioRow(
            [
                (Quality.P360.value, "360p"),
                (Quality.P480.value, "480p"),
                (Quality.P720.value, "720p"),
                (Quality.P1080.value, "1080p"),
                (Quality.P1440.value, "1440p"),
                (Quality.P2160.value, "4K"),
                (Quality.BEST.value, "최고"),
                (Quality.AUDIO.value, "오디오만"),
            ],
            default_value=Quality.P1080.value,
        )
        self.container = _RadioRow(
            [
                (Container.MP4.value, "mp4"),
                (Container.MKV.value, "mkv"),
                (Container.WEBM.value, "webm"),
            ],
            default_value=Container.MP4.value,
        )
        self.codec = _RadioRow(
            [
                (CodecPref.AUTO.value, "자동"),
                (CodecPref.H264.value, "H.264"),
                (CodecPref.VP9.value, "VP9"),
                (CodecPref.AV1.value, "AV1"),
            ],
            default_value=CodecPref.AUTO.value,
        )
        self.audio = _RadioRow(
            [
                (AudioPref.BEST.value, "최고"),
                (AudioPref.M4A.value, "m4a (AAC)"),
                (AudioPref.OPUS.value, "opus"),
            ],
            default_value=AudioPref.BEST.value,
        )

        for row in (self.quality, self.container, self.codec, self.audio):
            row.selected.connect(lambda *_: self._refresh())

        self.browse_btn = QPushButton("가용 포맷 보기…")
        self.browse_btn.setToolTip("URL의 실제 가용 비디오/오디오 포맷을 yt-dlp로 조회")
        self.browse_btn.clicked.connect(self.browse_requested.emit)

        self.clear_override_btn = QPushButton("오버라이드 해제")
        self.clear_override_btn.setVisible(False)
        self.clear_override_btn.clicked.connect(self.clear_override)

        self.spec_label = QLabel()
        self.spec_label.setStyleSheet(
            "color: #555; font-family: 'JetBrains Mono','Consolas',monospace; font-size: 11px;"
        )
        self.spec_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setVerticalSpacing(6)
        grid.addWidget(QLabel("화질:"),     0, 0)
        grid.addWidget(self.quality,        0, 1, 1, 2)
        grid.addWidget(QLabel("컨테이너:"), 1, 0)
        grid.addWidget(self.container,      1, 1, 1, 2)
        grid.addWidget(QLabel("비디오 코덱:"), 2, 0)
        grid.addWidget(self.codec,          2, 1, 1, 2)
        grid.addWidget(QLabel("오디오:"),   3, 0)
        grid.addWidget(self.audio,          3, 1, 1, 2)

        bottom = QHBoxLayout()
        bottom.addWidget(self.browse_btn)
        bottom.addWidget(self.clear_override_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.spec_label)

        outer = QVBoxLayout(self)
        outer.addLayout(grid)
        outer.addSpacing(4)
        outer.addLayout(bottom)
        self._refresh()

    # ── public API ─────────────────────────────────────────────────────────
    def choice(self) -> FormatChoice:
        return FormatChoice(
            quality=Quality(self.quality.value() or Quality.P1080.value),
            container=Container(self.container.value() or Container.MP4.value),
            codec=CodecPref(self.codec.value() or CodecPref.AUTO.value),
            audio=AudioPref(self.audio.value() or AudioPref.BEST.value),
        )

    def current_spec(self) -> str:
        return self._override or compose_format_spec(self.choice())

    def current_container(self) -> str:
        return self.container.value() or Container.MP4.value

    def set_override(self, spec: str) -> None:
        self._override = spec
        for row in (self.quality, self.container, self.codec, self.audio):
            row.set_enabled_all(False)
        self.clear_override_btn.setVisible(True)
        self._refresh()

    def clear_override(self) -> None:
        self._override = None
        for row in (self.quality, self.container, self.codec, self.audio):
            row.set_enabled_all(True)
        self.clear_override_btn.setVisible(False)
        self._refresh()

    # ── internal ──────────────────────────────────────────────────────────
    def _refresh(self) -> None:
        spec = self.current_spec()
        prefix = "수동" if self._override else "spec"
        self.spec_label.setText(f"{prefix}: {spec}")
        self.changed.emit()


# ────────────────────────────────────────────────────────────────────────────
# Output panel
# ────────────────────────────────────────────────────────────────────────────
class OutputPanel(QGroupBox):
    """Output folder + subtitle settings."""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__("출력")
        default_dir = settings.output_dir if settings else "downloads"
        self.output_dir = Path(default_dir).expanduser().resolve()
        self._build_ui()
        if settings is not None:
            self._apply_settings(settings)

    def _apply_settings(self, s: Settings) -> None:
        self.subs_check.setChecked(s.embed_subs)
        self.sub_langs.setText(",".join(s.sub_langs))
        self.sub_langs.setEnabled(s.embed_subs)
        if s.cookies_from_browser:
            self.cookies.setText(s.cookies_from_browser)

    def _build_ui(self) -> None:
        self.dir_label = QLabel(str(self.output_dir))
        self.dir_label.setStyleSheet("color: #444;")
        self.dir_label.setWordWrap(True)

        pick_btn = QPushButton("폴더…")
        pick_btn.clicked.connect(self._pick)

        self.subs_check = QCheckBox("자막 다운로드 + 임베드")
        self.subs_check.setChecked(True)
        self.sub_langs = QLineEdit("ko,en")
        self.sub_langs.setMaximumWidth(160)
        self.sub_langs.setPlaceholderText("ko,en,ja")
        self.subs_check.toggled.connect(self.sub_langs.setEnabled)

        self.cookies = QLineEdit()
        self.cookies.setPlaceholderText("(예: chrome / firefox / edge — 비워두면 사용 안 함)")

        grid = QGridLayout(self)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        grid.addWidget(QLabel("폴더:"),       0, 0)
        grid.addWidget(self.dir_label,        0, 1)
        grid.addWidget(pick_btn,              0, 2)

        sub_row = QHBoxLayout()
        sub_row.addWidget(self.subs_check)
        sub_row.addWidget(QLabel("언어:"))
        sub_row.addWidget(self.sub_langs)
        sub_row.addStretch(1)
        sub_w = QWidget()
        sub_w.setLayout(sub_row)
        grid.addWidget(QLabel("자막:"),       1, 0)
        grid.addWidget(sub_w,                 1, 1, 1, 2)

        grid.addWidget(QLabel("쿠키:"),       2, 0)
        grid.addWidget(self.cookies,          2, 1, 1, 2)

    def _pick(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "출력 폴더 선택", str(self.output_dir))
        if path:
            self.output_dir = Path(path)
            self.dir_label.setText(path)

    def sub_languages(self) -> list[str]:
        if not self.subs_check.isChecked():
            return []
        return [s.strip() for s in self.sub_langs.text().split(",") if s.strip()]

    def cookies_browser(self) -> str | None:
        v = self.cookies.text().strip().lower()
        return v or None


# ────────────────────────────────────────────────────────────────────────────
# Format browser dialog
# ────────────────────────────────────────────────────────────────────────────
_FMT_COLUMNS = ("종류", "ID", "확장자", "해상도", "FPS", "VCodec", "ACodec", "크기", "비트레이트", "노트")


class FormatBrowserDialog(QDialog):
    """Async-fetches ``yt-dlp -J URL`` and shows the format table."""

    def __init__(self, url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"가용 포맷 — {url[:80]}")
        self.resize(960, 540)
        self._url = url
        self._formats: list[dict[str, Any]] = []
        self._selected_spec: str | None = None
        self._build_ui()
        self._fetch()

    def _build_ui(self) -> None:
        self.title_label = QLabel("로드 중…")
        self.title_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet("color: #666; font-size: 11px;")

        self.table = QTableWidget(0, len(_FMT_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_FMT_COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(lambda *_: self._accept_selection())

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.use_btn = QPushButton("이 포맷 사용")
        self.use_btn.setDefault(True)
        self.use_btn.clicked.connect(self._accept_selection)
        buttons.addButton(self.use_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.title_label)
        layout.addWidget(self.meta_label)
        layout.addSpacing(6)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

    def _fetch(self) -> None:
        thread = QThread(self)
        worker = FormatFetchWorker(self._url)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_loaded(self, info: dict, formats: list[dict]) -> None:
        title = str(info.get("title") or "(제목 없음)")
        channel = str(info.get("channel") or info.get("uploader") or "?")
        duration = info.get("duration")
        dur_str = self._format_duration(duration) if isinstance(duration, (int, float)) else "?"
        vid = str(info.get("id") or "?")
        self.title_label.setText(title)
        self.meta_label.setText(f"채널: {channel}    길이: {dur_str}    ID: {vid}    포맷 {len(formats)}개")

        self._formats = formats
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(formats))
        for row, fmt in enumerate(formats):
            self._fill_row(row, fmt)
        self.table.setSortingEnabled(True)
        if formats:
            self.table.selectRow(0)

    def _fill_row(self, row: int, fmt: dict) -> None:
        vc = (fmt.get("vcodec") or "none")
        ac = (fmt.get("acodec") or "none")
        has_v = vc != "none"
        has_a = ac != "none"
        kind = (
            "비디오+오디오" if has_v and has_a
            else "비디오"   if has_v
            else "오디오"   if has_a
            else "?"
        )
        res = ""
        if has_v:
            w = fmt.get("width")
            h = fmt.get("height")
            if w and h:
                res = f"{w}x{h}"
            elif fmt.get("resolution"):
                res = str(fmt["resolution"])
        fps = fmt.get("fps")
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        size_s = self._format_bytes(size) if size else ""
        tbr = fmt.get("tbr")
        tbr_s = f"{tbr:.0f}k" if isinstance(tbr, (int, float)) else ""
        cells = (
            kind,
            str(fmt.get("format_id", "")),
            str(fmt.get("ext", "")),
            res,
            f"{fps:.0f}" if isinstance(fps, (int, float)) else "",
            self._truncate(vc, 18),
            self._truncate(ac, 18),
            size_s,
            tbr_s,
            self._truncate(str(fmt.get("format_note") or ""), 30),
        )
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col in (3, 4, 7, 8):  # right-align numerics
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, col, item)

    def _on_failed(self, category: str, message: str) -> None:
        self.title_label.setText("로드 실패")
        self.meta_label.setText(f"[{category}] {message}")
        self.use_btn.setEnabled(False)

    def _accept_selection(self) -> None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._formats):
            return
        fmt = self._formats[row]
        self._selected_spec = spec_for_format_id(fmt)
        self.accept()

    def selected_spec(self) -> str | None:
        return self._selected_spec

    @staticmethod
    def _format_bytes(n: int) -> str:
        size = float(n)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    @staticmethod
    def _format_duration(s: float) -> str:
        s = int(s)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

    @staticmethod
    def _truncate(text: str, n: int) -> str:
        return text if len(text) <= n else text[: n - 1] + "…"


# ────────────────────────────────────────────────────────────────────────────
# Main window
# ────────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self, settings: Settings | None = None,
                 settings_source: Path | None = None) -> None:
        super().__init__()
        self._settings = settings or Settings()
        title_suffix = f" — settings: {settings_source.name}" if settings_source else ""
        self.setWindowTitle(f"YCollector v{__version__}{title_suffix}")
        self.resize(1080, 720)

        self.url_box = QPlainTextEdit()
        self.url_box.setPlaceholderText(
            "URL 붙여넣기 (한 줄에 하나, '#' 주석)\n"
            "예: https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.url_box.setFont(QFont("Consolas", 10))
        self.url_box.setMaximumHeight(140)

        self.format_panel = FormatPanel(self._settings)
        self.format_panel.browse_requested.connect(self._open_format_browser)
        self.format_panel.changed.connect(self._update_status_hint)

        self.output_panel = OutputPanel(self._settings)

        self.dl_btn = QPushButton("지금 다운로드")
        self.dl_btn.setMinimumHeight(36)
        self.dl_btn.setMinimumWidth(160)
        self.dl_btn.setDefault(True)
        self.dl_btn.clicked.connect(self._start_download)

        self.cancel_btn = QPushButton("취소 (이어받기 가능)")
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.setMinimumWidth(160)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setStyleSheet("QPushButton { color: #b91c1c; font-weight: 600; }")
        self.cancel_btn.setToolTip(
            "현재 다운로드를 중단합니다. .part 파일이 남아 있어, "
            "동일한 URL/옵션으로 다시 다운로드하면 yt-dlp가 자동으로 이어받기를 합니다."
        )
        self.cancel_btn.clicked.connect(self._cancel_download)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))

        side_panels = QHBoxLayout()
        side_panels.addWidget(self.format_panel, 3)
        side_panels.addWidget(self.output_panel, 2)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        action_row.addWidget(self.cancel_btn)
        action_row.addWidget(self.dl_btn)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("URL"))
        layout.addWidget(self.url_box)
        layout.addLayout(side_panels)
        layout.addLayout(action_row)
        layout.addWidget(QLabel("로그"))
        layout.addWidget(self.log_view, 1)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self._update_status_hint()

        self._thread: QThread | None = None
        self._worker: DownloadWorker | None = None

        # 상태 바 — 진행률 hook 이 없는 구간(메타데이터 추출 / merge / 자막 임베드)
        # 에도 "준비 중 / 후처리 중 + 경과 시간"이 보이도록 200ms QTimer 로 페인팅.
        self._status_state: str = "idle"   # idle | preparing | downloading | postprocessing
        self._status_t0: float = 0.0
        self._status_event: ProgressEvent | None = None
        self._status_url_idx: tuple[int, int] | None = None  # (i, total)
        self._status_title: str | None = None  # 현재 영상 제목 (item_meta 도착 후)
        self._status_frame_idx: int = 0
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(200)
        self._status_timer.timeout.connect(self._paint_status_bar)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _extract_urls(self) -> list[str]:
        return [
            line.strip()
            for line in self.url_box.toPlainText().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def _update_status_hint(self) -> None:
        self.statusBar().showMessage(f"포맷 spec: {self.format_panel.current_spec()}")

    # ── slots ──────────────────────────────────────────────────────────────
    def _open_format_browser(self) -> None:
        urls = self._extract_urls()
        if not urls:
            QMessageBox.information(
                self, "포맷 탐색",
                "URL을 먼저 입력하세요. 첫 번째 URL의 가용 포맷을 yt-dlp로 조회합니다."
            )
            return
        dlg = FormatBrowserDialog(urls[0], self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            spec = dlg.selected_spec()
            if spec:
                self.format_panel.set_override(spec)
                self.log_view.appendPlainText(f"[i] 포맷 오버라이드: {spec}")

    def _start_download(self) -> None:
        urls = self._extract_urls()
        if not urls:
            self.log_view.appendPlainText("[!] URL이 비어 있습니다.")
            return

        self.dl_btn.setVisible(False)
        self.cancel_btn.setVisible(True)
        self.cancel_btn.setEnabled(True)
        self.log_view.appendPlainText(f"\n— {len(urls)}개 다운로드 시작 —")

        thread = QThread(self)
        worker = DownloadWorker(
            urls=urls,
            output_dir=self.output_panel.output_dir,
            format_spec=self.format_panel.current_spec(),
            container=self.format_panel.current_container(),
            write_subs=bool(self.output_panel.sub_languages()),
            sub_langs=self.output_panel.sub_languages(),
            cookies_from_browser=self.output_panel.cookies_browser(),
            socket_timeout=self._settings.socket_timeout,
            retries=self._settings.retries,
            fragment_retries=self._settings.fragment_retries,
            throttled_rate=self._settings.throttled_rate,
            playlist_mode=self._settings.playlist_mode,
            max_downloads=self._settings.max_downloads,
            playlist_items=self._settings.playlist_items,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log.connect(self.log_view.appendPlainText)
        worker.progress.connect(self._on_progress)
        worker.item_started.connect(self._on_item_started)
        worker.item_meta.connect(self._on_item_meta)
        worker.item_done.connect(self._on_item_done)
        worker.item_failed.connect(self._on_item_failed)
        worker.all_done.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _cancel_download(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText("취소 중…")
            self.statusBar().showMessage("취소 요청됨 — 정리 중…")

    def _on_thread_finished(self) -> None:
        self.dl_btn.setVisible(True)
        self.cancel_btn.setVisible(False)
        self.cancel_btn.setText("취소 (이어받기 가능)")
        self._status_timer.stop()
        self._status_state = "idle"
        self.statusBar().showMessage("완료", 4000)
        if self._thread is not None:
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None

    # ── status bar — 단계별 + 경과 시간 페인터 ─────────────────────────────────
    _SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def _on_item_started(self, i: int, total: int, url: str) -> None:  # noqa: ARG002
        # 새 URL 시작 — 상태 머신 reset, 페인터 가동.
        self._status_state = "preparing"
        self._status_t0 = time.monotonic()
        self._status_event = None
        self._status_url_idx = (i, total)
        self._status_title = None  # 새 영상 → 이전 제목 비우기
        if not self._status_timer.isActive():
            self._status_timer.start()

    def _on_item_meta(self, meta: MetaInfo) -> None:
        # 다운로드 시작 전 yt-dlp 가 추출한 제목/채널을 prominent 하게 로그에 남기고
        # 상태바 / 윈도우 타이틀에도 반영.
        self.log_view.appendPlainText(f"  ▶ {meta.title}")
        self.log_view.appendPlainText(
            f"     채널: {meta.channel}   길이: {meta.duration}   ID: {meta.video_id}"
        )
        self._status_title = meta.title

    def _on_item_done(self, path: str) -> None:
        self.log_view.appendPlainText(f"  ✓ {path}")
        # all_done 가 도착할 때까지 stop 은 잠시 보류 — 다음 URL 이 곧 시작될 수 있음.

    def _on_item_failed(self, url: str, category: str, message: str) -> None:  # noqa: ARG002
        self.log_view.appendPlainText(f"  ✗ [{category}] {message}")

    def _on_progress(self, event: ProgressEvent) -> None:
        if event.status == "downloading":
            self._status_state = "downloading"
            self._status_event = event
        elif event.status == "finished":
            # 프래그먼트 완료 — 다음 fragment 가 오면 다시 downloading 으로.
            self._status_state = "postprocessing"
            self._status_event = None

    def _paint_status_bar(self) -> None:
        elapsed = time.monotonic() - self._status_t0
        self._status_frame_idx = (self._status_frame_idx + 1) % len(self._SPINNER_FRAMES)
        ch = self._SPINNER_FRAMES[self._status_frame_idx]
        idx_prefix = ""
        if self._status_url_idx is not None:
            i, total = self._status_url_idx
            idx_prefix = f"[{i}/{total}]  "
        # 제목이 도착했으면 상태바에 함께 표시 (60자 잘라).
        title_suffix = ""
        if self._status_title:
            t = self._status_title
            title_suffix = f"   ▶ {t[:60] + '…' if len(t) > 60 else t}"

        if self._status_state == "downloading" and self._status_event is not None:
            e = self._status_event
            pct = e.percent or 0.0
            mb = e.downloaded_bytes / 1024 / 1024
            speed = (e.speed or 0) / 1024 / 1024
            eta_str = f"   ETA {e.eta}s" if e.eta else ""
            self.statusBar().showMessage(
                f"{idx_prefix}{ch} {pct:5.1f}%   {mb:7.1f} MB   "
                f"{speed:5.2f} MB/s{eta_str}   ({elapsed:.0f}s){title_suffix}"
            )
        elif self._status_state == "preparing":
            self.statusBar().showMessage(
                f"{idx_prefix}{ch} 준비 중... {elapsed:.1f}s{title_suffix}"
            )
        elif self._status_state == "postprocessing":
            self.statusBar().showMessage(
                f"{idx_prefix}{ch} 후처리 중... {elapsed:.1f}s{title_suffix}"
            )


def main() -> int:
    settings, source = load_settings()
    app = QApplication(sys.argv)
    win = MainWindow(settings, source)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
