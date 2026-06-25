"""Utility helpers: filename sanitization, URL classification, MIME detection."""

import re
import mimetypes
import os

# Characters forbidden in Windows filenames
_FORBIDDEN = r'[<>:"/\\|?*\x00-\x1f]'
_BIDI_CONTROLS = re.compile(r'[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def safe_filename(name: str, fallback: str = "attachment") -> str:
    """Return a safe filename, stripping dangerous characters."""
    if not name:
        return fallback
    # Strip null bytes that some MSG parsers leave in strings
    name = name.strip('\x00')
    # Take only the basename (strip any path components)
    name = os.path.basename(name)
    # Replace forbidden characters with underscores
    name = re.sub(_FORBIDDEN, "_", name)
    # Prevent right-to-left override characters from disguising executable
    # extensions in the attachment list or save dialog.
    name = _BIDI_CONTROLS.sub("", name)
    # Strip leading/trailing dots and spaces
    name = name.strip(". ")
    if not name:
        return fallback
    # Check reserved Windows names (without extension)
    stem = os.path.splitext(name)[0].upper()
    if stem in _RESERVED_NAMES:
        name = "_" + name
    if len(name) > 240:
        stem, extension = os.path.splitext(name)
        extension = extension[:20]
        name = stem[: 240 - len(extension)] + extension
    return name


def classify_url(href: str) -> str:
    """Return a CSS class name for the link type."""
    if not href:
        return "url-other"
    href_lower = href.lower()
    if href_lower.startswith("https://"):
        return "url-https"
    if href_lower.startswith("http://"):
        return "url-http"
    if href_lower.startswith("mailto:"):
        return "url-mailto"
    return "url-other"


def detect_mime(data: bytes, filename: str = "") -> str:
    """Guess MIME type from filename extension, falling back to magic bytes."""
    if filename:
        mime, _ = mimetypes.guess_type(filename)
        if mime:
            return mime
    # Simple magic-byte detection for common image types
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    if data[:3] == b'GIF':
        return "image/gif"
    if data[:2] in (b'\xff\xd8',):
        return "image/jpeg"
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return "image/webp"
    return "application/octet-stream"

