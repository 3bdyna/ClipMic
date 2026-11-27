from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import threading
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Optional

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None  # type: ignore[assignment]

APP_NAME = "ClipMic"
APP_VERSION = "1.0.1"
GITHUB_REPO = "3bdyna/clipmic"
STARTUP_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "ClipMic"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Directory holding bundled read-only assets (_MEIPASS in frozen mode, repo root in dev)."""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def exe_dir() -> Path:
    """Directory containing the running executable (or repo root in dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_asset(*parts: str) -> Path:
    """Resolve a read-only bundled asset, checking _MEIPASS first, then executable directory."""
    primary = bundle_dir().joinpath(*parts)
    if primary.exists():
        return primary
    alt = exe_dir().joinpath(*parts)
    if alt.exists():
        return alt
    return primary


def procloop_dll_path() -> Path:
    """Resolve clipmic_procloop.dll in both dev and frozen builds."""
    return bundled_asset("native", "procloopback", "clipmic_procloop.dll")


def user_data_dir() -> Path:
    """Per-user writable application data directory (%LOCALAPPDATA%\\ClipMic)."""
    base = os.environ.get("LOCALAPPDATA")
    if base:
        root = Path(base) / APP_NAME
    else:
        root = Path.home() / "AppData" / "Local" / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def config_path() -> Path:
    return user_data_dir() / "config.json"


def clips_json_path() -> Path:
    return user_data_dir() / "clips.json"


def effects_json_path() -> Path:
    return user_data_dir() / "effects.json"


def clips_dir() -> Path:
    d = user_data_dir() / "clips"
    d.mkdir(parents=True, exist_ok=True)
    return d


def clip_sources_dir() -> Path:
    d = user_data_dir() / "clips" / "_sources"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = user_data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def temp_dir() -> Path:
    d = user_data_dir() / "temp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def updates_dir() -> Path:
    """Per-user staging directory for verified update installers (%LOCALAPPDATA%\\ClipMic\\updates)."""
    d = user_data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def migrate_legacy_data(legacy_root: Optional[Path] = None) -> bool:
    """One-time safe migration from legacy repo/app-dir files into %LOCALAPPDATA%\\ClipMic.

    Copies (never deletes) legacy config.json, clips.json, and clips/*.wav if the target
    directory has not been migrated yet and does not already have those files.
    """
    dst_root = user_data_dir()
    marker = dst_root / ".migrated_v1"
    if marker.exists():
        return False

    src_root = (legacy_root or exe_dir()).resolve()
    try:
        if src_root == dst_root.resolve():
            marker.write_text("same\n", encoding="utf-8")
            return False
    except OSError:
        return False

    copied_any = False
    for fname in ("config.json", "clips.json"):
        src = src_root / fname
        dst = dst_root / fname
        if src.is_file() and not dst.exists():
            try:
                shutil.copy2(src, dst)
                copied_any = True
            except OSError:
                pass

    src_clips = src_root / "clips"
    if src_clips.is_dir():
        dst_clips = clips_dir()
        for wav in src_clips.glob("*.wav"):
            dst = dst_clips / wav.name
            if not dst.exists():
                try:
                    shutil.copy2(wav, dst)
                    copied_any = True
                except OSError:
                    pass

    try:
        marker.write_text("migrated\n" if copied_any else "clean\n", encoding="utf-8")
    except OSError:
        pass
    return copied_any


def cleanup_temp_files(*, clean_old_installers: bool = False) -> int:
    """Remove orphaned temporary preview/capture WAV files and partial downloads safely."""
    removed = 0
    dirs = [
        temp_dir(),
        Path(tempfile.gettempdir()) / "clipmic_cache",
        Path(tempfile.gettempdir()) / "clipmic_captures",
    ]
    for d in dirs:
        if not d.is_dir():
            continue
        for wav in d.glob("*.wav"):
            try:
                wav.unlink()
                removed += 1
            except OSError:
                pass
    upd_dir = user_data_dir() / "updates"
    if upd_dir.is_dir():
        patterns = ["*.part", "ClipMic-Setup-*.exe"] if clean_old_installers else ["*.part"]
        for pattern in patterns:
            for item in upd_dir.glob(pattern):
                try:
                    item.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


def ensure_stdio() -> None:
    """Guard against sys.stdout/sys.stderr being None in windowed GUI builds."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def setup_logging() -> logging.Logger:
    """Configure a lightweight rotating file logger in %LOCALAPPDATA%\\ClipMic\\logs\\clipmic.log."""
    logger = logging.getLogger("clipmic")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        try:
            log_file = logs_dir() / "clipmic.log"
            handler = RotatingFileHandler(
                str(log_file),
                maxBytes=1_000_000,
                backupCount=2,
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            )
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    return logger


def install_exception_hooks(on_error: Optional[Callable[[str, str], None]] = None) -> None:
    """Install global sys.excepthook and threading.excepthook to log & surface unhandled errors."""
    logger = setup_logging()

    def _handle(exc_type, exc_value, exc_tb, context: str = "main") -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            return
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        logger.error("Unhandled exception (%s):\n%s", context, tb_text)
        if on_error is not None:
            try:
                summary = f"{exc_type.__name__}: {exc_value}"
                on_error(summary, tb_text)
            except Exception:
                pass

    def _sys_hook(exc_type, exc_value, exc_tb) -> None:
        _handle(exc_type, exc_value, exc_tb, "main")

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        tname = args.thread.name if args.thread else "unknown"
        _handle(args.exc_type, args.exc_value, args.exc_traceback, f"thread:{tname}")

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook


def build_startup_command(
    *,
    frozen: Optional[bool] = None,
    executable: Optional[str] = None,
    main_script: Optional[Path] = None,
) -> str:
    """Build a properly quoted Windows Run command that launches ClipMic minimized to tray."""
    use_frozen = is_frozen() if frozen is None else bool(frozen)
    exe = Path(executable or sys.executable).resolve()
    if use_frozen:
        return f'"{exe}" --minimized'

    py_exe = exe
    if py_exe.name.lower() == "python.exe":
        pyw = py_exe.with_name("pythonw.exe")
        if pyw.is_file():
            py_exe = pyw
    script = (main_script or (bundle_dir() / "main.py")).resolve()
    return f'"{py_exe}" "{script}" --minimized'


def is_startup_enabled() -> bool:
    """Check whether ClipMic is registered in HKCU\\...\\Run."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, STARTUP_VALUE_NAME)
            return bool(isinstance(val, str) and val.strip())
    except OSError:
        return False


def set_startup_enabled(enabled: bool) -> None:
    """Enable or disable Start with Windows via HKCU\\...\\Run (current user, no admin required)."""
    if winreg is None:
        raise RuntimeError("Windows registry module is unavailable.")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_PATH, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, build_startup_command())
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass
