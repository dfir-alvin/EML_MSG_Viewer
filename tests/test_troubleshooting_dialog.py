import os
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication

    from viewer.security import RemoteImageFailure
    from viewer.troubleshooting_dialog import (
        ImageIssueLogsDialog,
        ProcessingLogEntry,
        ProcessingLogsDialog,
    )

    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 is not installed")
class TroubleshootingDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selected_rows_copy_as_tab_separated_table(self):
        failures = (
            RemoteImageFailure(
                "images.example.test",
                "Remote image timed out",
                "https://images.example.test/a.png?recipient=one",
            ),
            RemoteImageFailure(
                "192.0.2.5",
                "Remote image uses a non-public address",
                "http://192.0.2.5/pixel.gif",
            ),
        )
        dialog = ImageIssueLogsDialog(failures)
        dialog._table.selectAll()
        dialog._copy_selected_rows()
        copied = QApplication.clipboard().text().splitlines()
        self.assertEqual(copied[0], "Number\tReason\tFull URL")
        self.assertIn(failures[0].full_url, copied[1])
        self.assertIn(failures[1].full_url, copied[2])
        dialog.close()

    def test_individual_cell_can_be_copied(self):
        failure = RemoteImageFailure(
            "images.example.test",
            "Remote image timed out",
            "https://images.example.test/a.png?recipient=one",
        )
        dialog = ImageIssueLogsDialog((failure,))
        dialog._copy_cell(dialog._table.item(0, 2))
        self.assertEqual(QApplication.clipboard().text(), failure.full_url)
        dialog.close()

    def test_processing_logs_copy_as_tab_separated_table(self):
        entry = ProcessingLogEntry(
            "2026-06-21T12:34:56-05:00",
            "Remote image result",
            "3 remote image(s) blocked or failed",
        )
        dialog = ProcessingLogsDialog((entry,))
        dialog._table.selectAll()
        dialog._copy_selected_rows()
        copied = QApplication.clipboard().text().splitlines()
        self.assertEqual(copied[0], "Number\tTime\tEvent\tDetails")
        self.assertIn(entry.event, copied[1])
        self.assertIn(entry.details, copied[1])
        dialog.close()


if __name__ == "__main__":
    unittest.main()
