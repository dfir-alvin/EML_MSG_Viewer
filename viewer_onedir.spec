# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for EML/MSG Email Viewer — ONEDIR (fast-launch) variant.

Produces two executables in a shared folder:
  dist/EML_MSG_Viewer_onedir/EML_MSG_Viewer.exe              (blue icon)
  dist/EML_MSG_Viewer_onedir/EML_MSG_Viewer_remote_image.exe  (red icon)

Build with:  pyinstaller viewer_onedir.spec
or:          build.bat  (builds both onefile and onedir)
"""

import sys
import os
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
        # Only include qtwebengine translations to keep size down
        import glob
        for f in glob.glob(os.path.join(translations_dir, "qtwebengine*")):
            datas.append((f, "PyQt6/Qt6/translations"))

# Include icon files for runtime window icon
for ico in ("email_blue.ico", "email_red.ico"):
    ico_path = os.path.join("resources", ico)
    if os.path.isfile(ico_path):
        datas.append((ico_path, "resources"))

# Shared settings
hiddenimports = [
    "viewer.load_worker",
    "viewer.load_thread",
    "viewer.remote_fetch",
    "viewer.security",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineCore",
    "PyQt6.sip",
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
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
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
    upx=False,          # UPX can corrupt Qt DLLs
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # No terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="resources/email_blue.ico" if os.path.isfile("resources/email_blue.ico") else None,
    contents_directory=".",  # Keep DLLs next to EXE (avoids python3XX.dll lookup issue)
)

# ---------------------------------------------------------------------------
# EXE 2: Remote image viewer (red icon, remote images allowed)
# ---------------------------------------------------------------------------

a2 = Analysis(
    ["main_remote_image.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
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
    icon="resources/email_red.ico" if os.path.isfile("resources/email_red.ico") else None,
    contents_directory=".",
)

# ---------------------------------------------------------------------------
# Single COLLECT — shared DLLs are deduplicated automatically
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
    name="EML_MSG_Viewer_onedir",
)
