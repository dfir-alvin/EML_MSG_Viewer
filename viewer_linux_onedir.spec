# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for EML/MSG Email Viewer — Linux ONEDIR variant.

Produces two executables in a shared folder:
  dist/EML_MSG_Viewer_Linux/EML_MSG_Viewer              (blue icon)
  dist/EML_MSG_Viewer_Linux/EML_MSG_Viewer_remote_image  (red icon)

Build with:  python3 -m PyInstaller viewer_linux_onedir.spec
or:          bash build_linux.sh
"""

import sys
import os
import glob as _glob
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect Qt WebEngine resources (required for QtWebEngine to function)
qt6_path = None
try:
    import PyQt6
    qt6_path = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6")
except Exception:
    pass

datas = []

if qt6_path:
    resources_dir = os.path.join(qt6_path, "resources")
    if os.path.isdir(resources_dir):
        datas.append((resources_dir, "PyQt6/Qt6/resources"))

    translations_dir = os.path.join(qt6_path, "translations")
    if os.path.isdir(translations_dir):
        for f in _glob.glob(os.path.join(translations_dir, "qtwebengine*")):
            datas.append((f, "PyQt6/Qt6/translations"))

# Include icon files for runtime window icon.
# Bundle both .ico (fallback) and .png (preferred on Linux — reliable on all
# Qt platform plugins without depending on libqico.so from imageformats).
for icon in ("email_blue.ico", "email_red.ico", "email_blue.png", "email_red.png"):
    icon_path = os.path.join("resources", icon)
    if os.path.isfile(icon_path):
        datas.append((icon_path, "resources"))

# Force Qt platform plugins and QtWebEngineProcess into the bundle
binaries = []
if qt6_path:
    # QtWebEngineProcess — PyInstaller hooks do not reliably collect this on
    # Linux. Place it at the onedir root (".") so Qt finds it via its default
    # applicationDirPath() search without needing QTWEBENGINEPROCESS_PATH.
    # Search common locations; different PyQt6 builds use different paths.
    _webengine_proc = None
    for _candidate in (
        os.path.join(qt6_path, "libexec", "QtWebEngineProcess"),
        os.path.join(qt6_path, "bin", "QtWebEngineProcess"),
    ):
        if os.path.isfile(_candidate):
            _webengine_proc = _candidate
            break
    # Fallback: glob the entire Qt6 tree (slow but foolproof)
    if _webengine_proc is None:
        import glob as _glob2
        _hits = _glob2.glob(
            os.path.join(qt6_path, "**", "QtWebEngineProcess"), recursive=True
        )
        if _hits:
            _webengine_proc = _hits[0]
    if _webengine_proc:
        print(f"[spec] QtWebEngineProcess found: {_webengine_proc}")
        binaries.append((_webengine_proc, "."))
    else:
        print("[spec] WARNING: QtWebEngineProcess not found — WebEngine will fail at runtime")

    plugins_dir = os.path.join(qt6_path, "plugins")
    for subdir in (
        "platforms",
        "wayland-shell-integration",
        "wayland-graphics-integration-client",
        "wayland-decoration-client",
        "xcbglintegrations",
        "imageformats",
        "iconengines",
    ):
        src = os.path.join(plugins_dir, subdir)
        if os.path.isdir(src):
            binaries.append((src + "/*", f"PyQt6/Qt6/plugins/{subdir}"))

# Shared settings
hiddenimports = [
    "viewer.load_worker",
    "viewer.load_thread",
    "viewer.remote_fetch",
    "viewer.security",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineCore",
    "PyQt6.sip",
    "PyQt6.QtDBus",
    "extract_msg",
    "extract_msg.attachments",
    "tnefparse",
    "RTFDE",
    "psutil",
    "bleach",
    "bleach.css_sanitizer",
    "tinycss2",
    "bs4",
    "bs4.builder",
    "bs4.builder._html5lib",
    "bs4.builder._htmlparser",
    "bs4.builder._lxml",
    "html.parser",
    "email.mime",
    "email.mime.text",
    "email.mime.multipart",
    "email.mime.base",
    "email.policy",
    "quopri",
]

excludes = [
    "tkinter",
    "matplotlib",
    "numpy",
    "scipy",
    "pandas",
    "PIL",
    "cv2",
    "wx",
    "PySide2",
    "PySide6",
    "PyQt5",
]

# ---------------------------------------------------------------------------
# EXE 1: Standard viewer (blue icon, remote images blocked)
# ---------------------------------------------------------------------------

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EML_MSG_Viewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---------------------------------------------------------------------------
# EXE 2: Remote image viewer (red icon, remote images allowed)
# ---------------------------------------------------------------------------

a2 = Analysis(
    ["main_remote_image.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)

pyz2 = PYZ(a2.pure, a2.zipped_data, cipher=block_cipher)

exe2 = EXE(
    pyz2,
    a2.scripts,
    [],
    exclude_binaries=True,
    name="EML_MSG_Viewer_remote_image",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---------------------------------------------------------------------------
# Single COLLECT — shared libraries are deduplicated automatically
# ---------------------------------------------------------------------------

coll = COLLECT(
    exe,
    exe2,
    a.binaries,
    a.zipfiles,
    a.datas,
    a2.binaries,
    a2.zipfiles,
    a2.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EML_MSG_Viewer_Linux",
)
