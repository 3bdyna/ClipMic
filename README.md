# ClipMic

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Platform: Windows 10/11](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6.svg)](#download--install)
[![Latest Release](https://img.shields.io/github/v/release/3bdyna/clipmic?label=Download)](https://github.com/3bdyna/clipmic/releases/latest)

**Windows soundboard, rolling per-app audio clipper, and Direct Mic APO audio injector — no virtual audio cable needed.**

> **Honest Note:** I am not a professional programmer—I mostly vibe-coded ClipMic with AI because I wanted a clean, lightweight Windows soundboard and audio clipper that injects directly into my microphone without messing with virtual audio cables. Expect occasional quirks, and bug reports or PRs are very welcome!

---

## What It Does & Key Features

ClipMic lets you capture, trim, and play back desktop or game audio directly through your real physical microphone on Windows 10/11:

- **Direct Mic Injection (Windows APO):** Inject clips straight into your physical microphone via the bundled Windows Audio Processing Object (`clipmic_apo.dll`)—no VB-Cable, Voicemeeter, or virtual audio routing required—with independent mic and speaker monitor volume controls.
- **Rolling Per-App & Mic Audio Buffering:** Continuously buffers the last 5–180 seconds of audio per active app/game via Windows Process Loopback (`clipmic_procloop.dll`) alongside an optional parallel microphone buffer, with automatic WASAPI loopback fallback.
- **Instant Fast Clip (`F8`) & Channel Picker (`F9`):** Hit `F8` to grab an instant clip, or `F9` to freeze the rolling buffer, solo/preview individual app or mic tracks on mini-waveforms, and pick which channels to mix.
- **Non-Destructive Multi-Track Trimming & FFmpeg Voice Effects:** Trim clips visually with scrubbing playback, apply built-in or custom FFmpeg voice effects, bind clips to global soundboard hotkeys, drag-and-drop audio/video files to import, or export to MP3.
- **English & Arabic (RTL) UI:** Full bilingual interface with live language switching, right-to-left (RTL) layout support, dark native Windows title bars, and system tray integration.
- **Built-In SHA-256 Verified Auto-Updater:** Checks GitHub Releases in the background (frozen builds only) and verifies downloads against `SHA256SUMS.txt` before updating in-place.
- **100% Local & Zero Telemetry:** No accounts, no tracking, no analytics, and no third-party media downloaders—all audio stays on your machine.

---

## Download & Install

1. Download the latest installer (`ClipMic-Setup-*.exe`) from [**GitHub Releases**](https://github.com/3bdyna/clipmic/releases/latest).
2. Run `ClipMic-Setup-*.exe` to install per-user (no administrator privileges required unless you enable **Direct Mic APO** inside the app).

> **Windows SmartScreen Note:** Because ClipMic is an unsigned indie app, Windows Defender SmartScreen may show a *"Windows protected your PC"* prompt. Click **More info** → **Run anyway** to proceed.

---

## Build / Run from Source

### Requirements
- Windows 10 (20H1+) or Windows 11
- Python 3.11+
- FFmpeg available on `PATH` (for effects processing, media import, and MP3 export)

### Quick Start & Tests
```powershell
pip install -r requirements.txt
python main.py
```

Run the unit test suite:
```powershell
$env:PYTHONPATH=".;tests"; python -m unittest test_clipmic -v
```

---

## License & Credits

Licensed under the **GNU General Public License v3.0 (GPL-3.0)** — see [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.txt](THIRD_PARTY_NOTICES.txt).

The Direct Mic APO technique was inspired by [Onion Board](https://github.com/Onion-Alien/onion-board).
