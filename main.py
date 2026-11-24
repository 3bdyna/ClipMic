from __future__ import annotations

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import ctypes
import sys
from pathlib import Path
from typing import Optional

import paths

# Guard against None stdout/stderr in windowed (console=False) builds before any imports print.
paths.ensure_stdio()


def _parse_apo_cli(argv: list[str]) -> Optional[tuple[str, Optional[str], Optional[Path]]]:
    """Parse elevated APO CLI flags so dev Python and frozen .exe dispatch identically."""
    action: Optional[str] = None
    guid: Optional[str] = None
    log_file: Optional[Path] = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--apo-install":
            action = "install"
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                guid = argv[i + 1]
                i += 1
        elif arg == "--apo-uninstall":
            action = "uninstall"
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                guid = argv[i + 1]
                i += 1
        elif arg == "--apo-restore-all":
            action = "restore-all"
        elif arg == "--apo-log" and i + 1 < len(argv):
            log_file = Path(argv[i + 1])
            i += 1
        i += 1
    if action is None:
        return None
    return action, guid, log_file


def _dispatch_apo_cli(argv: list[str]) -> Optional[int]:
    parsed = _parse_apo_cli(argv)
    if parsed is None:
        return None
    action, guid, log_file = parsed
    from native.directmic import apo_setup
    return apo_setup.run_cli_action(action, guid, log_file)


_MUTEX_NAME = "Global\\ClipMic_SingleInstance_Mutex"
_SHOW_MSG_NAME = "ClipMic_ShowMainWindow_Msg"


def _show_window_msg_id() -> int:
    return int(ctypes.windll.user32.RegisterWindowMessageW(_SHOW_MSG_NAME))


def _acquire_single_instance_mutex(name: str = _MUTEX_NAME):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    handle = kernel32.CreateMutexW(None, False, name)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        if handle:
            kernel32.CloseHandle(ctypes.c_void_p(int(handle)))
        return None
    return handle


def _release_single_instance_mutex(handle) -> None:
    """Explicitly close the single-instance mutex handle so silent updates never race."""
    if not handle:
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(ctypes.c_void_p(int(handle)))
    except Exception:
        pass


def main(argv: Optional[list[str]] = None) -> int:
    cli_args = sys.argv[1:] if argv is None else argv
    apo_rc = _dispatch_apo_cli(cli_args)
    if apo_rc is not None:
        return apo_rc

    logger = paths.setup_logging()

    from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

    import i18n
    import updater
    from audio_buffer import MultiChannelCapture
    from hotkeys import (
        ClipEntry,
        DEFAULT_FAST_CLIP_SECONDS,
        HotkeyManager,
        clamp_buffer_seconds,
        clamp_fast_clip_seconds,
        load_clips,
        load_config,
        save_clips,
        save_config,
    )
    from i18n import t
    from mic_inject import MicInjector
    from ui import MainWindow, ensure_on_screen, make_app_icon

    start_minimized = "--minimized" in cli_args

    mutex = _acquire_single_instance_mutex()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_app_icon())

    config = load_config()
    i18n.set_lang(str(config.get("language", "en")))
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl() else Qt.LayoutDirection.LeftToRight)

    class _CrashBridge(QObject):
        error_raised = Signal(str)

    crash_bridge = _CrashBridge()
    _showing_crash = {"active": False}

    def _show_crash_dialog(summary: str) -> None:
        if _showing_crash["active"]:
            return
        _showing_crash["active"] = True
        try:
            log_file = paths.logs_dir() / "clipmic.log"
            QMessageBox.critical(
                None,
                t("app.unexpected_error_title"),
                t("app.unexpected_error_body", summary=summary, log_file=log_file),
            )
        finally:
            _showing_crash["active"] = False

    crash_bridge.error_raised.connect(_show_crash_dialog)

    def _on_unhandled(summary: str, _tb: str) -> None:
        if QApplication.instance() is not None:
            crash_bridge.error_raised.emit(summary)
        else:
            try:
                ctypes.windll.user32.MessageBoxW(
                    None,
                    t("app.error_short", summary=summary),
                    t("app.error_title"),
                    0x10,
                )
            except Exception:
                pass

    paths.install_exception_hooks(_on_unhandled)

    show_msg_id = _show_window_msg_id()
    if mutex is None:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "ClipMic")
        if hwnd:
            # Notify the existing Qt event loop to call showNormal() properly.
            # Raw Win32 ShowWindow() on a hidden Qt window causes an unpainted pure white window.
            pid = ctypes.c_uint32(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                try:
                    user32.AllowSetForegroundWindow(pid.value)
                except Exception:
                    pass
            user32.PostMessageW(hwnd, show_msg_id, 0, 0)
        else:
            QMessageBox.information(None, t("app.title"), t("app.already_running"))
        return 0

    paths.migrate_legacy_data()
    paths.cleanup_temp_files(clean_old_installers=True)
    logger.info(
        "ClipMic v%s starting (frozen=%s, data=%s)",
        paths.APP_VERSION,
        paths.is_frozen(),
        paths.user_data_dir(),
    )
    mutex_holder: dict[str, object] = {"handle": mutex}

    def release_mutex() -> None:
        h = mutex_holder["handle"]
        if h:
            mutex_holder["handle"] = None
            _release_single_instance_mutex(h)
    clips = load_clips()
    buf_s = clamp_buffer_seconds(config.get("buffer_seconds", 30))
    fast_s = clamp_fast_clip_seconds(config.get("fast_clip_seconds", DEFAULT_FAST_CLIP_SECONDS))
    ring = MultiChannelCapture(duration_s=float(max(buf_s, fast_s)))
    injector = MicInjector()
    hotkeys = HotkeyManager()

    ring.set_include_discord(True)
    try:
        ring.start(
            config.get("loopback_device"),
            mic_device=config.get("mic_device"),
            capture_mic=True,
            auto_reconnect=True,
        )
    except Exception as exc:
        logger.warning("Initial audio capture start failed (watchdog active): %s", exc)

    window_ref: list[MainWindow] = []

    def apply_hotkeys(cfg: dict, clip_list: list[ClipEntry]) -> list[str]:
        save_config(cfg)
        save_clips(clip_list)
        win = window_ref[0]
        bindings = {
            "__capture__": (
                str(cfg.get("capture_hotkey", "F9")),
                lambda: win.capture_requested.emit(),
            )
        }
        stop_hk = str(cfg.get("stop_hotkey", "") or "").strip()
        if stop_hk:
            bindings["__stop__"] = (stop_hk, lambda: win.stop_audio_requested.emit())
        fast_hk = str(cfg.get("fast_clip_hotkey", "") or "").strip()
        if fast_hk:
            bindings["__fast_clip__"] = (fast_hk, lambda: win.fast_clip_requested.emit())
        for c in clip_list:
            if c.hotkey.strip():
                cid = c.id
                bindings[f"clip_{cid}"] = (
                    c.hotkey.strip(),
                    lambda cid=cid: win.play_clip_requested.emit(cid),
                )
        return hotkeys.set_bindings(bindings)

    update_exit_ref: list = []

    def _on_update_exit(installer_path: Path) -> None:
        if update_exit_ref:
            update_exit_ref[0](installer_path)

    win = MainWindow(ring, injector, config, clips, apply_hotkeys, on_update_exit=_on_update_exit)
    ring.set_status_callback(lambda state, detail: win.loopback_status_changed.emit(state, detail))
    window_ref.append(win)
    apply_hotkeys(config, clips)

    def show_main_window() -> None:
        ensure_on_screen(win)
        win.showNormal()
        win.raise_()
        win.activateWindow()

    class _ShowWindowEventFilter(QAbstractNativeEventFilter):
        def nativeEventFilter(self, event_type, message):
            try:
                if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                    msg_addr = int(message)
                    msg_id = ctypes.c_uint32.from_address(msg_addr + ctypes.sizeof(ctypes.c_void_p)).value
                    if msg_id == show_msg_id:
                        show_main_window()
                        return True, 0
            except Exception:
                pass
            return False, 0

    _show_filter = _ShowWindowEventFilter()
    app.installNativeEventFilter(_show_filter)
    # Ensure native HWND exists even when starting --minimized so second-instance PostMessage finds it
    win.winId()

    # System tray icon
    tray = QSystemTrayIcon(make_app_icon(), app)
    menu = QMenu()
    act_show = QAction(menu)
    act_clip = QAction(menu)
    act_stop = QAction(menu)
    act_restore_all = QAction(menu)
    act_quit = QAction(menu)
    act_show.triggered.connect(show_main_window)
    act_clip.triggered.connect(win.capture_last_30s)
    act_stop.triggered.connect(win.stop_all_audio)
    act_restore_all.triggered.connect(win.restore_all_mics_action)

    def retranslate_tray(secs: Optional[int] = None) -> None:
        cur_s = clamp_buffer_seconds(config.get("buffer_seconds", buf_s)) if secs is None else secs
        tray.setToolTip(t("tray.tooltip", n=cur_s))
        act_show.setText(t("tray.open"))
        act_clip.setText(t("tray.clip_last", n=cur_s))
        act_stop.setText(t("tray.stop_all"))
        act_restore_all.setText(t("tray.restore_all_mics"))
        act_quit.setText(t("tray.quit"))
        menu.setLayoutDirection(Qt.LayoutDirection.RightToLeft if i18n.is_rtl() else Qt.LayoutDirection.LeftToRight)

    retranslate_tray(buf_s)
    win.buffer_duration_changed.connect(retranslate_tray)
    win.language_changed.connect(lambda _lang: retranslate_tray())

    cleaned_up = {"done": False}

    def cleanup_all() -> None:
        if cleaned_up["done"]:
            return
        cleaned_up["done"] = True
        win.hide()
        tray.hide()
        app.processEvents()
        win.shutdown()
        hotkeys.stop()
        injector.close()
        ring.stop()
        paths.cleanup_temp_files()

    def quit_app() -> None:
        cleanup_all()
        release_mutex()
        app.quit()

    def request_update_exit(installer_path: Path) -> None:
        exe_path = Path(installer_path)
        if not exe_path.is_file():
            raise FileNotFoundError(f"Verified update installer not found: {exe_path}")
        logger.info("Handing off to verified installer: %s", exe_path)
        cleanup_all()
        release_mutex()
        try:
            updater.launch_installer(exe_path, silent=True)
        except Exception as exc:
            logger.error("Failed to launch verified installer: %s", exc)
            QMessageBox.warning(
                None,
                t("update.download_failed_title"),
                t("update.download_failed_body", err=str(exc)),
            )
        finally:
            app.quit()

    update_exit_ref.append(request_update_exit)

    act_quit.triggered.connect(quit_app)
    menu.addAction(act_show)
    menu.addAction(act_clip)
    menu.addAction(act_stop)
    menu.addSeparator()
    menu.addAction(act_restore_all)
    menu.addAction(act_quit)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: show_main_window()
        if reason == QSystemTrayIcon.ActivationReason.Trigger
        else None
    )
    tray.show()

    # Minimize to tray or exit cleanly on X button depending on user preference
    def on_close(event) -> None:
        if bool(config.get("minimize_to_tray", True)):
            event.ignore()
            win.hide()
        else:
            event.accept()
            quit_app()

    win.closeEvent = on_close
    if not start_minimized:
        ensure_on_screen(win)
        win.show()

    rc = app.exec()
    cleanup_all()
    release_mutex()
    logger.info("ClipMic exited cleanly (rc=%d)", rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
