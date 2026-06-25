"""Bounded parsing of EML, MSG, and TNEF files into a common model."""

from __future__ import annotations

import email
import email.parser
import email.policy
import os
from dataclasses import dataclass, field

from viewer.remote_fetch import detect_safe_raster
from viewer.security import (
    DEFAULT_PARSE_LIMITS,
    InlineAsset,
    ParseLimits,
    SecurityLimitError,
)
from viewer.utils import detect_mime, safe_filename


@dataclass
class AttachmentInfo:
    filename: str
    mime_type: str
    data: bytes


@dataclass
class ParsedEmail:
    subject: str = ""
    from_: str = ""
    to: str = ""
    date: str = ""
    return_path: str = ""
    all_headers: list[tuple[str, str]] = field(default_factory=list)
    html_body: str = ""
    text_body: str = ""
    inline_images: dict[str, InlineAsset] = field(default_factory=dict)
    attachments: list[AttachmentInfo] = field(default_factory=list)
    processing_notes: list[str] = field(default_factory=list)
    # Raw bytes, source filename, and parsed headers of an embedded TNEF
    # (winmail.dat / webmail.dat) found inside an opened eml/msg. Empty unless
    # such an attachment was present.
    embedded_tnef_data: bytes = b""
    embedded_tnef_name: str = ""
    embedded_tnef_headers: list[tuple[str, str]] = field(default_factory=list)


class _ParseBudget:
    def __init__(self, limits: ParseLimits):
        self.limits = limits
        self.parts = 0
        self.headers = 0
        self.decoded_bytes = 0

    def add_part(self, depth: int) -> None:
        if depth > self.limits.max_mime_depth:
            raise SecurityLimitError(
                "mime_depth",
                f"Message exceeds the maximum MIME depth ({self.limits.max_mime_depth})",
            )
        self.parts += 1
        if self.parts > self.limits.max_parts:
            raise SecurityLimitError(
                "part_count",
                f"Message contains more than {self.limits.max_parts} MIME parts",
            )

    def add_headers(self, count: int) -> None:
        self.headers += count
        if self.headers > self.limits.max_headers:
            raise SecurityLimitError(
                "header_count",
                f"Message contains more than {self.limits.max_headers} headers",
            )

    def consume(self, data: bytes, per_item_limit: int, label: str) -> bytes:
        size = len(data)
        if size > per_item_limit:
            raise SecurityLimitError(
                f"{label}_size",
                f"{label.capitalize()} exceeds its safe size limit",
            )
        self.decoded_bytes += size
        if self.decoded_bytes > self.limits.max_decoded_bytes:
            raise SecurityLimitError(
                "decoded_size",
                "Decoded message content exceeds the total safe size limit",
            )
        return data


def _strip_cid(cid: str) -> str:
    return cid.strip().lstrip("<").rstrip(">")


def _check_tnef_size(data: bytes, limits: ParseLimits) -> None:
    """Bound a TNEF blob before the third-party parser reads all of it.

    The tnefparse library parses the entire blob inside its constructor, before
    any per-item _ParseBudget check can engage, so this dedicated cap is applied
    up front at every TNEF entry point.
    """
    if len(data) > limits.max_tnef_bytes:
        raise SecurityLimitError(
            "tnef_size",
            f"TNEF/winmail.dat content exceeds the "
            f"{limits.max_tnef_bytes // (1024 * 1024)} MiB limit",
        )


def _read_limited(path: str, limits: ParseLimits) -> bytes:
    try:
        size = os.path.getsize(path)
    except OSError:
        size = -1
    if size > limits.max_source_bytes:
        raise SecurityLimitError(
            "source_size",
            f"Email file exceeds the {limits.max_source_bytes // (1024 * 1024)} MiB limit",
        )
    with open(path, "rb") as handle:
        raw = handle.read(limits.max_source_bytes + 1)
    if len(raw) > limits.max_source_bytes:
        raise SecurityLimitError(
            "source_size",
            f"Email file exceeds the {limits.max_source_bytes // (1024 * 1024)} MiB limit",
        )
    return raw


def _decode_text(data: bytes, charset: str | None) -> str:
    try:
        return data.decode(charset or "utf-8", errors="replace")
    except (LookupError, UnicodeError):
        return data.decode("utf-8", errors="replace")


def _part_bytes(part) -> bytes:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        if isinstance(raw, str):
            return raw.encode(part.get_content_charset() or "utf-8", errors="replace")
        return b""
    return bytes(payload)


_TNEF_MAGIC = b"\x78\x9f\x3e\x22"
_TNEF_FILENAMES = {"winmail.dat", "webmail.dat"}

_MAPI_HEADER_SKIP = frozenset({
    0x1000,  # MAPI_BODY
    0x1009,  # MAPI_RTF_COMPRESSED
    0x1013,  # MAPI_BODY_HTML
})

_MAPI_NAMES: dict[int, str] | None = None


def _get_mapi_name(prop_id: int) -> str:
    global _MAPI_NAMES
    if _MAPI_NAMES is None:
        try:
            import tnefparse.properties as _p
            _MAPI_NAMES = {
                v: k[5:].replace("_", " ").title()
                for k, v in vars(_p).items()
                if k.startswith("MAPI_") and isinstance(v, int)
            }
        except ImportError:
            _MAPI_NAMES = {}
    return _MAPI_NAMES.get(prop_id) or f"MAPI-0x{prop_id:04X}"


def _tnef_prop_value(prop) -> str | None:
    value = prop.data
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="strict").rstrip("\x00")
        except UnicodeDecodeError:
            try:
                return value.decode("utf-16-le", errors="strict").rstrip("\x00")
            except UnicodeDecodeError:
                return None
    if value is None:
        return None
    return str(value)


def _rtf_to_html(
    rtf_bytes: bytes,
    limits: ParseLimits,
    budget: _ParseBudget,
) -> str:
    budget.consume(bytes(rtf_bytes), limits.max_attachment_bytes, "RTF body")
    try:
        import RTFDE

        decoder = RTFDE.DeEncapsulator(rtf_bytes)
        decoder.deencapsulate()
        converted = getattr(decoder, "html", None) or getattr(decoder, "text", None) or ""
        if isinstance(converted, bytes):
            converted = converted.decode("utf-8", errors="replace")
        encoded = str(converted).encode("utf-8", errors="replace")
        budget.consume(encoded, limits.max_body_bytes, "message body")
        return str(converted)
    except (SecurityLimitError, MemoryError):
        raise
    except Exception:
        return ""


class EmlParser:
    def __init__(self, limits: ParseLimits = DEFAULT_PARSE_LIMITS):
        self.limits = limits

    def parse(self, path: str) -> ParsedEmail:
        raw = _read_limited(path, self.limits)
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        message = email.parser.BytesParser(policy=email.policy.default).parsebytes(raw)

        result = ParsedEmail(
            subject=str(message.get("Subject", "")),
            from_=str(message.get("From", "")),
            to=str(message.get("To", "")),
            date=str(message.get("Date", "")),
            return_path=str(message.get("Return-Path", "")),
            all_headers=[(name, str(value)) for name, value in message.items()],
        )
        budget = _ParseBudget(self.limits)
        self._walk_iterative(message, result, budget)
        return result

    def _walk_iterative(self, root, result: ParsedEmail, budget: _ParseBudget) -> None:
        stack = [(root, 0)]
        while stack:
            part, depth = stack.pop()
            budget.add_part(depth)
            budget.add_headers(len(part.items()))

            if part.is_multipart():
                children = list(part.iter_parts())
                for child in reversed(children):
                    stack.append((child, depth + 1))
                continue

            content_type = part.get_content_type()
            disposition = part.get_content_disposition() or ""
            cid_raw = part.get("Content-ID", "")
            cid = _strip_cid(cid_raw) if cid_raw else ""
            data = _part_bytes(part)

            if content_type == "text/html" and disposition != "attachment":
                data = budget.consume(data, self.limits.max_body_bytes, "message body")
                result.html_body = _decode_text(data, part.get_content_charset())
                continue

            if content_type == "text/plain" and disposition != "attachment" and not result.html_body:
                data = budget.consume(data, self.limits.max_body_bytes, "message body")
                result.text_body = _decode_text(data, part.get_content_charset())
                continue

            if cid and content_type.startswith("image/"):
                data = budget.consume(data, self.limits.max_attachment_bytes, "inline image")
                safe_mime = detect_safe_raster(data)
                if safe_mime:
                    result.inline_images[cid] = InlineAsset(data=data, mime_type=safe_mime)
                else:
                    result.attachments.append(
                        AttachmentInfo(
                            filename=safe_filename(part.get_filename() or cid, "inline-image"),
                            mime_type=content_type,
                            data=data,
                        )
                    )
                continue

            if disposition == "attachment" or (
                content_type not in ("text/html", "text/plain") and not cid
            ):
                if not data:
                    continue
                data = budget.consume(data, self.limits.max_attachment_bytes, "attachment")
                filename = safe_filename(part.get_filename() or "", fallback="attachment")
                if filename.lower() in _TNEF_FILENAMES and data[:4] == _TNEF_MAGIC:
                    _merge_tnef(data, result, self.limits, budget, filename)
                else:
                    result.attachments.append(
                        AttachmentInfo(filename=filename, mime_type=content_type, data=data)
                    )


class MsgParser:
    def __init__(self, limits: ParseLimits = DEFAULT_PARSE_LIMITS):
        self.limits = limits

    def parse(self, path: str) -> ParsedEmail:
        _read_limited(path, self.limits)
        try:
            import extract_msg
        except ImportError as exc:
            raise ImportError("extract-msg is required to open MSG files") from exc

        message = extract_msg.openMsg(path)
        budget = _ParseBudget(self.limits)
        result = ParsedEmail()
        try:
            budget.add_part(0)
            result.subject = message.subject or ""
            result.from_ = message.sender or ""
            result.to = message.to or ""
            result.date = str(message.date) if message.date else ""

            header_parsed = False
            try:
                if message.header:
                    raw_header = (
                        message.header.as_string()
                        if hasattr(message.header, "as_string")
                        else str(message.header)
                    )
                    parsed_header = email.message_from_string(
                        raw_header,
                        policy=email.policy.default,
                    )
                    result.all_headers = [(key, str(value)) for key, value in parsed_header.items()]
                    budget.add_headers(len(result.all_headers))
                    result.return_path = str(parsed_header.get("Return-Path", ""))
                    result.subject = result.subject or str(parsed_header.get("Subject", ""))
                    result.from_ = result.from_ or str(parsed_header.get("From", ""))
                    result.to = result.to or str(parsed_header.get("To", ""))
                    result.date = result.date or str(parsed_header.get("Date", ""))
                    header_parsed = bool(result.all_headers)
            except (SecurityLimitError, MemoryError):
                raise
            except Exception:
                pass

            if not header_parsed:
                result.all_headers = self._headers_from_msg(message)
                budget.add_headers(len(result.all_headers))

            html = getattr(message, "htmlBody", None)
            if html:
                html_bytes = html if isinstance(html, bytes) else str(html).encode("utf-8")
                budget.consume(html_bytes, self.limits.max_body_bytes, "message body")
                result.html_body = _decode_text(html_bytes, "utf-8")

            text = getattr(message, "body", None) or ""
            if text:
                text_bytes = text if isinstance(text, bytes) else str(text).encode("utf-8")
                budget.consume(text_bytes, self.limits.max_body_bytes, "message body")
                result.text_body = _decode_text(text_bytes, "utf-8")

            for attachment in message.attachments or []:
                budget.add_part(1)
                raw_data = getattr(attachment, "data", None)
                if not isinstance(raw_data, (bytes, bytearray, memoryview)):
                    continue
                data = budget.consume(
                    bytes(raw_data),
                    self.limits.max_attachment_bytes,
                    "attachment",
                )
                cid = _strip_cid(getattr(attachment, "cid", None) or "")
                raw_name = (
                    getattr(attachment, "longFilename", None)
                    or getattr(attachment, "shortFilename", None)
                    or ""
                )
                filename = safe_filename(raw_name, fallback="attachment")
                declared_mime = (
                    getattr(attachment, "mimetype", None) or "application/octet-stream"
                ).strip("\x00")
                safe_mime = detect_safe_raster(data) if cid else None
                if cid and safe_mime:
                    result.inline_images[cid] = InlineAsset(data=data, mime_type=safe_mime)
                elif filename.lower() in _TNEF_FILENAMES and data[:4] == _TNEF_MAGIC:
                    _merge_tnef(data, result, self.limits, budget, filename)
                else:
                    result.attachments.append(
                        AttachmentInfo(filename=filename, mime_type=declared_mime, data=data)
                    )

            if not result.html_body and not result.text_body:
                rtf = getattr(message, "rtfBody", None)
                if isinstance(rtf, (bytes, bytearray, memoryview)) and rtf:
                    result.html_body = _rtf_to_html(bytes(rtf), self.limits, budget)
            return result
        finally:
            try:
                message.close()
            except Exception:
                pass

    @staticmethod
    def _headers_from_msg(message) -> list[tuple[str, str]]:
        candidates = [
            ("Date", "date"),
            ("From", "sender"),
            ("To", "to"),
            ("CC", "cc"),
            ("BCC", "bcc"),
            ("Subject", "subject"),
            ("Reply-To", "reply_to"),
            ("Message-ID", "message_id"),
            ("In-Reply-To", "in_reply_to"),
            ("References", "references"),
        ]
        headers: list[tuple[str, str]] = []
        for display, attribute in candidates:
            value = getattr(message, attribute, None)
            if value:
                headers.append((display, value if isinstance(value, str) else str(value)))
        return headers


class TnefParser:
    def __init__(self, limits: ParseLimits = DEFAULT_PARSE_LIMITS):
        self.limits = limits

    def parse(self, path: str) -> ParsedEmail:
        data = _read_limited(path, self.limits)
        _check_tnef_size(data, self.limits)
        budget = _ParseBudget(self.limits)
        budget.consume(data, self.limits.max_attachment_bytes, "TNEF data")
        return self._parse_bytes(data, budget)

    def parse_bytes(self, data: bytes, budget: _ParseBudget | None = None) -> ParsedEmail:
        _check_tnef_size(data, self.limits)
        active_budget = budget or _ParseBudget(self.limits)
        if budget is None:
            active_budget.consume(data, self.limits.max_attachment_bytes, "TNEF data")
        return self._parse_bytes(data, active_budget)

    def _parse_bytes(self, data: bytes, budget: _ParseBudget) -> ParsedEmail:
        try:
            from tnefparse import TNEF
        except ImportError as exc:
            raise ImportError("tnefparse is required to open TNEF files") from exc

        tnef = self._construct_tnef(TNEF, data)
        result = ParsedEmail()
        budget.add_part(0)
        self._extract_metadata(tnef, result, budget)
        self._extract_body(tnef, result, budget)
        self._extract_attachments(tnef, result, budget)
        return result

    def _construct_tnef(self, tnef_class, data: bytes):
        """Build the TNEF object under a deterministic embedded-depth bound.

        tnefparse recurses on embedded-message attachments by instantiating the
        same TNEF class inside its own constructor, with no depth limit. We wrap
        __init__ with a depth counter so a hostile chain of nested TNEF messages
        fails fast and explicitly as a SecurityLimitError. The recursive call
        site (TNEFAttachment.add_attr) is not guarded by the library's own
        try/except, so the error propagates out cleanly rather than being
        swallowed. Parsing is single-threaded within the worker, so temporarily
        replacing the class initializer is safe here.
        """
        max_depth = self.limits.max_tnef_embed_depth
        original_init = tnef_class.__init__
        depth = {"value": 0}

        def counting_init(instance, *args, **kwargs):
            if depth["value"] >= max_depth:
                raise SecurityLimitError(
                    "tnef_embed_depth",
                    "TNEF/winmail.dat exceeds the maximum embedded-message depth",
                )
            depth["value"] += 1
            try:
                original_init(instance, *args, **kwargs)
            finally:
                depth["value"] -= 1

        tnef_class.__init__ = counting_init
        try:
            return tnef_class(data, do_checksum=False)
        finally:
            tnef_class.__init__ = original_init

    def _extract_metadata(self, tnef, result: ParsedEmail, budget: _ParseBudget) -> None:
        def mapi_string(properties, property_id: int) -> str:
            for prop in properties:
                if prop.name == property_id:
                    value = prop.data
                    if isinstance(value, bytes):
                        return value.decode("utf-8", errors="replace").rstrip("\x00")
                    return str(value) if value is not None else ""
            return ""

        properties = getattr(tnef, "mapiprops", []) or []
        budget.add_headers(len(properties))
        result.subject = mapi_string(properties, 0x0037)
        sender_name = mapi_string(properties, 0x0C1A)
        sender_email = mapi_string(properties, 0x5D01) or mapi_string(properties, 0x0C1F)
        if sender_name and sender_email:
            result.from_ = f"{sender_name} <{sender_email}>"
        else:
            result.from_ = sender_email or sender_name
        result.to = mapi_string(properties, 0x0E04)
        for prop in properties:
            if prop.name == 0x0039:
                result.date = str(prop.data)
                break

        transport_headers = mapi_string(properties, 0x007D)
        if transport_headers:
            parsed = email.message_from_string(transport_headers)
            result.all_headers = [(k, str(v)) for k, v in parsed.items()]
        else:
            headers: list[tuple[str, str]] = []
            for prop in properties:
                if prop.name in _MAPI_HEADER_SKIP:
                    continue
                display_value = _tnef_prop_value(prop)
                if display_value:
                    headers.append((_get_mapi_name(prop.name), display_value))
            result.all_headers = headers

    def _extract_body(self, tnef, result: ParsedEmail, budget: _ParseBudget) -> None:
        html = getattr(tnef, "htmlbody", None)
        if html:
            html_bytes = html if isinstance(html, bytes) else str(html).encode("utf-8")
            budget.consume(html_bytes, self.limits.max_body_bytes, "message body")
            result.html_body = _decode_text(html_bytes, "utf-8")
            return

        rtf = getattr(tnef, "rtfbody", None)
        if isinstance(rtf, (bytes, bytearray, memoryview)) and rtf:
            converted = _rtf_to_html(bytes(rtf), self.limits, budget)
            if converted:
                result.html_body = converted
                return

        body = getattr(tnef, "body", None)
        if body:
            body_bytes = body if isinstance(body, bytes) else str(body).encode("utf-8")
            budget.consume(body_bytes, self.limits.max_body_bytes, "message body")
            result.text_body = _decode_text(body_bytes, "utf-8")

    def _extract_attachments(self, tnef, result: ParsedEmail, budget: _ParseBudget) -> None:
        try:
            dump_attachments = tnef.dump().get("attachments", [])
        except MemoryError:
            raise
        except Exception:
            dump_attachments = []

        for index, attachment in enumerate(getattr(tnef, "attachments", None) or []):
            budget.add_part(1)
            raw_data = getattr(attachment, "data", None)
            if not isinstance(raw_data, (bytes, bytearray, memoryview)) or not raw_data:
                continue
            data = budget.consume(
                bytes(raw_data),
                self.limits.max_attachment_bytes,
                "attachment",
            )
            dump_info = dump_attachments[index] if index < len(dump_attachments) else {}
            raw_name = dump_info.get("long_filename") or dump_info.get("filename") or ""
            if not raw_name:
                try:
                    raw_name = attachment.long_filename() or ""
                except Exception:
                    raw_name = getattr(attachment, "name", None) or ""
            filename = safe_filename(raw_name, fallback="attachment")

            declared_mime = ""
            cid = ""
            for attribute in getattr(attachment, "mapi_attrs", None) or []:
                if attribute.name == 0x370E:
                    value = attribute.data
                    declared_mime = (
                        value.decode("utf-8", errors="replace").rstrip("\x00")
                        if isinstance(value, bytes)
                        else str(value)
                    ).strip()
                elif attribute.name == 0x3712:
                    value = attribute.data
                    raw_cid = (
                        value.decode("utf-8", errors="replace").rstrip("\x00")
                        if isinstance(value, bytes)
                        else str(value)
                    )
                    cid = _strip_cid(raw_cid)

            mime_type = declared_mime or detect_mime(data, filename)
            safe_mime = detect_safe_raster(data) if cid else None
            if cid and safe_mime:
                result.inline_images[cid] = InlineAsset(data=data, mime_type=safe_mime)
            else:
                result.attachments.append(
                    AttachmentInfo(filename=filename, mime_type=mime_type, data=data)
                )


def _merge_tnef(
    tnef_data: bytes,
    result: ParsedEmail,
    limits: ParseLimits,
    budget: _ParseBudget,
    source_name: str,
) -> None:
    # Surface the raw blob and source name so the GUI can offer the
    # "<name> Headers" and "Export <name>" controls for the embedded TNEF
    # (winmail.dat / webmail.dat), keeping the merged content as the default view.
    result.embedded_tnef_data = tnef_data
    result.embedded_tnef_name = source_name

    def preserve_as_attachment(reason: str) -> None:
        result.processing_notes.append(
            f"Embedded {source_name} TNEF attachment was detected but {reason}; "
            "it was preserved as an attachment."
        )
        result.attachments.append(
            AttachmentInfo(
                filename=source_name,
                mime_type="application/ms-tnef",
                data=tnef_data,
            )
        )

    try:
        parsed = TnefParser(limits).parse_bytes(tnef_data, budget)
    except SecurityLimitError as exc:
        # TNEF-specific guards (size/embedded depth) only bound the embedded
        # blob, not the parent message. Preserve it so an analyst can still
        # export and inspect it. Parent-message budget exhaustion (decoded
        # size, part/header counts) is a genuine limit and still propagates.
        if exc.code in ("tnef_size", "tnef_embed_depth"):
            preserve_as_attachment("exceeded a TNEF safety limit")
            return
        raise
    except MemoryError:
        raise
    except Exception:
        preserve_as_attachment("could not be parsed")
        return

    result.processing_notes.append(
        f"Embedded {source_name} TNEF attachment was detected and parsed inside the opened email."
    )
    # Preserve the TNEF's own headers for the "<name> Headers" view, alongside
    # the merged content below. The raw blob/name were captured above.
    result.embedded_tnef_headers = list(parsed.all_headers)
    if parsed.html_body:
        result.html_body = parsed.html_body
    if parsed.text_body:
        result.text_body = parsed.text_body
    result.inline_images.update(parsed.inline_images)
    result.attachments.extend(parsed.attachments)
    result.processing_notes.extend(parsed.processing_notes)


def parse_email_file(
    path: str,
    limits: ParseLimits = DEFAULT_PARSE_LIMITS,
) -> ParsedEmail:
    """Auto-detect and parse an email file under a single security policy."""
    # Enforce the source limit before handing the path to any third-party parser.
    size = os.path.getsize(path)
    if size > limits.max_source_bytes:
        raise SecurityLimitError(
            "source_size",
            f"Email file exceeds the {limits.max_source_bytes // (1024 * 1024)} MiB limit",
        )

    lower = path.lower()
    if lower.endswith(".msg"):
        return MsgParser(limits).parse(path)

    basename = os.path.basename(lower)
    if basename in _TNEF_FILENAMES:
        with open(path, "rb") as handle:
            header = handle.read(4)
        if header == _TNEF_MAGIC:
            parsed = TnefParser(limits).parse(path)
            parsed.processing_notes.append(
                f"Opened {basename} as a TNEF attachment file; these files are normally "
                "embedded inside another email."
            )
            return parsed

    return EmlParser(limits).parse(path)
