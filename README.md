# ClipMic

Windows desktop audio clipper and soundboard with per-app rolling capture, voice effects, and direct microphone injection (no virtual cable required).

## Features
- **Rolling Multi-Channel Buffer**: Continuously buffers the last 5–180 seconds of audio per active app/game via Windows Process Loopback (`clipmic_procloop.dll`) plus an optional parallel microphone buffer, with automatic WASAPI loopback fallback.
- **Instant Capture & Channel Picker (`F9`)**: Freeze the rolling buffer, solo/preview individual app or mic tracks on mini-waveforms, and select which channels to mix.
- **Waveform Trimmer & Voice Effects**: Trim clips visually with scrubbing playback, apply built-in or custom FFmpeg voice effects, save to your soundboard with global hotkeys, or export to MP3.
- **Direct Mic Injection (Windows APO)**: Inject clips directly into your real microphone via the bundled Windows Audio Processing Object (`clipmic_apo.dll`) with independent mic and monitor volume controls.
- **Drag-and-Drop Media Import**: Drop audio or video files directly onto the window to decode, trim, apply effects, and bind to hotkeys.
- **Bilingual UI**: Live English and Arabic (RTL) interface with dark native Windows title bars and system tray support.

## Requirements
- Windows 10 (20H1+) / Windows 11
- Python 3.11+
- FFmpeg available on `PATH` (for effects processing, media import, and MP3 export)

## Quick Start
```powershell
pip install -r requirements.txt
python main.py
```

## Project Structure
- `main.py` — App entrypoint, single-instance guard, system tray, and lifecycle management.
- `ui.py` — PySide6 dark UI (`MainWindow`, `ChannelPickerDialog`, `TrimDialog`, `EffectBuilderDialog`, `ManageEffectsDialog`).
- `audio_buffer.py` — Per-app process loopback, microphone ring buffer, WASAPI fallback, and track alignment/mixing.
- `mic_inject.py` — Direct Mic APO playback and monitor routing.
- `effects.py` — Voice effect presets, FFmpeg filter pipeline, waveform peak computation, and media import/MP3 export.
- `hotkeys.py` — Global hotkey registration and config/clip persistence.
- `i18n.py` — English / Arabic localization and RTL helpers.
- `native/` — C++ sources and build scripts for `clipmic_procloop.dll` (Process Loopback) and `clipmic_apo.dll` (Direct Mic APO).

## License
See [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).
