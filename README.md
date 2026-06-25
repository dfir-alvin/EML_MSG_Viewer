# EML/MSG Email Viewer

All-in-one email file viewer designed around safely view untrusted emails for analysis. Application is largely written via agentic engineering workflow (aka LLM/AI). Program is designed with rudimentary security mechanisms to try and prevent successful execution of potential malware due to file parser abuse, accidental user interactions, and hopefully some of the issues that may come with agentic coding this application. Defense in Depth is still recommended when using this program to review untrusted emails. This project was largely inspired by Outlook's UI, but solves issues of licensing on shared VMs and some user preferring Linux over Windows. 

## Features

- Support for EML and MSG (with experimental support for embedded winmail.dat files)
- Two modes:
  - **Default (offline):** all remote images are blocked (blue icon)
  - **Remote image variant:** restricted remote image loading with SSRF protection (red icon)
- Main screen shows both From and Reply-Path header for easy validation of source
- Allow direct searching of headers
- Allows export of attachments
- Disables URL clicks, but allow right click to copy URL
- Supports opening files from the command line: `EML_MSG_Viewer.exe path/to/email.eml` for automation in sandbox

## Security

- Sandbox parsing process segmented from main GUI process
- Enforce Chromium sandbox isolation on QtWebEngine renderer
- Force sanitize URLs
- Remote image fetching attempts to check DNS responses against private IP ranges and MIME type before loading
- Force hard parser and resource limits on files

## Requirements

- Python 3.11+
- Windows 10/11 or Linux (x86_64 or aarch64)

Runtime dependencies (pinned in `requirements-runtime.in`):

| Package | Purpose |
|---|---|
| PyQt6 ≥ 6.6 | GUI framework |
| PyQt6-WebEngine ≥ 6.6 | Sandboxed HTML rendering |
| extract-msg ≥ 0.52 | Outlook MSG parsing |
| tnefparse ≥ 1.4 | TNEF / winmail.dat parsing |
| bleach ≥ 6.1 | HTML sanitisation |
| tinycss2 ≥ 1.0 | CSS parsing for sanitiser |
| beautifulsoup4 ≥ 4.12 | HTML tree manipulation |
| psutil ≥ 6 | Worker process memory monitoring |

## Building a distributable executable (Recommended)

### Windows

```bat
build.bat
```

The script:
1. Creates an isolated venv (`.venv-build`)
2. Installs build dependencies
3. Runs `pip-audit` against runtime dependencies
4. Runs the security test suite
5. Builds two PyInstaller outputs:
   - `dist\EML_MSG_Viewer.exe` — single-file executable
   - `dist\EML_MSG_Viewer_onedir\` — onedir layout (faster startup)
6. Runs a sandbox smoke test against the packaged executable
7. Optionally code-signs (set `SIGNTOOL_PATH` and `SIGN_CERT_SHA1`)
8. Produces `dist\sbom.cdx.json` (CycloneDX SBOM) and `dist\SHA256SUMS.txt`

### Linux (x86/64 and ARM64)

Auto downloads `appimagetool` and verifies against hash to build appimage of file. 

```bash
bash build_linux.sh
```

## Running from source (Not Recommeded)

```bash
# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # Linux

# Install runtime dependencies
pip install -r requirements.txt

# Launch the viewer
python main.py

# Open a file directly
python main.py path/to/email.eml

# Launch with restricted remote image loading
python main_remote_image.py
```

## Project structure

```
main.py                  # Entry point (offline mode)
main_remote_image.py     # Entry point (restricted remote image mode)
viewer/
  app_window.py          # Main window, drag-and-drop, file loading
  email_parser.py        # EML, MSG, and TNEF parsers with resource limits
  body_view.py           # Hardened QtWebEngine wrapper
  header_panel.py        # Header display + "Email Headers..." dialog trigger
  header_dialog.py       # Searchable all-headers dialog
  attachment_panel.py    # Attachment list and save-to-disk
  sanitizer.py           # HTML sanitisation and remote image extraction
  security.py            # ParseLimits, RemoteFetchPolicy, shared types
  load_thread.py         # Qt thread supervising the worker process
  load_worker.py         # Out-of-process worker entry point
  remote_fetch.py        # SSRF-resistant remote image fetcher
  cid_scheme_handler.py  # Custom cid: URL scheme for inline images
tests/                   # Security regression tests
resources/               # Application icons
build.bat                # Windows build script
build_linux.sh           # Linux build script
requirements-runtime.in  # Pinned runtime dependencies
requirements-build.in    # Build-only dependencies (PyInstaller, pip-audit, etc.)
```

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

This project uses PyQt6 (GPL-3.0), extract-msg (GPL-3.0), and other open-source libraries. See `dist/sbom.cdx.json` (generated at build time) for the full dependency inventory with license identifiers.
