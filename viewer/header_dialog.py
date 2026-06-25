"""Searchable all-headers dialog."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QHeaderView, QLabel, QMenu, QApplication,
)
from PyQt6.QtCore import Qt, QPoint


class AllHeadersDialog(QDialog):
    """
    Shows all email headers in a searchable table.
    Columns: Header Name | Value
    Search box filters rows live via textChanged.
    Cells are read-only but selectable; Ctrl+C copies selected text.
    """

    def __init__(self, headers: list[tuple[str, str]], parent=None, title: str = "Email Headers"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 500)
        self._headers = headers
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Search box
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search headers...")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter)
        layout.addWidget(self._search)

        # Table
        self._table = QTableWidget(len(self._headers), 2, self)
        self._table.setHorizontalHeaderLabels(["Header Name", "Value"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setWordWrap(True)

        for row, (name, value) in enumerate(self._headers):
            name_item = QTableWidgetItem(name)
            name_item.setToolTip(name)
            value_item = QTableWidgetItem(value)
            value_item.setToolTip(value)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, value_item)

        self._table.resizeRowsToContents()
        layout.addWidget(self._table)

    def _show_context_menu(self, pos: QPoint) -> None:
        item = self._table.itemAt(pos)
        if item is None:
            return

        row = self._table.row(item)
        name_item = self._table.item(row, 0)
        value_item = self._table.item(row, 1)
        cell_text = item.text()
        row_text = f"{name_item.text()}: {value_item.text()}" if name_item and value_item else cell_text

        menu = QMenu(self)
        copy_cell = menu.addAction("Copy Cell Value")
        copy_row = menu.addAction("Copy Header: Value")

        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == copy_cell:
            QApplication.clipboard().setText(cell_text)
        elif action == copy_row:
            QApplication.clipboard().setText(row_text)

    def _filter(self, text: str) -> None:
        """Show only rows where name or value contains the search text (case-insensitive)."""
        text_lower = text.lower()
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            value_item = self._table.item(row, 1)
            name_text = name_item.text().lower() if name_item else ""
            value_text = value_item.text().lower() if value_item else ""
            match = (not text_lower) or (text_lower in name_text) or (text_lower in value_text)
            self._table.setRowHidden(row, not match)
