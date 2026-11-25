# AGENTS.md

## Project Overview
**ClipMic** is a Windows 10/11 soundboard, rolling audio-buffer clipper, and virtual/direct microphone audio injector built with **Python 3.11**, **PySide6**, **NumPy**, **PyAudioWPatch**, **sounddevice**, **FFmpeg**, and custom **C++ native DLLs** (`native/`).

## Repository Layout
Flat root architecture (keep source files minimal and avoid over-abstraction):

### Core Python Modules
- `main.py` — Application entry point, single-instance Windows mutex (`_acquire_single_instance_mutex`), and APO CLI dispatch (`_parse_apo_cli`, `_dispatch_apo_cli`).
- `ui.py` — PySide6 desktop UI (`MainWindow`), custom styling (`_ClipMicStyle`), waveform/trimming dialogs (`TrimDialog`, `WaveformWidget`, `MiniWaveformWidget`), channel picker (`ChannelPickerDialog`), effect dialogs (`EffectBuilderDialog`, `ManageEffectsDialog`), telemetry/drop-zone widgets, and background worker threads (`_RenderWorker`, `_DecodeWorker`, `_ExportWorker`, `_ApoProcessWorker`, `_DeviceScanWorker`).
- `audio_buffer.py` — Rolling WASAPI loopback & per-process audio capture (`LoopbackRingBuffer`, `MicRingBuffer`, `MultiChannelCapture`, `_ProcLoopLib`), COM audio session enumeration (`enumerate_active_audio_sessions`), channel classification (`detect_channel_kind`), and track mixing (`mix_float_tracks`).
- `mic_inject.py` — Microphone injection and speaker preview (`MicInjector`, `DirectMicRing`), WASAPI output helpers, and 48 kHz mono resampling (`_to_48k_mono`).
- `effects.py` — FFmpeg-backed audio/video decoding, WAV I/O, MP3 export, waveform peak computation, and built-in/custom effect presets (`build_filter_from_params`, `process_clip`, `decode_audio_to_wav`, `export_wav_to_mp3`).
- `hotkeys.py` — Persistent config/clip storage (`load_config`, `save_config`, `load_clips`, `save_clips`, `ClipEntry`) and Win32 global hotkey registration (`HotkeyManager`, `parse_hotkey`).
- `i18n.py` — Localization (`t`, `get_lang`, `set_lang`, `is_rtl`, `localize_effect_name`).
- `paths.py` — Single source of truth for versioning (`APP_VERSION`, `GITHUB_REPO`), frozen/dev path resolution (`is_frozen`, `bundle_dir`, `user_data_dir`, `updates_dir`), legacy data migration (`migrate_legacy_data`), logging/exception hooks, and Windows startup registration (`is_startup_enabled`, `set_startup_enabled`).
- `updater.py` — Stdlib-only GitHub Releases updater (`check_for_updates`, `download_and_verify_update`, `launch_installer`, strict HTTPS host allowlist + redirect validation, SHA-256 verification).

### Native Components (`native/`)
- `native/directmic/` — `clipmic_apo.cpp`, `clipmic_apo.dll`, `apo_setup.py`, `testhost.cpp`, `build.ps1` (DirectMic APO integration for injecting audio into a physical microphone endpoint).
- `native/procloopback/` — `clipmic_procloop.cpp`, `clipmic_procloop.dll`, `build.ps1` (Windows per-process WASAPI loopback capture).

### Packaging, Installer, Release Pipeline & Tests
- `ClipMic.spec` — PyInstaller build spec (bundles `native/directmic/clipmic_apo.dll`, `native/procloopback/clipmic_procloop.dll`, `ffmpeg.exe`, `clipmic.ico`, `LICENSE`, `THIRD_PARTY_NOTICES.txt`; excludes `tkinter` and unused Qt modules; must NEVER exclude `ssl`/`urllib`).
- `installer/ClipMic.iss` — Per-user Inno Setup 6 installer (`PrivilegesRequired=lowest`, fixed `AppId`, `AppMutex` silent-update wait loop, `/RELAUNCH` auto-relaunch).
- `scripts/build_release.ps1` — 1-click PowerShell 5.1+ release pipeline (reads `paths.APP_VERSION`, runs tests, builds PyInstaller bundle, compiles Inno Setup installer, writes `SHA256SUMS.txt`, optional `-Publish` via `gh release create`).
- `requirements.txt` — Pinned runtime dependencies (`PySide6`, `numpy`, `PyAudioWPatch`, `sounddevice`, `comtypes`, `psutil`).
- `tests/test_clipmic.py` — Unit tests covering effects/audio core, packaging/i18n readiness, preview end-of-clip latency, trim dialog UI, updater security/logic, and release/installer guardrails.

## Commands
- **Run app:** `python main.py`
- **Run tests:** `$env:PYTHONPATH=".;tests"; python -m unittest test_clipmic -v`
- **Build local release installer (no publish):** `.\scripts\build_release.ps1`
- **Publish release to GitHub:** `.\scripts\build_release.ps1 -Publish` (or `-SkipBuild -SkipTests -Publish` after verifying a clean local build)

## Release, Installer & Updater Guardrails (DO NOT BREAK)
1. **Single Version Source:** `paths.APP_VERSION` (`MAJOR.MINOR.PATCH`) is the ONLY hardcoded version string. Never hardcode versions in `ui.py`, `updater.py`, `installer/ClipMic.iss`, or `scripts/build_release.ps1`.
2. **Never Change Inno Setup `AppId`:** The fixed GUID `{{8D4F6C2B-9A3E-4E71-B5C8-3F1D2E6A940B}` in `installer/ClipMic.iss` must NEVER change, or Windows will install duplicate copies instead of upgrading in-place.
3. **Mutex Name Parity:** `main._MUTEX_NAME` (`Global\ClipMic_SingleInstance_Mutex`) MUST stay identical to `AppMutex` and `CheckForMutexes(...)` in `installer/ClipMic.iss` (enforced by automated tests). Always release the mutex in `main.py` before launching the silent installer.
4. **User Data Preservation:** Never store user clips/configs inside the install directory, and NEVER delete `%LOCALAPPDATA%\ClipMic` during install, upgrade, or uninstall.
5. **Updater Security Invariants:** Keep `updater.py` 100% Python stdlib (zero new pip packages). Never weaken `TRUSTED_DOWNLOAD_HOSTS`, never disable TLS verification or per-hop redirect validation, and never skip `SHA256SUMS.txt` verification before launching an installer.
6. **Non-Intrusive Startup Checks:** Background startup update checks run ONLY in frozen builds (`paths.is_frozen()`), fail silently offline, and NEVER show modal dialogs on startup.
7. **DirectMic APO DLL Upgrades:** `clipmic_apo.dll` is installed to `%ProgramData%\ClipMic\MicPlugin\` only via explicit elevated `apo_setup.py` flow when a user enables Direct Mic. Never silently modify Windows registry or `Audiosrv` from the per-user installer; if a future release updates `clipmic_apo.dll`, users refresh it via Disable Mic → Enable Mic.
8. **Network Policy:** No telemetry and no third-party media/audio downloaders. The ONLY permitted outbound network traffic is `updater.py` querying and downloading hash-verified releases from `paths.GITHUB_REPO` on GitHub Releases.
9. **Explicit Release Gate:** Never bump `paths.APP_VERSION`, create git tags, or run `-Publish` unless the user explicitly says `"publish update"` (or asks to release).
10. **Auto-Apply Local App Updates:** After any app code/UI edit (skip for docs-only edits like `README.md`/`AGENTS.md`), once unit tests pass and changes are committed/pushed, automatically run `.\scripts\build_release.ps1 -SkipTests -InstallLocal` so the user's installed app updates in-place and relaunches automatically.
11. **2-Word Publish Workflow (`publish update`):** When the user says `"publish update"`, check if tag `v<APP_VERSION>` already exists on GitHub; if so, auto-bump the patch version in `paths.py`, run tests, commit & push to `origin/main`, and run `.\scripts\build_release.ps1 -Publish`.

## Workspace & Coding Rules
- Only read files explicitly needed for the current task. Never scan directories outside the project root unless given an exact file path.
- Make minimal, targeted edits; never rewrite whole files when a small diff works.
- Keep architecture flat and simple. No over-abstraction, no unused plugins, no telemetry, and no online downloaders.
- Stop and ask before installing heavy dependencies or editing Windows system/registry settings (including APO/startup changes outside normal app flow).
- Do not write sprawling test suites or long markdown docs unless asked.
- Always run `$env:PYTHONPATH=".;tests"; python -m unittest test_clipmic -v` after code changes.
- After every code or project update, stage, commit, and push changes to `origin/main` on GitHub.
