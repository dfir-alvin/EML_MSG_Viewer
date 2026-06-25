import base64
import socket
import struct
import time
import unittest
from unittest.mock import MagicMock, patch

from viewer.remote_fetch import (
    RemoteImageError,
    ValidatedRemoteUrl,
    _open_connection,
    detect_safe_raster,
    fetch_remote_images,
    inspect_safe_raster,
    validate_declared_raster,
    validate_remote_url,
    validate_safe_raster,
)
from viewer.security import (
    BLANK_PNG_BYTES,
    InlineAsset,
    RemoteFetchPolicy,
    RemoteImageReference,
)


def _address(ip: str):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))


def _png(width: int, height: int, frames: int = 1) -> bytes:
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    chunks = [struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00" * 4]
    if frames > 1:
        actl = struct.pack(">II", frames, 0)
        chunks.append(struct.pack(">I", len(actl)) + b"acTL" + actl + b"\x00" * 4)
    chunks.append(b"\x00\x00\x00\x00IEND" + b"\x00" * 4)
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _gif(width: int, height: int, frames: int = 1) -> bytes:
    header = b"GIF89a" + struct.pack("<HH", width, height) + b"\x00\x00\x00"
    frame = (
        b"\x2c" + b"\x00" * 4 + struct.pack("<HH", width, height)
        + b"\x00\x02\x02\x4c\x01\x00"
    )
    return header + frame * frames + b"\x3b"


class RemoteFetchSecurityTests(unittest.TestCase):
    def test_loopback_is_rejected_before_dns_or_connect(self):
        with patch("viewer.remote_fetch.socket.getaddrinfo") as resolve:
            with self.assertRaisesRegex(
                RemoteImageError,
                r"Non-public IP address: 127\.0\.0\.1",
            ):
                validate_remote_url("https://127.0.0.1/image.png")
            resolve.assert_not_called()

    @patch(
        "viewer.remote_fetch.socket.getaddrinfo",
        return_value=[_address("93.184.216.34"), _address("10.0.0.2")],
    )
    def test_mixed_public_private_dns_answer_is_rejected(self, _resolve):
        validated = validate_remote_url("https://example.test/image.png")
        with self.assertRaisesRegex(RemoteImageError, r"Non-public IP address: 10\.0\.0\.2"):
            _open_connection(validated, time.monotonic() + 1)

    def test_url_validation_does_not_perform_preflight_dns(self):
        with patch("viewer.remote_fetch.socket.getaddrinfo") as resolve:
            validated = validate_remote_url("https://example.test/a.png?token=1")
        resolve.assert_not_called()
        self.assertEqual(validated.hostname, "example.test")
        self.assertEqual(validated.target, "/a.png?token=1")

    def test_credentials_and_nonstandard_ports_are_rejected(self):
        with self.assertRaisesRegex(RemoteImageError, "Credentials are not allowed"):
            validate_remote_url("https://user:pass@example.com/a.png")
        with self.assertRaisesRegex(RemoteImageError, "port is not allowed: 8443"):
            validate_remote_url("https://example.com:8443/a.png")

    def test_only_safe_raster_magic_is_accepted(self):
        png = _png(10, 10)
        gif = _gif(10, 10)
        self.assertEqual(detect_safe_raster(png), "image/png")
        self.assertEqual(detect_safe_raster(gif), "image/gif")
        self.assertIsNone(detect_safe_raster(b"<svg onload='alert(1)'></svg>"))
        self.assertIsNone(detect_safe_raster(b"<html>not an image</html>"))

    def test_common_transparent_gif_is_accepted(self):
        gif = base64.b64decode(
            "R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
        )
        raster = validate_declared_raster(gif, "image/gif")
        self.assertEqual(raster.mime_type, "image/gif")
        self.assertEqual((raster.width, raster.height), (1, 1))

    def test_mime_mismatch_reports_header_bytes_and_detected_type(self):
        gif = _gif(10, 10)
        with self.assertRaisesRegex(
            RemoteImageError,
            r"MIME mismatch: expected image/png; header 47 49 46 38 indicates image/gif",
        ):
            validate_declared_raster(gif, "image/png")

    def test_invalid_gif_reports_full_signature_instead_of_mime_mismatch(self):
        invalid_gif = b"GIF89a" + b"\x00" * 10
        with self.assertRaisesRegex(
            RemoteImageError,
            r"Invalid image/gif structure; header: 47 49 46 38 39 61",
        ):
            validate_safe_raster(invalid_gif)

    def test_raster_dimension_bombs_are_rejected(self):
        png = _png(100_000, 100_000)
        self.assertIsNone(detect_safe_raster(png))

    def test_animation_and_decoded_pixel_budgets_are_enforced(self):
        policy = RemoteFetchPolicy(
            max_animation_frames=2,
            max_image_decoded_pixels=150,
        )
        self.assertIsNotNone(inspect_safe_raster(_gif(5, 5, 2), policy))
        self.assertIsNone(inspect_safe_raster(_gif(5, 5, 3), policy))
        self.assertIsNone(inspect_safe_raster(_png(10, 10, 2), policy))

    def test_51_frame_gif_within_decoded_budget_is_accepted(self):
        raster = validate_declared_raster(_gif(600, 300, 51), "image/gif")
        self.assertEqual(raster.frames, 51)
        self.assertEqual(raster.decoded_pixels, 9_180_000)

    def test_connection_falls_back_to_next_validated_address(self):
        validated = ValidatedRemoteUrl(
            url="http://example.test/a.png",
            scheme="http",
            hostname="example.test",
            port=80,
            target="/a.png",
        )
        first_socket = MagicMock()
        first_socket.connect.side_effect = OSError("unreachable")
        connected_socket = MagicMock()
        with (
            patch(
                "viewer.remote_fetch.socket.getaddrinfo",
                return_value=[_address("93.184.216.34"), _address("93.184.216.35")],
            ) as resolve,
            patch(
                "viewer.remote_fetch.socket.socket",
                side_effect=[first_socket, connected_socket],
            ) as create_socket,
        ):
            connection = _open_connection(validated, time.monotonic() + 1)
        self.assertIs(connection.sock, connected_socket)
        resolve.assert_called_once()
        self.assertEqual(create_socket.call_count, 2)
        connection.close()

    def test_fetch_coordinator_honors_total_timeout(self):
        reference = RemoteImageReference("remote-test", "https://example.test/a.png")
        policy = RemoteFetchPolicy(
            max_workers=1,
            request_timeout_seconds=0.05,
            total_timeout_seconds=0.02,
        )

        def slow_fetch(ref, *_args):
            time.sleep(0.2)
            return ref.token, InlineAsset(BLANK_PNG_BYTES, "image/png")

        started = time.monotonic()
        with patch("viewer.remote_fetch._fetch_one", side_effect=slow_fetch):
            assets, errors = fetch_remote_images((reference,), policy)
        self.assertLess(time.monotonic() - started, 0.15)
        self.assertEqual(assets, {})
        self.assertTrue(any("total time limit" in error.reason for error in errors))

    def test_failure_report_contains_requested_structured_fields(self):
        reference = RemoteImageReference(
            "opaque-token",
            "https://images.example.test/private/pixel.png?recipient=secret",
        )
        policy = RemoteFetchPolicy(max_urls=1, max_workers=1)
        with patch(
            "viewer.remote_fetch._fetch_one",
            side_effect=RemoteImageError("Remote image resolved to a non-public address"),
        ):
            assets, errors = fetch_remote_images((reference,), policy)
        self.assertEqual(assets, {})
        self.assertEqual(len(errors), 1)
        failure = errors[0]
        self.assertEqual(failure.domain_or_ip, "images.example.test")
        self.assertIn("non-public address", failure.reason)
        self.assertEqual(failure.full_url, reference.url)
        self.assertNotIn("opaque-token", repr(failure))

    def test_active_image_mime_cannot_be_exposed_as_inline_asset(self):
        with self.assertRaises(ValueError):
            InlineAsset(b"<svg/>", "image/svg+xml")


if __name__ == "__main__":
    unittest.main()
