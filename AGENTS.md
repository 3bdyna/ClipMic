# AGENTS.md

## Project Overview
ClipMic is a Windows soundboard / microphone audio injector built with Python, Tkinter, and native DLLs (`native/`).

## Layout
- `main.py`, `ui.py`, `audio_buffer.py`, `effects.py`, `hotkeys.py`, `i18n.py`, `mic_inject.py`, `paths.py`
- `tests/test_clipmic.py` — unit tests (`python -m unittest discover -s tests -v`)
- `ClipMic.spec` — PyInstaller build spec

## Rules
- Follow `GEMINI.md` strictly: concise responses, minimal targeted edits, flat architecture (max 6-7 source files), no over-abstraction, no telemetry or online downloaders.
- Never scan directories outside the project root unless given an exact path.
- Stop and ask before installing heavy dependencies or editing Windows system/registry settings.
- Run `python -m unittest discover -s tests -v` after code changes.
- After every code or project update, stage, commit, and push changes to `origin/main` on GitHub.
