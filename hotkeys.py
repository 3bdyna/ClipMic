from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

import paths

_log = logging.getLogger("clipmic.hotkeys")

MIN_BUFFER_SECONDS = 5
MAX_BUFFER_SECONDS = 180
DEFAULT_BUFFER_SECONDS = 30

DEFAULT_CONFIG = {
    "capture_hotkey": "F9",
    "stop_hotkey": "F10",
    "monitor_enabled": True,
    "monitor_volume": 0.85,
    "mic_volume": 1.0,
    "mic_device": None,
    "include_discord": True,
    "loopback_device": None,
    "minimize_to_tray": True,
    "buffer_seconds": DEFAULT_BUFFER_SECONDS,
    "language": "en",
}

VALID_LANGUAGES = ("en", "ar")


def normalize_language(value: object) -> str:
    """Return a valid language code ('en' or 'ar'), defaulting to 'en'."""
    if isinstance(value, str):
        code = value.strip().lower()
        if code in VALID_LANGUAGES:
            return code
    return "en"


def clamp_buffer_seconds(value: object) -> int:
    """Validate and clamp capture ring buffer duration to [5, 180] seconds."""
    try:
        secs = int(round(float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_BUFFER_SECONDS
    return max(MIN_BUFFER_SECONDS, min(MAX_BUFFER_SECONDS, secs))

# Win32 RegisterHotKey constants
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
WM_APP_RELOAD = 0x8001

_SPECIAL_VK: dict[str, int] = {
    **{f"F{i}": 0x6F + i for i in range(1, 25)},
    **{f"NUMPAD{i}": 0x60 + i for i in range(10)},
    "SPACE": 0x20,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "DEL": 0x2E,
    "INSERT": 0x2D,
    "INS": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "PAUSE": 0x13,
    "CAPSLOCK": 0x14,
    "MINUS": 0xBD,
    "-": 0xBD,
    "EQUAL": 0xBB,
    "=": 0xBB,
    "[": 0xDB,
    "]": 0xDD,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
    "\\": 0xDC,
    "`": 0xC0,
}


@dataclass
class ClipEntry:
    id: str
    name: str
    file: str
    hotkey: str = ""
    preset: str = "Normal"
    duration: float = 0.0


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:6]}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def resolve_clip_path(file_field: str) -> Path:
    """Resolve a clip's relative or absolute file path against user_data_dir()."""
    p = Path(file_field)
    if p.is_absolute():
        return p
    return paths.user_data_dir() / p


def load_config(path: Optional[Path] = None) -> dict:
    target = path or paths.config_path()
    cfg = dict(DEFAULT_CONFIG)
    if target.is_file():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update(data)
            else:
                _log.warning("Ignoring malformed config file (not a JSON object): %s", target)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            _log.warning("Could not parse config file %s (%s); using defaults.", target, exc)
    cfg["buffer_seconds"] = clamp_buffer_seconds(cfg.get("buffer_seconds", DEFAULT_BUFFER_SECONDS))
    cfg["language"] = normalize_language(cfg.get("language", "en"))
    return cfg


def save_config(cfg: dict, path: Optional[Path] = None) -> None:
    target = path or paths.config_path()
    out = dict(cfg)
    out["buffer_seconds"] = clamp_buffer_seconds(out.get("buffer_seconds", DEFAULT_BUFFER_SECONDS))
    out["language"] = normalize_language(out.get("language", "en"))
    _atomic_write_text(target, json.dumps(out, indent=2))


def load_clips(path: Optional[Path] = None) -> list[ClipEntry]:
    target = path or paths.clips_json_path()
    paths.clips_dir().mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            _log.warning("Corrupt clips file (expected JSON list): %s", target)
            return []
        clips: list[ClipEntry] = []
        for item in raw:
            if not isinstance(item, dict) or "file" not in item or "name" not in item:
                continue
            try:
                dur = float(item.get("duration", 0.0))
            except (TypeError, ValueError):
                dur = 0.0
            clips.append(
                ClipEntry(
                    id=str(item.get("id") or uuid.uuid4().hex[:8]),
                    name=str(item["name"]),
                    file=str(item["file"]),
                    hotkey=str(item.get("hotkey", "") or ""),
                    preset=str(item.get("preset", "Normal") or "Normal"),
                    duration=dur,
                )
            )
        return clips
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _log.warning("Corrupt clips file %s (%s); recovering with empty list.", target, exc)
        try:
            bak = target.with_suffix(".json.corrupt")
            if not bak.exists():
                target.replace(bak)
        except OSError:
            pass
        return []


def save_clips(clips: list[ClipEntry], path: Optional[Path] = None) -> None:
    target = path or paths.clips_json_path()
    paths.clips_dir().mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target, json.dumps([asdict(c) for c in clips], indent=2))


def parse_hotkey(hotkey_str: str) -> tuple[int, int] | None:
    """Parse a hotkey string like 'Ctrl+Shift+F9' into (win32_mods, vk_code)."""
    text = (hotkey_str or "").strip()
    if not text:
        return None
    parts = [p.strip().upper() for p in text.split("+") if p.strip()]
    if not parts:
        return None

    mods = MOD_NOREPEAT
    vk = None
    for part in parts:
        if part in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif part == "ALT":
            mods |= MOD_ALT
        elif part == "SHIFT":
            mods |= MOD_SHIFT
        elif part in ("WIN", "SUPER", "META"):
            mods |= MOD_WIN
        else:
            if part in _SPECIAL_VK:
                vk = _SPECIAL_VK[part]
            elif len(part) == 1 and ("A" <= part <= "Z" or "0" <= part <= "9"):
                vk = ord(part)
            else:
                return None
    if vk is None:
        return None
    return mods, vk


class HotkeyManager:
    """Thread-safe Windows global hotkey manager using Win32 RegisterHotKey."""

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._lock = threading.Lock()
        self._bindings: dict[str, tuple[str, Callable[[], None]]] = {}
        self._id_to_cb: dict[int, Callable[[], None]] = {}
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._reload_done = threading.Event()
        self._thread: threading.Thread | None = None
        self._failed: list[str] = []

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="ClipMicHotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def stop(self) -> None:
        tid = self._thread_id
        if tid:
            self._user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread_id = None

    def set_bindings(self, bindings: dict[str, tuple[str, Callable[[], None]]]) -> list[str]:
        """Replace all registered hotkeys. Returns list of hotkey strings that failed to register."""
        with self._lock:
            self._bindings = dict(bindings)
            self._failed = []
        if not (self._thread and self._thread.is_alive()):
            self.start()
        elif self._thread_id:
            self._reload_done.clear()
            self._user32.PostThreadMessageW(self._thread_id, WM_APP_RELOAD, 0, 0)
            self._reload_done.wait(timeout=1.0)
        with self._lock:
            return list(self._failed)

    def _apply_bindings(self) -> None:
        for hk_id in list(self._id_to_cb.keys()):
            self._user32.UnregisterHotKey(None, hk_id)
        self._id_to_cb.clear()

        with self._lock:
            items = list(self._bindings.items())
            failed: list[str] = []

        next_id = 1
        for _name, (hk_str, cb) in items:
            parsed = parse_hotkey(hk_str)
            if not parsed:
                if hk_str.strip():
                    failed.append(hk_str)
                continue
            mods, vk = parsed
            if self._user32.RegisterHotKey(None, next_id, mods, vk):
                self._id_to_cb[next_id] = cb
                next_id += 1
            else:
                failed.append(hk_str)

        with self._lock:
            self._failed = failed
        self._reload_done.set()

    def _run(self) -> None:
        self._thread_id = self._kernel32.GetCurrentThreadId()
        msg = wt.MSG()
        # Force message queue creation before signaling ready
        self._user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._apply_bindings()
        self._ready.set()

        while self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_APP_RELOAD:
                self._apply_bindings()
            elif msg.message == WM_HOTKEY:
                cb = self._id_to_cb.get(int(msg.wParam))
                if cb:
                    try:
                        cb()
                    except Exception:
                        pass

        for hk_id in list(self._id_to_cb.keys()):
            self._user32.UnregisterHotKey(None, hk_id)
        self._id_to_cb.clear()
