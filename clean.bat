@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ================================================
echo  Repository cleanup for GitHub upload
echo  Working directory: %CD%
echo ================================================
echo.
echo This removes build artifacts and generated files.
echo Source code, tests, fixtures, docs, and .git are kept.
echo.

REM --- Top-level generated directories ------------------------------------
for %%D in (build dist .venv-build venv .pytest_cache .mypy_cache .ruff_cache) do (
    if exist "%%D" (
        echo Removing directory: %%D
        rmdir /s /q "%%D"
    )
)

REM --- Python bytecode caches (recursive) ---------------------------------
echo Removing __pycache__ directories...
for /d /r %%D in (__pycache__) do (
    if exist "%%D" rmdir /s /q "%%D"
)

echo Removing compiled Python files (*.pyc, *.pyo)...
del /s /q *.pyc >nul 2>&1
del /s /q *.pyo >nul 2>&1

REM --- PyInstaller stray spec workproduct ---------------------------------
if exist "*.spec.bak" del /q "*.spec.bak" >nul 2>&1

REM --- Downloaded build tools (binaries only) -----------------------------
REM Never remove the tools directory, appimagetool.sha256, or license file.
if exist "tools\appimagetool" (
    echo Removing downloaded build tool: tools\appimagetool
    del /q "tools\appimagetool" >nul 2>&1
)
if exist "tools\appimagetool-*.AppImage" (
    echo Removing downloaded architecture-specific appimagetool binaries...
    del /q "tools\appimagetool-*.AppImage" >nul 2>&1
)

REM --- Build/release outputs that may sit at the root --------------------
for %%F in (sbom.cdx.json SHA256SUMS.txt) do (
    if exist "%%F" (
        echo Removing generated artifact: %%F
        del /q "%%F" >nul 2>&1
    )
)

echo.
echo ================================================
echo  Cleanup complete.
echo ================================================
echo Remaining top-level entries:
echo.
dir /b /a
echo.

endlocal
