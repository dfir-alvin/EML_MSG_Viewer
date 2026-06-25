#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================"
echo " Repository cleanup for GitHub upload"
echo " Working directory: $SCRIPT_DIR"
echo "================================================"
echo
echo "This removes build artifacts and generated files."
echo "Source code, tests, fixtures, docs, .git, and tools metadata are kept."
echo

# Only these exact top-level directories may be removed recursively.
GENERATED_DIRS=(
    build
    dist
    .venv-build
    venv
    .pytest_cache
    .mypy_cache
    .ruff_cache
)

for name in "${GENERATED_DIRS[@]}"; do
    target="$SCRIPT_DIR/$name"
    case "$target" in
        "$SCRIPT_DIR/build"|"$SCRIPT_DIR/dist"|"$SCRIPT_DIR/.venv-build"|\
        "$SCRIPT_DIR/venv"|"$SCRIPT_DIR/.pytest_cache"|\
        "$SCRIPT_DIR/.mypy_cache"|"$SCRIPT_DIR/.ruff_cache")
            ;;
        *)
            echo "ERROR: Refusing unexpected recursive cleanup target: $target" >&2
            exit 1
            ;;
    esac
    if [[ -e "$target" || -L "$target" ]]; then
        echo "Removing directory: $name"
        rm -rf -- "$target"
    fi
done

echo "Removing __pycache__ directories..."
find "$SCRIPT_DIR" -type d -name __pycache__ -prune -exec rm -rf -- {} +

echo "Removing compiled Python files (*.pyc, *.pyo)..."
find "$SCRIPT_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

find "$SCRIPT_DIR" -maxdepth 1 -type f -name '*.spec.bak' -delete

# Remove only downloaded appimagetool binaries. Preserve the tools directory,
# tools/appimagetool.sha256, and tools/appimagetool.LICENSE.
if [[ -d "$SCRIPT_DIR/tools" ]]; then
    find "$SCRIPT_DIR/tools" -maxdepth 1 -type f \
        \( -name 'appimagetool' -o -name 'appimagetool-*.AppImage' \) \
        -print -delete
fi

for name in sbom.cdx.json SHA256SUMS.txt; do
    target="$SCRIPT_DIR/$name"
    if [[ -f "$target" || -L "$target" ]]; then
        echo "Removing generated artifact: $name"
        rm -f -- "$target"
    fi
done

echo
echo "================================================"
echo " Cleanup complete."
echo "================================================"
echo "Remaining top-level entries:"
echo
find "$SCRIPT_DIR" -mindepth 1 -maxdepth 1 -printf '%f\n' | sort
echo
