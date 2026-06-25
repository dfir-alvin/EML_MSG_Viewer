"""Shared security policy types and limit errors."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum


MiB = 1024 * 1024


class NetworkMode(str, Enum):
    """Network behavior selected by the application entry point."""

    OFFLINE = "offline"
    RESTRICTED_REMOTE_IMAGES = "restricted_remote_images"


@dataclass(frozen=True)
class ParseLimits:
    """Hard limits applied while processing an untrusted email file."""

    max_source_bytes: int = 100 * MiB
    max_mime_depth: int = 64
    max_parts: int = 2_000
    max_headers: int = 5_000
    max_body_bytes: int = 25 * MiB
    max_attachment_bytes: int = 100 * MiB
    max_decoded_bytes: int = 256 * MiB
    worker_memory_bytes: int = 768 * MiB
    parse_timeout_seconds: float = 20.0
    # TNEF/winmail.dat blobs are parsed by a third-party library that reads the
    # whole blob before any per-item budget engages, so they are capped both in
    # size (before parsing) and in embedded-message recursion depth.
    max_tnef_bytes: int = 32 * MiB
    max_tnef_embed_depth: int = 3

    def __post_init__(self) -> None:
        positive = (
            "max_source_bytes", "max_parts", "max_headers", "max_body_bytes",
            "max_attachment_bytes", "max_decoded_bytes", "worker_memory_bytes",
            "parse_timeout_seconds", "max_tnef_bytes", "max_tnef_embed_depth",
        )
        if any(getattr(self, name) <= 0 for name in positive) or self.max_mime_depth < 0:
            raise ValueError("Parse limits must be positive (MIME depth may be zero)")


@dataclass(frozen=True)
class RemoteFetchPolicy:
    """Limits for the automatic, SSRF-resistant remote-image fetcher."""

    max_urls: int = 50
    max_image_bytes: int = 10 * MiB
    max_total_bytes: int = 50 * MiB
    max_image_dimension: int = 8_192
    max_image_pixels: int = 16_000_000
    max_animation_frames: int = 200
    max_image_decoded_pixels: int = 40_000_000
    max_total_decoded_pixels: int = 80_000_000
    max_redirects: int = 3
    max_workers: int = 4
    request_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        positive = (
            "max_urls", "max_image_bytes", "max_total_bytes", "max_workers",
            "max_image_dimension", "max_image_pixels", "max_animation_frames",
            "max_image_decoded_pixels", "max_total_decoded_pixels",
            "request_timeout_seconds", "total_timeout_seconds",
        )
        if any(getattr(self, name) <= 0 for name in positive) or self.max_redirects < 0:
            raise ValueError("Remote fetch limits must be positive (redirects may be zero)")


@dataclass(frozen=True)
class InlineAsset:
    """A validated asset that may be exposed to QtWebEngine."""

    data: bytes
    mime_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise TypeError("Inline asset data must be bytes")
        if self.mime_type not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
            raise ValueError("Only validated raster MIME types may be exposed to WebEngine")


@dataclass(frozen=True)
class RemoteImageReference:
    token: str
    url: str


@dataclass(frozen=True)
class RemoteImageFailure:
    domain_or_ip: str
    reason: str
    full_url: str


@dataclass(frozen=True)
class SanitizedContent:
    html: str
    remote_images: tuple[RemoteImageReference, ...] = ()


DEFAULT_PARSE_LIMITS = ParseLimits()
DEFAULT_REMOTE_FETCH_POLICY = RemoteFetchPolicy()


class SecurityLimitError(ValueError):
    """Raised when an input exceeds a configured safe-processing limit."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_BLANK_PNG_DATA_URI = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
BLANK_PNG_BYTES = base64.b64decode(_BLANK_PNG_DATA_URI)
BLANK_PNG_DATA_URL = f"data:image/png;base64,{_BLANK_PNG_DATA_URI}"
