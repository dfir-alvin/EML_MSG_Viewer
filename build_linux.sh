#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv-build"
TOOLS_DIR="$SCRIPT_DIR/tools"
cd "$SCRIPT_DIR"

if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found." >&2
    exit 1
fi

APPIMAGETOOL_VERSION="1.9.1"
MACHINE_ARCH="$(uname -m)"
case "$MACHINE_ARCH" in
    x86_64|amd64)
        ARCH="x86_64"
        APPIMAGETOOL_ASSET="appimagetool-x86_64.AppImage"
        APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"
        ;;
    aarch64|arm64)
        ARCH="aarch64"
        APPIMAGETOOL_ASSET="appimagetool-aarch64.AppImage"
        APPIMAGETOOL_SHA256="f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158"
        ;;
    *)
        echo "ERROR: Unsupported architecture: $MACHINE_ARCH" >&2
        exit 1
        ;;
esac

APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/$APPIMAGETOOL_VERSION/$APPIMAGETOOL_ASSET"
APPIMAGETOOL_PATH="${APPIMAGETOOL_PATH:-$TOOLS_DIR/$APPIMAGETOOL_ASSET}"

if [[ ! -f "$APPIMAGETOOL_PATH" ]]; then
    if ! command -v curl &>/dev/null; then
        echo "ERROR: curl is required to download appimagetool." >&2
        exit 1
    fi

    mkdir -p "$(dirname "$APPIMAGETOOL_PATH")"
    DOWNLOAD_PATH="$(mktemp "${APPIMAGETOOL_PATH}.download.XXXXXX")"
    cleanup_download() { rm -f "$DOWNLOAD_PATH"; }
    trap cleanup_download EXIT

    echo "==> Downloading appimagetool $APPIMAGETOOL_VERSION for $ARCH"
    curl --fail --location --silent --show-error \
        --proto '=https' --proto-redir '=https' \
        --connect-timeout 20 --max-time 180 --max-redirs 5 \
        --max-filesize 30000000 \
        --output "$DOWNLOAD_PATH" "$APPIMAGETOOL_URL"

    DOWNLOADED_SHA256="$(sha256sum "$DOWNLOAD_PATH" | awk '{print $1}')"
    if [[ "$DOWNLOADED_SHA256" != "$APPIMAGETOOL_SHA256" ]]; then
        echo "ERROR: Downloaded appimagetool SHA-256 mismatch." >&2
        echo "Expected: $APPIMAGETOOL_SHA256" >&2
        echo "Actual:   $DOWNLOADED_SHA256" >&2
        exit 1
    fi

    mv "$DOWNLOAD_PATH" "$APPIMAGETOOL_PATH"
    trap - EXIT
fi

ACTUAL_SHA256="$(sha256sum "$APPIMAGETOOL_PATH" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$APPIMAGETOOL_SHA256" ]]; then
    echo "ERROR: appimagetool SHA-256 mismatch." >&2
    echo "Expected: $APPIMAGETOOL_SHA256" >&2
    echo "Actual:   $ACTUAL_SHA256" >&2
    exit 1
fi
chmod u+x "$APPIMAGETOOL_PATH"
export APPIMAGETOOL_PATH

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    python3 -m venv "$VENV_DIR"
fi
VENV_PYTHON="$VENV_DIR/bin/python"

echo "==> Installing build dependencies"
"$VENV_PYTHON" -m pip install -r "$SCRIPT_DIR/requirements-build.in"

echo "==> Auditing runtime dependencies"
"$VENV_PYTHON" -m pip_audit -r "$SCRIPT_DIR/requirements-runtime.in"

echo "==> Running security regression tests"
"$VENV_PYTHON" -m unittest discover -s "$SCRIPT_DIR/tests" -v

echo "==> Cleaning build artifacts"
rm -rf "$SCRIPT_DIR/build/" "$SCRIPT_DIR/dist/"
mkdir -p "$SCRIPT_DIR/dist/"

echo "==> Running PyInstaller"
"$VENV_PYTHON" -m PyInstaller "$SCRIPT_DIR/viewer_linux_onedir.spec" --clean -y

if command -v zip &>/dev/null; then
    (cd "$SCRIPT_DIR/dist" && zip -r "EML_MSG_Viewer_Linux.zip" "EML_MSG_Viewer_Linux/")
else
    echo "ERROR: zip is required for a complete release build." >&2
    exit 1
fi

bash "$SCRIPT_DIR/linux/make_appimage.sh" standard "$ARCH"
bash "$SCRIPT_DIR/linux/make_appimage.sh" remote_image "$ARCH"

if [[ "$(id -u)" == "0" ]]; then
    echo "ERROR: Packaged sandbox smoke tests cannot run as root." >&2
    exit 1
fi
echo "==> Smoke-testing sandboxed AppImages"
run_smoke_test() {
    local label="$1"
    local appimage="$2"
    local status

    echo "==> Smoke test started: $label"
    if QT_QPA_PLATFORM=offscreen APPIMAGE_EXTRACT_AND_RUN=1 PYTHONFAULTHANDLER=1 \
        timeout 25s "$appimage" --security-self-test; then
        echo "==> Smoke test passed: $label"
        return 0
    else
        status=$?
    fi

    case "$status" in
        2) reason="WebEngine reported that the security test page failed to load" ;;
        3) reason="the internal 15-second page-load watchdog expired" ;;
        4) reason="the internal WebEngine cleanup watchdog expired" ;;
        5) reason="the self-test raised a Python exception while loading" ;;
        124) reason="the external 25-second process watchdog expired" ;;
        139) reason="the process received SIGSEGV; inspect the preceding self-test phase and Qt messages" ;;
        *) reason="unexpected process failure" ;;
    esac
    echo "ERROR: Smoke test failed: $label (exit $status: $reason)." >&2
    return "$status"
}

run_smoke_test "offline/$ARCH sandbox and CID rendering" \
    "$SCRIPT_DIR/dist/EML_MSG_Viewer-${ARCH}.AppImage"
run_smoke_test "restricted-remote-image/$ARCH sandbox and CID rendering" \
    "$SCRIPT_DIR/dist/EML_MSG_Viewer_remote_image-${ARCH}.AppImage"

echo "==> Generating CycloneDX SBOM"
"$VENV_PYTHON" -m cyclonedx_py requirements "$SCRIPT_DIR/requirements-runtime.in" \
    --output-format JSON --output-file "$SCRIPT_DIR/dist/sbom.cdx.json"

if [[ -n "${GPG_KEY_ID:-}" ]]; then
    while IFS= read -r -d '' artifact; do
        gpg --batch --yes --armor --detach-sign --local-user "$GPG_KEY_ID" "$artifact"
    done < <(find "$SCRIPT_DIR/dist" -maxdepth 1 -type f \( -name '*.AppImage' -o -name '*.zip' \) -print0)
elif [[ "${RELEASE_BUILD:-0}" == "1" ]]; then
    echo "ERROR: RELEASE_BUILD=1 requires GPG_KEY_ID for artifact signing." >&2
    exit 1
fi

echo "==> Writing release checksums"
(
    cd "$SCRIPT_DIR/dist"
    find . -maxdepth 1 -type f ! -name 'SHA256SUMS.txt' -print0 \
        | sort -z \
        | xargs -0 sha256sum > SHA256SUMS.txt
)

echo "==> Build complete"
echo "    dist/EML_MSG_Viewer-${ARCH}.AppImage"
echo "    dist/EML_MSG_Viewer_remote_image-${ARCH}.AppImage"
echo "    dist/EML_MSG_Viewer_Linux.zip"
echo "    dist/sbom.cdx.json"
echo "    dist/SHA256SUMS.txt"
