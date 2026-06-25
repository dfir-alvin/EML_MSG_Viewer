"""Main application window and asynchronous safe-loading workflow."""

from __future__ import annotations

import os
from datetime import datetime

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QAction, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

import viewer.config as config
from viewer.attachment_panel import AttachmentPanel
from viewer.body_view import BodyView
from viewer.header_panel import HeaderPanel
from viewer.load_thread import EmailLoadThread
from viewer.load_worker import LoadedEmail
from viewer.security import DEFAULT_PARSE_LIMITS, NetworkMode, RemoteImageFailure
from viewer.troubleshooting_dialog import (
    ImageIssueLogsDialog,
    ProcessingLogEntry,
    ProcessingLogsDialog,
)


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("EML/MSG Email Viewer")
        self.resize(900, 700)
        self.setAcceptDrops(True)
        self._load_thread: EmailLoadThread | None = None
        self._pending_path = ""
        self._has_loaded_email = False
        self._remote_errors: tuple[RemoteImageFailure, ...] = ()
        self._processing_logs: list[ProcessingLogEntry] = []

        self._build_menu()
        self._build_ui()
        self._build_statusbar()

    def _build_menu(self) -> None:
        file_menu: QMenu = self.menuBar().addMenu("File")
        open_action = QAction("Open...", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_file_dialog)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        troubleshooting_menu: QMenu = self.menuBar().addMenu("Troubleshooting")
        image_logs_action = QAction("Image Issue Logs...", self)
        image_logs_action.triggered.connect(self._show_image_issue_logs)
        troubleshooting_menu.addAction(image_logs_action)
        processing_logs_action = QAction("Processing Logs...", self)
        processing_logs_action.triggered.connect(self._show_processing_logs)
        troubleshooting_menu.addAction(processing_logs_action)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        self._header_panel = HeaderPanel()
        layout.addWidget(self._header_panel)

        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._body_view = BodyView()
        self._splitter.addWidget(self._body_view)
        self._attachment_panel = AttachmentPanel()
        self._attachment_panel.setVisible(False)
        self._splitter.addWidget(self._attachment_panel)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 1)
        layout.addWidget(self._splitter)

    def _build_statusbar(self) -> None:
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        mode = (
            "restricted remote images enabled"
            if config.NETWORK_MODE is NetworkMode.RESTRICTED_REMOTE_IMAGES
            else "offline mode"
        )
        self._status.showMessage(f"Ready ({mode}) — drag an EML, MSG, or DAT file here")

    def load_file(self, path: str) -> None:
        """Start bounded processing without blocking the GUI thread."""
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            self._log_processing("File open failed", f"File not found: {path}")
            QMessageBox.critical(self, "Error Opening File", f"File not found:\n{path}")
            return

        self._cancel_active_load()
        self._pending_path = path
        self._log_processing("File open started", path)
        self._status.showMessage("Safely processing email…")

        thread = EmailLoadThread(
            path,
            DEFAULT_PARSE_LIMITS,
            config.NETWORK_MODE,
            config.REMOTE_FETCH_POLICY,
            self,
        )
        self._load_thread = thread
        thread.loaded.connect(lambda loaded, worker=thread: self._load_succeeded(worker, loaded))
        thread.failed.connect(lambda message, worker=thread: self._load_failed(worker, message))
        thread.phase_changed.connect(self._status.showMessage)
        thread.finished.connect(lambda worker=thread: self._load_finished(worker))
        thread.start()

    def _cancel_active_load(self) -> None:
        thread = self._load_thread
        if thread is None:
            return
        if thread.isRunning():
            thread.cancel()
            thread.wait(3_000)
        thread.deleteLater()
        self._load_thread = None

    def _load_succeeded(self, worker: EmailLoadThread, loaded: LoadedEmail) -> None:
        if worker is not self._load_thread:
            return
        path = self._pending_path
        parsed = loaded.parsed
        self._has_loaded_email = True
        self._remote_errors = loaded.remote_errors
        subject = parsed.subject or os.path.basename(path)
        self.setWindowTitle(f"{subject} — EML/MSG Email Viewer")
        self._header_panel.populate(parsed)
        self._body_view.load_content(loaded.rendered_html, loaded.assets)
        self._attachment_panel.populate(parsed.attachments)
        self._log_processing("File opened", path)
        for note in parsed.processing_notes:
            self._log_processing("TNEF/winmail.dat", note)
        if config.NETWORK_MODE is NetworkMode.RESTRICTED_REMOTE_IMAGES:
            self._log_processing(
                "Remote image result",
                f"{len(loaded.remote_errors)} remote image(s) blocked or failed for {path}",
            )
        else:
            self._log_processing(
                "Remote image result",
                "Remote image fetching was disabled by offline mode.",
            )

        if self._attachment_panel.isVisible():
            total = self._splitter.height() or 600
            body_height = int(total * 0.75)
            self._splitter.setSizes([body_height, total - body_height])

        status = path
        if loaded.remote_errors:
            status += f" — {len(loaded.remote_errors)} remote image(s) blocked or failed"
        elif config.NETWORK_MODE is NetworkMode.RESTRICTED_REMOTE_IMAGES:
            status += " — restricted remote images enabled"
        self._status.showMessage(status)

    def _show_image_issue_logs(self) -> None:
        if not self._has_loaded_email:
            QMessageBox.information(
                self,
                "Image Issue Logs",
                "Open an email first to view its image issue logs.",
            )
            return
        if config.NETWORK_MODE is NetworkMode.OFFLINE:
            QMessageBox.information(
                self,
                "Image Issue Logs",
                "This is the offline build. Remote images are intentionally not requested.",
            )
            return
        if not self._remote_errors:
            QMessageBox.information(
                self,
                "Image Issue Logs",
                "No remote images were blocked or failed for the current email.",
            )
            return
        ImageIssueLogsDialog(self._remote_errors, self).exec()

    def _show_processing_logs(self) -> None:
        if not self._processing_logs:
            QMessageBox.information(
                self,
                "Processing Logs",
                "No processing events have been recorded yet.",
            )
            return
        ProcessingLogsDialog(tuple(self._processing_logs), self).exec()

    def _log_processing(self, event: str, details: str) -> None:
        self._processing_logs.append(
            ProcessingLogEntry(
                timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
                event=event,
                details=details,
            )
        )

    def _load_failed(self, worker: EmailLoadThread, message: str) -> None:
        if worker is not self._load_thread:
            return
        self._log_processing(
            "File open failed",
            f"{self._pending_path}: {message}",
        )
        QMessageBox.critical(
            self,
            "Error Opening File",
            f"Could not safely process email file:\n{self._pending_path}\n\n{message}",
        )
        self._status.showMessage(f"Blocked: {message}")

    def _load_finished(self, worker: EmailLoadThread) -> None:
        if worker is self._load_thread:
            self._load_thread = None
        worker.deleteLater()

    def _open_file_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Email File",
            "",
            "Email Files (*.eml *.msg *.dat);;All Files (*.*)",
        )
        if path:
            self.load_file(path)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        mime = event.mimeData()
        if mime.hasUrls():
            urls = mime.urls()
            if urls and self._is_email_url(urls[0]):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        mime = event.mimeData()
        if mime.hasUrls():
            urls = mime.urls()
            if urls and urls[0].isLocalFile():
                path = urls[0].toLocalFile()
                if self._is_email_path(path):
                    event.acceptProposedAction()
                    self.load_file(path)
                    return
        event.ignore()

    @staticmethod
    def _is_email_url(url: QUrl) -> bool:
        return url.isLocalFile() and MainWindow._is_email_path(url.toLocalFile())

    @staticmethod
    def _is_email_path(path: str) -> bool:
        return path.lower().endswith((".eml", ".msg", ".dat"))

    def closeEvent(self, event) -> None:
        self._cancel_active_load()
        self._body_view.shutdown()
        super().closeEvent(event)
