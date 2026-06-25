#!/usr/bin/env bash
# make_appimage.sh — Assemble an AppDir and call appimagetool
# Usage: bash linux/make_appimage.sh <standard|remote_image> <x86_64|aarch64>
set -euo pipefail

VARIANT="${1:?Usage: $0 <standard|remote_image> <arch>}"
ARCH="${2:?Usage: $0 <variant> <arch>}"

# Resolve project root (parent of the directory containing this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

case "$ARCH" in
    x86_64|amd64)
        ARCH="x86_64"
        APPIMAGETOOL_ASSET="appimagetool-x86_64.AppImage"
        EXPECTED_APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
        ;;
    aarch64|arm64)
        ARCH="aarch64"
        APPIMAGETOOL_ASSET="appimagetool-aarch64.AppImage"
        EXPECTED_APPIMAGETOOL_SHA256="f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158"
        ;;
    *) echo "ERROR: Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

# Use only the reviewed, digest-pinned appimagetool selected by build_linux.sh.
APPIMAGETOOL="${APPIMAGETOOL_PATH:-$ROOT/tools/$APPIMAGETOOL_ASSET}"
if [[ ! -x "$APPIMAGETOOL" ]]; then
    echo "ERROR: Pinned appimagetool not found at $APPIMAGETOOL." >&2
    echo "       Run build_linux.sh to download and verify it." >&2
    exit 1
fi
ACTUAL_APPIMAGETOOL_SHA256="$(sha256sum "$APPIMAGETOOL" | awk '{print $1}')"
if [[ "$ACTUAL_APPIMAGETOOL_SHA256" != "$EXPECTED_APPIMAGETOOL_SHA256" ]]; then
    echo "ERROR: appimagetool SHA-256 mismatch." >&2
    exit 1
fi

# Paths
ONEDIR="$ROOT/dist/EML_MSG_Viewer_Linux"
APPDIR="$ROOT/build/AppDir_${VARIANT}"

if [[ "$VARIANT" == "standard" ]]; then
    APPRUN_SRC="$SCRIPT_DIR/AppRun_standard"
    DESKTOP_SRC="$SCRIPT_DIR/eml_msg_viewer.desktop"
    DESKTOP_NAME="eml_msg_viewer.desktop"
    APPDATA_SRC="$SCRIPT_DIR/net.nham.EMLMSGViewer.appdata.xml"
    APPDATA_NAME="net.nham.EMLMSGViewer.appdata.xml"
    ICON_SRC="$ROOT/resources/email_blue.png"
    ICON_NAME="email_blue"
    APPIMAGE_NAME="EML_MSG_Viewer-${ARCH}.AppImage"
    REGISTER_SCRIPT_NAME="register_EML_MSG_Viewer.sh"
    APP_DISPLAY_NAME="EML/MSG Email Viewer"
elif [[ "$VARIANT" == "remote_image" ]]; then
    APPRUN_SRC="$SCRIPT_DIR/AppRun_remote_image"
    DESKTOP_SRC="$SCRIPT_DIR/eml_msg_viewer_remote_image.desktop"
    DESKTOP_NAME="eml_msg_viewer_remote_image.desktop"
    APPDATA_SRC="$SCRIPT_DIR/net.nham.EMLMSGViewerRemoteImage.appdata.xml"
    APPDATA_NAME="net.nham.EMLMSGViewerRemoteImage.appdata.xml"
    ICON_SRC="$ROOT/resources/email_red.png"
    ICON_NAME="email_red"
    APPIMAGE_NAME="EML_MSG_Viewer_remote_image-${ARCH}.AppImage"
    REGISTER_SCRIPT_NAME="register_EML_MSG_Viewer_remote_image.sh"
    APP_DISPLAY_NAME="EML/MSG Email Viewer (Remote Images)"
else
    echo "ERROR: Unknown variant '$VARIANT'. Expected 'standard' or 'remote_image'." >&2
    exit 1
fi

OUTPUT="$ROOT/dist/$APPIMAGE_NAME"

if [[ ! -d "$ONEDIR" ]]; then
    echo "ERROR: PyInstaller output not found at $ONEDIR" >&2
    echo "       Run: python3 -m PyInstaller viewer_linux_onedir.spec --clean -y" >&2
    exit 1
fi

echo "==> Building AppDir for variant: $VARIANT ($ARCH)"

# Clean and recreate AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR"

# --- AppRun ---
cp "$APPRUN_SRC" "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"

# --- .desktop file ---
# AppDir root: required by appimagetool to build the AppImage.
# usr/share/applications/: required by appimaged and other desktop integration
# tools (e.g. xdg-desktop-menu) to register the app with the desktop environment.
APPLICATIONS_DIR="$APPDIR/usr/share/applications"
mkdir -p "$APPLICATIONS_DIR"
cp "$DESKTOP_SRC" "$APPDIR/$DESKTOP_NAME"
cp "$DESKTOP_SRC" "$APPLICATIONS_DIR/$DESKTOP_NAME"

# --- Icons ---
# Three locations required for full icon support across all environments:
#
# 1. AppDir root as <Icon>.png  — appimagetool reads this for the AppImage
# 2. AppDir root as .DirIcon    — file managers (Nautilus, Dolphin) read this;
#                                  use cp, not ln -sf: symlinks can be lost when
#                                  squashfs is created with certain mksquashfs flags
# 3. usr/share/icons/hicolor/   — desktop environments (GNOME, KDE) use this
#                                  hierarchy for launcher/taskbar icons
ICON_HICOLOR_DIR="$APPDIR/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$ICON_HICOLOR_DIR"
cp "$ICON_SRC" "$APPDIR/${ICON_NAME}.png"
cp "$ICON_SRC" "$APPDIR/.DirIcon"
cp "$ICON_SRC" "$ICON_HICOLOR_DIR/${ICON_NAME}.png"

# --- AppStream metadata ---
# Silences the appimagetool "upstream metadata missing" warning and enables
# icon display in application launchers (GNOME Software, KDE Discover, etc.)
METAINFO_DIR="$APPDIR/usr/share/metainfo"
mkdir -p "$METAINFO_DIR"
cp "$APPDATA_SRC" "$METAINFO_DIR/$APPDATA_NAME"

# --- PyInstaller onedir output ---
cp -a "$ONEDIR" "$APPDIR/EML_MSG_Viewer"

# --- Verify QtWebEngineProcess is at the onedir root ---
# Qt searches applicationDirPath() (i.e. the directory of the main executable)
# for QtWebEngineProcess. If PyInstaller placed it elsewhere (e.g. libexec/),
# find it and copy it to the root so Qt's default search succeeds.
WEP="$APPDIR/EML_MSG_Viewer/QtWebEngineProcess"
if [[ ! -f "$WEP" ]]; then
    echo "==> QtWebEngineProcess not at onedir root — searching bundle ..."
    FOUND_WEP="$(find "$APPDIR/EML_MSG_Viewer" -name "QtWebEngineProcess" -type f 2>/dev/null | head -1)"
    if [[ -n "$FOUND_WEP" ]]; then
        echo "==> Found at $FOUND_WEP — copying to onedir root"
        cp "$FOUND_WEP" "$WEP"
        chmod +x "$WEP"
    else
        echo "ERROR: QtWebEngineProcess not found anywhere in $APPDIR/EML_MSG_Viewer" >&2
        echo "       The AppImage will crash at launch. Aborting." >&2
        exit 1
    fi
else
    echo "==> QtWebEngineProcess OK: $WEP"
fi

echo "==> Running appimagetool -> $OUTPUT"
ARCH="$ARCH" "$APPIMAGETOOL" --appimage-extract-and-run "$APPDIR" "$OUTPUT"

# --- Generate desktop registration script in dist/ ---
# This script is optional — run it manually after placing the AppImage in its
# permanent location to register it with the desktop environment (launcher,
# file associations, taskbar icon).
REGISTER_SCRIPT="$ROOT/dist/$REGISTER_SCRIPT_NAME"
ICON_DIST="$ROOT/dist/${ICON_NAME}.png"

cp "$ICON_SRC" "$ICON_DIST"

cat > "$REGISTER_SCRIPT" << SCRIPT
#!/usr/bin/env bash
# ${REGISTER_SCRIPT_NAME}
# Registers "${APP_DISPLAY_NAME}" with the desktop environment.
#
# Run this once after placing the AppImage in its permanent location.
# It installs the .desktop file and icon into your home directory so the
# app appears in the launcher and .eml/.msg files open with it.
#
# To unregister, run:  bash ${REGISTER_SCRIPT_NAME} --uninstall
set -euo pipefail

SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
APPIMAGE="\$SCRIPT_DIR/${APPIMAGE_NAME}"
ICON_SRC="\$SCRIPT_DIR/${ICON_NAME}.png"

DESKTOP_DIR="\$HOME/.local/share/applications"
ICON_DIR="\$HOME/.local/share/icons/hicolor/256x256/apps"
DESKTOP_FILE="\$DESKTOP_DIR/${DESKTOP_NAME}"

if [[ "\${1:-}" == "--uninstall" ]]; then
    echo "==> Unregistering ${APP_DISPLAY_NAME} ..."
    rm -f "\$DESKTOP_FILE" "\$ICON_DIR/${ICON_NAME}.png"
    update-desktop-database "\$DESKTOP_DIR" 2>/dev/null || true
    echo "==> Done. The app has been removed from the launcher."
    exit 0
fi

if [[ ! -f "\$APPIMAGE" ]]; then
    echo "ERROR: AppImage not found at \$APPIMAGE" >&2
    echo "       Move the AppImage to its permanent location first, then re-run this script." >&2
    exit 1
fi

echo "==> Registering ${APP_DISPLAY_NAME} ..."
mkdir -p "\$DESKTOP_DIR" "\$ICON_DIR"

# Install icon
cp "\$ICON_SRC" "\$ICON_DIR/${ICON_NAME}.png"

# Write .desktop file with the correct Exec= path to this AppImage
cat > "\$DESKTOP_FILE" << EOF
$(grep -v '^Exec=' "$DESKTOP_SRC")
Exec=\$APPIMAGE %F
EOF

# Notify the desktop environment
update-desktop-database "\$DESKTOP_DIR" 2>/dev/null || true
if command -v xdg-icon-resource &>/dev/null; then
    xdg-icon-resource install --size 256 "\$ICON_SRC" "${ICON_NAME}" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "\$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

echo "==> Done! ${APP_DISPLAY_NAME} is now registered."
echo "    It will appear in your launcher shortly."
echo "    To unregister: bash \$(basename "\$0") --uninstall"
SCRIPT

chmod +x "$REGISTER_SCRIPT"
echo "==> Registration script: $REGISTER_SCRIPT"

echo "==> Done: $OUTPUT"
