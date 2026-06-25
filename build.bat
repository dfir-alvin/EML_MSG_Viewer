@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================
echo  EML/MSG Email Viewer - Hardened Build
echo ============================================

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found in PATH.
    exit /b 1
)

set "VENV_DIR=.venv-build"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
if not exist "%VENV_PYTHON%" (
    echo Creating isolated build environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 exit /b 1
)

echo Installing build dependencies...
"%VENV_PYTHON%" -m pip install -r requirements-build.in
if errorlevel 1 exit /b 1

echo Auditing runtime dependencies...
"%VENV_PYTHON%" -m pip_audit -r requirements-runtime.in
if errorlevel 1 exit /b 1

echo Running security regression tests...
"%VENV_PYTHON%" -m unittest discover -s tests -v
if errorlevel 1 exit /b 1

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo Building onefile executables...
"%VENV_PYTHON%" -m PyInstaller viewer.spec --clean -y
if errorlevel 1 exit /b 1

echo Building onedir executables...
"%VENV_PYTHON%" -m PyInstaller viewer_onedir.spec --clean -y
if errorlevel 1 exit /b 1

echo Smoke-testing packaged QtWebEngine...
set "OLD_QT_QPA_PLATFORM=%QT_QPA_PLATFORM%"
set "QT_QPA_PLATFORM=offscreen"
"dist\EML_MSG_Viewer_onedir\EML_MSG_Viewer.exe" --security-self-test
if errorlevel 1 exit /b 1
"dist\EML_MSG_Viewer_onedir\EML_MSG_Viewer_remote_image.exe" --security-self-test
if errorlevel 1 exit /b 1
set "QT_QPA_PLATFORM=%OLD_QT_QPA_PLATFORM%"

if "%RELEASE_BUILD%"=="1" (
    if not defined SIGNTOOL_PATH (
        echo ERROR: RELEASE_BUILD=1 requires SIGNTOOL_PATH and SIGN_CERT_SHA1.
        exit /b 1
    )
    if not defined SIGN_CERT_SHA1 (
        echo ERROR: RELEASE_BUILD=1 requires SIGNTOOL_PATH and SIGN_CERT_SHA1.
        exit /b 1
    )
)

if defined SIGNTOOL_PATH if defined SIGN_CERT_SHA1 (
    echo Signing Windows executables...
    for %%F in (
        "dist\EML_MSG_Viewer.exe"
        "dist\EML_MSG_Viewer_remote_image.exe"
        "dist\EML_MSG_Viewer_onedir\EML_MSG_Viewer.exe"
        "dist\EML_MSG_Viewer_onedir\EML_MSG_Viewer_remote_image.exe"
    ) do (
        "%SIGNTOOL_PATH%" sign /sha1 "%SIGN_CERT_SHA1%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 %%F
        if errorlevel 1 exit /b 1
    )
)

echo Creating signed onedir archive...
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\EML_MSG_Viewer_onedir' -DestinationPath 'dist\EML_MSG_Viewer_onedir.zip'"
if errorlevel 1 exit /b 1

echo Generating CycloneDX SBOM...
"%VENV_PYTHON%" -m cyclonedx_py requirements requirements-runtime.in --output-format JSON --output-file dist\sbom.cdx.json
if errorlevel 1 exit /b 1

echo Writing release checksums...
powershell -NoProfile -Command "$root=(Resolve-Path 'dist').Path; Get-ChildItem 'dist' -File -Recurse | Where-Object Name -ne 'SHA256SUMS.txt' | Sort-Object FullName | ForEach-Object { $relative=$_.FullName.Substring($root.Length+1).Replace('\','/'); '{0} *{1}' -f (Get-FileHash -Algorithm SHA256 $_.FullName).Hash.ToLower(),$relative } | Set-Content -Encoding ASCII 'dist\SHA256SUMS.txt'"
if errorlevel 1 exit /b 1

echo ============================================
echo  Build complete
echo  dist\EML_MSG_Viewer.exe
echo  dist\EML_MSG_Viewer_remote_image.exe
echo  dist\EML_MSG_Viewer_onedir.zip
echo  dist\sbom.cdx.json
echo  dist\SHA256SUMS.txt
echo ============================================
