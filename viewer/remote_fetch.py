"""Restricted remote-image fetching that never delegates networking to WebEngine."""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import struct
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

from viewer.security import (
    InlineAsset,
    RemoteFetchPolicy,
    RemoteImageFailure,
    RemoteImageReference,
)


_ALLOWED_CONTENT_TYPES = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/gif": "image/gif",
    "image/webp": "image/webp",
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class RemoteImageError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedRemoteUrl:
    url: str
    scheme: str
    hostname: str
    port: int
    target: str


@dataclass(frozen=True)
class SafeRasterInfo:
    mime_type: str
    width: int
    height: int
    frames: int = 1

    @property
    def decoded_pixels(self) -> int:
        return self.width * self.height * self.frames


class _TotalByteBudget:
    def __init__(self, limit: int):
        self._limit = limit
        self._used = 0
        self._lock = threading.Lock()

    def add(self, amount: int) -> None:
        with self._lock:
            new_total = self._used + amount
            if new_total > self._limit:
                raise RemoteImageError(
                    f"Total remote image bytes exceed limit: {new_total} "
                    f"(maximum {self._limit})"
                )
            self._used += amount


class _TotalPixelBudget:
    def __init__(self, limit: int):
        self._limit = limit
        self._used = 0
        self._lock = threading.Lock()

    def add(self, amount: int) -> None:
        with self._lock:
            new_total = self._used + amount
            if new_total > self._limit:
                raise RemoteImageError(
                    f"Total decoded image pixels exceed limit: {new_total} "
                    f"(maximum {self._limit})"
                )
            self._used += amount


def _failure_record(reference: RemoteImageReference, reason: object) -> RemoteImageFailure:
    """Return structured troubleshooting data for one failed image."""
    try:
        hostname = urlsplit(reference.url).hostname or "remote image"
        hostname = hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        hostname = "remote image"
    return RemoteImageFailure(
        domain_or_ip=hostname,
        reason=str(reason),
        full_url=reference.url,
    )


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    position = 2
    start_of_frame = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            return None
        marker = data[position]
        position += 1
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if position + 2 > len(data):
            return None
        segment_length = int.from_bytes(data[position:position + 2], "big")
        if segment_length < 2 or position + segment_length > len(data):
            return None
        if marker in start_of_frame and segment_length >= 7:
            height = int.from_bytes(data[position + 3:position + 5], "big")
            width = int.from_bytes(data[position + 5:position + 7], "big")
            return width, height
        position += segment_length
    return None


def _png_info(data: bytes) -> tuple[tuple[int, int], int] | None:
    if len(data) < 33 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    position = 8
    dimensions: tuple[int, int] | None = None
    frames = 1
    first_chunk = True
    saw_end = False
    while position + 12 <= len(data):
        chunk_length = int.from_bytes(data[position:position + 4], "big")
        chunk_type = data[position + 4:position + 8]
        payload_start = position + 8
        payload_end = payload_start + chunk_length
        if payload_end + 4 > len(data):
            return None
        if first_chunk:
            if chunk_type != b"IHDR" or chunk_length != 13:
                return None
            dimensions = struct.unpack(">II", data[payload_start:payload_start + 8])
            first_chunk = False
        elif chunk_type == b"acTL":
            if chunk_length != 8:
                return None
            frames = int.from_bytes(data[payload_start:payload_start + 4], "big")
            if frames <= 0:
                return None
        elif chunk_type == b"IEND":
            if chunk_length != 0:
                return None
            saw_end = True
            break
        position = payload_end + 4
    if dimensions is None or not saw_end:
        return None
    return dimensions, frames


def _skip_gif_sub_blocks(data: bytes, position: int) -> int | None:
    while position < len(data):
        block_size = data[position]
        position += 1
        if block_size == 0:
            return position
        if position + block_size > len(data):
            return None
        position += block_size
    return None


def _gif_info(data: bytes) -> tuple[tuple[int, int], int] | None:
    if len(data) < 14 or not data.startswith((b"GIF87a", b"GIF89a")):
        return None
    dimensions = struct.unpack("<HH", data[6:10])
    packed = data[10]
    position = 13
    if packed & 0x80:
        position += 3 * (2 ** ((packed & 0x07) + 1))
    if position > len(data):
        return None

    frames = 0
    while position < len(data):
        marker = data[position]
        if marker == 0x3B:  # trailer
            return (dimensions, frames) if frames else None
        if marker == 0x21:  # extension
            if position + 2 > len(data):
                return None
            position = _skip_gif_sub_blocks(data, position + 2)
            if position is None:
                return None
            continue
        if marker != 0x2C or position + 10 > len(data):
            return None

        frames += 1
        descriptor_packed = data[position + 9]
        position += 10
        if descriptor_packed & 0x80:
            position += 3 * (2 ** ((descriptor_packed & 0x07) + 1))
        if position >= len(data):
            return None
        position += 1  # LZW minimum code size
        position = _skip_gif_sub_blocks(data, position)
        if position is None:
            return None
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30:
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    return None


def _webp_info(data: bytes) -> tuple[tuple[int, int], int] | None:
    dimensions = _webp_dimensions(data)
    if dimensions is None or len(data) < 20:
        return None
    declared_end = 8 + int.from_bytes(data[4:8], "little")
    if declared_end > len(data) or declared_end < 20:
        return None
    position = 12
    animated = False
    frames = 0
    while position + 8 <= declared_end:
        chunk_type = data[position:position + 4]
        chunk_length = int.from_bytes(data[position + 4:position + 8], "little")
        payload_start = position + 8
        payload_end = payload_start + chunk_length
        if payload_end > declared_end:
            return None
        if chunk_type == b"VP8X" and chunk_length >= 1:
            animated = bool(data[payload_start] & 0x02)
        elif chunk_type == b"ANIM":
            animated = True
        elif chunk_type == b"ANMF":
            frames += 1
        position = payload_end + (chunk_length & 1)
    if position != declared_end:
        return None
    if animated and frames == 0:
        return None
    return dimensions, frames if animated else 1


def _header_hex(data: bytes, length: int = 4) -> str:
    if not data:
        return "(empty)"
    return " ".join(f"{value:02X}" for value in data[:length])


def _header_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    # Treat the GIF8 family as GIF here; the full six-byte version signature
    # and container structure are validated separately below.
    if data.startswith(b"GIF8"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_safe_raster(
    data: bytes,
    policy: RemoteFetchPolicy | None = None,
) -> SafeRasterInfo:
    """Return bounded raster metadata or raise a value-rich rejection reason."""
    policy = policy or RemoteFetchPolicy()
    mime_type = _header_mime(data)
    if mime_type is None:
        raise RemoteImageError(f"Unsupported image header: {_header_hex(data)}")

    parsed: tuple[tuple[int, int], int] | None
    if mime_type == "image/png":
        parsed = _png_info(data)
    elif mime_type == "image/jpeg":
        dimensions = _jpeg_dimensions(data)
        parsed = (dimensions, 1) if dimensions else None
    elif mime_type == "image/gif":
        parsed = _gif_info(data)
    else:
        parsed = _webp_info(data)
    if parsed is None:
        header_length = 6 if mime_type == "image/gif" else 8
        raise RemoteImageError(
            f"Invalid {mime_type} structure; header: {_header_hex(data, header_length)}"
        )

    dimensions, frames = parsed
    width, height = dimensions
    if width <= 0 or height <= 0:
        raise RemoteImageError(f"Invalid image dimensions: {width}x{height}")
    if width > policy.max_image_dimension or height > policy.max_image_dimension:
        raise RemoteImageError(
            f"Image dimensions exceed limit: {width}x{height} "
            f"(maximum {policy.max_image_dimension} per dimension)"
        )
    pixels = width * height
    if pixels > policy.max_image_pixels:
        raise RemoteImageError(
            f"Image pixel count exceeds limit: {pixels} "
            f"(maximum {policy.max_image_pixels})"
        )
    if frames <= 0 or frames > policy.max_animation_frames:
        raise RemoteImageError(
            f"Image frame count exceeds limit: {frames} "
            f"(maximum {policy.max_animation_frames})"
        )
    if pixels * frames > policy.max_image_decoded_pixels:
        raise RemoteImageError(
            f"Decoded image pixels exceed limit: {pixels * frames} "
            f"(maximum {policy.max_image_decoded_pixels})"
        )
    return SafeRasterInfo(mime_type, width, height, frames)


def inspect_safe_raster(
    data: bytes,
    policy: RemoteFetchPolicy | None = None,
) -> SafeRasterInfo | None:
    """Return bounded raster metadata, or None for callers that need a predicate."""
    try:
        return validate_safe_raster(data, policy)
    except RemoteImageError:
        return None


def detect_safe_raster(data: bytes, policy: RemoteFetchPolicy | None = None) -> str | None:
    """Return a raster MIME only when its structure and decoded size are safe."""
    info = inspect_safe_raster(data, policy)
    return info.mime_type if info else None


def validate_declared_raster(
    data: bytes,
    expected_mime: str,
    policy: RemoteFetchPolicy | None = None,
) -> SafeRasterInfo:
    """Validate header MIME and structure, preserving offending values in errors."""
    header_mime = _header_mime(data)
    if header_mime != expected_mime:
        raise RemoteImageError(
            f"MIME mismatch: expected {expected_mime}; header "
            f"{_header_hex(data)} indicates {header_mime or 'unknown data'}"
        )
    return validate_safe_raster(data, policy)


def _is_public_address(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
        parsed = parsed.ipv4_mapped
    return parsed.is_global


def validate_remote_url(url: str) -> ValidatedRemoteUrl:
    """Validate URL syntax and policy without performing a DNS lookup."""
    if not url or any(ord(ch) < 0x20 for ch in url):
        raise RemoteImageError("Malformed remote image URL")
    try:
        parsed: SplitResult = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise RemoteImageError("Malformed remote image URL") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise RemoteImageError("Only HTTP and HTTPS images are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise RemoteImageError("Credentials are not allowed in remote image URLs")
    if not parsed.hostname:
        raise RemoteImageError("Remote image URL has no hostname")

    raw_hostname = parsed.hostname
    if "%" in raw_hostname:
        raise RemoteImageError("Scoped IP addresses are not allowed")
    try:
        literal_address = ipaddress.ip_address(raw_hostname)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        hostname = raw_hostname.lower()
        if not _is_public_address(hostname):
            raise RemoteImageError(f"Non-public IP address: {hostname}")
    else:
        try:
            hostname = raw_hostname.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise RemoteImageError("Invalid remote image hostname") from exc

    expected_port = 443 if scheme == "https" else 80
    port = port or expected_port
    if port != expected_port:
        raise RemoteImageError(f"Remote image port is not allowed: {port}")

    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    canonical_host = f"[{hostname}]" if ":" in hostname else hostname
    canonical = urlunsplit((scheme, canonical_host, parsed.path or "/", parsed.query, ""))
    return ValidatedRemoteUrl(
        url=canonical,
        scheme=scheme,
        hostname=hostname,
        port=port,
        target=target,
    )


def _host_header(validated: ValidatedRemoteUrl) -> str:
    host = validated.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return host


def _open_connection(
    validated: ValidatedRemoteUrl,
    deadline: float,
) -> http.client.HTTPConnection:
    """Resolve, validate, and connect as one operation immediately before GET."""
    try:
        resolved = socket.getaddrinfo(
            validated.hostname,
            validated.port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except OSError as exc:
        raise RemoteImageError(f"Remote image hostname could not be resolved: {exc}") from exc

    addresses: list[str] = []
    for item in resolved:
        address = item[4][0]
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise RemoteImageError("Remote image hostname resolved to no IP addresses")
    non_public = [address for address in addresses if not _is_public_address(address)]
    if non_public:
        raise RemoteImageError(f"Non-public IP address: {', '.join(non_public)}")

    last_error: OSError | None = None
    for address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        raw = None
        try:
            parsed_address = ipaddress.ip_address(address.split("%", 1)[0])
            family = socket.AF_INET6 if parsed_address.version == 6 else socket.AF_INET
            raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            raw.settimeout(remaining)
            destination = (
                (address, validated.port, 0, 0)
                if family == socket.AF_INET6
                else (address, validated.port)
            )
            raw.connect(destination)
            if validated.scheme == "https":
                context = ssl.create_default_context()
                raw = context.wrap_socket(raw, server_hostname=validated.hostname)

            connection = http.client.HTTPConnection(
                validated.hostname,
                validated.port,
                timeout=remaining,
            )
            connection.sock = raw
            return connection
        except OSError as exc:
            last_error = exc
            if raw is not None:
                raw.close()
    if last_error is None:
        raise RemoteImageError("Remote image connection timed out")
    raise RemoteImageError(f"Remote image host could not be reached: {last_error}") from last_error


def _fetch_one(
    reference: RemoteImageReference,
    policy: RemoteFetchPolicy,
    deadline: float,
    byte_budget: _TotalByteBudget,
    pixel_budget: _TotalPixelBudget,
) -> tuple[str, InlineAsset]:
    current_url = reference.url
    for redirect_count in range(policy.max_redirects + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RemoteImageError("Remote image fetch timed out")
        request_deadline = min(
            deadline,
            time.monotonic() + policy.request_timeout_seconds,
        )
        validated = validate_remote_url(current_url)
        connection = _open_connection(validated, request_deadline)
        try:
            connection.request(
                "GET",
                validated.target,
                headers={
                    "Host": _host_header(validated),
                    "Accept": "image/png,image/jpeg,image/gif,image/webp",
                    "Accept-Encoding": "identity",
                    "User-Agent": "EML-MSG-Viewer-ImageFetcher/1",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status in _REDIRECT_STATUSES:
                if redirect_count >= policy.max_redirects:
                    raise RemoteImageError("Remote image has too many redirects")
                location = response.getheader("Location")
                if not location:
                    raise RemoteImageError("Remote image redirect has no destination")
                current_url = urljoin(validated.url, location)
                continue
            if response.status != 200:
                raise RemoteImageError(f"Remote image returned HTTP {response.status}")

            encoding = (response.getheader("Content-Encoding") or "identity").lower()
            if encoding not in ("", "identity"):
                raise RemoteImageError(f"Unsupported Content-Encoding: {encoding}")

            declared = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
            expected_mime = _ALLOWED_CONTENT_TYPES.get(declared)
            if expected_mime is None:
                raise RemoteImageError(
                    f"Unsupported Content-Type: {declared or '(missing)'}"
                )

            length_header = response.getheader("Content-Length")
            if length_header:
                try:
                    declared_length = int(length_header)
                except ValueError as exc:
                    raise RemoteImageError(
                        f"Invalid Content-Length: {length_header}"
                    ) from exc
                if declared_length < 0 or declared_length > policy.max_image_bytes:
                    raise RemoteImageError(
                        f"Remote image Content-Length exceeds limit: {declared_length} "
                        f"(maximum {policy.max_image_bytes})"
                    )

            chunks: list[bytes] = []
            image_size = 0
            while True:
                remaining = request_deadline - time.monotonic()
                if remaining <= 0:
                    raise RemoteImageError("Remote image fetch timed out")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = response.read(min(64 * 1024, policy.max_image_bytes + 1 - image_size))
                if not chunk:
                    break
                image_size += len(chunk)
                if image_size > policy.max_image_bytes:
                    raise RemoteImageError(
                        f"Downloaded image bytes exceed limit: {image_size} "
                        f"(maximum {policy.max_image_bytes})"
                    )
                byte_budget.add(len(chunk))
                chunks.append(chunk)

            data = b"".join(chunks)
            raster = validate_declared_raster(data, expected_mime, policy)
            pixel_budget.add(raster.decoded_pixels)
            return reference.token, InlineAsset(data=data, mime_type=raster.mime_type)
        finally:
            connection.close()

    raise RemoteImageError("Remote image has too many redirects")


def fetch_remote_images(
    references: tuple[RemoteImageReference, ...],
    policy: RemoteFetchPolicy,
) -> tuple[dict[str, InlineAsset], tuple[RemoteImageFailure, ...]]:
    """Fetch a bounded set of images concurrently and return safe local assets."""
    refs = references[: policy.max_urls]
    if not refs:
        return {}, ()

    deadline = time.monotonic() + policy.total_timeout_seconds
    byte_budget = _TotalByteBudget(policy.max_total_bytes)
    pixel_budget = _TotalPixelBudget(policy.max_total_decoded_pixels)
    assets: dict[str, InlineAsset] = {}
    errors: list[RemoteImageFailure] = []

    executor = ThreadPoolExecutor(max_workers=policy.max_workers, thread_name_prefix="remote-image")
    futures = {
        executor.submit(
            _fetch_one,
            ref,
            policy,
            deadline,
            byte_budget,
            pixel_budget,
        ): ref
        for ref in refs
    }
    pending = set(futures)
    try:
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                for future in pending:
                    errors.append(
                        _failure_record(
                            futures[future],
                            "Remote image fetch reached the total time limit",
                        )
                    )
                break
            completed, pending = wait(
                pending,
                timeout=remaining,
                return_when=FIRST_COMPLETED,
            )
            if not completed:
                for future in pending:
                    errors.append(
                        _failure_record(
                            futures[future],
                            "Remote image fetch reached the total time limit",
                        )
                    )
                break
            for future in completed:
                ref = futures[future]
                try:
                    token, asset = future.result()
                    assets[token] = asset
                except MemoryError:
                    raise
                except Exception as exc:
                    errors.append(_failure_record(ref, exc))
    finally:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    return assets, tuple(errors)
