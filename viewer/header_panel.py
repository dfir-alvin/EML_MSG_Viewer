"""Header panel: displays key email headers and opens the all-headers dialog."""

from __future__ import annotations

import os

from PyQt6.QtWidgets import (
    QWidget, QFormLayout, QLineEdit, QPushButton, QHBoxLayout, QVBoxLayout,
    QFileDialog, QMessageBox,
)
from PyQt6.QtCore import Qt

from viewer.email_parser import ParsedEmail
from viewer.utils import safe_filename


class HeaderPanel(QWidget):
    """
    Displays Date, From, Return-Path, Subject, To as read-only selectable fields.
    An 'Email Headers...' button opens the full AllHeadersDialog.

    When the opened eml/msg embeds a TNEF file (winmail.dat / webmail.dat), two
    extra buttons appear: '<name> Headers' opens that file's own headers, and
    'Export <name>' saves the raw blob out. The default email-header view is the
    more reliable one; these controls let an analyst inspect the embedded TNEF.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dialog = None
        self._tnef_dialog = None
        self._all_headers: list[tuple[str, str]] = []
        self._tnef_data: bytes = b""
        self._tnef_name: str = ""
        self._tnef_headers: list[tuple[str, str]] = []
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(4)

        self._fields: dict[str, QLineEdit] = {}
        for label in ("Date", "From", "Return-Path", "Subject", "To"):
            le = QLineEdit()
            le.setReadOnly(True)
            le.setFrame(False)
            le.setStyleSheet("background: transparent;")
            self._fields[label] = le
            form.addRow(label + ":", le)

        outer.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        self._headers_btn = QPushButton("Email Headers...")
        self._headers_btn.setFixedWidth(130)
        self._headers_btn.clicked.connect(self._open_headers_dialog)
        btn_row.addWidget(self._headers_btn)

        self._tnef_headers_btn = QPushButton()
        self._tnef_headers_btn.clicked.connect(self._open_tnef_headers_dialog)
        self._tnef_headers_btn.setVisible(False)
        btn_row.addWidget(self._tnef_headers_btn)

        self._export_tnef_btn = QPushButton()
        self._export_tnef_btn.clicked.connect(self._export_tnef)
        self._export_tnef_btn.setVisible(False)
        btn_row.addWidget(self._export_tnef_btn)

        btn_row.addStretch()
        outer.addLayout(btn_row)

    def populate(self, parsed: ParsedEmail) -> None:
        """Fill header fields from a ParsedEmail."""
        self._fields["Date"].setText(parsed.date)
        self._fields["From"].setText(parsed.from_)
        self._fields["Return-Path"].setText(parsed.return_path)
        self._fields["Subject"].setText(parsed.subject)
        self._fields["To"].setText(parsed.to)
        self._all_headers = parsed.all_headers
        self._tnef_data = parsed.embedded_tnef_data
        self._tnef_name = parsed.embedded_tnef_name
        self._tnef_headers = parsed.embedded_tnef_headers

        has_tnef = bool(parsed.embedded_tnef_data)
        if has_tnef:
            self._tnef_headers_btn.setText(f"{self._tnef_name} Headers")
            self._export_tnef_btn.setText(f"Export {self._tnef_name}")
            # The labels are filename-driven, so size to fit rather than clip.
            self._tnef_headers_btn.adjustSize()
            self._export_tnef_btn.adjustSize()
            # The header view is empty when the embedded TNEF could not be
            # parsed; the export button stays available for the raw blob.
            self._tnef_headers_btn.setVisible(bool(self._tnef_headers))
        else:
            self._tnef_headers_btn.setVisible(False)
        self._export_tnef_btn.setVisible(has_tnef)

        # Close stale dialogs if open
        if self._dialog and self._dialog.isVisible():
            self._dialog.close()
        self._dialog = None
        if self._tnef_dialog and self._tnef_dialog.isVisible():
            self._tnef_dialog.close()
        self._tnef_dialog = None

    def clear(self) -> None:
        """Clear all header fields."""
        for le in self._fields.values():
            le.clear()
        self._all_headers = []
        self._tnef_data = b""
        self._tnef_name = ""
        self._tnef_headers = []
        self._tnef_headers_btn.setVisible(False)
        self._export_tnef_btn.setVisible(False)
        if self._dialog and self._dialog.isVisible():
            self._dialog.close()
        self._dialog = None
        if self._tnef_dialog and self._tnef_dialog.isVisible():
            self._tnef_dialog.close()
        self._tnef_dialog = None

    def _open_headers_dialog(self) -> None:
        from viewer.header_dialog import AllHeadersDialog
        if self._dialog and self._dialog.isVisible():
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        self._dialog = AllHeadersDialog(self._all_headers, self)
        self._dialog.show()

    def _open_tnef_headers_dialog(self) -> None:
        from viewer.header_dialog import AllHeadersDialog
        if self._tnef_dialog and self._tnef_dialog.isVisible():
            self._tnef_dialog.raise_()
            self._tnef_dialog.activateWindow()
            return
        self._tnef_dialog = AllHeadersDialog(
            self._tnef_headers, self, title=f"{self._tnef_name} Headers"
        )
        self._tnef_dialog.show()

    def _export_tnef(self) -> None:
        if not self._tnef_data:
            return
        safe_name = safe_filename(self._tnef_name, fallback="winmail.dat")
        downloads = os.path.join(os.path.expanduser("~"), "Downloads", safe_name)
        # Use a dialog instance so a default suffix is appended when the user
        # clears the extension; the static getSaveFileName does not do this.
        extension = os.path.splitext(safe_name)[1].lstrip(".") or "dat"
        dialog = QFileDialog(self, f"Export {self._tnef_name}", downloads)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        dialog.setDefaultSuffix(extension)
        dialog.setNameFilters([f"TNEF data (*.{extension})", "All Files (*.*)"])
        dialog.selectFile(safe_name)
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return
        selected = dialog.selectedFiles()
        if not selected:
            return
        save_path = selected[0]
        try:
            with open(save_path, "wb") as f:
                f.write(self._tnef_data)
        except OSError as e:
            QMessageBox.critical(
                self,
                "Export Failed",
                f"Could not save file:\n{e}",
            )
