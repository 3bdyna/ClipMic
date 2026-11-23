from __future__ import annotations

import ctypes
from dataclasses import dataclass
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import comtypes
from comtypes import COMMETHOD, GUID, IUnknown
import numpy as np
import psutil
import sounddevice as sd

import paths

_log = logging.getLogger("clipmic.audio_buffer")


def _import_pyaudio():
    """Lazily import pyaudiowpatch only when legacy WASAPI loopback fallback is needed."""
    import pyaudiowpatch as pyaudio
    return pyaudio

# Minimal Windows Core Audio COM interfaces to inspect active playback sessions (apps/games)
CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IAudioSessionManager2 = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")


class IAudioSessionControl(IUnknown):
    _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
    _methods_ = [
        COMMETHOD([], ctypes.HRESULT, "GetState", (["out"], ctypes.POINTER(ctypes.c_int), "pRetVal")),
        COMMETHOD([], ctypes.HRESULT, "GetDisplayName", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
        COMMETHOD([], ctypes.HRESULT, "SetDisplayName", (["in"], ctypes.c_wchar_p, "Value"), (["in"], ctypes.POINTER(GUID), "EventContext")),
        COMMETHOD([], ctypes.HRESULT, "GetIconPath", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
        COMMETHOD([], ctypes.HRESULT, "SetIconPath", (["in"], ctypes.c_wchar_p, "Value"), (["in"], ctypes.POINTER(GUID), "EventContext")),
        COMMETHOD([], ctypes.HRESULT, "GetGroupingParam", (["out"], ctypes.POINTER(GUID), "pRetVal")),
        COMMETHOD([], ctypes.HRESULT, "SetGroupingParam", (["in"], ctypes.POINTER(GUID), "Override"), (["in"], ctypes.POINTER(GUID), "EventContext")),
        COMMETHOD([], ctypes.HRESULT, "RegisterAudioSessionNotification", (["in"], ctypes.c_void_p, "NewNotifications")),
        COMMETHOD([], ctypes.HRESULT, "UnregisterAudioSessionNotification", (["in"], ctypes.c_void_p, "NewNotifications")),
    ]


class IAudioSessionControl2(IAudioSessionControl):
    _iid_ = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
    _methods_ = [
        COMMETHOD([], ctypes.HRESULT, "GetSessionIdentifier", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
        COMMETHOD([], ctypes.HRESULT, "GetSessionInstanceIdentifier", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
        COMMETHOD([], ctypes.HRESULT, "GetProcessId", (["out"], ctypes.POINTER(ctypes.c_uint32), "pRetVal")),
        COMMETHOD([], ctypes.HRESULT, "IsSystemSoundsSession"),
        COMMETHOD([], ctypes.HRESULT, "SetDuckingPreference", (["in"], ctypes.c_int, "optOut")),
    ]


class IAudioSessionEnumerator(IUnknown):
    _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
    _methods_ = [
        COMMETHOD([], ctypes.HRESULT, "GetCount", (["out"], ctypes.POINTER(ctypes.c_int), "SessionCount")),
        COMMETHOD([], ctypes.HRESULT, "GetSession", (["in"], ctypes.c_int, "SessionCount"), (["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionControl)), "Session")),
    ]


class IAudioSessionManager(IUnknown):
    _iid_ = GUID("{BFA971F1-4D5E-40BB-935E-967039BFBEE4}")
    _methods_ = [
        COMMETHOD([], ctypes.HRESULT, "GetAudioSessionControl", (["in"], ctypes.POINTER(GUID), "AudioSessionGuid"), (["in"], ctypes.c_uint32, "StreamFlags"), (["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionControl)), "SessionControl")),
        COMMETHOD([], ctypes.HRESULT, "GetSimpleAudioVolume", (["in"], ctypes.POINTER(GUID), "AudioSessionGuid"), (["in"], ctypes.c_uint32, "StreamFlags"), (["out"], ctypes.POINTER(ctypes.c_void_p), "AudioVolume")),
    ]


class IAudioSessionManager2(IAudioSessionManager):
    _iid_ = IID_IAudioSessionManager2
    _methods_ = [
        COMMETHOD([], ctypes.HRESULT, "GetSessionEnumerator", (["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionEnumerator)), "SessionEnum")),
        COMMETHOD([], ctypes.HRESULT, "RegisterSessionNotification", (["in"], ctypes.c_void_p, "SessionNotification")),
        COMMETHOD([], ctypes.HRESULT, "UnregisterSessionNotification", (["in"], ctypes.c_void_p, "SessionNotification")),
        COMMETHOD([], ctypes.HRESULT, "RegisterDuckNotification", (["in"], ctypes.c_wchar_p, "sessionID"), (["in"], ctypes.c_void_p, "duckNotification")),
        COMMETHOD([], ctypes.HRESULT, "UnregisterDuckNotification", (["in"], ctypes.c_void_p, "duckNotification")),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], ctypes.HRESULT, "Activate", (["in"], ctypes.POINTER(GUID), "iid"), (["in"], ctypes.c_uint32, "dwClsCtx"), (["in"], ctypes.c_void_p, "pActivationParams"), (["out"], ctypes.POINTER(ctypes.c_void_p), "ppInterface")),
        COMMETHOD([], ctypes.HRESULT, "OpenPropertyStore", (["in"], ctypes.c_uint32, "stgmAccess"), (["out"], ctypes.POINTER(ctypes.c_void_p), "ppProperties")),
        COMMETHOD([], ctypes.HRESULT, "GetId", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "ppstrId")),
        COMMETHOD([], ctypes.HRESULT, "GetState", (["out"], ctypes.POINTER(ctypes.c_uint32), "pdwState")),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        COMMETHOD([], ctypes.HRESULT, "GetCount", (["out"], ctypes.POINTER(ctypes.c_uint32), "pcDevices")),
        COMMETHOD([], ctypes.HRESULT, "Item", (["in"], ctypes.c_uint32, "nDevice"), (["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "ppDevice")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], ctypes.HRESULT, "EnumAudioEndpoints", (["in"], ctypes.c_int, "dataFlow"), (["in"], ctypes.c_uint32, "dwStateMask"), (["out"], ctypes.POINTER(ctypes.POINTER(IMMDeviceCollection)), "ppDevices")),
        COMMETHOD([], ctypes.HRESULT, "GetDefaultAudioEndpoint", (["in"], ctypes.c_int, "dataFlow"), (["in"], ctypes.c_int, "role"), (["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "ppEndpoint")),
    ]


def get_default_render_device_id() -> Optional[str]:
    """Return the Windows Core Audio endpoint ID of the default render (speaker/headset) device."""
    coinit = False
    try:
        try:
            comtypes.CoInitialize()
            coinit = True
        except OSError:
            pass
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
        dev = enum.GetDefaultAudioEndpoint(0, 0)  # eRender, eConsole
        ep_id = dev.GetId()
        return str(ep_id) if ep_id else None
    except Exception:
        return None
    finally:
        if coinit:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass


_FRIENDLY_APP_NAMES: dict[str, str] = {
    "discord": "Discord",
    "discordptb": "Discord",
    "discordcanary": "Discord",
    "chrome": "Chrome",
    "msedge": "Edge",
    "firefox": "Firefox",
    "brave": "Brave",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
    "arc": "Arc",
    "spotify": "Spotify",
    "vlc": "VLC",
    "obs64": "OBS",
    "teams": "Teams",
    "ms-teams": "Teams",
    "telegram": "Telegram",
    "whatsapp": "WhatsApp",
    "steamwebhelper": "Steam",
    "steam": "Steam",
    "cs2": "CS2",
    "r5apex": "Apex Legends",
    "eldenring": "Elden Ring",
    "rocketleague": "Rocket League",
    "overwatch": "Overwatch",
    "leagueoflegends": "League of Legends",
    "valorant-win64-shipping": "Valorant",
    "fortniteclient-win64-shipping": "Fortnite",
}


def _clean_proc_name(raw: str) -> str:
    name = raw[:-4] if raw.lower().endswith(".exe") else raw
    low = name.lower()
    if low in _FRIENDLY_APP_NAMES:
        return _FRIENDLY_APP_NAMES[low]
    for suffix in ("-win64-shipping", "-wingdk-shipping", "_win64_shipping", "-shipping"):
        if low.endswith(suffix) and len(name) > len(suffix):
            name = name[:-len(suffix)]
            low = name.lower()
            break
    if low in _FRIENDLY_APP_NAMES:
        return _FRIENDLY_APP_NAMES[low]
    if name.islower() and len(name) > 2:
        return name.replace("_", " ").replace("-", " ").title()
    return name


class _ProcMeta:
    __slots__ = ("pid", "create_time", "root_pid", "app_key", "friendly", "exe_path", "has_game_dll", "last_seen_scan")

    def __init__(
        self,
        pid: int,
        create_time: float,
        root_pid: int,
        app_key: str,
        friendly: str,
        exe_path: str,
        last_seen_scan: int,
    ) -> None:
        self.pid = pid
        self.create_time = create_time
        self.root_pid = root_pid
        self.app_key = app_key
        self.friendly = friendly
        self.exe_path = exe_path
        self.has_game_dll: Optional[bool] = None
        self.last_seen_scan = last_seen_scan


class _ProcessMetaCache:
    """Thread-safe cache of psutil process metadata and game-DLL checks keyed by (pid, create_time)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[tuple[int, float], _ProcMeta] = {}
        self._pid_to_proc: dict[int, tuple[psutil.Process, float]] = {}
        self._scan_gen: int = 0
        self.hits: int = 0
        self.misses: int = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._by_key)

    def clear(self) -> None:
        with self._lock:
            self._by_key.clear()
            self._pid_to_proc.clear()

    def pid_alive(self, pid: int) -> bool:
        with self._lock:
            cached = self._pid_to_proc.get(pid)
        if cached is not None:
            proc, ct = cached
            try:
                if proc.is_running():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                pass
            with self._lock:
                if self._pid_to_proc.get(pid) == cached:
                    self._pid_to_proc.pop(pid, None)
                self._by_key.pop((pid, ct), None)
            return False
        return bool(psutil.pid_exists(pid))

    def resolve(self, pid: int) -> Optional[tuple[int, str, str, str]]:
        """Return cached (root_pid, app_key, friendly_name, exe_path) for pid."""
        with self._lock:
            cached = self._pid_to_proc.get(pid)
        if cached is not None:
            proc, ct = cached
            try:
                alive = proc.is_running()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                alive = False
            if alive:
                with self._lock:
                    meta = self._by_key.get((pid, ct))
                    if meta is not None:
                        meta.last_seen_scan = self._scan_gen
                        self.hits += 1
                        return meta.root_pid, meta.app_key, meta.friendly, meta.exe_path
            else:
                with self._lock:
                    if self._pid_to_proc.get(pid) == cached:
                        self._pid_to_proc.pop(pid, None)
                    self._by_key.pop((pid, ct), None)

        try:
            proc = psutil.Process(pid)
            ct = float(proc.create_time())
            raw_name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return None
        if not raw_name:
            return None

        target_exe_low = raw_name.lower()
        root_proc = proc
        # Walk up ancestor chain while parent shares the exact same executable (Chrome/Discord/Electron trees)
        for _ in range(12):
            try:
                parent = root_proc.parent()
                if parent is None or parent.pid <= 0 or parent.pid == root_proc.pid:
                    break
                if parent.name().lower() == target_exe_low:
                    root_proc = parent
                else:
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                break

        exe_path = ""
        try:
            exe_path = root_proc.exe() or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            try:
                exe_path = proc.exe() or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                exe_path = ""

        friendly = _clean_proc_name(raw_name)
        app_key = friendly.lower()
        root_pid = int(root_proc.pid)

        with self._lock:
            old = self._pid_to_proc.get(pid)
            if old is not None and old[1] != ct:
                self._by_key.pop((pid, old[1]), None)
            meta = _ProcMeta(pid, ct, root_pid, app_key, friendly, exe_path, self._scan_gen)
            self._by_key[(pid, ct)] = meta
            self._pid_to_proc[pid] = (proc, ct)
            self.misses += 1

        return root_pid, app_key, friendly, exe_path

    def has_game_gpu_module(self, pid: int) -> bool:
        """Cached per-(pid, create_time) check for 3D game/SDK DLLs."""
        with self._lock:
            cached = self._pid_to_proc.get(pid)
        if cached is not None:
            proc, ct = cached
            try:
                alive = proc.is_running()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                alive = False
            if alive:
                with self._lock:
                    meta = self._by_key.get((pid, ct))
                    if meta is not None and meta.has_game_dll is not None:
                        meta.last_seen_scan = self._scan_gen
                        self.hits += 1
                        return meta.has_game_dll
                result = _has_game_gpu_module(pid)
                with self._lock:
                    meta = self._by_key.get((pid, ct))
                    if meta is not None:
                        meta.has_game_dll = result
                return result
            else:
                with self._lock:
                    if self._pid_to_proc.get(pid) == cached:
                        self._pid_to_proc.pop(pid, None)
                    self._by_key.pop((pid, ct), None)

        # Ensure metadata entry exists if process is accessible, then cache module check
        self.resolve(pid)
        with self._lock:
            cached2 = self._pid_to_proc.get(pid)
        result = _has_game_gpu_module(pid)
        if cached2 is not None:
            with self._lock:
                meta = self._by_key.get((pid, cached2[1]))
                if meta is not None:
                    meta.has_game_dll = result
        return result

    def prune(self, active_pids: Optional[set[int]] = None, max_stale_scans: int = 30) -> int:
        """Evict dead processes and entries unseen for >max_stale_scans cycles."""
        with self._lock:
            self._scan_gen += 1
            cur_gen = self._scan_gen
            snapshot_items = list(self._pid_to_proc.items())

        dead_keys: list[tuple[int, float]] = []
        for pid, (proc, ct) in snapshot_items:
            if active_pids is not None and pid in active_pids:
                continue
            try:
                alive = proc.is_running()
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                alive = False
            if not alive:
                dead_keys.append((pid, ct))
                continue
            with self._lock:
                meta = self._by_key.get((pid, ct))
                if meta is None or (cur_gen - meta.last_seen_scan) > max_stale_scans:
                    dead_keys.append((pid, ct))

        removed = 0
        with self._lock:
            for pid, ct in dead_keys:
                cur = self._pid_to_proc.get(pid)
                if cur is not None and cur[1] == ct:
                    self._pid_to_proc.pop(pid, None)
                if self._by_key.pop((pid, ct), None) is not None:
                    removed += 1
            # Clean any orphaned keys in _by_key
            live_keys = {(pid, ct) for pid, (_p, ct) in self._pid_to_proc.items()}
            for k in list(self._by_key.keys()):
                if k not in live_keys:
                    self._by_key.pop(k, None)
                    removed += 1
        return removed


_DEFAULT_PROC_CACHE = _ProcessMetaCache()


def _resolve_root_process(
    pid: int,
    proc_cache: Optional[_ProcessMetaCache] = None,
) -> Optional[tuple[int, str, str, str]]:
    """Return (root_pid, app_key, friendly_name, exe_path) grouping same-exe child trees."""
    cache = proc_cache if proc_cache is not None else _DEFAULT_PROC_CACHE
    return cache.resolve(pid)


def enumerate_active_audio_sessions(proc_cache: Optional[_ProcessMetaCache] = None) -> list[dict]:
    """Scan ALL active Windows render endpoints (TV, HDMI, speakers, headsets) for active app sessions.

    Returns grouped app dicts:
      {"key": str, "name": str, "root_pids": list[int], "pids": list[int], "exe_path": str}
    ClipMic's own process is always excluded.
    """
    cache = proc_cache if proc_cache is not None else _DEFAULT_PROC_CACHE
    my_pid = os.getpid()
    grouped: dict[str, dict] = {}
    seen_pids: set[int] = set()
    coinit = False
    try:
        try:
            comtypes.CoInitialize()
            coinit = True
        except OSError:
            pass
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
        # eRender = 0, DEVICE_STATE_ACTIVE = 0x00000001
        devices = enum.EnumAudioEndpoints(0, 1)
        dev_count = int(devices.GetCount())
        for d_idx in range(dev_count):
            try:
                dev = devices.Item(d_idx)
                mgr_ptr = dev.Activate(ctypes.byref(IID_IAudioSessionManager2), comtypes.CLSCTX_ALL, None)
                mgr = ctypes.cast(mgr_ptr, ctypes.POINTER(IAudioSessionManager2))
                sess_enum = mgr.GetSessionEnumerator()
                s_count = int(sess_enum.GetCount())
            except Exception:
                continue

            for s_idx in range(s_count):
                try:
                    ctrl = sess_enum.GetSession(s_idx)
                    if int(ctrl.GetState()) != 1:  # AudioSessionStateActive
                        continue
                    ctrl2 = ctrl.QueryInterface(IAudioSessionControl2)
                    pid = int(ctrl2.GetProcessId())
                    if pid <= 0 or pid == my_pid:
                        continue
                    seen_pids.add(pid)
                    resolved = _resolve_root_process(pid, proc_cache=cache)
                    if resolved is None:
                        continue
                    root_pid, app_key, friendly, exe_path = resolved
                    if root_pid == my_pid:
                        continue
                    seen_pids.add(root_pid)
                    entry = grouped.get(app_key)
                    if entry is None:
                        grouped[app_key] = {
                            "key": app_key,
                            "name": friendly,
                            "root_pids": [root_pid],
                            "pids": [pid],
                            "exe_path": exe_path,
                        }
                    else:
                        if root_pid not in entry["root_pids"]:
                            entry["root_pids"].append(root_pid)
                        if pid not in entry["pids"]:
                            entry["pids"].append(pid)
                        if not entry["exe_path"] and exe_path:
                            entry["exe_path"] = exe_path
                except Exception:
                    continue
    except Exception:
        return []
    finally:
        if coinit:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass
    cache.prune(seen_pids)
    return list(grouped.values())


def get_active_audio_sessions() -> list[str]:
    """Return deduplicated names of apps/games currently playing audio on any active render device."""
    return [s["name"] for s in enumerate_active_audio_sessions()]


def list_loopback_devices() -> list[str]:
    """List available Windows WASAPI loopback playback device names."""
    pyaudio = _import_pyaudio()
    pa = pyaudio.PyAudio()
    try:
        return [str(d["name"]) for d in pa.get_loopback_device_info_generator()]
    except Exception:
        return []
    finally:
        try:
            pa.terminate()
        except Exception:
            pass


def _downmix_to_stereo(
    frames: np.ndarray,
    scratch: Optional[np.ndarray] = None,
    fscratch: Optional[np.ndarray] = None,
) -> np.ndarray:
    """Downmix N-channel int16 frames (N > 2, e.g. 5.1/7.1) into stereo int16 with zero steady-state allocs."""
    n, ch = frames.shape
    if scratch is not None and scratch.shape[0] >= n and scratch.shape[1] == 2:
        out = scratch[:n]
    else:
        out = np.empty((n, 2), dtype=np.int16)

    if fscratch is not None and fscratch.shape[0] >= n and fscratch.shape[1] >= 3:
        left = fscratch[:n, 0]
        right = fscratch[:n, 1]
        tmp = fscratch[:n, 2]
    else:
        left = np.empty(n, dtype=np.float32)
        right = np.empty(n, dtype=np.float32)
        tmp = np.empty(n, dtype=np.float32)

    if ch >= 6:
        # Standard Windows surround order: FL(0), FR(1), FC(2), LFE(3), BL(4), BR(5), [SL(6), SR(7)]
        np.copyto(left, frames[:, 0], casting="unsafe")
        np.copyto(right, frames[:, 1], casting="unsafe")
        np.multiply(frames[:, 2], np.float32(0.707), out=tmp, casting="unsafe")
        np.add(left, tmp, out=left)
        np.add(right, tmp, out=right)
        np.multiply(frames[:, 4], np.float32(0.5), out=tmp, casting="unsafe")
        np.add(left, tmp, out=left)
        np.multiply(frames[:, 5], np.float32(0.5), out=tmp, casting="unsafe")
        np.add(right, tmp, out=right)
        if ch >= 8:
            np.multiply(frames[:, 6], np.float32(0.5), out=tmp, casting="unsafe")
            np.add(left, tmp, out=left)
            np.multiply(frames[:, 7], np.float32(0.5), out=tmp, casting="unsafe")
            np.add(right, tmp, out=right)
    else:
        # Quad / 3ch / 5ch fallback: average even channels -> L, odd channels -> R
        left.fill(0.0)
        right.fill(0.0)
        l_cnt = 0
        r_cnt = 0
        for c in range(ch):
            if c % 2 == 0:
                np.add(left, frames[:, c], out=left, casting="unsafe")
                l_cnt += 1
            else:
                np.add(right, frames[:, c], out=right, casting="unsafe")
                r_cnt += 1
        if l_cnt > 1:
            left *= 1.0 / l_cnt
        if r_cnt > 1:
            right *= 1.0 / r_cnt

    np.clip(left, -32768.0, 32767.0, out=left)
    np.clip(right, -32768.0, 32767.0, out=right)
    np.copyto(out[:, 0], left, casting="unsafe")
    np.copyto(out[:, 1], right, casting="unsafe")
    return out


class LoopbackRingBuffer:
    """Continuously captures the last N seconds of Windows WASAPI loopback audio in RAM.

    Handles mono, stereo, and multi-channel (5.1/7.1) endpoints, plus automatic recovery
    on USB/Bluetooth hot-swap, default playback device changes, and sleep/wake resume.
    """

    def __init__(
        self,
        duration_s: float = 30.0,
        chunk_frames: int = 1024,
        on_status_change: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.duration_s = float(duration_s)
        self.chunk_frames = int(chunk_frames)
        self._on_status_change = on_status_change

        self._lock = threading.Lock()
        self._ctrl_lock = threading.Lock()
        self._pa: Optional[object] = None
        self._pa_continue: int = 0
        self._stream = None
        self._running = False
        self._stop_event = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None

        self.state: str = "stopped"  # "stopped" | "active" | "reconnecting" | "error"
        self.last_error: str = ""
        self.sample_rate: int = 48000
        self.channels: int = 2
        self._stream_channels: int = 2
        self.device_name: str = ""
        self.target_device: Optional[str] = None
        self._active_render_id: Optional[str] = None
        self.include_discord: bool = True
        self.active_apps: list[str] = []
        self._discord_only: bool = False

        self._capacity: int = 0
        self._buf: np.ndarray = np.zeros((0, 2), dtype=np.int16)
        scratch_len = max(4096, self.chunk_frames * 4)
        self._downmix_scratch: np.ndarray = np.empty((scratch_len, 2), dtype=np.int16)
        self._downmix_fscratch: np.ndarray = np.empty((scratch_len, 3), dtype=np.float32)
        self._write_pos: int = 0
        self._filled: int = 0
        self._last_callback_ts: float = 0.0
        self._last_signal_ts: float = 0.0

    def set_status_callback(self, cb: Optional[Callable[[str, str], None]]) -> None:
        self._on_status_change = cb

    def _set_state(self, state: str, detail: str = "") -> None:
        changed = (state != self.state) or (detail != self.last_error)
        self.state = state
        self.last_error = detail
        if changed and self._on_status_change is not None:
            try:
                self._on_status_change(state, detail)
            except Exception:
                pass

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_reconnecting(self) -> bool:
        return self.state == "reconnecting"

    def set_include_discord(self, enabled: bool) -> None:
        self.include_discord = bool(enabled)

    def refresh_active_sessions(self) -> list[str]:
        """Query active playback apps (called from UI timer, never on audio thread)."""
        apps = get_active_audio_sessions()
        self.active_apps = apps
        self._discord_only = bool(apps) and all(a.lower() == "discord" for a in apps)
        return apps

    def is_receiving_audio(self) -> bool:
        return self._running and (time.monotonic() - self._last_signal_ts) < 1.8

    def _find_loopback_device(self, pa) -> dict:
        pyaudio = _import_pyaudio()
        if self.target_device:
            for loopback in pa.get_loopback_device_info_generator():
                if str(loopback["name"]) == self.target_device:
                    return loopback

        if hasattr(pa, "get_default_wasapi_loopback"):
            try:
                dev = pa.get_default_wasapi_loopback()
                if dev:
                    return dev
            except Exception:
                pass

        wasapi_info = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_out = pa.get_device_info_by_index(int(wasapi_info["defaultOutputDevice"]))
        if default_out.get("isLoopbackDevice"):
            return default_out

        for loopback in pa.get_loopback_device_info_generator():
            if default_out["name"] in loopback["name"]:
                return loopback

        raise RuntimeError("No Windows WASAPI loopback playback device found.")

    def _close_stream_only(self) -> None:
        """Tear down PortAudio stream and instance without stopping the watchdog."""
        self._running = False
        stream = self._stream
        pa = self._pa
        self._stream = None
        self._pa = None

        if stream is not None:
            try:
                if stream.is_active():
                    stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        if pa is not None:
            try:
                pa.terminate()
            except Exception:
                pass

    def _open_stream(self) -> None:
        pyaudio = _import_pyaudio()
        self._pa_continue = int(pyaudio.paContinue)
        pa = pyaudio.PyAudio()
        try:
            dev = self._find_loopback_device(pa)
            rate = int(round(float(dev.get("defaultSampleRate") or 48000)))
            if rate <= 0:
                rate = 48000
            native_ch = max(1, int(dev.get("maxInputChannels") or 2))
            buf_ch = 1 if native_ch == 1 else 2
            capacity = max(1, int(round(rate * self.duration_s)))

            # Try native channel count first (required for 5.1/7.1 WASAPI loopback), then fallbacks
            chan_candidates: list[int] = [native_ch]
            for fallback_ch in (2, 1):
                if fallback_ch not in chan_candidates:
                    chan_candidates.append(fallback_ch)

            stream = None
            opened_ch = native_ch
            last_exc: Optional[Exception] = None
            for try_ch in chan_candidates:
                try:
                    stream = pa.open(
                        format=pyaudio.paInt16,
                        channels=try_ch,
                        rate=rate,
                        frames_per_buffer=self.chunk_frames,
                        input=True,
                        input_device_index=int(dev["index"]),
                        stream_callback=self._on_audio,
                        start=False,
                    )
                    opened_ch = try_ch
                    break
                except Exception as exc:
                    last_exc = exc

            if stream is None:
                raise last_exc or RuntimeError("Failed to open WASAPI loopback stream.")

            buf_ch = 1 if opened_ch == 1 else 2
            with self._lock:
                self._pa = pa
                self.sample_rate = rate
                self.channels = buf_ch
                self._stream_channels = opened_ch
                self.device_name = str(dev["name"])
                # Reallocate only if shape changed (pre-allocated fixed memory during steady state)
                if self._buf.shape != (capacity, buf_ch):
                    self._buf = np.zeros((capacity, buf_ch), dtype=np.int16)
                else:
                    self._buf.fill(0)
                self._capacity = capacity
                self._write_pos = 0
                self._filled = 0
                self._last_callback_ts = time.monotonic()

            self._stream = stream
            self._active_render_id = get_default_render_device_id() if self.target_device is None else None
            self._running = True
            self._stream.start_stream()
            self._set_state("active", "")
            _log.info(
                "WASAPI loopback started: %s (%d Hz, %dch -> %dch buffer)",
                self.device_name,
                rate,
                opened_ch,
                buf_ch,
            )
        except Exception:
            try:
                pa.terminate()
            except Exception:
                pass
            self._pa = None
            self._stream = None
            self._running = False
            raise

    def _ensure_watchdog(self) -> None:
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._stop_event.clear()
        t = threading.Thread(target=self._watchdog_loop, name="ClipMic-AudioWatchdog", daemon=True)
        self._watchdog_thread = t
        t.start()

    def _needs_reconnect(self, last_wall: float, last_mono: float) -> tuple[bool, str]:
        now_wall = time.time()
        now_mono = time.monotonic()
        # Sleep/wake resume detection (wall clock jumped significantly relative to monotonic or vice versa)
        drift = abs((now_wall - last_wall) - (now_mono - last_mono))
        if drift > 4.0:
            return True, "system wake from sleep"

        if not self._running or self._stream is None:
            return True, "stream inactive"

        try:
            if not self._stream.is_active():
                return True, "device disconnected or stream stopped"
        except Exception:
            return True, "stream handle error"

        if self.target_device is None:
            cur_id = get_default_render_device_id()
            if cur_id and self._active_render_id and cur_id != self._active_render_id:
                return True, "default playback device changed"

        return False, ""

    def _watchdog_loop(self) -> None:
        backoff = 1.0
        last_wall = time.time()
        last_mono = time.monotonic()
        while not self._stop_event.is_set():
            wait_s = 1.5 if self.state == "active" else backoff
            if self._stop_event.wait(wait_s):
                break

            with self._ctrl_lock:
                if self._stop_event.is_set():
                    break
                need, reason = self._needs_reconnect(last_wall, last_mono)
                last_wall = time.time()
                last_mono = time.monotonic()
                if not need:
                    backoff = 1.0
                    continue

                _log.warning("WASAPI loopback reconnect triggered (%s)", reason)
                self._set_state("reconnecting", reason)
                self._close_stream_only()
                try:
                    self._open_stream()
                    backoff = 1.0
                except Exception as exc:
                    _log.warning("WASAPI loopback reconnect failed (retry in %.1fs): %s", backoff, exc)
                    self._set_state("reconnecting", str(exc))
                    backoff = min(10.0, backoff * 2.0)

    def start(self, target_device: Optional[str] = None, *, auto_reconnect: bool = True) -> None:
        with self._ctrl_lock:
            if target_device is not None:
                self.target_device = target_device or None
            if self._running:
                if auto_reconnect:
                    self._ensure_watchdog()
                return
            self._stop_event.clear()
            try:
                self._open_stream()
            except Exception as exc:
                self._set_state("reconnecting" if auto_reconnect else "error", str(exc))
                if auto_reconnect:
                    self._ensure_watchdog()
                raise
            if auto_reconnect:
                self._ensure_watchdog()

    def restart(self, target_device: Optional[str] = None) -> None:
        with self._ctrl_lock:
            self._close_stream_only()
            self.target_device = target_device or None
            try:
                self._open_stream()
            except Exception as exc:
                self._set_state("reconnecting", str(exc))
                self._ensure_watchdog()
                raise
            self._ensure_watchdog()

    def stop(self) -> None:
        self._stop_event.set()
        wd = self._watchdog_thread
        if wd is not None and wd is not threading.current_thread() and wd.is_alive():
            wd.join(timeout=0.4)
        self._watchdog_thread = None
        with self._ctrl_lock:
            self._close_stream_only()
            self._set_state("stopped", "")

    def _on_audio(self, in_data, frame_count: int, time_info, status_flags):
        pa_continue = self._pa_continue
        if not self._running or not in_data:
            return (None, pa_continue)

        raw = np.frombuffer(in_data, dtype=np.int16)
        in_ch = self._stream_channels
        usable = (len(raw) // in_ch) * in_ch
        if usable == 0:
            return (None, pa_continue)
        frames = raw[:usable].reshape(-1, in_ch)
        if in_ch > 2:
            frames = _downmix_to_stereo(frames, self._downmix_scratch, self._downmix_fscratch)

        # If Discord listening is toggled OFF and Discord is the only active app, skip buffering
        if not self.include_discord and self._discord_only:
            return (None, pa_continue)

        # Peak check without allocating an abs copy of the full array
        if int(frames.max()) > 40 or int(frames.min()) < -40:
            self._last_signal_ts = time.monotonic()

        n = len(frames)
        with self._lock:
            cap = self._capacity
            if cap <= 0 or frames.shape[1] != self._buf.shape[1]:
                return (None, pa_continue)
            if n >= cap:
                self._buf[:] = frames[-cap:]
                self._write_pos = 0
                self._filled = cap
            else:
                end = self._write_pos + n
                if end <= cap:
                    self._buf[self._write_pos:end] = frames
                else:
                    first = cap - self._write_pos
                    self._buf[self._write_pos:] = frames[:first]
                    self._buf[:n - first] = frames[first:]
                self._write_pos = end % cap
                self._filled = min(cap, self._filled + n)
            self._last_callback_ts = time.monotonic()

        return (None, pa_continue)

    def set_duration(self, duration_s: float) -> None:
        """Resize the loopback ring buffer in place without restarting the audio stream."""
        dur = max(1.0, min(180.0, float(duration_s)))
        with self._lock:
            self.duration_s = dur
            rate = self.sample_rate if self.sample_rate > 0 else CAPTURE_SAMPLE_RATE
            ch = self.channels if self.channels > 0 else 2
            new_cap = max(1, int(round(rate * dur)))
            if self._capacity == new_cap and self._buf.shape == (new_cap, ch):
                return
            if self._buf.shape[0] > 0 and self._capacity > 0:
                if self._filled == 0:
                    recent = np.zeros((0, ch), dtype=np.int16)
                elif self._filled < self._capacity:
                    recent = self._buf[:self._filled]
                else:
                    pos = self._write_pos
                    recent = np.concatenate((self._buf[pos:], self._buf[:pos]), axis=0)
                keep = min(len(recent), new_cap)
                new_buf = np.zeros((new_cap, ch), dtype=np.int16)
                if keep > 0:
                    new_buf[:keep] = recent[-keep:]
                self._buf = new_buf
                self._capacity = new_cap
                self._filled = keep
                self._write_pos = keep if keep < new_cap else 0
            else:
                self._capacity = new_cap

    def snapshot(self) -> tuple[np.ndarray, int]:
        """Return a chronological copy of the buffered audio (int16 frames, sample_rate)."""
        with self._lock:
            if self._filled == 0:
                return np.zeros((0, self.channels), dtype=np.int16), self.sample_rate
            if self._filled < self._capacity:
                data = self._buf[:self._filled].copy()
            else:
                pos = self._write_pos
                data = np.concatenate((self._buf[pos:], self._buf[:pos]), axis=0)
            return data, self.sample_rate

    def buffered_seconds(self) -> float:
        with self._lock:
            if self.sample_rate <= 0:
                return 0.0
            return self._filled / float(self.sample_rate)


# ---------------------------------------------------------------------------
# Multi-Channel Per-App Process Loopback + Microphone 30s Capture Engine
# ---------------------------------------------------------------------------

CAPTURE_SAMPLE_RATE = 48000
CAPTURE_CHANNELS = 2
_IDLE_RELEASE_S = 60.0
_SCAN_INTERVAL_S = 2.2

_NON_GAME_EXES: set[str] = {
    "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "arc", "waterfox",
    "discord", "discordptb", "discordcanary", "teams", "ms-teams", "slack", "zoom",
    "telegram", "whatsapp", "skype", "ts3client_win64", "mumble",
    "spotify", "vlc", "wmplayer", "foobar2000", "itunes", "musicbee", "audacity",
    "obs64", "obs32", "streamlabs obs", "voicemeeter", "voicemeeterpro",
    "explorer", "shellexperiencehost", "applicationframehost", "searchhost",
    "python", "pythonw", "powershell", "pwsh", "cmd", "windowsterminal",
    "code", "devenv", "steamwebhelper", "steam", "epicgameslauncher", "riotclientservices",
}

_GAME_PATH_MARKERS: tuple[str, ...] = (
    "\\steamapps\\",
    "\\epic games\\",
    "\\riot games\\",
    "\\xboxgames\\",
    "\\ea games\\",
    "\\origin games\\",
    "\\ubisoft game launcher\\games\\",
    "\\gog galaxy\\games\\",
    "\\battle.net\\",
    "\\rockstar games\\",
    "\\blizzard\\",
    "\\games\\",
)

_GAME_DLL_MARKERS: tuple[str, ...] = (
    "d3d12.dll",
    "vulkan-1.dll",
    "steam_api64.dll",
    "steam_api.dll",
    "xinput1_4.dll",
    "xinput1_3.dll",
    "eossdk-win64-shipping.dll",
)

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)


def _query_qpc() -> int:
    val = ctypes.c_int64(0)
    _kernel32.QueryPerformanceCounter(ctypes.byref(val))
    return int(val.value)


def _query_qpc_freq() -> int:
    val = ctypes.c_int64(0)
    _kernel32.QueryPerformanceFrequency(ctypes.byref(val))
    return int(val.value) if val.value > 0 else 10_000_000


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_uint32),
    ]


def _get_foreground_fullscreen_pid() -> Optional[int]:
    """Return the PID owning the foreground window if it fills its monitor (fullscreen/borderless)."""
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_uint32(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value <= 0:
            return None
        wr = _RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(wr)):
            return None
        mon = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not mon:
            return None
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not _user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
            return None
        mr = mi.rcMonitor
        win_w = wr.right - wr.left
        win_h = wr.bottom - wr.top
        mon_w = mr.right - mr.left
        mon_h = mr.bottom - mr.top
        if mon_w <= 0 or mon_h <= 0:
            return None
        # Covers fullscreen and borderless fullscreen (within 8px tolerance)
        if win_w >= mon_w - 8 and win_h >= mon_h - 8:
            return int(pid.value)
    except Exception:
        pass
    return None


def _has_game_gpu_module(pid: int) -> bool:
    """Best-effort check for 3D game/SDK DLLs in a process (safe against anti-cheat AccessDenied)."""
    try:
        proc = psutil.Process(pid)
        for mm in proc.memory_maps():
            p = (mm.path or "").lower()
            if p.endswith(_GAME_DLL_MARKERS):
                return True
    except Exception:
        return False
    return False


def detect_channel_kind(
    exe_name: str,
    exe_path: str = "",
    pids: Optional[list[int]] = None,
    *,
    foreground_fs_pid: Optional[int] = None,
    check_modules: bool = True,
    proc_cache: Optional[_ProcessMetaCache] = None,
) -> str:
    """Classify a process channel as 'game' or 'app'."""
    stem = exe_name[:-4].lower() if exe_name.lower().endswith(".exe") else exe_name.lower()
    if not stem and exe_path:
        stem = Path(exe_path).stem.lower()
    if stem in _NON_GAME_EXES:
        return "app"

    low_path = (exe_path or "").lower()
    if any(marker in low_path for marker in _GAME_PATH_MARKERS):
        return "game"
    if stem.endswith(("-win64-shipping", "-wingdk-shipping", "_win64_shipping")):
        return "game"

    pid_set = set(pids or [])
    if foreground_fs_pid is not None and foreground_fs_pid in pid_set:
        return "game"

    if check_modules and pids:
        cache = proc_cache if proc_cache is not None else _DEFAULT_PROC_CACHE
        for p in pids[:2]:
            if cache.has_game_gpu_module(p):
                return "game"

    return "app"


class _ProcLoopLib:
    """Thin ctypes wrapper over native/procloopback/clipmic_procloop.dll."""

    def __init__(self, dll_path: Optional[Path] = None) -> None:
        self._path = dll_path or paths.procloop_dll_path()
        self._dll = None
        self._supported: Optional[bool] = None
        self.last_probe_hr: int = 0

    def _load(self):
        if self._dll is not None:
            return self._dll
        if not self._path.is_file():
            return None
        try:
            dll = ctypes.CDLL(str(self._path))
            dll.procloop_is_supported.argtypes = [ctypes.POINTER(ctypes.c_int32)]
            dll.procloop_is_supported.restype = ctypes.c_int
            dll.procloop_create.argtypes = [ctypes.c_uint32, ctypes.c_double, ctypes.POINTER(ctypes.c_int32)]
            dll.procloop_create.restype = ctypes.c_void_p
            dll.procloop_snapshot.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_uint32,
                ctypes.POINTER(ctypes.c_uint32),
                ctypes.POINTER(ctypes.c_int64),
                ctypes.POINTER(ctypes.c_int64),
            ]
            dll.procloop_snapshot.restype = ctypes.c_uint32
            dll.procloop_ms_since_signal.argtypes = [ctypes.c_void_p]
            dll.procloop_ms_since_signal.restype = ctypes.c_int64
            dll.procloop_is_running.argtypes = [ctypes.c_void_p]
            dll.procloop_is_running.restype = ctypes.c_int
            dll.procloop_last_error.argtypes = [ctypes.c_void_p]
            dll.procloop_last_error.restype = ctypes.c_int32
            if hasattr(dll, "procloop_resize"):
                dll.procloop_resize.argtypes = [ctypes.c_void_p, ctypes.c_double]
                dll.procloop_resize.restype = ctypes.c_int
            if hasattr(dll, "procloop_capacity_frames"):
                dll.procloop_capacity_frames.argtypes = [ctypes.c_void_p]
                dll.procloop_capacity_frames.restype = ctypes.c_uint32
            dll.procloop_destroy.argtypes = [ctypes.c_void_p]
            dll.procloop_destroy.restype = None
            self._dll = dll
            return dll
        except Exception as exc:
            _log.warning("Could not load %s: %s", self._path, exc)
            return None

    def is_supported(self) -> bool:
        if self._supported is not None:
            return self._supported
        dll = self._load()
        if dll is None:
            self._supported = False
            return False
        hr = ctypes.c_int32(0)
        try:
            ok = bool(dll.procloop_is_supported(ctypes.byref(hr)))
            self.last_probe_hr = int(hr.value)
            self._supported = ok
        except Exception as exc:
            _log.warning("procloop_is_supported failed: %s", exc)
            self._supported = False
        return bool(self._supported)

    def create(self, pid: int, duration_s: float) -> tuple[Optional[int], int]:
        dll = self._load()
        if dll is None:
            return None, -1
        hr = ctypes.c_int32(0)
        handle = dll.procloop_create(ctypes.c_uint32(pid), ctypes.c_double(duration_s), ctypes.byref(hr))
        return (int(handle) if handle else None), int(hr.value)

    def snapshot(self, handle: int, max_frames: int) -> tuple[np.ndarray, int, int, int]:
        dll = self._load()
        if dll is None or not handle:
            return np.zeros((0, CAPTURE_CHANNELS), dtype=np.float32), CAPTURE_SAMPLE_RATE, _query_qpc(), _query_qpc_freq()
        buf = np.empty((max_frames, CAPTURE_CHANNELS), dtype=np.float32)
        sr = ctypes.c_uint32(CAPTURE_SAMPLE_RATE)
        end_qpc = ctypes.c_int64(0)
        qpc_freq = ctypes.c_int64(_query_qpc_freq())
        n = int(
            dll.procloop_snapshot(
                ctypes.c_void_p(handle),
                buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                ctypes.c_uint32(max_frames),
                ctypes.byref(sr),
                ctypes.byref(end_qpc),
                ctypes.byref(qpc_freq),
            )
        )
        take = min(n, max_frames)
        return buf[:take].copy(), int(sr.value) or CAPTURE_SAMPLE_RATE, int(end_qpc.value), int(qpc_freq.value) or _query_qpc_freq()

    def ms_since_signal(self, handle: int) -> int:
        dll = self._load()
        if dll is None or not handle:
            return -1
        return int(dll.procloop_ms_since_signal(ctypes.c_void_p(handle)))

    def is_running(self, handle: int) -> bool:
        dll = self._load()
        if dll is None or not handle:
            return False
        return bool(dll.procloop_is_running(ctypes.c_void_p(handle)))

    def resize(self, handle: int, duration_s: float) -> bool:
        dll = self._load()
        if dll is None or not handle or not hasattr(dll, "procloop_resize"):
            return False
        return bool(dll.procloop_resize(ctypes.c_void_p(handle), ctypes.c_double(duration_s)))

    def capacity_frames(self, handle: int) -> int:
        dll = self._load()
        if dll is None or not handle or not hasattr(dll, "procloop_capacity_frames"):
            return 0
        return int(dll.procloop_capacity_frames(ctypes.c_void_p(handle)))

    def destroy(self, handle: int) -> None:
        dll = self._load()
        if dll is not None and handle:
            try:
                dll.procloop_destroy(ctypes.c_void_p(handle))
            except Exception:
                pass


class MicRingBuffer:
    """Lightweight parallel shared-mode WASAPI microphone ring buffer (48 kHz stereo int16 internal, float32 snapshot).

    Never opens exclusive mode so Discord, games, and ClipMic's Direct Mic APO stay unblocked.
    Fails gracefully if no microphone is available or Windows mic permission is denied.
    """

    def __init__(self, duration_s: float = 30.0) -> None:
        self.duration_s = float(duration_s)
        self.sample_rate = CAPTURE_SAMPLE_RATE
        self.channels = CAPTURE_CHANNELS
        self._capacity = max(CAPTURE_SAMPLE_RATE, int(round(self.duration_s * CAPTURE_SAMPLE_RATE)))
        self._lock = threading.Lock()
        self._stream: Optional[sd.InputStream] = None
        self._running = False
        self._buf: Optional[np.ndarray] = None
        self._f32_scratch: np.ndarray = np.empty((2048, CAPTURE_CHANNELS), dtype=np.float32)
        self._i16_scratch: np.ndarray = np.empty((2048, CAPTURE_CHANNELS), dtype=np.int16)
        self._write_pos = 0
        self._filled = 0
        self._last_write_qpc = 0
        self._last_signal_qpc = 0
        self._src_rate = CAPTURE_SAMPLE_RATE
        self.device_name: Optional[str] = None
        self.last_error: str = ""

    @property
    def is_running(self) -> bool:
        return self._running

    def _resolve_input_device(self, target_name: Optional[str]) -> tuple[Optional[int], int, int]:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
        wasapi_idx = next((i for i, h in enumerate(hostapis) if "WASAPI" in h.get("name", "").upper()), None)
        chosen_idx: Optional[int] = None
        if target_name:
            low = target_name.lower()
            for idx, d in enumerate(devices):
                if int(d.get("max_input_channels", 0)) <= 0:
                    continue
                if wasapi_idx is not None and d.get("hostapi") != wasapi_idx:
                    continue
                if str(d.get("name", "")).lower() == low:
                    chosen_idx = idx
                    break
        if chosen_idx is None and wasapi_idx is not None:
            def_in = hostapis[wasapi_idx].get("default_input_device")
            if def_in is not None and int(def_in) >= 0:
                chosen_idx = int(def_in)
        if chosen_idx is None:
            def_in = sd.default.device[0] if isinstance(sd.default.device, (tuple, list)) else None
            if def_in is not None and int(def_in) >= 0:
                chosen_idx = int(def_in)
        if chosen_idx is None:
            raise RuntimeError("No microphone input device available.")
        info = devices[chosen_idx]
        max_ch = max(1, min(2, int(info.get("max_input_channels", 1))))
        sr = int(round(float(info.get("default_samplerate") or CAPTURE_SAMPLE_RATE)))
        if sr <= 0:
            sr = CAPTURE_SAMPLE_RATE
        return chosen_idx, max_ch, sr

    def start(self, device_name: Optional[str] = None) -> bool:
        self.stop()
        self.device_name = device_name
        self.last_error = ""
        try:
            dev_idx, in_ch, in_sr = self._resolve_input_device(device_name)
            with self._lock:
                self._buf = np.zeros((self._capacity, CAPTURE_CHANNELS), dtype=np.int16)
                self._write_pos = 0
                self._filled = 0
                self._src_rate = in_sr
                self._last_write_qpc = _query_qpc()
                self._last_signal_qpc = 0

            stream = sd.InputStream(
                device=dev_idx,
                samplerate=in_sr,
                channels=in_ch,
                dtype="float32",
                blocksize=960,
                callback=self._on_audio,
            )
            stream.start()
            self._stream = stream
            self._running = True
            _log.info("Mic %.0fs buffer started (dev=%s, %d Hz, %dch)", self.duration_s, device_name or "Default", in_sr, in_ch)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            _log.warning("Mic %.0fs buffer unavailable (%s): %s", self.duration_s, device_name or "Default", exc)
            self.stop()
            return False

    def set_duration(self, duration_s: float) -> None:
        """Resize the microphone ring buffer in place without dropping the active stream."""
        dur = max(1.0, min(180.0, float(duration_s)))
        new_cap = max(CAPTURE_SAMPLE_RATE, int(round(dur * CAPTURE_SAMPLE_RATE)))
        with self._lock:
            self.duration_s = dur
            if self._buf is None:
                self._capacity = new_cap
                return
            if new_cap == self._capacity and self._buf.shape == (new_cap, CAPTURE_CHANNELS):
                return
            if self._filled == 0:
                recent = np.zeros((0, CAPTURE_CHANNELS), dtype=np.int16)
            elif self._filled < self._capacity:
                recent = self._buf[:self._filled]
            else:
                pos = self._write_pos
                recent = np.concatenate((self._buf[pos:], self._buf[:pos]), axis=0)
            keep = min(len(recent), new_cap)
            new_buf = np.zeros((new_cap, CAPTURE_CHANNELS), dtype=np.int16)
            if keep > 0:
                new_buf[:keep] = recent[-keep:]
            self._buf = new_buf
            self._capacity = new_cap
            self._filled = keep
            self._write_pos = keep if keep < new_cap else 0

    def stop(self) -> None:
        self._running = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        with self._lock:
            self._buf = None
            self._write_pos = 0
            self._filled = 0

    def _on_audio(self, indata, frames: int, _time_info, _status) -> None:
        if not self._running or frames <= 0:
            return
        arr = np.asarray(indata, dtype=np.float32)
        in_n = len(arr)
        if in_n <= 0:
            return

        if self._f32_scratch.shape[0] < in_n:
            self._f32_scratch = np.empty((in_n, CAPTURE_CHANNELS), dtype=np.float32)
            self._i16_scratch = np.empty((in_n, CAPTURE_CHANNELS), dtype=np.int16)

        if arr.ndim == 1:
            stereo = self._f32_scratch[:in_n]
            stereo[:, 0] = arr
            stereo[:, 1] = arr
        elif arr.shape[1] == 1:
            stereo = self._f32_scratch[:in_n]
            mono = arr[:, 0]
            stereo[:, 0] = mono
            stereo[:, 1] = mono
        else:
            stereo = arr[:, :2]

        if self._src_rate != CAPTURE_SAMPLE_RATE and len(stereo) > 1:
            out_n = max(1, int(round(len(stereo) * (CAPTURE_SAMPLE_RATE / float(self._src_rate)))))
            x_old = np.linspace(0.0, 1.0, len(stereo), endpoint=False)
            x_new = np.linspace(0.0, 1.0, out_n, endpoint=False)
            stereo = np.column_stack(
                (
                    np.interp(x_new, x_old, stereo[:, 0]).astype(np.float32),
                    np.interp(x_new, x_old, stereo[:, 1]).astype(np.float32),
                )
            )

        now_qpc = _query_qpc()
        if float(stereo.max()) > 0.015 or float(stereo.min()) < -0.015:
            self._last_signal_qpc = now_qpc

        n = len(stereo)
        if self._f32_scratch.shape[0] < n:
            self._f32_scratch = np.empty((n, CAPTURE_CHANNELS), dtype=np.float32)
            self._i16_scratch = np.empty((n, CAPTURE_CHANNELS), dtype=np.int16)

        fbuf = self._f32_scratch[:n]
        np.clip(stereo, -1.0, 1.0, out=fbuf)
        fbuf *= np.float32(32767.0)
        np.rint(fbuf, out=fbuf)
        i16 = self._i16_scratch[:n]
        np.copyto(i16, fbuf, casting="unsafe")

        with self._lock:
            buf = self._buf
            if buf is None:
                return
            cap = self._capacity
            if n >= cap:
                buf[:] = i16[-cap:]
                self._write_pos = 0
                self._filled = cap
            else:
                end = self._write_pos + n
                if end <= cap:
                    buf[self._write_pos:end] = i16
                else:
                    first = cap - self._write_pos
                    buf[self._write_pos:] = i16[:first]
                    buf[:n - first] = i16[first:]
                self._write_pos = end % cap
                self._filled = min(cap, self._filled + n)
            self._last_write_qpc = now_qpc

    def ms_since_signal(self) -> int:
        sig = self._last_signal_qpc
        if sig <= 0:
            return -1
        now = _query_qpc()
        if now <= sig:
            return 0
        return int(((now - sig) * 1000) // _query_qpc_freq())

    def snapshot(self) -> tuple[np.ndarray, int, int, int]:
        freq = _query_qpc_freq()
        with self._lock:
            buf = self._buf
            if buf is None or self._filled == 0:
                return np.zeros((0, CAPTURE_CHANNELS), dtype=np.float32), CAPTURE_SAMPLE_RATE, _query_qpc(), freq
            if self._filled < self._capacity:
                raw = buf[:self._filled].copy()
            else:
                pos = self._write_pos
                raw = np.concatenate((buf[pos:], buf[:pos]), axis=0)
            end_qpc = self._last_write_qpc or _query_qpc()
        data = raw.astype(np.float32)
        data *= np.float32(1.0 / 32768.0)
        return data, CAPTURE_SAMPLE_RATE, end_qpc, freq


def measure_channel_levels(audio_f32: np.ndarray, sample_rate: int = CAPTURE_SAMPLE_RATE) -> tuple[float, float]:
    """Return (peak_dbfs, max_200ms_rms_dbfs) for float32 audio in [-1, 1]."""
    arr = np.asarray(audio_f32, dtype=np.float32)
    if arr.size == 0:
        return -120.0, -120.0
    mono = arr.mean(axis=1) if arr.ndim > 1 else arr
    peak = float(np.max(np.abs(mono)))
    if peak <= 1e-7:
        return -120.0, -120.0
    peak_db = float(20.0 * np.log10(peak))

    win = max(1, int(sample_rate * 0.2))
    n = len(mono)
    if n <= win:
        rms = float(np.sqrt(np.mean(mono * mono)))
    else:
        usable = (n // win) * win
        blocks = mono[:usable].reshape(-1, win)
        rms_vals = np.sqrt(np.mean(blocks * blocks, axis=1))
        rms = float(np.max(rms_vals))
        if usable < n:
            tail = mono[usable:]
            rms = max(rms, float(np.sqrt(np.mean(tail * tail))))
    rms_db = float(20.0 * np.log10(max(rms, 1e-7)))
    return peak_db, rms_db


def is_channel_audible(
    audio_f32: np.ndarray,
    kind: str,
    sample_rate: int = CAPTURE_SAMPLE_RATE,
) -> tuple[bool, float, float]:
    """Determine whether a channel snapshot contains real sound above its noise floor.

    Uses a stricter noise gate for Microphone channels so room hiss doesn't trigger false positives.
    """
    peak_db, rms_db = measure_channel_levels(audio_f32, sample_rate)
    if kind == "mic":
        audible = (peak_db > -36.0) and (rms_db > -44.0)
    else:
        audible = (peak_db > -55.0) and (rms_db > -60.0)
    return audible, peak_db, rms_db


def mix_float_tracks(tracks: list[np.ndarray]) -> np.ndarray:
    """Sum time-aligned float32 stereo tracks with a clean soft limiter so summing never clips.

    Returns int16 stereo array ready for WAV export / TrimDialog.
    Single tracks under -1.4 dBFS pass bit-transparently; multi-track peaks smoothly soft-limit.
    """
    if not tracks:
        return np.zeros((0, CAPTURE_CHANNELS), dtype=np.int16)

    max_len = max(len(t) for t in tracks)
    if max_len == 0:
        return np.zeros((0, CAPTURE_CHANNELS), dtype=np.int16)

    acc = np.zeros((max_len, CAPTURE_CHANNELS), dtype=np.float32)
    for t in tracks:
        arr = np.asarray(t, dtype=np.float32)
        if arr.ndim == 1:
            arr = np.column_stack((arr, arr))
        elif arr.shape[1] == 1:
            arr = np.repeat(arr, 2, axis=1)
        elif arr.shape[1] > 2:
            arr = arr[:, :2]
        n = len(arr)
        if n > 0:
            # Right-align shorter tracks to the common end instant
            acc[max_len - n:] += arr

    # Transparent soft-limiter above 0.85 linear (~-1.4 dBFS) with 0.985 ceiling
    thr = np.float32(0.85)
    headroom = np.float32(0.135)
    abs_acc = np.abs(acc)
    over = abs_acc > thr
    if np.any(over):
        excess = (abs_acc[over] - thr) / headroom
        limited = thr + headroom * np.tanh(excess)
        acc[over] = np.copysign(limited, acc[over])

    np.clip(acc, -0.999, 0.999, out=acc)
    return np.round(acc * 32767.0).astype(np.int16)


def _compute_mini_peaks(audio_f32: np.ndarray, bins: int = 120) -> np.ndarray:
    if audio_f32.size == 0:
        return np.zeros(bins, dtype=np.float32)
    mono = audio_f32.mean(axis=1) if audio_f32.ndim > 1 else audio_f32
    abs_m = np.abs(mono)
    n = len(abs_m)
    if n < bins:
        out = np.zeros(bins, dtype=np.float32)
        out[:n] = abs_m
    else:
        usable = (n // bins) * bins
        out = abs_m[:usable].reshape(bins, -1).max(axis=1).astype(np.float32)
    m = float(out.max())
    if m > 1e-6:
        out = out / m
    return out


@dataclass
class ChannelClip:
    key: str
    name: str
    kind: str  # "game" | "app" | "mic" | "system"
    audio: np.ndarray  # float32 (N, 2)
    sample_rate: int
    peak_db: float
    rms_db: float
    ms_since_signal: int
    mini_peaks: np.ndarray


class CaptureSnapshot:
    """Frozen, time-aligned 30s multi-channel snapshot kept alive across Picker <-> Trim round-trips."""

    def __init__(self, sample_rate: int, channels: list[ChannelClip]) -> None:
        self.sample_rate = int(sample_rate)
        self.channels = list(channels)
        self.created_at = time.monotonic()
        self.duration_s = (
            max((len(c.audio) for c in self.channels), default=0) / float(self.sample_rate)
            if self.sample_rate > 0
            else 0.0
        )

    @property
    def is_empty(self) -> bool:
        return len(self.channels) == 0

    def channel_by_key(self, key: str) -> Optional[ChannelClip]:
        for ch in self.channels:
            if ch.key == key:
                return ch
        return None

    def default_selected_keys(self) -> list[str]:
        return choose_default_channels(self.channels)

    def mix(self, selected_keys: list[str]) -> tuple[np.ndarray, int]:
        by_key = {c.key: c.audio for c in self.channels}
        tracks = [by_key[k] for k in selected_keys if k in by_key]
        return mix_float_tracks(tracks), self.sample_rate

    def release(self) -> None:
        for ch in self.channels:
            ch.audio = np.zeros((0, CAPTURE_CHANNELS), dtype=np.float32)
            ch.mini_peaks = np.zeros(0, dtype=np.float32)
        self.channels.clear()


def choose_default_channels(channels: list[ChannelClip]) -> list[str]:
    """Pre-check detected Game channel(s); otherwise pick the single best non-mic app (or Mic if alone)."""
    if not channels:
        return []
    games = [c.key for c in channels if c.kind == "game"]
    if games:
        return games
    non_mic = [c for c in channels if c.kind != "mic"]
    if non_mic:
        # Prefer most recent active audio, breaking ties by active RMS energy
        def _rank(c: ChannelClip) -> tuple[int, float, float]:
            recency_bucket = 0 if (0 <= c.ms_since_signal <= 5000) else 1
            return (recency_bucket, -c.rms_db, -c.peak_db)

        best = min(non_mic, key=_rank)
        return [best.key]
    return [channels[0].key]


def _align_raw_tracks(
    raw_items: list[tuple[str, str, str, np.ndarray, int, int, int, int]],
    max_frames: int,
) -> list[tuple[str, str, str, np.ndarray, int, int]]:
    """Align raw (key, name, kind, audio_f32, sr, end_qpc, qpc_freq, ms_sig) to a common end QPC."""
    if not raw_items:
        return []
    ref_qpc = max(item[5] for item in raw_items)
    aligned: list[tuple[str, str, str, np.ndarray, int, int]] = []
    max_len = 0
    prelim: list[tuple[str, str, str, np.ndarray, int, int]] = []

    for key, name, kind, audio, sr, end_qpc, qpc_freq, ms_sig in raw_items:
        arr = np.asarray(audio, dtype=np.float32)
        if arr.ndim == 1:
            arr = np.column_stack((arr, arr))
        elif arr.shape[1] == 1:
            arr = np.repeat(arr, 2, axis=1)
        elif arr.shape[1] > 2:
            arr = arr[:, :2]

        # Resample if fallback device had a non-48kHz sample rate
        if sr != CAPTURE_SAMPLE_RATE and len(arr) > 1:
            out_n = max(1, int(round(len(arr) * (CAPTURE_SAMPLE_RATE / float(sr)))))
            x_old = np.linspace(0.0, 1.0, len(arr), endpoint=False)
            x_new = np.linspace(0.0, 1.0, out_n, endpoint=False)
            arr = np.column_stack(
                (
                    np.interp(x_new, x_old, arr[:, 0]).astype(np.float32),
                    np.interp(x_new, x_old, arr[:, 1]).astype(np.float32),
                )
            )
            sr = CAPTURE_SAMPLE_RATE

        if end_qpc > 0 and ref_qpc > end_qpc and qpc_freq > 0:
            lag_s = (ref_qpc - end_qpc) / float(qpc_freq)
            max_lag_s = max(1.0, max_frames / float(sr))
            if 0.015 < lag_s < max_lag_s:
                lag_frames = min(max_frames, int(round(lag_s * sr)))
                if lag_frames > 0:
                    pad = np.zeros((lag_frames, CAPTURE_CHANNELS), dtype=np.float32)
                    arr = np.concatenate((arr, pad), axis=0)

        if len(arr) > max_frames:
            arr = arr[-max_frames:]
        max_len = max(max_len, len(arr))
        prelim.append((key, name, kind, arr, sr, ms_sig))

    if max_len == 0:
        return []

    for key, name, kind, arr, sr, ms_sig in prelim:
        if len(arr) < max_len:
            pad_left = np.zeros((max_len - len(arr), CAPTURE_CHANNELS), dtype=np.float32)
            arr = np.concatenate((pad_left, arr), axis=0)
        aligned.append((key, name, kind, arr, sr, ms_sig))
    return aligned


class _AppChannelState:
    __slots__ = ("key", "name", "exe_path", "pids", "kind", "handles", "last_seen_ts", "last_active_ts")

    def __init__(self, key: str, name: str, exe_path: str, pids: list[int], kind: str) -> None:
        self.key = key
        self.name = name
        self.exe_path = exe_path
        self.pids = list(pids)
        self.kind = kind
        self.handles: dict[int, int] = {}  # root_pid -> native handle
        now = time.monotonic()
        self.last_seen_ts = now
        self.last_active_ts = now


class MultiChannelCapture:
    """Per-App Process Loopback + Optional Microphone 30s rolling capture manager.

    - Scans all active Windows render endpoints every ~1.5s.
    - Captures each active app process tree independently of speaker mute/volume/device.
    - Releases inactive/exited app buffers after ~60s to keep RAM/CPU low.
    - Runs a parallel shared-mode 30s Microphone buffer (configurable).
    - Automatically falls back to LoopbackRingBuffer ('All System Audio') if Process Loopback is unavailable.
    """

    def __init__(
        self,
        duration_s: float = 30.0,
        on_status_change: Optional[Callable[[str, str], None]] = None,
        *,
        dll_path: Optional[Path] = None,
        idle_release_s: float = _IDLE_RELEASE_S,
        scan_interval_s: float = _SCAN_INTERVAL_S,
    ) -> None:
        self.duration_s = float(duration_s)
        self._on_status_change = on_status_change
        self._idle_release_s = float(idle_release_s)
        self._scan_interval_s = float(scan_interval_s)

        self._lib = _ProcLoopLib(dll_path)
        self._proc_cache = _ProcessMetaCache()
        self._lock = threading.Lock()
        self._channels: dict[str, _AppChannelState] = {}
        self._mic_ring = MicRingBuffer(duration_s=self.duration_s)
        self._fallback_ring = LoopbackRingBuffer(duration_s=self.duration_s, on_status_change=self._on_fallback_status)

        self._stop_event = threading.Event()
        self._scan_thread: Optional[threading.Thread] = None
        self._running = False
        self._use_procloop = False
        self._fallback_active = False

        self.state: str = "stopped"
        self.last_error: str = ""
        self.active_apps: list[str] = []
        self.include_discord: bool = True
        self.capture_mic_buffer: bool = True
        self._mic_device: Optional[str] = None
        self._fallback_device: Optional[str] = None

    def set_status_callback(self, cb: Optional[Callable[[str, str], None]]) -> None:
        self._on_status_change = cb

    def _emit_status(self, state: str, detail: str = "") -> None:
        changed = (state != self.state) or (detail != self.last_error)
        self.state = state
        self.last_error = detail
        if changed and self._on_status_change is not None:
            try:
                self._on_status_change(state, detail)
            except Exception:
                pass

    def _on_fallback_status(self, state: str, detail: str) -> None:
        if self._fallback_active and not self._use_procloop:
            self._emit_status(state, detail)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_reconnecting(self) -> bool:
        return self._fallback_active and not self._use_procloop and self._fallback_ring.is_reconnecting

    @property
    def mode(self) -> str:
        return "procloop" if self._use_procloop else "fallback"

    @property
    def mic_active(self) -> bool:
        return self.capture_mic_buffer and self._mic_ring.is_running

    @property
    def device_name(self) -> str:
        if self._use_procloop:
            return "Per-App Capture (All Outputs)"
        return self._fallback_ring.device_name or "Desktop Audio"

    def set_include_discord(self, enabled: bool) -> None:
        self.include_discord = bool(enabled)
        self._fallback_ring.set_include_discord(self.include_discord)

    def set_duration(self, duration_s: float) -> None:
        """Resize all active per-app, microphone, and fallback ring buffers on the fly."""
        dur = max(1.0, min(180.0, float(duration_s)))
        with self._lock:
            self.duration_s = dur
            handles = [h for ch in self._channels.values() for h in ch.handles.values()]
        for h in handles:
            self._lib.resize(h, dur)
        self._mic_ring.set_duration(dur)
        self._fallback_ring.set_duration(dur)
        _log.info("Capture buffer duration updated to %.0fs (%d native channels)", dur, len(handles))

    def set_capture_mic_buffer(self, enabled: bool) -> None:
        self.capture_mic_buffer = bool(enabled)
        if not self._running:
            return
        if self.capture_mic_buffer:
            self._restart_mic_async(self._mic_device)
        else:
            self._mic_ring.stop()

    def set_mic_device(self, device_name: Optional[str]) -> None:
        new_dev = device_name or None
        if new_dev == self._mic_device and (not self.capture_mic_buffer or self._mic_ring.is_running):
            return
        self._mic_device = new_dev
        if self._running and self.capture_mic_buffer:
            self._restart_mic_async(self._mic_device)

    def _restart_mic_async(self, device_name: Optional[str]) -> None:
        def _worker() -> None:
            if self._running and self.capture_mic_buffer:
                self._mic_ring.start(device_name)

        threading.Thread(target=_worker, name="ClipMic-MicStart", daemon=True).start()

    def _ensure_fallback_started(self) -> None:
        if self._fallback_active and self._fallback_ring.is_running:
            return
        self._fallback_active = True
        try:
            self._fallback_ring.start(self._fallback_device, auto_reconnect=True)
        except Exception as exc:
            _log.warning("Fallback WASAPI loopback start failed: %s", exc)

    def start(
        self,
        target_device: Optional[str] = None,
        *,
        mic_device: Optional[str] = None,
        capture_mic: bool = True,
        auto_reconnect: bool = True,
    ) -> None:
        self._fallback_device = target_device or None
        self._mic_device = mic_device or None
        self.capture_mic_buffer = bool(capture_mic)
        self._stop_event.clear()

        self._use_procloop = self._lib.is_supported()
        self._running = True

        if self.capture_mic_buffer:
            self._restart_mic_async(self._mic_device)

        if self._use_procloop:
            self._emit_status("active", "")
            t = threading.Thread(target=self._scanner_loop, name="ClipMic-AppScanner", daemon=True)
            self._scan_thread = t
            t.start()
            _log.info("MultiChannelCapture started in Per-App Process Loopback mode.")
        else:
            _log.warning(
                "Process Loopback unavailable (hr=0x%08X); falling back to All System Audio loopback.",
                self._lib.last_probe_hr & 0xFFFFFFFF,
            )
            self._fallback_active = True
            self._fallback_ring.start(self._fallback_device, auto_reconnect=auto_reconnect)
            self._emit_status(self._fallback_ring.state, self._fallback_ring.last_error)

    def restart(self, target_device: Optional[str] = None) -> None:
        self._fallback_device = target_device or None
        if self._fallback_active or not self._use_procloop:
            self._fallback_ring.restart(self._fallback_device)

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        t = self._scan_thread
        if t is not None and t is not threading.current_thread() and t.is_alive():
            t.join(timeout=0.4)
        self._scan_thread = None

        with self._lock:
            channels = list(self._channels.values())
            self._channels.clear()
        for ch in channels:
            for h in ch.handles.values():
                self._lib.destroy(h)
            ch.handles.clear()

        self._proc_cache.clear()
        self._mic_ring.stop()
        if self._fallback_active:
            self._fallback_ring.stop()
            self._fallback_active = False
        self._emit_status("stopped", "")

    def _scanner_loop(self) -> None:
        # Run first scan immediately on background thread so startup never blocks UI
        while not self._stop_event.is_set():
            try:
                self._scan_once()
            except Exception as exc:
                _log.debug("Session scan error: %s", exc)
            if self._stop_event.wait(self._scan_interval_s):
                break

    def _scan_once(self) -> None:
        if not self._running or not self._use_procloop:
            return
        sessions = enumerate_active_audio_sessions(proc_cache=self._proc_cache)
        fg_fs_pid = _get_foreground_fullscreen_pid()
        now = time.monotonic()
        active_names: list[str] = []
        to_destroy: list[int] = []
        to_create: list[tuple[str, str, int, str]] = []  # (key, name, root_pid, kind)
        attach_failed = False

        # Classify new apps outside the lock so memory_maps() never blocks UI reads
        with self._lock:
            existing_keys = set(self._channels.keys())

        pre_kinds: dict[str, str] = {}
        for sess in sessions:
            if not self._running or self._stop_event.is_set():
                return
            key = sess["key"]
            if not self.include_discord and key == "discord":
                continue
            if key not in existing_keys:
                pre_kinds[key] = detect_channel_kind(
                    sess["name"],
                    sess["exe_path"],
                    sess["pids"] + sess["root_pids"],
                    foreground_fs_pid=fg_fs_pid,
                    proc_cache=self._proc_cache,
                )

        if not self._running or self._stop_event.is_set():
            return

        with self._lock:
            seen_keys: set[str] = set()
            for sess in sessions:
                key = sess["key"]
                name = sess["name"]
                if not self.include_discord and key == "discord":
                    continue
                seen_keys.add(key)
                active_names.append(name)

                ch = self._channels.get(key)
                if ch is None:
                    kind = pre_kinds.get(key, "app")
                    ch = _AppChannelState(key, name, sess["exe_path"], sess["pids"], kind)
                    self._channels[key] = ch
                else:
                    ch.last_seen_ts = now
                    ch.last_active_ts = now
                    ch.pids = list(sess["pids"])
                    if sess["exe_path"] and not ch.exe_path:
                        ch.exe_path = sess["exe_path"]
                    if ch.kind != "game" and fg_fs_pid is not None and (
                        fg_fs_pid in sess["pids"] or fg_fs_pid in sess["root_pids"]
                    ):
                        ch.kind = detect_channel_kind(
                            ch.name,
                            ch.exe_path,
                            sess["pids"] + sess["root_pids"],
                            foreground_fs_pid=fg_fs_pid,
                            check_modules=False,
                            proc_cache=self._proc_cache,
                        )

                for rpid in list(ch.handles.keys()):
                    h = ch.handles[rpid]
                    if not self._lib.is_running(h) or not self._proc_cache.pid_alive(rpid):
                        to_destroy.append(ch.handles.pop(rpid))

                for rpid in sess["root_pids"]:
                    if rpid not in ch.handles:
                        to_create.append((key, name, rpid, ch.kind))

            for key in list(self._channels.keys()):
                ch = self._channels[key]
                if key in seen_keys:
                    continue
                for rpid in list(ch.handles.keys()):
                    h = ch.handles[rpid]
                    if not self._lib.is_running(h) or not self._proc_cache.pid_alive(rpid):
                        to_destroy.append(ch.handles.pop(rpid))
                    else:
                        ms_sig = self._lib.ms_since_signal(h)
                        if 0 <= ms_sig < int(self._idle_release_s * 1000):
                            ch.last_active_ts = max(ch.last_active_ts, now - (ms_sig / 1000.0))

                if not ch.handles and (now - ch.last_seen_ts) >= self._idle_release_s:
                    del self._channels[key]
                elif (now - ch.last_active_ts) >= self._idle_release_s and (now - ch.last_seen_ts) >= self._idle_release_s:
                    for h in ch.handles.values():
                        to_destroy.append(h)
                    ch.handles.clear()
                    del self._channels[key]

            self.active_apps = active_names

        # Create/destroy native process-loopback handles completely outside self._lock
        created_handles: list[tuple[str, int, int]] = []
        for key, name, rpid, kind in to_create:
            if not self._running or self._stop_event.is_set():
                break
            handle, hr = self._lib.create(rpid, self.duration_s)
            if handle:
                created_handles.append((key, rpid, handle))
                _log.info("Attached process loopback: %s (root_pid=%d, kind=%s)", name, rpid, kind)
            else:
                _log.warning("Process loopback attach failed for %s (pid=%d, hr=0x%08X)", name, rpid, hr & 0xFFFFFFFF)
                attach_failed = True

        if created_handles:
            with self._lock:
                for key, rpid, handle in created_handles:
                    ch = self._channels.get(key)
                    if not self._running or ch is None or rpid in ch.handles:
                        to_destroy.append(handle)
                    else:
                        ch.handles[rpid] = handle

        for h in to_destroy:
            self._lib.destroy(h)

        if attach_failed:
            self._ensure_fallback_started()

    def refresh_active_sessions(self) -> list[str]:
        if self._use_procloop:
            with self._lock:
                return list(self.active_apps)
        return self._fallback_ring.refresh_active_sessions()

    def is_receiving_audio(self) -> bool:
        if not self._running:
            return False
        if self._use_procloop:
            with self._lock:
                for ch in self._channels.values():
                    for h in ch.handles.values():
                        ms = self._lib.ms_since_signal(h)
                        if 0 <= ms < 1800:
                            return True
            if self.mic_active:
                ms = self._mic_ring.ms_since_signal()
                if 0 <= ms < 1800:
                    return True
            return False
        return self._fallback_ring.is_receiving_audio()

    def snapshot_channels(self) -> CaptureSnapshot:
        """Freeze a time-aligned 30s snapshot of all audible app channels + optional Microphone."""
        max_frames = max(CAPTURE_SAMPLE_RATE, int(round(self.duration_s * CAPTURE_SAMPLE_RATE)))
        raw_items: list[tuple[str, str, str, np.ndarray, int, int, int, int]] = []
        fg_fs_pid = _get_foreground_fullscreen_pid()

        # 1. Per-app process loopback channels
        if self._use_procloop:
            with self._lock:
                chan_list = [
                    (ch.key, ch.name, ch.kind, ch.exe_path, list(ch.pids), list(ch.handles.values()))
                    for ch in self._channels.values()
                    if ch.handles
                ]
            for key, name, kind, exe_path, pids, handles in chan_list:
                if not self.include_discord and key == "discord":
                    continue
                # Refresh game detection right at F9 snapshot in case user alt-tabbed into a game
                if kind != "game" and fg_fs_pid is not None and fg_fs_pid in pids:
                    kind = detect_channel_kind(name, exe_path, pids, foreground_fs_pid=fg_fs_pid, check_modules=False)

                if len(handles) == 1:
                    audio, sr, end_qpc, qpc_freq = self._lib.snapshot(handles[0], max_frames)
                    ms_sig = self._lib.ms_since_signal(handles[0])
                else:
                    # Merge multiple disjoint root trees for the same app name
                    sub_snaps = []
                    ms_sig = -1
                    for h in handles:
                        a, s, eq, qf = self._lib.snapshot(h, max_frames)
                        if a.size > 0:
                            sub_snaps.append((a, s, eq, qf))
                        ms = self._lib.ms_since_signal(h)
                        if ms >= 0 and (ms_sig < 0 or ms < ms_sig):
                            ms_sig = ms
                    if not sub_snaps:
                        continue
                    sr = sub_snaps[0][1]
                    qpc_freq = sub_snaps[0][3]
                    end_qpc = max(x[2] for x in sub_snaps)
                    mlen = max(len(x[0]) for x in sub_snaps)
                    merged = np.zeros((mlen, CAPTURE_CHANNELS), dtype=np.float32)
                    for a, _s, _eq, _qf in sub_snaps:
                        merged[mlen - len(a):] += a
                    audio = merged

                if audio.size > 0:
                    raw_items.append((key, name, kind, audio, sr, end_qpc, qpc_freq, ms_sig))

        # 2. Fallback All System Audio channel (if procloop unavailable or needed as fallback with no app audio)
        if self._fallback_active and (not self._use_procloop or not raw_items):
            fb_i16, fb_sr = self._fallback_ring.snapshot()
            if fb_i16.size > 0:
                fb_f32 = fb_i16.astype(np.float32)
                fb_f32 *= np.float32(1.0 / 32768.0)
                if fb_f32.ndim == 1:
                    fb_f32 = np.column_stack((fb_f32, fb_f32))
                elif fb_f32.shape[1] == 1:
                    fb_f32 = np.repeat(fb_f32, 2, axis=1)
                raw_items.append(
                    (
                        "__system__",
                        "All System Audio",
                        "system",
                        fb_f32,
                        int(fb_sr),
                        _query_qpc(),
                        _query_qpc_freq(),
                        0 if self._fallback_ring.is_receiving_audio() else 5000,
                    )
                )

        # 3. Optional Microphone 30s channel
        if self.capture_mic_buffer and self._mic_ring.is_running:
            mic_f32, mic_sr, mic_qpc, mic_freq = self._mic_ring.snapshot()
            if mic_f32.size > 0:
                raw_items.append(
                    (
                        "__mic__",
                        "Microphone",
                        "mic",
                        mic_f32,
                        mic_sr,
                        mic_qpc,
                        mic_freq,
                        self._mic_ring.ms_since_signal(),
                    )
                )

        # 4. Time-align all snapshots and auto-hide silent channels
        aligned = _align_raw_tracks(raw_items, max_frames)
        audible_channels: list[ChannelClip] = []
        for key, name, kind, audio, sr, ms_sig in aligned:
            ok, peak_db, rms_db = is_channel_audible(audio, kind, sr)
            if not ok:
                continue
            audible_channels.append(
                ChannelClip(
                    key=key,
                    name=name,
                    kind=kind,
                    audio=audio,
                    sample_rate=sr,
                    peak_db=peak_db,
                    rms_db=rms_db,
                    ms_since_signal=ms_sig,
                    mini_peaks=_compute_mini_peaks(audio, bins=120),
                )
            )

        # Sort neatly: Game first, then Apps (by loudness), then System, then Mic last
        order = {"game": 0, "app": 1, "system": 2, "mic": 3}
        audible_channels.sort(key=lambda c: (order.get(c.kind, 2), -c.rms_db, c.name.lower()))
        return CaptureSnapshot(CAPTURE_SAMPLE_RATE, audible_channels)

    def snapshot(self) -> tuple[np.ndarray, int]:
        """Backward-compatible single-array snapshot mixing default selected channels."""
        snap = self.snapshot_channels()
        if snap.is_empty:
            return np.zeros((0, CAPTURE_CHANNELS), dtype=np.int16), CAPTURE_SAMPLE_RATE
        try:
            return snap.mix(snap.default_selected_keys())
        finally:
            snap.release()
