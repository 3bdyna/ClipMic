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
- `paths.py` — Frozen/dev path resolution (`is_frozen`, `bundle_dir`, `user_data_dir`), legacy data migration (`migrate_legacy_data`), logging/exception hooks, and Windows startup registration (`is_startup_enabled`, `set_startup_enabled`).

### Native Components (`native/`)
- `native/directmic/` — `clipmic_apo.cpp`, `clipmic_apo.dll`, `apo_setup.py`, `testhost.cpp`, `build.ps1` (DirectMic APO integration for injecting audio into a physical microphone endpoint).
- `native/procloopback/` — `clipmic_procloop.cpp`, `clipmic_procloop.dll`, `build.ps1` (Windows per-process WASAPI loopback capture).

### Packaging, Config & Tests
- `ClipMic.spec` — PyInstaller build spec (bundles `native/directmic/clipmic_apo.dll`, `native/procloopback/clipmic_procloop.dll`, `ffmpeg.exe`, `clipmic.ico`, `LICENSE`, `THIRD_PARTY_NOTICES.txt`; excludes `tkinter` and unused Qt modules).
- `requirements.txt` — Pinned runtime dependencies (`PySide6`, `numpy`, `PyAudioWPatch`, `sounddevice`, `comtypes`, `psutil`).
- `tests/test_clipmic.py` — Unit tests covering effects/audio core, packaging/i18n readiness, preview end-of-clip latency, and trim dialog UI.

## Commands
- **Run app:** `python main.py`
- **Run tests:** `python -m unittest discover -s tests -v`
- **Build executable:** `pyinstaller ClipMic.spec`

## Workspace & Coding Rules
- Follow `GEMINI.md` strictly:
  - Concise responses only: short bullets, zero filler, no recaps.
  - Only read files explicitly needed for the current task. Never scan directories outside the project root unless given an exact file path.
  - Make minimal, targeted edits; never rewrite whole files when a small diff works.
  - Keep architecture flat and simple (max 6-7 core source files). No over-abstraction, no unused plugins, no telemetry, and no online downloaders.
  - Stop and ask before installing heavy dependencies or editing Windows system/registry settings (including APO/startup changes outside normal app flow).
  - Do not write sprawling test suites or long markdown docs unless asked.
- Always run `python -m unittest discover -s tests -v` after code changes.
- After every code or project update, stage, commit, and push changes to `origin/main` on GitHub.
