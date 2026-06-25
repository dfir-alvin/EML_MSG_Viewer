"""
Custom QWebEngineUrlSchemeHandler that serves inline images via the cid: scheme.

IMPORTANT: The cid: scheme must be registered BEFORE QApplication is created.
Call register_cid_scheme() at the very top of main.py before anything else.
"""

from __future__ import annotations

from PyQt6.QtWebEngineCore import (
    QWebEngineUrlScheme,
    QWebEngineUrlSchemeHandler,
    QWebEngineUrlRequestJob,
)
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice

from urllib.parse import unquote

from viewer.security import InlineAsset


def register_cid_scheme() -> None:
    """
    Register the cid: URL scheme with QtWebEngine.
    Must be called before QApplication is instantiated.
    """
    scheme = QWebEngineUrlScheme(b"cid")
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)


class CidSchemeHandler(QWebEngineUrlSchemeHandler):
    """
    Serves inline images stored in the ParsedEmail.inline_images dict.

    inline_images is keyed by bare Content-ID (no angle brackets, no 'cid:' prefix).
    The handler resolves both url.host() and url.path() to handle Qt version differences.
    """

    def __init__(self, inline_images: dict[str, InlineAsset], parent=None):
        super().__init__(parent)
        self._images = inline_images

    def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:
        try:
            url = job.requestUrl()

            # Try both host and path; Qt may put the cid key in either place.
            candidates = [
                url.host(),
                url.path().lstrip("/"),
                url.host() + url.path(),
                url.toString().removeprefix("cid:").lstrip("/"),
            ]

            asset: InlineAsset | None = None
            for key in candidates:
                decoded_key = unquote(key)
                if decoded_key in self._images:
                    asset = self._images[decoded_key]
                    break

            if asset is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return

            # The reply device must remain alive until WebEngine finishes the
            # request. Parenting it to the job gives it exactly that lifetime.
            buf = QBuffer(job)
            buf.setData(QByteArray(asset.data))
            if not buf.open(QIODevice.OpenModeFlag.ReadOnly):
                raise RuntimeError("Could not open the inline-image reply buffer")
            job.reply(QByteArray(asset.mime_type.encode("ascii")), buf)
        except Exception:
            # An exception escaping a Python implementation of this C++
            # virtual method can abort the entire Qt process. Fail the one
            # resource request instead.
            try:
                job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            except (RuntimeError, TypeError):
                pass
