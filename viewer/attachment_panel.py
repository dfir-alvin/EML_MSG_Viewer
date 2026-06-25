"""Attachment panel with right-click Export functionality."""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QGroupBox, QVBoxLayout, QListWidget, QListWidgetItem, QMenu,
    QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt, QPoint

from viewer.email_parser import AttachmentInfo
from viewer.utils import safe_filename


class AttachmentPanel(QGroupBox):
    """
    Displays a list of attachments.
    Right-click → 'Export / Save As...' saves the file.
    Double-click does nothing (intentionally disabled).
    Panel is hidden when there are no attachments.
    """

    def __init__(self, parent=None):
        super().__init__("Attachments", parent)
        self._attachments: list[AttachmentInfo] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._list = QListWidget()
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        # Explicitly do NOT connect itemDoubleClicked
        layout.addWidget(self._list)

    def populate(self, attachments: list[AttachmentInfo]) -> None:
        """Fill the list with attachments; hide panel if empty."""
        self._attachments = attachments
        self._list.clear()

        for att in attachments:
            size_kb = len(att.data) / 1024
            label = f"{att.filename}  ({size_kb:.1f} KB)  [{att.mime_type}]"
            item = QListWidgetItem(label)
            item.setToolTip(att.filename)
            self._list.addItem(item)

        self.setVisible(bool(attachments))

    def clear(self) -> None:
        """Clear the list and hide the panel."""
        self._attachments = []
        self._list.clear()
        self.setVisible(False)

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        row = self._list.row(item)
        if row < 0 or row >= len(self._attachments):
            return

        att = self._attachments[row]
        menu = QMenu(self)
        export_action = menu.addAction("Export / Save As...")
        action = menu.exec(self._list.mapToGlobal(pos))

        if action == export_action:
            self._export(att)

    def _export(self, att: AttachmentInfo) -> None:
        safe_name = safe_filename(att.filename, fallback="attachment")
        downloads = os.path.join(os.path.expanduser("~"), "Downloads", safe_name)
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Attachment",
            downloads,
            "All Files (*.*)",
        )
        if not save_path:
            return

        try:
            with open(save_path, "wb") as f:
                f.write(att.data)
        except OSError as e:
            QMessageBox.critical(
                self,
                "Export Failed",
                f"Could not save file:\n{e}",
            )
