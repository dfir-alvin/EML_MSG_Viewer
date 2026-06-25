"""
Secure WebEngine body viewer.

Three independent security layers:
  1. WebEngine settings (JS off, no remote access, etc.)
  2. SecureWebPage — acceptNavigationRequest blocks all navigation
  3. BlockAllRequestInterceptor — blocks all non-cid/data requests
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6 import sip
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
    QWebEngineUrlRequestInfo,
)
from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWidgets import QMenu, QApplication, QToolTip
from PyQt6.QtGui import QCursor

from viewer.cid_scheme_handler import CidSchemeHandler
from viewer.security import InlineAsset


# ---------------------------------------------------------------------------
# Layer 3 — Request interceptor
# ---------------------------------------------------------------------------

class BlockAllRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Block every outbound request except cid: and data: URLs."""

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        scheme = info.requestUrl().scheme().lower()
        if scheme in ("cid", "data"):
            return  # allow our safe content
        info.block(True)


# ---------------------------------------------------------------------------
# Layer 2 — Secure page
# ---------------------------------------------------------------------------

class SecureWebPage(QWebEnginePage):
    """
    Override acceptNavigationRequest so that only the initial setHtml() call
    is allowed. All link clicks, redirects, and form submits are blocked.
    """

    def acceptNavigationRequest(
        self,
        url: QUrl,
        nav_type: QWebEnginePage.NavigationType,
        is_main_frame: bool,
    ) -> bool:
        # NavigationTypeTyped is used by setHtml() / programmatic loads
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeTyped:
            return True
        # Block everything else
        return False


# ---------------------------------------------------------------------------
# Body view widget
# ---------------------------------------------------------------------------

class BodyView(QWebEngineView):
    """
    Secure email body viewer.

    Usage:
        body_view.load_email(parsed_email)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # Deferred: profile and page are created on first load_content() call
        self._profile: QWebEngineProfile | None = None
        self._page: SecureWebPage | None = None
        self._handler: CidSchemeHandler | None = None
        self._interceptor: BlockAllRequestInterceptor | None = None
        self._shutdown_started = False
        self._shutdown_complete = False
        self._shutdown_callbacks: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_content(self, html: str, inline_images: dict[str, InlineAsset]) -> None:
        """Load sanitized HTML with associated inline images."""
        if self._shutdown_started:
            raise RuntimeError("BodyView cannot load content after shutdown has started")

        # Keep references so the old objects can be explicitly disposed after
        # the view has switched to the new page.
        old_page = self._page
        old_profile = self._profile

        # Create a fresh off-the-record profile per email
        profile = QWebEngineProfile(self)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        self._profile = profile

        # Install cid: handler with this email's images
        handler = CidSchemeHandler(inline_images, profile)
        self._handler = handler
        profile.installUrlSchemeHandler(b"cid", handler)

        # Install request interceptor
        interceptor = BlockAllRequestInterceptor(profile)
        self._interceptor = interceptor
        profile.setUrlRequestInterceptor(interceptor)

        # Apply security settings (Layer 1)
        settings = profile.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.DnsPrefetchEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.HyperlinkAuditingEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PdfViewerEnabled, False)

        # Create secure page (Layer 2)
        page = SecureWebPage(profile, self)
        self._page = page
        self.setPage(page)

        if old_page is not None:
            old_page.deleteLater()
        if old_profile is not None:
            old_profile.deleteLater()

        # Show Qt tooltip immediately when hovering a link (bypasses Chromium delay)
        page.linkHovered.connect(self._on_link_hovered)

        # Load HTML — base URL uses cid: so relative cid: lookups work
        page.setHtml(html, QUrl("cid://email/"))

    def clear(self) -> None:
        """Show an empty page."""
        if self._page is not None:
            self._page.setHtml("<html><body></body></html>", QUrl("cid://email/"))

    def shutdown(self, on_finished: Callable[[], None] | None = None) -> None:
        """Destroy the active page before its profile, then report completion."""
        if self._shutdown_complete:
            if on_finished is not None:
                QTimer.singleShot(0, on_finished)
            return
        if on_finished is not None:
            self._shutdown_callbacks.append(on_finished)
        if self._shutdown_started:
            return

        self._shutdown_started = True
        page = self._page
        profile = self._profile
        self._page = None
        self._profile = None
        self._handler = None
        self._interceptor = None

        def delete_profile(*_args) -> None:
            if profile is None or sip.isdeleted(profile):
                self._finish_shutdown()
                return
            profile.destroyed.connect(self._finish_shutdown)
            profile.deleteLater()

        if page is None or sip.isdeleted(page):
            delete_profile()
            return

        # QWebEngineProfile must outlive every page associated with it. Detach
        # the current view-owned page and wait for its destroyed signal before
        # scheduling profile deletion.
        page.destroyed.connect(delete_profile)
        self.setPage(None)
        if not sip.isdeleted(page):
            page.deleteLater()

    def _finish_shutdown(self, *_args) -> None:
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        callbacks = self._shutdown_callbacks
        self._shutdown_callbacks = []
        for callback in callbacks:
            QTimer.singleShot(0, callback)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Hover tooltip — instant, bypasses Chromium's built-in title delay
    # ------------------------------------------------------------------

    def _on_link_hovered(self, url: str) -> None:
        if url:
            # For CID links, show the friendly tooltip instead of the raw cid: URL
            if url.startswith("cid:"):
                cid_value = url[4:]
                filename = cid_value.split("@")[0] if "@" in cid_value else cid_value
                display = f"[Check Attachment(s) for file named: {filename}]"
            else:
                display = url
            QToolTip.showText(QCursor.pos(), display, self)
        else:
            QToolTip.hideText()

    # ------------------------------------------------------------------
    # Context menu — custom, safe menu only
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event) -> None:
        """
        Replace the default context menu entirely.
        Only show "Copy URL" when right-clicking a link or blocked image.
        """
        req = self.lastContextMenuRequest()
        link_url = req.linkUrl() if req else QUrl()

        if not link_url or link_url.isEmpty():
            return  # No context menu outside links/images

        url_str = link_url.toString()

        # Parent to the top-level window (not the WebEngineView) so Qt applies
        # the native window style rather than inheriting the web view's stylesheet.
        top = self.window()
        menu = QMenu(top)
        # Explicit palette-based stylesheet prevents transparency bleed-through
        menu.setStyleSheet(
            "QMenu { background-color: palette(window); color: palette(window-text); "
            "border: 1px solid palette(mid); }"
            "QMenu::item:selected { background-color: palette(highlight); "
            "color: palette(highlighted-text); }"
        )

        copy_action = menu.addAction("Copy URL")
        copy_action.triggered.connect(
            lambda checked=False, u=url_str: QApplication.clipboard().setText(u)
        )
        menu.addSeparator()
        label_action = menu.addAction(url_str)
        label_action.setEnabled(False)

        menu.exec(event.globalPos())
