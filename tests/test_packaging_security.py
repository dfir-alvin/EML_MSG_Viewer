from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PackagingSecurityTests(unittest.TestCase):
    def test_linux_launchers_do_not_disable_chromium_sandbox(self):
        for name in ("AppRun_standard", "AppRun_remote_image"):
            text = (ROOT / "linux" / name).read_text(encoding="utf-8")
            self.assertNotIn("export QTWEBENGINE_CHROMIUM_FLAGS", text)
            self.assertNotIn("QTWEBENGINE_DISABLE_SANDBOX=1", text)
            self.assertIn("Refusing to run as root", text)
            self.assertIn("mode 0700", text)

    def test_linux_builder_downloads_only_versioned_verified_tool(self):
        text = (ROOT / "build_linux.sh").read_text(encoding="utf-8")
        self.assertNotIn("releases/download/continuous", text)
        self.assertIn('APPIMAGETOOL_VERSION="1.9.1"', text)
        self.assertIn("appimagetool-x86_64.AppImage", text)
        self.assertIn("appimagetool-aarch64.AppImage", text)
        self.assertIn("APPIMAGETOOL_SHA256", text)
        self.assertIn("DOWNLOADED_SHA256", text)
        self.assertIn("--proto '=https'", text)
        self.assertIn("--max-filesize 30000000", text)
        self.assertLess(text.index('if [[ "$DOWNLOADED_SHA256"'), text.index('mv "$DOWNLOAD_PATH"'))
        self.assertLess(text.index('ACTUAL_SHA256='), text.index('chmod u+x'))

    def test_webengine_never_receives_http_network_access(self):
        text = (ROOT / "viewer" / "body_view.py").read_text(encoding="utf-8")
        self.assertNotIn("ALLOW_REMOTE_IMAGES", text)
        self.assertIn("LocalContentCanAccessRemoteUrls, False", text)
        self.assertIn('if scheme in ("cid", "data")', text)
        self.assertIn("old_profile.deleteLater()", text)

    def test_cid_handler_uses_current_pyqt_api_and_is_smoke_tested(self):
        handler = (ROOT / "viewer" / "cid_scheme_handler.py").read_text(encoding="utf-8")
        entry_point = (ROOT / "main.py").read_text(encoding="utf-8")
        linux_builder = (ROOT / "build_linux.sh").read_text(encoding="utf-8")
        self.assertNotIn("QIODevice.OpenMode.ReadWrite", handler)
        self.assertIn("QIODevice.OpenModeFlag.ReadOnly", handler)
        self.assertIn("QWebEngineUrlRequestJob.Error.RequestFailed", handler)
        self.assertIn("cid:security-self-test", entry_point)
        self.assertIn("[security-self-test]", entry_point)
        self.assertIn("phase=cleanup", entry_point)
        self.assertIn("faulthandler.enable", entry_point)
        self.assertIn("app.setQuitOnLastWindowClosed(False)", entry_point)
        self.assertIn('139) reason="the process received SIGSEGV', linux_builder)

    def test_webengine_shutdown_destroys_page_before_profile(self):
        body_view = (ROOT / "viewer" / "body_view.py").read_text(encoding="utf-8")
        window = (ROOT / "viewer" / "app_window.py").read_text(encoding="utf-8")
        shutdown = body_view[
            body_view.index("    def shutdown(") : body_view.index("    def _finish_shutdown(")
        ]
        self.assertIn("page.destroyed.connect(delete_profile)", shutdown)
        self.assertIn("self.setPage(None)", shutdown)
        self.assertIn("profile.destroyed.connect(self._finish_shutdown)", shutdown)
        self.assertNotIn("page.deleteLater()\n        if profile", shutdown)
        self.assertIn("self._body_view.shutdown()", window)

    def test_troubleshooting_menu_exposes_image_and_processing_logs(self):
        window = (ROOT / "viewer" / "app_window.py").read_text(encoding="utf-8")
        report = (ROOT / "viewer" / "troubleshooting_dialog.py").read_text(encoding="utf-8")
        self.assertIn('addMenu("Troubleshooting")', window)
        self.assertIn('QAction("Image Issue Logs..."', window)
        self.assertIn('QAction("Processing Logs..."', window)
        self.assertIn('(\"Number\", \"Reason\", \"Full URL\")', report)
        self.assertIn('(\"Number\", \"Time\", \"Event\", \"Details\")', report)
        self.assertNotIn('(\"Number\", \"Domain/IP\", \"Reason\", \"Full URL\")', report)
        self.assertIn("Remote image result", window)
        self.assertIn("File open failed", window)
        self.assertIn("TNEF/winmail.dat", window)
        self.assertIn("Copy Selected Rows", report)
        self.assertIn("Copy Cell", report)
        self.assertIn("customContextMenuRequested", report)
        self.assertIn("QKeySequence.StandardKey.Copy", report)

    def test_builds_install_reviewed_dependency_inputs(self):
        windows = (ROOT / "build.bat").read_text(encoding="utf-8")
        linux = (ROOT / "build_linux.sh").read_text(encoding="utf-8")
        for text in (windows, linux):
            self.assertIn("requirements-build.in", text)
            self.assertNotIn("--require-hashes", text)
            self.assertIn(".venv-build", text)
        self.assertIn("VENV_PYTHON", windows)
        self.assertIn("VENV_PYTHON", linux)

    def test_cleanup_scripts_preserve_tools_metadata(self):
        windows = (ROOT / "clean.bat").read_text(encoding="utf-8")
        linux = (ROOT / "clean_linux.sh").read_text(encoding="utf-8")

        self.assertNotIn('rmdir /s /q "tools"', windows.lower())
        self.assertIn(r'tools\appimagetool-*.AppImage', windows)
        self.assertIn("Never remove the tools directory", windows)

        self.assertNotIn('rm -rf -- "$SCRIPT_DIR/tools"', linux)
        self.assertIn("appimagetool-*.AppImage", linux)
        self.assertIn("tools/appimagetool.sha256", linux)
        self.assertIn("tools/appimagetool.LICENSE", linux)
        self.assertIn("Refusing unexpected recursive cleanup target", linux)


if __name__ == "__main__":
    unittest.main()
