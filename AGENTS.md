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
- **Releases, local install (`-InstallLocal`), installer/updater edits, or `"publish update"`:** Load the on-demand `publish-release` skill (`.agents/skills/publish-release/SKILL.md`).

## Always-On Guardrails & Coding Rules
1. **Explicit Release Gate:** Never bump `paths.APP_VERSION`, create git tags, or run `-Publish` unless the user explicitly says `"publish update"` (or asks to release).
2. **Core Safety Invariants:** `paths.APP_VERSION` is the single version source; keep `updater.py` 100% Python stdlib with HTTPS/TLS/SHA-256 checks; never store user data in the install dir or delete `%LOCALAPPDATA%\ClipMic`; no telemetry or third-party downloaders.
3. **Flat & Minimal:** Only read files needed for the task; make targeted edits; keep architecture flat and simple. Stop and ask before installing heavy dependencies or editing Windows system/registry settings.
4. **Smart Verification:** For small/iterative edits, run fast targeted verification (`python -m py_compile <touched.py>` or the relevant test class). Always run the full suite (`$env:PYTHONPATH=".;tests"; python -m unittest test_clipmic -v`) before finishing multi-file/critical changes (audio, updater, packaging, installer) or publishing a release.
5. **Sync:** After completing verified updates, stage, commit, and push changes to `origin/main` on GitHub.
