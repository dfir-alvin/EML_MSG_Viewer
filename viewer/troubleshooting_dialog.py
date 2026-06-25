"""Copyable image-issue and processing-log dialogs."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMenu,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from viewer.security import RemoteImageFailure


@dataclass(frozen=True)
class ProcessingLogEntry:
    timestamp: str
    event: str
    details: str


class _CopyableTableDialog(QDialog):
    def __init__(
        self,
        title: str,
        summary: str,
        headers: tuple[str, ...],
        rows: tuple[tuple[str, ...], ...],
        stretch_columns: tuple[int, ...],
        parent=None,
    ):
        super().__init__(parent)
        self._headers = headers
        self.setWindowTitle(title)
        self.resize(1100, 500)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(summary))

        self._table = QTableWidget(len(rows), len(headers), self)
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_cell_context_menu)
        self._table.setAlternatingRowColors(True)
        self._table.setWordWrap(False)
        self._table.verticalHeader().setVisible(False)

        for row_number, values in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self._table.setItem(row_number, column, item)

        header = self._table.horizontalHeader()
        for column in range(len(headers)):
            mode = (
                QHeaderView.ResizeMode.Stretch
                if column in stretch_columns
                else QHeaderView.ResizeMode.ResizeToContents
            )
            header.setSectionResizeMode(column, mode)
        if rows:
            self._table.selectRow(0)
        layout.addWidget(self._table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        copy_button = buttons.addButton(
            "Copy Selected Rows",
            QDialogButtonBox.ButtonRole.ActionRole,
        )
        copy_button.clicked.connect(self._copy_selected_rows)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self)
        self._copy_shortcut.activated.connect(self._copy_selected_rows)

    @staticmethod
    def _tsv_value(value: str) -> str:
        return value.replace("\r", "").replace("\n", "\\n").replace("\t", "\\t")

    def _copy_selected_rows(self) -> None:
        rows = sorted(index.row() for index in self._table.selectionModel().selectedRows())
        if not rows and self._table.currentRow() >= 0:
            rows = [self._table.currentRow()]
        if not rows:
            return

        lines = ["\t".join(self._headers)]
        for row in rows:
            values = [
                self._table.item(row, column).text()
                for column in range(self._table.columnCount())
            ]
            lines.append("\t".join(self._tsv_value(value) for value in values))
        QApplication.clipboard().setText("\n".join(lines))

    def _show_cell_context_menu(self, position: QPoint) -> None:
        item = self._table.itemAt(position)
        if item is None:
            return
        menu = QMenu(self)
        copy_cell = menu.addAction("Copy Cell")
        selected = menu.exec(self._table.viewport().mapToGlobal(position))
        if selected == copy_cell:
            self._copy_cell(item)

    @staticmethod
    def _copy_cell(item: QTableWidgetItem) -> None:
        QApplication.clipboard().setText(item.text())


class ImageIssueLogsDialog(_CopyableTableDialog):
    HEADERS = ("Number", "Reason", "Full URL")

    def __init__(self, failures: tuple[RemoteImageFailure, ...], parent=None):
        rows = tuple(
            (str(index), failure.reason, failure.full_url)
            for index, failure in enumerate(failures, 1)
        )
        super().__init__(
            "Image Issue Logs",
            f"{len(failures)} remote image(s) were blocked or failed.",
            self.HEADERS,
            rows,
            (1, 2),
            parent,
        )


class ProcessingLogsDialog(_CopyableTableDialog):
    HEADERS = ("Number", "Time", "Event", "Details")

    def __init__(self, entries: tuple[ProcessingLogEntry, ...], parent=None):
        rows = tuple(
            (str(index), entry.timestamp, entry.event, entry.details)
            for index, entry in enumerate(entries, 1)
        )
        super().__init__(
            "Processing Logs",
            f"{len(entries)} processing event(s) recorded during this session.",
            self.HEADERS,
            rows,
            (2, 3),
            parent,
        )
