# Security model and release process

Email files and every field inside them are treated as untrusted. Parsing and
sanitization run in a disposable worker with time, memory, depth, part-count,
header-count, and decoded-size limits. QtWebEngine has JavaScript disabled and
may load only `cid:` assets validated by the application or a built-in blank
`data:` image.

## Untrusted-parser trust boundary

EML, MSG, and TNEF/`winmail.dat`/`webmail.dat` content is parsed by third-party
libraries (`tnefparse`, `extract-msg`, and the `RTFDE`/`compressed-rtf` RTF
de-encapsulators reachable through both). For the TNEF path these libraries read
and decode the entire blob inside their own constructors, before the
application's per-item parse budget (`max_parts`, `max_headers`,
`max_decoded_bytes`) can engage. RTF de-encapsulation and decompression likewise
expand their input before the result is size-checked.

The effective containment for these parsers is therefore layered and does not
rely on the per-item budget:

- A dedicated pre-parse size cap on TNEF blobs (`max_tnef_bytes`) applied at
  every TNEF entry point, including blobs embedded in EML and MSG messages.
- A deterministic bound on embedded-TNEF recursion (`max_tnef_embed_depth`); a
  hostile chain of nested TNEF messages fails fast as a security limit. An
  embedded blob that hits a TNEF size or depth limit is preserved as an
  exportable attachment rather than aborting the whole message.
- A wall-clock timeout enforced by the parent process.
- An in-process hard memory cap inside the worker: `RLIMIT_AS` on POSIX and a
  Windows Job Object (`JOB_OBJECT_LIMIT_PROCESS_MEMORY`) on Windows, plus a
  cross-platform RSS watchdog thread that self-terminates the worker. These
  back up, and react faster than, the parent's periodic RSS poll.
- Disposable single-use process isolation, so any parser crash, hang, or
  out-of-memory condition is contained to the worker.

The standard executable is fully offline. The red remote-image executable
automatically fetches only HTTP images on port 80 and HTTPS images on port 443.
It performs one DNS resolution as part of connection setup, validates and pins
the returned public IP addresses, rechecks every
redirect, does not use browser cookies, authentication, referrers, or system
proxies, and accepts only size-limited PNG, JPEG, GIF, and WebP data. SVG and
private, loopback, link-local, reserved, and mixed-address DNS results are
blocked. Remote images are additionally limited to 8,192 pixels per dimension,
16 million pixels per canvas, 200 animation frames, 40 million decoded pixels
per image, and 80 million decoded pixels in total. Requests have a ten-second
limit and the complete image-loading phase has a thirty-second limit.

## Builds

- Build with a Python version supported by the dependencies in
  `requirements-build.in`; no exact interpreter version is enforced.
- Both platform scripts create and reuse an isolated `.venv-build` environment;
  build dependencies are never installed into the invoking Python environment.
- Python dependencies use reviewed version ranges rather than generated hash
  locks, and are audited before each build.
- Build scripts run the dependency audit and security regression tests before
  packaging, then produce a CycloneDX SBOM and `SHA256SUMS.txt`.
- Linux requires the reviewed appimagetool snapshot at `tools/appimagetool`.
  Its digest must match `tools/appimagetool.sha256`; the build never downloads
  or executes a moving upstream artifact.

Set `RELEASE_BUILD=1` for a release. Windows then requires `SIGNTOOL_PATH` and
`SIGN_CERT_SHA1`. Linux requires `GPG_KEY_ID` and produces detached armored
signatures for the AppImages and ZIP archive.

The Linux launchers refuse root execution, sandbox-disabling environment
flags, and insecure runtime directories. Systems that cannot run QtWebEngine's
sandbox are intentionally unsupported.
