"""YCollector GUI — Phase 0 Day 1 (PySide6).

Minimal main window:

  - URL paste box (multi-line, '#' comments)
  - Format / output directory selectors
  - Single "지금 다운로드" button (sequential, no real queue yet)
  - Live log

Queue, library, preset picker, transcribe, etc. arrive in later phases.
For full UX wireframe see plan §11.5.2.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ycollector import __version__
from ycollector.engine import DownloadError, ProgressEvent, YtdlpEngine


class DownloadWorker(QObject):
    """Sequentially download a list of URLs using ``YtdlpEngine``.

    Lives on a dedicated ``QThread``; signals marshal updates to the UI thread.
    """

    progress = Signal(object)            # ProgressEvent
    log = Signal(str)
    item_done = Signal(str)              # filepath
    item_failed = Signal(str, str, str)  # url, category, message
    all_done = Signal()

    def __init__(self, urls: list[str], output_dir: Path, format_: str) -> None:
        super().__init__()
        self._urls = urls
        self._output_dir = output_dir
        self._format = format_

    def run(self) -> None:
        try:
            engine = YtdlpEngine()
        except FileNotFoundError as exc:
            self.log.emit(f"[!] {exc}")
            self.all_done.emit()
            return
        self.log.emit(f"yt-dlp {engine.version()}  (ycollector {__version__})")

        for i, url in enumerate(self._urls, start=1):
            self.log.emit(f"\n[{i}/{len(self._urls)}] {url}")
            try:
                path = engine.download(
                    url,
                    format=self._format,
                    output_dir=self._output_dir,
                    on_progress=self.progress.emit,
                    on_log=self.log.emit,
                )
                self.item_done.emit(str(path))
            except DownloadError as exc:
                self.item_failed.emit(url, exc.category, exc.message)
        self.all_done.emit()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"YCollector v{__version__}")
        self.resize(960, 640)

        # --- URL input ---
        self.url_box = QPlainTextEdit()
        self.url_box.setPlaceholderText(
            "URL 붙여넣기 (한 줄에 하나, '#' 주석)\n"
            "예: https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        self.url_box.setFont(QFont("Consolas", 10))

        # --- Controls ---
        self.format_input = QLineEdit("bv*[height<=1080]+ba/b[height<=1080]")
        self.output_dir = (Path.cwd() / "downloads").resolve()
        self.output_dir_label = QLabel(str(self.output_dir))
        self.output_dir_label.setStyleSheet("color: #555;")

        pick_btn = QPushButton("폴더…")
        pick_btn.clicked.connect(self._pick_output_dir)

        self.dl_btn = QPushButton("지금 다운로드")
        self.dl_btn.setDefault(True)
        self.dl_btn.clicked.connect(self._start_download)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("형식:"))
        controls.addWidget(self.format_input, 2)
        controls.addSpacing(12)
        controls.addWidget(QLabel("폴더:"))
        controls.addWidget(self.output_dir_label, 1)
        controls.addWidget(pick_btn)
        controls.addSpacing(12)
        controls.addWidget(self.dl_btn)

        # --- Log ---
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(QFont("Consolas", 9))

        # --- Layout ---
        layout = QVBoxLayout()
        layout.addWidget(QLabel("URL"))
        layout.addWidget(self.url_box, 2)
        layout.addLayout(controls)
        layout.addWidget(QLabel("로그"))
        layout.addWidget(self.log_view, 3)

        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())

        self._thread: QThread | None = None
        self._worker: DownloadWorker | None = None

    # ── slots ──────────────────────────────────────────────────────────────
    def _pick_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "출력 폴더 선택", str(self.output_dir)
        )
        if path:
            self.output_dir = Path(path)
            self.output_dir_label.setText(path)

    def _start_download(self) -> None:
        urls = [
            line.strip()
            for line in self.url_box.toPlainText().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        if not urls:
            self.log_view.appendPlainText("[!] URL이 비어 있습니다.")
            return

        self.dl_btn.setEnabled(False)
        self.statusBar().showMessage(f"다운로드 시작: {len(urls)}개")

        thread = QThread(self)
        worker = DownloadWorker(urls, self.output_dir, self.format_input.text())
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.log.connect(self.log_view.appendPlainText)
        worker.progress.connect(self._on_progress)
        worker.item_done.connect(lambda p: self.log_view.appendPlainText(f"  ✓ {p}"))
        worker.item_failed.connect(
            lambda url, cat, msg: self.log_view.appendPlainText(f"  ✗ [{cat}] {msg}")
        )
        worker.all_done.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._worker = worker
        thread.start()

    def _on_thread_finished(self) -> None:
        self.dl_btn.setEnabled(True)
        self.statusBar().showMessage("완료", 3000)
        if self._thread is not None:
            self._thread.deleteLater()
        if self._worker is not None:
            self._worker.deleteLater()
        self._thread = None
        self._worker = None

    def _on_progress(self, event: ProgressEvent) -> None:
        if event.status != "downloading":
            return
        pct = event.percent or 0.0
        mb = event.downloaded_bytes / 1024 / 1024
        speed = (event.speed or 0) / 1024 / 1024
        self.statusBar().showMessage(
            f"{pct:5.1f}%   {mb:7.1f} MB   {speed:5.2f} MB/s"
        )


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
