import unittest
import struct
from pathlib import Path

try:
    from viewer.sanitizer import sanitize_html
    from viewer.load_worker import process_email
    from viewer.security import (
        DEFAULT_PARSE_LIMITS,
        DEFAULT_REMOTE_FETCH_POLICY,
        InlineAsset,
        NetworkMode,
        RemoteFetchPolicy,
    )
except ImportError as import_error:  # pragma: no cover - dependency bootstrap only
    sanitize_html = None
    IMPORT_ERROR = import_error
else:
    IMPORT_ERROR = None


@unittest.skipIf(sanitize_html is None, f"sanitizer dependencies unavailable: {IMPORT_ERROR}")
class SanitizerSecurityTests(unittest.TestCase):
    def test_xss_and_navigation_elements_are_removed(self):
        result = sanitize_html(
            '<script>alert(1)</script><iframe src="https://evil.test"></iframe>'
            '<img src=x onerror="alert(2)">',
            {},
        )
        self.assertNotIn("<script", result.html.lower())
        self.assertNotIn("<iframe", result.html.lower())
        self.assertNotIn("onerror", result.html.lower())

    def test_mutation_xss_shape_stays_inert(self):
        payload = (
            '<math><mtext><table><mglyph><style><!--</style>'
            '<img title="--><img src=x onerror=alert(1)>">'
        )
        result = sanitize_html(payload, {})
        self.assertNotIn("onerror", result.html.lower())
        self.assertNotIn("<style><!--", result.html.lower())

    def test_document_head_metadata_does_not_become_visible_body_text(self):
        result = sanitize_html(
            "<html><head><title>Email from G2A.COM</title>"
            "<meta name='description' content='metadata'></head>"
            "<body><p>Visible message</p></body></html>",
            {},
        )
        self.assertNotIn("Email from G2A.COM", result.html)
        self.assertNotIn("metadata", result.html)
        self.assertIn("Visible message", result.html)

    def test_remote_src_never_reaches_webengine(self):
        url = "http://127.0.0.1:80/admin?action=delete"
        result = sanitize_html(
            f'<img src="{url}">',
            {},
            NetworkMode.RESTRICTED_REMOTE_IMAGES,
        )
        self.assertNotIn(f'src="{url}"', result.html)
        self.assertIn('src="cid:remote-', result.html)
        self.assertEqual(result.remote_images[0].url, url)

    def test_css_url_tokens_and_escaped_forms_are_dropped(self):
        payload = (
            r'<div style="background-image:url(https://evil.test/a.png);'
            r'color:red;list-style:u\72 l(https://evil.test/b.png)">safe</div>'
        )
        result = sanitize_html(payload, {})
        lowered = result.html.lower()
        self.assertNotIn("evil.test", lowered)
        self.assertIn("color:red", lowered.replace(" ", ""))

    def test_embedded_raster_stays_on_local_cid_scheme(self):
        data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + struct.pack(">II", 10, 10)
        asset = InlineAsset(data, "image/png")
        result = sanitize_html('<img src="cid:image001@example">', {"image001@example": asset})
        self.assertIn('src="cid:image001@example"', result.html)
        self.assertEqual(result.remote_images, ())

    def test_remote_url_count_is_bounded(self):
        html = "".join(f'<img src="https://example.test/{i}.png">' for i in range(3))
        result = sanitize_html(
            html,
            {},
            NetworkMode.RESTRICTED_REMOTE_IMAGES,
            RemoteFetchPolicy(max_urls=2),
        )
        self.assertEqual(len(result.remote_images), 2)

    def test_sample_email_is_parsed_and_sanitized_in_worker_pipeline(self):
        sample = Path(__file__).resolve().parents[1] / "test_email.eml"
        loaded = process_email(
            str(sample),
            DEFAULT_PARSE_LIMITS,
            NetworkMode.OFFLINE,
            DEFAULT_REMOTE_FETCH_POLICY,
        )
        self.assertEqual(loaded.parsed.subject, "Test Email with HTML and Attachments")
        self.assertEqual(loaded.parsed.html_body, "")
        self.assertIn("Content-Security-Policy", loaded.rendered_html)

if __name__ == "__main__":
    unittest.main()
