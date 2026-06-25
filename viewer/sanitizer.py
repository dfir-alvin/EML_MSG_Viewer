"""HTML sanitization and remote-image extraction for untrusted email bodies."""

from __future__ import annotations

import hashlib
import html as html_module
import re
import secrets
from urllib.parse import quote, unquote, urlsplit

import bleach
import tinycss2
from bleach.css_sanitizer import CSSSanitizer
from bs4 import BeautifulSoup, NavigableString

from viewer.security import (
    BLANK_PNG_DATA_URL,
    DEFAULT_REMOTE_FETCH_POLICY,
    InlineAsset,
    NetworkMode,
    RemoteFetchPolicy,
    RemoteImageReference,
    SanitizedContent,
)
from viewer.utils import classify_url


ALLOWED_TAGS = [
    "a", "abbr", "acronym", "address", "article", "aside",
    "b", "bdi", "bdo", "big", "blockquote", "br",
    "caption", "center", "cite", "code", "col", "colgroup",
    "dd", "del", "details", "dfn", "div", "dl", "dt", "em",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
    "h6", "header", "hr", "i", "img", "ins", "kbd", "li", "main",
    "mark", "nav", "ol", "p", "pre", "q", "rp", "rt", "ruby", "s",
    "samp", "section", "small", "span", "strong", "sub", "summary", "sup",
    "table", "tbody", "td", "tfoot", "th", "thead", "time", "tr", "u",
    "ul", "var", "wbr",
]

ALLOWED_ATTRS: dict[str, list[str]] = {
    "*": ["class", "id", "style", "title", "dir", "lang"],
    "a": ["href", "title", "class", "style", "target"],
    "img": ["src", "alt", "width", "height", "style", "class"],
    "td": ["colspan", "rowspan", "align", "valign", "width", "bgcolor"],
    "th": ["colspan", "rowspan", "align", "valign", "width"],
    "col": ["span", "width", "style"],
    "colgroup": ["span"],
    "table": ["border", "cellpadding", "cellspacing", "width", "align", "bgcolor", "style"],
    "tr": ["align", "valign", "bgcolor", "style"],
    "ol": ["start", "type"],
    "li": ["value"],
    "time": ["datetime"],
    "blockquote": ["cite"],
    "del": ["datetime"],
    "ins": ["datetime"],
}

# URL-bearing shorthands and image properties are deliberately absent. CSS URL
# tokens are also rejected structurally below so escaped forms cannot bypass a
# regular expression.
SAFE_CSS_PROPERTIES = [
    "background-color", "background-position", "background-repeat", "background-size",
    "border", "border-bottom", "border-collapse", "border-color", "border-left",
    "border-radius", "border-right", "border-spacing", "border-style", "border-top",
    "border-width", "bottom", "caption-side", "clear", "color", "direction", "display",
    "empty-cells", "float", "font", "font-family", "font-size", "font-style",
    "font-variant", "font-weight", "height", "left", "letter-spacing", "line-height",
    "list-style-position", "list-style-type", "margin", "margin-bottom", "margin-left",
    "margin-right", "margin-top", "max-height", "max-width", "min-height", "min-width",
    "opacity", "overflow", "overflow-x", "overflow-y", "padding", "padding-bottom",
    "padding-left", "padding-right", "padding-top", "page-break-after", "page-break-before",
    "position", "right", "table-layout", "text-align", "text-decoration", "text-indent",
    "text-overflow", "text-transform", "top", "vertical-align", "visibility", "white-space",
    "width", "word-break", "word-spacing", "word-wrap", "z-index",
]

_SECURITY_HEAD = """
<head>
<meta http-equiv="Content-Security-Policy"
      content="default-src 'none'; img-src cid: data:; style-src 'unsafe-inline';
               connect-src 'none'; font-src 'none'; frame-src 'none'; media-src 'none';
               object-src 'none'; base-uri 'none'; form-action 'none'">
<style>
a.url-https  { color: #1a73e8; }
a.url-http   { color: #e67e22; }
a.url-mailto { color: #27ae60; }
a.url-other  { color: #8e44ad; }
a { cursor: default !important; text-decoration: underline; }
</style>
</head>
"""


def _tokens_contain_url(tokens) -> bool:
    for token in tokens:
        token_type = getattr(token, "type", "")
        if token_type == "url":
            return True
        if token_type == "function" and getattr(token, "lower_name", "") == "url":
            return True
        nested = getattr(token, "content", None) or getattr(token, "arguments", None)
        if nested and _tokens_contain_url(nested):
            return True
    return False


def _remove_css_urls(style: str) -> str:
    declarations = tinycss2.parse_declaration_list(style, skip_comments=True, skip_whitespace=True)
    safe = []
    for declaration in declarations:
        if getattr(declaration, "type", "") != "declaration":
            continue
        if _tokens_contain_url(declaration.value):
            continue
        safe.append(declaration)
    return tinycss2.serialize(safe).strip()


def _remote_token(url: str, salt: bytes) -> str:
    digest = hashlib.sha256(salt + url.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"remote-{digest}"


def _cid_value(src: str) -> str:
    value = src[4:] if src.lower().startswith("cid:") else src
    return unquote(value).lstrip("/")


def _replace_with_blocked_image(soup: BeautifulSoup, image, original_url: str = "") -> None:
    image["src"] = BLANK_PNG_DATA_URL
    image["style"] = (image.get("style", "") + "; cursor: default;").lstrip("; ")
    if not original_url:
        return
    try:
        host = urlsplit(original_url).hostname or "remote host"
    except ValueError:
        host = "remote host"
    tooltip = f"Remote image blocked: {host}"
    image["title"] = tooltip
    wrapper = soup.new_tag("a", href=original_url, title=tooltip)
    wrapper["class"] = classify_url(original_url)
    wrapper["style"] = "cursor: default;"
    image.replace_with(wrapper)
    wrapper.append(image)


def sanitize_html(
    raw_html: str,
    inline_images: dict[str, InlineAsset],
    network_mode: NetworkMode = NetworkMode.OFFLINE,
    remote_policy: RemoteFetchPolicy = DEFAULT_REMOTE_FETCH_POLICY,
) -> SanitizedContent:
    """Sanitize HTML and replace every fetchable image with a local CID token."""
    soup = BeautifulSoup(raw_html, "html.parser")

    # Email clients render the document body, not metadata from <head>. Bleach
    # strips unsupported tags but preserves their text, which would otherwise
    # turn content such as <title>Email from ...</title> into visible body text.
    original_body = soup.find("body")
    if original_body is not None:
        soup = BeautifulSoup(original_body.decode_contents(), "html.parser")
    else:
        for metadata in soup.find_all(("head", "title")):
            metadata.decompose()

    for tag_name in (
        "script", "style", "link", "object", "embed", "applet", "form", "input",
        "button", "select", "textarea", "iframe", "frame", "frameset", "base",
    ):
        for element in soup.find_all(tag_name):
            element.decompose()
    for element in soup.find_all("meta"):
        if element.get("http-equiv", "").lower() == "refresh":
            element.decompose()

    for element in soup.find_all(True):
        for attribute in list(element.attrs):
            if attribute.lower().startswith("on"):
                del element[attribute]
        for attribute in ("href", "src", "action", "formaction"):
            value = element.get(attribute, "")
            if isinstance(value, str) and re.match(
                r"\s*(javascript|vbscript)\s*:", value, re.IGNORECASE
            ):
                del element[attribute]

    css_sanitizer = CSSSanitizer(allowed_css_properties=SAFE_CSS_PROPERTIES)
    cleaned = bleach.clean(
        str(soup),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=["http", "https", "mailto", "cid"],
        css_sanitizer=css_sanitizer,
        strip=True,
        strip_comments=True,
    )
    rendered = BeautifulSoup(cleaned, "html.parser")

    for element in rendered.find_all(style=True):
        safe_style = _remove_css_urls(element.get("style", ""))
        if safe_style:
            element["style"] = safe_style
        else:
            del element["style"]

    for anchor in rendered.find_all("a"):
        href = anchor.get("href", "")
        classes = anchor.get("class", [])
        if isinstance(classes, str):
            classes = classes.split()
        css_class = classify_url(href)
        if css_class not in classes:
            classes.append(css_class)
        anchor["class"] = classes
        if href and not anchor.get("title"):
            anchor["title"] = href
        anchor["style"] = (anchor.get("style", "") + "; cursor: default;").lstrip("; ")
        anchor.attrs.pop("target", None)

    remote_references: dict[str, RemoteImageReference] = {}
    remote_tokens_by_url: dict[str, str] = {}
    token_salt = secrets.token_bytes(16)
    for image in rendered.find_all("img"):
        src = image.get("src", "")
        src_lower = src.lower()
        if src_lower.startswith("cid:"):
            cid = _cid_value(src)
            if cid in inline_images:
                image["src"] = f"cid:{quote(cid, safe='@._-')}"
                image["style"] = (image.get("style", "") + "; cursor: default;").lstrip("; ")
            else:
                filename = cid.split("@", 1)[0]
                image["title"] = f"Missing embedded image: {filename}"
                _replace_with_blocked_image(rendered, image)
            continue

        if src_lower.startswith(("http://", "https://")):
            if network_mode is NetworkMode.RESTRICTED_REMOTE_IMAGES:
                token = remote_tokens_by_url.get(src) or _remote_token(src, token_salt)
                if token not in remote_references and len(remote_references) >= remote_policy.max_urls:
                    _replace_with_blocked_image(rendered, image, src)
                    continue
                remote_tokens_by_url[src] = token
                remote_references[token] = RemoteImageReference(token=token, url=src)
                image["src"] = f"cid:{token}"
                try:
                    host = urlsplit(src).hostname or "remote host"
                except ValueError:
                    host = "remote host"
                image["title"] = f"Remote image: {host}"
                image["style"] = (image.get("style", "") + "; cursor: default;").lstrip("; ")
            else:
                _replace_with_blocked_image(rendered, image, src)
            continue

        _replace_with_blocked_image(rendered, image, src)

    cid_text_pattern = re.compile(r"\[cid:([^\]]+)\]")
    for text_node in list(rendered.find_all(string=cid_text_pattern)):
        if not isinstance(text_node, NavigableString):
            continue
        text = str(text_node)
        matches = cid_text_pattern.findall(text)
        plain_parts = re.split(r"\[cid:[^\]]+\]", text)
        replacements = []
        for index, part in enumerate(plain_parts):
            if part:
                replacements.append(NavigableString(part))
            if index < len(matches):
                filename = matches[index].split("@", 1)[0]
                label = rendered.new_tag("span")
                label.string = f"[Embedded image: {filename}]"
                label["style"] = "color: #888; font-style: italic;"
                replacements.append(label)
        for replacement in replacements:
            text_node.insert_before(replacement)
        text_node.extract()

    return SanitizedContent(
        html=f"<!doctype html><html>{_SECURITY_HEAD}<body>{rendered}</body></html>",
        remote_images=tuple(remote_references.values()),
    )


def text_to_html(text: str) -> str:
    """Convert plain text to escaped HTML without creating navigable links."""
    escaped = html_module.escape(text)
    url_pattern = re.compile(r"(https?://[^\s<>\"']+|mailto:[^\s<>\"']+)", re.IGNORECASE)

    def replace_url(match):
        url = match.group(0)
        css_class = classify_url(url)
        escaped_url = html_module.escape(url, quote=True)
        return (
            f'<span class="{css_class}" title="{escaped_url}" '
            f'style="cursor:default; text-decoration:underline;">{escaped_url}</span>'
        )

    body = url_pattern.sub(replace_url, escaped).replace("\n", "<br>")
    return (
        "<html><head><meta http-equiv='Content-Security-Policy' "
        "content=\"default-src 'none'; img-src cid: data:; style-src 'unsafe-inline'; "
        "connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'\">"
        "<style>"
        ".url-https{color:#1a73e8}.url-http{color:#e67e22}"
        ".url-mailto{color:#27ae60}.url-other{color:#8e44ad}"
        "</style></head><body><pre style='white-space:pre-wrap;font-family:monospace;'>"
        f"{body}</pre></body></html>"
    )
