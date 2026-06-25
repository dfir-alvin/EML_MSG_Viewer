"""EML/MSG Email Viewer entry point."""

import multiprocessing
import os
import platform
import sys
import traceback


# PyInstaller requires this before importing the GUI when a spawned security
# worker re-enters the executable.
multiprocessing.freeze_support()


def main() -> int:
    # The custom scheme must be registered before QApplication is constructed.
    from viewer.cid_scheme_handler import register_cid_scheme

    register_cid_scheme()

    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMessageBox

    import viewer.config as config
    from viewer.app_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("EML/MSG Email Viewer")
    app.setOrganizationName("EmailViewer")

    if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
        QMessageBox.critical(
            None,
            "Unsafe Launch Refused",
            "This viewer will not run as root because QtWebEngine's sandbox must remain enabled.",
        )
        return 1

    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    icon_path = os.path.join(base, "resources", config.ICON_NAME)
    if os.path.isfile(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()
    window.show()

    if "--security-self-test" in sys.argv:
        import faulthandler

        from PyQt6.QtCore import (
            PYQT_VERSION_STR,
            QT_VERSION_STR,
            QTimer,
            QtMsgType,
            qInstallMessageHandler,
        )
        from viewer.security import BLANK_PNG_BYTES, InlineAsset

        def self_test_log(message: str) -> None:
            line = f"[security-self-test] {message}\n".encode("utf-8", errors="replace")
            try:
                os.write(2, line)
            except OSError:
                pass

        try:
            faulthandler.enable(all_threads=True)
        except Exception as exc:
            self_test_log(f"diagnostic=faulthandler-unavailable error={exc!r}")

        qt_levels = {
            QtMsgType.QtDebugMsg: "debug",
            QtMsgType.QtInfoMsg: "info",
            QtMsgType.QtWarningMsg: "warning",
            QtMsgType.QtCriticalMsg: "critical",
            QtMsgType.QtFatalMsg: "fatal",
        }

        def qt_message_handler(msg_type, context, message: str) -> None:
            level = qt_levels.get(msg_type, "unknown")
            location = ""
            if context.file:
                location = f" source={context.file}:{context.line}"
            self_test_log(f"qt_{level}={message}{location}")

        qInstallMessageHandler(qt_message_handler)
        app.setQuitOnLastWindowClosed(False)

        state = {"phase": "setup", "exit_code": 3}

        self_test_log(
            "phase=setup "
            f"python={platform.python_version()} pyqt={PYQT_VERSION_STR} "
            f"qt={QT_VERSION_STR} machine={platform.machine()} "
            f"qpa={os.environ.get('QT_QPA_PLATFORM', '<default>')} "
            f"network_mode={config.NETWORK_MODE.name} sandbox=required"
        )

        def cleanup_finished() -> None:
            if state["phase"] != "cleanup":
                return
            exit_code = state["exit_code"]
            state["phase"] = "done"
            self_test_log(f"phase=cleanup result=passed exit_code={exit_code}")
            window.close()
            QTimer.singleShot(0, lambda: app.exit(exit_code))

        def begin_cleanup(exit_code: int, reason: str) -> None:
            if state["phase"] in ("cleanup", "done"):
                return
            state["phase"] = "cleanup"
            state["exit_code"] = exit_code
            self_test_log(f"phase=cleanup result=started reason={reason}")

            def start_body_shutdown() -> None:
                if state["phase"] != "cleanup":
                    return
                self_test_log("phase=cleanup event=page-first-shutdown-started")
                window._body_view.shutdown(cleanup_finished)

            # Do not delete the WebEngine page from inside its loadFinished
            # signal. Return to the event loop first, then begin teardown.
            QTimer.singleShot(0, start_body_shutdown)
            QTimer.singleShot(5_000, cleanup_watchdog_timeout)

        def load_finished(ok: bool) -> None:
            if state["phase"] != "loading":
                return
            self_test_log(f"phase=loading result={'passed' if ok else 'failed'}")
            begin_cleanup(0 if ok else 2, "page-load-finished")

        def load_watchdog_timeout() -> None:
            if state["phase"] == "loading":
                self_test_log("phase=loading result=timeout limit_seconds=15")
                begin_cleanup(3, "page-load-timeout")

        def cleanup_watchdog_timeout() -> None:
            if state["phase"] == "cleanup":
                state["exit_code"] = 4
                self_test_log(
                    "phase=cleanup result=timeout limit_seconds=5 "
                    "action=forcing-event-loop-exit"
                )
                app.exit(4)

        window._body_view.loadStarted.connect(
            lambda: self_test_log("phase=loading event=load-started")
        )
        window._body_view.loadFinished.connect(load_finished)
        state["phase"] = "loading"
        try:
            window._body_view.load_content(
                "<html><head><meta http-equiv='Content-Security-Policy' "
                "content=\"default-src 'none'; img-src cid:; style-src 'unsafe-inline'\"></head>"
                "<body><p>sandbox and CID handler smoke test</p>"
                "<img src='cid:security-self-test' alt='test'></body></html>",
                {
                    "security-self-test": InlineAsset(
                        data=BLANK_PNG_BYTES,
                        mime_type="image/png",
                    )
                },
            )
        except Exception as exc:
            self_test_log(
                f"phase=loading result=exception type={type(exc).__name__} error={exc!r}"
            )
            self_test_log(
                "phase=loading traceback="
                + traceback.format_exc().strip().replace("\n", " | ")
            )
            QTimer.singleShot(0, lambda: begin_cleanup(5, "page-load-exception"))

        QTimer.singleShot(15_000, load_watchdog_timeout)
        app.aboutToQuit.connect(
            lambda: self_test_log(f"phase={state['phase']} event=about-to-quit")
        )
        result = app.exec()
        self_test_log(f"phase={state['phase']} event=event-loop-exited exit_code={result}")
        return result

    if len(sys.argv) > 1:
        path = sys.argv[1]
        if os.path.isfile(path):
            window.load_file(path)
        else:
            window.statusBar().showMessage(f"File not found: {path}")

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
