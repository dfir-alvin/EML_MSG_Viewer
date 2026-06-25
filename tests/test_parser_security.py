import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from viewer.email_parser import (
    ParsedEmail,
    TnefParser,
    _ParseBudget,
    _TNEF_MAGIC,
    _merge_tnef,
    parse_email_file,
)
from viewer.security import ParseLimits, SecurityLimitError
from viewer.utils import safe_filename


def _write_temp(data: bytes, suffix: str = ".eml") -> str:
    descriptor, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    return path


def _nested_mime(depth: int) -> bytes:
    lines = ["MIME-Version: 1.0", 'Content-Type: multipart/mixed; boundary="b0"', ""]
    for index in range(depth):
        content_type = (
            f'Content-Type: multipart/mixed; boundary="b{index + 1}"'
            if index + 1 < depth
            else "Content-Type: text/plain"
        )
        lines.extend((f"--b{index}", content_type, ""))
    lines.append("payload")
    for index in reversed(range(depth)):
        lines.append(f"--b{index}--")
    return "\r\n".join(lines).encode()


class ParserSecurityTests(unittest.TestCase):
    def test_attachment_filename_cannot_spoof_with_bidi_override(self):
        self.assertEqual(safe_filename("invoice\u202egnp.exe"), "invoicegnp.exe")
        self.assertEqual(safe_filename("../../CON.txt"), "_CON.txt")

    def test_source_size_limit(self):
        path = _write_temp(b"Subject: large\r\n\r\n" + b"x" * 128)
        self.addCleanup(os.unlink, path)
        with self.assertRaisesRegex(SecurityLimitError, "exceeds") as caught:
            parse_email_file(path, ParseLimits(max_source_bytes=64))
        self.assertEqual(caught.exception.code, "source_size")

    def test_mime_depth_is_bounded_without_recursion(self):
        path = _write_temp(_nested_mime(100))
        self.addCleanup(os.unlink, path)
        with self.assertRaises(SecurityLimitError) as caught:
            parse_email_file(path, ParseLimits(max_mime_depth=16))
        self.assertEqual(caught.exception.code, "mime_depth")

    def test_attachment_and_decoded_size_limits(self):
        message = (
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Content-Disposition: attachment; filename=data.bin\r\n"
            b"Content-Transfer-Encoding: base64\r\n\r\n"
            b"QUJDREVGR0g=\r\n"
        )
        path = _write_temp(message)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(SecurityLimitError) as caught:
            parse_email_file(path, ParseLimits(max_attachment_bytes=4))
        self.assertEqual(caught.exception.code, "attachment_size")

    def test_header_count_limit(self):
        message = b"".join(f"X-Test-{i}: value\r\n".encode() for i in range(10)) + b"\r\nbody"
        path = _write_temp(message)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(SecurityLimitError) as caught:
            parse_email_file(path, ParseLimits(max_headers=5))
        self.assertEqual(caught.exception.code, "header_count")

    def test_standalone_webmail_dat_records_processing_note(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "webmail.dat")
            with open(path, "wb") as handle:
                handle.write(_TNEF_MAGIC + b"data")
            with patch.object(TnefParser, "parse", return_value=ParsedEmail()):
                parsed = parse_email_file(path)
        self.assertTrue(any("normally embedded inside another email" in note for note in parsed.processing_notes))

    def test_embedded_webmail_dat_records_processing_note(self):
        result = ParsedEmail()
        limits = ParseLimits()
        with patch.object(TnefParser, "parse_bytes", return_value=ParsedEmail()):
            _merge_tnef(
                _TNEF_MAGIC + b"data",
                result,
                limits,
                _ParseBudget(limits),
                "webmail.dat",
            )
        self.assertTrue(any("inside the opened email" in note for note in result.processing_notes))

    def test_embedded_tnef_preserves_blob_name_and_headers(self):
        for source_name in ("winmail.dat", "webmail.dat"):
            with self.subTest(source_name=source_name):
                result = ParsedEmail()
                limits = ParseLimits()
                blob = _TNEF_MAGIC + b"data"
                parsed_tnef = ParsedEmail(
                    all_headers=[("From", "a@b.com"), ("Subject", "Hi")]
                )
                with patch.object(TnefParser, "parse_bytes", return_value=parsed_tnef):
                    _merge_tnef(blob, result, limits, _ParseBudget(limits), source_name)
                self.assertEqual(result.embedded_tnef_data, blob)
                self.assertEqual(result.embedded_tnef_name, source_name)
                self.assertEqual(
                    result.embedded_tnef_headers,
                    [("From", "a@b.com"), ("Subject", "Hi")],
                )

    def test_embedded_tnef_failed_parse_keeps_blob_without_headers(self):
        result = ParsedEmail()
        limits = ParseLimits(max_tnef_bytes=64)
        blob = _TNEF_MAGIC + b"x" * 4096
        # Oversized embedded blob is preserved for export; no headers parsed.
        _merge_tnef(blob, result, limits, _ParseBudget(limits), "winmail.dat")
        self.assertEqual(result.embedded_tnef_data, blob)
        self.assertEqual(result.embedded_tnef_name, "winmail.dat")
        self.assertEqual(result.embedded_tnef_headers, [])
        self.assertTrue(
            any("preserved as an attachment" in note for note in result.processing_notes)
        )

    def test_embedded_tnef_sample_eml_exposes_winmail_dat(self):
        sample = Path(__file__).resolve().parents[1] / "test_email_winmail.eml"
        parsed = parse_email_file(str(sample))
        self.assertEqual(parsed.embedded_tnef_name, "winmail.dat")
        self.assertTrue(parsed.embedded_tnef_data.startswith(_TNEF_MAGIC))
        header_names = [name for name, _ in parsed.embedded_tnef_headers]
        self.assertIn("From", header_names)
        self.assertIn("Subject", header_names)
        self.assertTrue(
            any("inside the opened email" in note for note in parsed.processing_notes)
        )

    def test_parse_limits_reject_nonpositive_tnef_bounds(self):
        with self.assertRaises(ValueError):
            ParseLimits(max_tnef_bytes=0)
        with self.assertRaises(ValueError):
            ParseLimits(max_tnef_embed_depth=0)

    def test_standalone_tnef_size_cap_is_enforced_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "webmail.dat")
            with open(path, "wb") as handle:
                handle.write(_TNEF_MAGIC + b"x" * 4096)
            with self.assertRaises(SecurityLimitError) as caught:
                parse_email_file(path, ParseLimits(max_tnef_bytes=64))
        self.assertEqual(caught.exception.code, "tnef_size")

    def test_embedded_tnef_size_cap_preserves_blob_as_attachment(self):
        result = ParsedEmail()
        limits = ParseLimits(max_tnef_bytes=64)
        blob = _TNEF_MAGIC + b"x" * 4096
        # Must not raise: an oversized embedded blob is preserved for export.
        _merge_tnef(blob, result, limits, _ParseBudget(limits), "webmail.dat")
        self.assertEqual(len(result.attachments), 1)
        attachment = result.attachments[0]
        self.assertEqual(attachment.filename, "webmail.dat")
        self.assertEqual(attachment.data, blob)
        self.assertTrue(any("TNEF safety limit" in note for note in result.processing_notes))

    def test_embedded_tnef_depth_violation_preserves_blob_as_attachment(self):
        result = ParsedEmail()
        limits = ParseLimits()
        with patch.object(
            TnefParser,
            "parse_bytes",
            side_effect=SecurityLimitError("tnef_embed_depth", "too deep"),
        ):
            _merge_tnef(
                _TNEF_MAGIC + b"data",
                result,
                limits,
                _ParseBudget(limits),
                "webmail.dat",
            )
        self.assertEqual(len(result.attachments), 1)
        self.assertEqual(result.attachments[0].filename, "webmail.dat")

    def test_embedded_tnef_parent_budget_limit_still_propagates(self):
        result = ParsedEmail()
        limits = ParseLimits()
        with patch.object(
            TnefParser,
            "parse_bytes",
            side_effect=SecurityLimitError("decoded_size", "parent message too large"),
        ):
            with self.assertRaises(SecurityLimitError) as caught:
                _merge_tnef(
                    _TNEF_MAGIC + b"data",
                    result,
                    limits,
                    _ParseBudget(limits),
                    "webmail.dat",
                )
        self.assertEqual(caught.exception.code, "decoded_size")
        self.assertEqual(result.attachments, [])

    def test_embedded_tnef_recursion_depth_is_bounded(self):
        # A TNEF whose attachment is itself a TNEF, nested past the limit,
        # must surface as a SecurityLimitError rather than a RecursionError.
        parser = TnefParser(ParseLimits(max_tnef_embed_depth=2))

        class FakeTNEF:
            depth = 0

            def __init__(self, data, do_checksum=False):
                # Emulate tnefparse recursing into an embedded message.
                FakeTNEF.depth += 1
                try:
                    FakeTNEF(data, do_checksum)
                finally:
                    FakeTNEF.depth -= 1

        with self.assertRaises(SecurityLimitError) as caught:
            parser._construct_tnef(FakeTNEF, b"payload")
        self.assertEqual(caught.exception.code, "tnef_embed_depth")


if __name__ == "__main__":
    unittest.main()
