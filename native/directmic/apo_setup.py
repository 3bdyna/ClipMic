"""ClipMic Direct Mic APO installer, backup, and one-click restore helper.

Follows Onion Board's proven Windows APO registration model (GPLv3):
  - Uses EFX (endpoint effect, slot 7) so BOTH normal apps AND raw-stream apps like Discord run it.
  - Uses SeBackupPrivilege + SeRestorePrivilege (REG_OPTION_BACKUP_RESTORE) instead of altering
    TrustedInstaller ownership/ACLs on MMDevices keys.
  - Grants WRITE RESTRICTED (S-1-5-33), LOCAL SERVICE (S-1-5-19), and Authenticated Users (S-1-5-11)
    on %ProgramData%\\ClipMic\\MicPlugin and pre-creates ring.bin so audiodg.exe's write-restricted
    token can always map ring.bin.
  - Stops BOTH Audiosrv and AudioEndpointBuilder BEFORE modifying FxProperties, then always restarts
    them in a finally block (completely silent, CREATE_NO_WINDOW).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
import winreg
from pathlib import Path
from typing import Optional

try:
    import paths as _paths
except ImportError:
    _paths = None

CLSID_CLIPMIC = "{A4E7B219-8F3C-4D6A-9E12-7B5C1D3F8A42}"
IID_IAPO = "{FD7F2B29-24D0-4B5C-B177-592C39F9CA10}"
FX_ASSOC_DEFAULT = "{11111111-1111-1111-1111-111111111111}"

STATE_ROOT = r"SOFTWARE\ClipMic\MicPlugin"
ENDPOINTS_KEY = rf"{STATE_ROOT}\Endpoints"
CAPTURE_ROOT = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
CLSID_KEY = rf"SOFTWARE\Classes\CLSID\{CLSID_CLIPMIC}"
APO_REG_KEY = rf"SOFTWARE\Classes\AudioEngine\AudioProcessingObjects\{CLSID_CLIPMIC}"

FX_FMT = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d},%d"
MODES_FMT = "{d3993a3f-99c2-4402-b5ec-a92a0367664b},%d"
LFX, GFX, SFX, MFX, EFX = 1, 2, 5, 6, 7
COMP_SFX, COMP_MFX, COMP_EFX = 13, 14, 15

PKEY_FX_ASSOC = FX_FMT % 0
PKEY_FX_LFX = FX_FMT % LFX
PKEY_FX_GFX = FX_FMT % GFX
PKEY_FX_SFX = FX_FMT % SFX
PKEY_FX_EFX = FX_FMT % EFX
PKEY_FX_COMP_SFX = FX_FMT % COMP_SFX
PKEY_FX_COMP_MFX = FX_FMT % COMP_MFX
PKEY_FX_COMP_EFX = FX_FMT % COMP_EFX
PKEY_MODES_SFX = MODES_FMT % SFX
PKEY_MODES_EFX = MODES_FMT % EFX
PKEY_DISABLE_SYSFX = "{1da5d803-d492-4edd-8c23-e0c0ffee7f0e},5"
PKEY_FRIENDLY_NAME = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
PKEY_DEVICE_DESC = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"

MODE_DEFAULT = "{C18E2F7E-933D-4965-B7D1-1EEF228D2AF3}"
MODE_COMMS = "{98951333-B9CD-48B1-A0A3-FF40682D73F7}"
MODE_SPEECH = "{FC1CFC9B-B9D6-4CFA-B5E0-4BB2166878B2}"
APO_MODES = [MODE_DEFAULT, MODE_COMMS, MODE_SPEECH]

DATA_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "ClipMic" / "MicPlugin"
INSTALL_DIR = DATA_DIR
INSTALL_DLL = INSTALL_DIR / "clipmic_apo.dll"
RING_PATH = DATA_DIR / "ring.bin"
LOCAL_DLL = Path(__file__).resolve().parent / "clipmic_apo.dll"

RING_MAGIC = 0x434D5043  # "CPMC"
RING_VERSION = 1
RING_RATE = 48000
RING_CAPACITY = 65536
HEADER_BYTES = 4096
HEADER_FMT = "<IIIIQQIIffIIII"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_SC_MANAGER_CONNECT = 0x0001
_SERVICE_QUERY_STATUS = 0x0004
_SERVICE_START = 0x0010
_SERVICE_STOP = 0x0020
_SERVICE_CONTROL_STOP = 0x00000001
_SC_STATUS_PROCESS_INFO = 0
_SERVICE_STOPPED = 0x00000001
_SERVICE_RUNNING = 0x00000004
_ERROR_SERVICE_ALREADY_RUNNING = 1056
_ERROR_SERVICE_NOT_ACTIVE = 1062
_ERROR_CANCELLED = 1223
_SEE_MASK_NOCLOSEPROCESS = 0x00000040


class _SERVICE_STATUS_PROCESS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", wt.DWORD),
        ("dwCurrentState", wt.DWORD),
        ("dwControlsAccepted", wt.DWORD),
        ("dwWin32ExitCode", wt.DWORD),
        ("dwServiceSpecificExitCode", wt.DWORD),
        ("dwCheckPoint", wt.DWORD),
        ("dwWaitHint", wt.DWORD),
        ("dwProcessId", wt.DWORD),
        ("dwServiceFlags", wt.DWORD),
    ]


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("fMask", wt.ULONG),
        ("hwnd", wt.HWND),
        ("lpVerb", wt.LPCWSTR),
        ("lpFile", wt.LPCWSTR),
        ("lpParameters", wt.LPCWSTR),
        ("lpDirectory", wt.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wt.HINSTANCE),
        ("lpIDList", wt.LPVOID),
        ("lpClass", wt.LPCWSTR),
        ("hkeyClass", wt.HKEY),
        ("dwHotKey", wt.DWORD),
        ("hIconOrMonitor", wt.HANDLE),
        ("hProcess", wt.HANDLE),
    ]


def setup_log_path() -> Path:
    """Writable path for elevated APO setup result log."""
    if _paths is not None:
        return _paths.logs_dir() / "apo_setup.log"
    return DATA_DIR / "setup.log"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def find_source_dll() -> Optional[Path]:
    candidates: list[Path] = []
    if _paths is not None:
        candidates.append(_paths.bundled_asset("native", "directmic", "clipmic_apo.dll"))
        candidates.append(_paths.bundled_asset("clipmic_apo.dll"))
    candidates.extend([LOCAL_DLL, INSTALL_DLL])
    for path in candidates:
        if path.is_file():
            return path
    return None


def _val_contains_clsid(val: object, clsid: str = CLSID_CLIPMIC) -> bool:
    low = clsid.lower()
    if isinstance(val, str):
        return val.strip().lower() == low
    if isinstance(val, (list, tuple)):
        return any(isinstance(x, str) and x.strip().lower() == low for x in val)
    return False


def _strip_clsid(val: object, clsid: str = CLSID_CLIPMIC):
    low = clsid.lower()
    if isinstance(val, str):
        return None if val.strip().lower() == low else val
    if isinstance(val, (list, tuple)):
        kept = [x for x in val if isinstance(x, str) and x.strip() and x.strip().lower() != low]
        return kept if kept else None
    return val


def list_capture_endpoints() -> list[dict]:
    """Enumerate active Windows capture (microphone) endpoints from the registry (read-only)."""
    results: list[dict] = []
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CAPTURE_ROOT, 0, winreg.KEY_READ)
    except OSError:
        return results
    with root:
        i = 0
        while True:
            try:
                guid = winreg.EnumKey(root, i)
                i += 1
            except OSError:
                break
            try:
                with winreg.OpenKey(root, guid, 0, winreg.KEY_READ) as ek:
                    state, _ = winreg.QueryValueEx(ek, "DeviceState")
                    if state != 1:
                        continue
                    friendly = ""
                    desc = ""
                    try:
                        with winreg.OpenKey(ek, "Properties", 0, winreg.KEY_READ) as pk:
                            try:
                                friendly, _ = winreg.QueryValueEx(pk, PKEY_FRIENDLY_NAME)
                            except OSError:
                                pass
                            try:
                                desc, _ = winreg.QueryValueEx(pk, PKEY_DEVICE_DESC)
                            except OSError:
                                pass
                    except OSError:
                        pass
                    full_name = (
                        f"{friendly} ({desc})"
                        if friendly and desc and desc not in friendly
                        else (friendly or desc or guid)
                    )
                    results.append({"guid": guid, "name": full_name, "friendly": friendly, "desc": desc})
            except OSError:
                continue
    return results


def _read_reg_value(key, name: str) -> tuple[bool, Optional[int], object]:
    try:
        val, rtype = winreg.QueryValueEx(key, name)
        return True, rtype, val
    except FileNotFoundError:
        return False, None, None


def get_status(endpoint_guid: Optional[str] = None) -> dict:
    """Return current APO installation status without modifying anything."""
    dll_built = find_source_dll() is not None
    dll_installed = INSTALL_DLL.is_file()
    com_ok = False
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{CLSID_KEY}\InprocServer32", 0, winreg.KEY_READ):
            com_ok = True
    except OSError:
        pass

    installed_endpoints: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ENDPOINTS_KEY, 0, winreg.KEY_READ) as ek:
            i = 0
            while True:
                try:
                    installed_endpoints.append(winreg.EnumKey(ek, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass

    active_on_target = False
    enhancements_disabled = False
    slot_used = None
    target = endpoint_guid or (installed_endpoints[0] if installed_endpoints else None)
    if target:
        try:
            fx_path = rf"{CAPTURE_ROOT}\{target}\FxProperties"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, fx_path, 0, winreg.KEY_READ) as fx:
                dis_existed, _, dis_val = _read_reg_value(fx, PKEY_DISABLE_SYSFX)
                if dis_existed and isinstance(dis_val, int) and dis_val != 0:
                    enhancements_disabled = True
                for pid, mkey, label in (
                    (COMP_EFX, PKEY_MODES_EFX, "EFX-Composite"),
                    (EFX, PKEY_MODES_EFX, "EFX"),
                    (COMP_SFX, PKEY_MODES_SFX, "SFX-Composite"),
                    (SFX, PKEY_MODES_SFX, "SFX"),
                ):
                    existed, _, val = _read_reg_value(fx, FX_FMT % pid)
                    modes_ok, _, _ = _read_reg_value(fx, mkey)
                    if existed and modes_ok and _val_contains_clsid(val):
                        active_on_target = True
                        slot_used = label
                        break
        except OSError:
            pass

    file_backups: list[str] = []
    if DATA_DIR.is_dir():
        for p in DATA_DIR.glob("backup_*.json"):
            file_backups.append(p.stem.removeprefix("backup_"))

    return {
        "dll_built": dll_built,
        "dll_installed": dll_installed,
        "com_registered": com_ok,
        "installed_endpoints": installed_endpoints,
        "active": bool(dll_installed and com_ok and active_on_target),
        "enhancements_disabled": enhancements_disabled,
        "slot": slot_used,
        "has_backup": bool(installed_endpoints or file_backups),
    }


def _enable_privileges(*priv_names: str) -> None:
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi.OpenProcessToken.restype = wt.BOOL

    class LUID(ctypes.Structure):
        _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", LUID), ("Attributes", wt.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    advapi.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(LUID)]
    advapi.LookupPrivilegeValueW.restype = wt.BOOL
    advapi.AdjustTokenPrivileges.argtypes = [
        wt.HANDLE, wt.BOOL, ctypes.POINTER(TOKEN_PRIVILEGES), wt.DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ]
    advapi.AdjustTokenPrivileges.restype = wt.BOOL

    token = wt.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x20 | 0x8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        for name in priv_names:
            luid = LUID()
            if not advapi.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
                raise ctypes.WinError(ctypes.get_last_error())
            tp = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES(luid, 0x2),))
            advapi.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None)
            if ctypes.get_last_error():
                raise PermissionError(f"Could not enable {name}")
    finally:
        kernel.CloseHandle(token)


class _BackupKey:
    """Open/create a TrustedInstaller-owned HKLM key via REG_OPTION_BACKUP_RESTORE (no ACL changes)."""

    def __init__(self, subkey: str, create: bool = True) -> None:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        fn = advapi.RegCreateKeyExW
        fn.argtypes = [
            wt.HKEY, wt.LPCWSTR, wt.DWORD, wt.LPWSTR, wt.DWORD, wt.DWORD,
            ctypes.c_void_p, ctypes.POINTER(wt.HKEY), ctypes.POINTER(wt.DWORD),
        ]
        fn.restype = wt.LONG
        if not create:
            try:
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, subkey).Close()
            except PermissionError:
                pass
        h = wt.HKEY()
        disp = wt.DWORD()
        err = fn(
            winreg.HKEY_LOCAL_MACHINE, subkey, 0, None,
            0x4,  # REG_OPTION_BACKUP_RESTORE
            winreg.KEY_ALL_ACCESS, None, ctypes.byref(h), ctypes.byref(disp),
        )
        if err:
            raise ctypes.WinError(err)
        self.h = int(h.value)
        self.created = disp.value == 1

    def get(self, name: str):
        try:
            return winreg.QueryValueEx(self.h, name)
        except OSError:
            return None

    def set(self, name: str, kind: int, value) -> None:
        winreg.SetValueEx(self.h, name, 0, kind, value)

    def delete(self, name: str) -> None:
        try:
            winreg.DeleteValue(self.h, name)
        except FileNotFoundError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        winreg.CloseKey(self.h)


def _run_hidden(cmd: list[str], timeout: int = 20) -> int:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
        return r.returncode
    except Exception:
        return -1


def _scm_advapi():
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.OpenSCManagerW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
    advapi.OpenSCManagerW.restype = wt.HANDLE
    advapi.OpenServiceW.argtypes = [wt.HANDLE, wt.LPCWSTR, wt.DWORD]
    advapi.OpenServiceW.restype = wt.HANDLE
    advapi.CloseServiceHandle.argtypes = [wt.HANDLE]
    advapi.CloseServiceHandle.restype = wt.BOOL
    advapi.QueryServiceStatusEx.argtypes = [
        wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
    ]
    advapi.QueryServiceStatusEx.restype = wt.BOOL
    advapi.ControlService.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p]
    advapi.ControlService.restype = wt.BOOL
    advapi.StartServiceW.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p]
    advapi.StartServiceW.restype = wt.BOOL
    return advapi


def _query_service_state(advapi, svc: wt.HANDLE) -> int:
    ssp = _SERVICE_STATUS_PROCESS()
    needed = wt.DWORD()
    if not advapi.QueryServiceStatusEx(
        svc, _SC_STATUS_PROCESS_INFO, ctypes.byref(ssp), ctypes.sizeof(ssp), ctypes.byref(needed),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(ssp.dwCurrentState)


def _wait_service_state(advapi, svc: wt.HANDLE, target_state: int, timeout_s: float = 15.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while True:
        if _query_service_state(advapi, svc) == target_state:
            return
        if time.perf_counter() >= deadline:
            raise TimeoutError(f"Timed out waiting for Audiosrv state {target_state}")
        time.sleep(0.05)


def _stop_audiosrv(timeout_s: float = 15.0) -> None:
    advapi = _scm_advapi()
    scm = advapi.OpenSCManagerW(None, None, _SC_MANAGER_CONNECT)
    if not scm:
        raise ctypes.WinError(ctypes.get_last_error())
    svc = None
    try:
        svc = advapi.OpenServiceW(scm, "Audiosrv", _SERVICE_QUERY_STATUS | _SERVICE_STOP)
        if not svc:
            raise ctypes.WinError(ctypes.get_last_error())
        if _query_service_state(advapi, svc) == _SERVICE_STOPPED:
            return
        ssp = _SERVICE_STATUS_PROCESS()
        if not advapi.ControlService(svc, _SERVICE_CONTROL_STOP, ctypes.byref(ssp)):
            err = ctypes.get_last_error()
            if err != _ERROR_SERVICE_NOT_ACTIVE:
                raise ctypes.WinError(err)
        _wait_service_state(advapi, svc, _SERVICE_STOPPED, timeout_s=timeout_s)
    finally:
        if svc:
            advapi.CloseServiceHandle(svc)
        advapi.CloseServiceHandle(scm)


def _start_audiosrv(timeout_s: float = 15.0) -> None:
    advapi = _scm_advapi()
    scm = advapi.OpenSCManagerW(None, None, _SC_MANAGER_CONNECT)
    if not scm:
        raise ctypes.WinError(ctypes.get_last_error())
    svc = None
    try:
        svc = advapi.OpenServiceW(scm, "Audiosrv", _SERVICE_QUERY_STATUS | _SERVICE_START)
        if not svc:
            raise ctypes.WinError(ctypes.get_last_error())
        if _query_service_state(advapi, svc) == _SERVICE_RUNNING:
            return
        if not advapi.StartServiceW(svc, 0, None):
            err = ctypes.get_last_error()
            if err != _ERROR_SERVICE_ALREADY_RUNNING:
                raise ctypes.WinError(err)
        _wait_service_state(advapi, svc, _SERVICE_RUNNING, timeout_s=timeout_s)
    finally:
        if svc:
            advapi.CloseServiceHandle(svc)
        advapi.CloseServiceHandle(scm)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_install_up_to_date(src_dll: Path) -> bool:
    expected_ring_bytes = HEADER_BYTES + RING_CAPACITY * 4
    if not DATA_DIR.is_dir() or not RING_PATH.is_file() or not INSTALL_DLL.is_file():
        return False
    try:
        if RING_PATH.stat().st_size != expected_ring_bytes:
            return False
        return _sha256_file(src_dll) == _sha256_file(INSTALL_DLL)
    except OSError:
        return False


def _harden_dll_acls() -> None:
    """Lock clipmic_apo.dll: RX for LOCAL SERVICE / WRITE RESTRICTED / Users, Full only for SYSTEM & Admins."""
    if not INSTALL_DLL.exists():
        return
    _run_hidden([
        "icacls", str(INSTALL_DLL),
        "/inheritance:r",
        "/grant", "*S-1-5-18:F",       # SYSTEM
        "/grant", "*S-1-5-32-544:F",   # Administrators
        "/grant", "*S-1-5-19:RX",      # LOCAL SERVICE (audiodg.exe)
        "/grant", "*S-1-5-33:RX",      # WRITE RESTRICTED (audiodg.exe)
        "/grant", "*S-1-5-11:RX",      # Authenticated Users (read/execute ONLY, never writable)
    ])


def _ensure_data_dir_and_ring() -> None:
    """Set hardened ACLs on %ProgramData%\\ClipMic\\MicPlugin and pre-create user-writable ring.bin."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Directory: Administrators/SYSTEM full; audiodg.exe (LOCAL SERVICE + WRITE RESTRICTED) modify for apo.log;
    # Authenticated Users get RX only so unprivileged users cannot plant or replace clipmic_apo.dll.
    _run_hidden([
        "icacls", str(DATA_DIR),
        "/inheritance:r",
        "/grant", "*S-1-5-18:(OI)(CI)F",      # SYSTEM
        "/grant", "*S-1-5-32-544:(OI)(CI)F",  # Administrators
        "/grant", "*S-1-5-19:(OI)(CI)M",      # LOCAL SERVICE (audiodg.exe)
        "/grant", "*S-1-5-33:(OI)(CI)M",      # WRITE RESTRICTED (audiodg.exe)
        "/grant", "*S-1-5-11:(OI)(CI)RX",     # Authenticated Users (read/execute only on dir)
    ])
    total_bytes = HEADER_BYTES + RING_CAPACITY * 4
    buf = bytearray(total_bytes)
    struct.pack_into(
        HEADER_FMT, buf, 0,
        RING_MAGIC, RING_VERSION, RING_RATE, RING_CAPACITY,
        0, 0, 0, 0, 1.0, 1.0, 0, 0, 0, 0,
    )
    if not RING_PATH.exists() or RING_PATH.stat().st_size != total_bytes:
        RING_PATH.write_bytes(buf)
    # ring.bin: writable by audiodg.exe (S-1-5-19 + S-1-5-33) AND the current user (Authenticated Users)
    _run_hidden([
        "icacls", str(RING_PATH),
        "/inheritance:r",
        "/grant", "*S-1-5-18:F",
        "/grant", "*S-1-5-32-544:F",
        "/grant", "*S-1-5-19:M",
        "/grant", "*S-1-5-33:M",
        "/grant", "*S-1-5-11:M",
    ])


def _first_vendor_clsid(info: dict) -> str:
    if not info or not info.get("existed"):
        return ""
    val = info.get("value")
    if isinstance(val, str) and val.strip() and val.strip().lower() != CLSID_CLIPMIC.lower():
        return val.strip()
    if isinstance(val, list):
        for item in val:
            if isinstance(item, str) and item.strip() and item.strip().lower() != CLSID_CLIPMIC.lower():
                return item.strip()
    return ""


def _backup_endpoint(endpoint_guid: str, fx: _BackupKey) -> dict:
    """Back up original FxProperties before making any modification (never overwrites existing backup)."""
    ep_state_path = rf"{ENDPOINTS_KEY}\{endpoint_guid}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ep_state_path, 0, winreg.KEY_READ) as ek:
            raw_json, _ = winreg.QueryValueEx(ek, "BackupJson")
            data = json.loads(raw_json)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass

    bak_file = DATA_DIR / f"backup_{endpoint_guid}.json"
    if bak_file.is_file():
        try:
            data = json.loads(bak_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, ep_state_path, 0, winreg.KEY_WRITE) as ek:
                    winreg.SetValueEx(ek, "BackupJson", 0, winreg.REG_SZ, json.dumps(data))
                return data
        except (OSError, json.JSONDecodeError):
            pass

    tracked = (
        PKEY_FX_EFX, PKEY_FX_SFX, PKEY_FX_LFX, PKEY_FX_GFX,
        PKEY_FX_COMP_SFX, PKEY_FX_COMP_MFX, PKEY_FX_COMP_EFX,
        PKEY_MODES_EFX, PKEY_MODES_SFX, PKEY_FX_ASSOC, PKEY_DISABLE_SYSFX,
    )
    backup: dict[str, dict] = {}
    for prop in tracked:
        res = fx.get(prop)
        if res is None:
            backup[prop] = {"existed": False, "type": None, "value": None}
        else:
            val, rtype = res
            cleaned = _strip_clsid(val)
            if cleaned is None:
                backup[prop] = {"existed": False, "type": None, "value": None}
            else:
                backup[prop] = {"existed": True, "type": rtype, "value": cleaned}
    backup["__fx_created__"] = {"existed": not fx.created, "type": None, "value": fx.created}

    orig_efx = (
        _first_vendor_clsid(backup[PKEY_FX_EFX])
        or _first_vendor_clsid(backup[PKEY_FX_COMP_EFX])
        or _first_vendor_clsid(backup[PKEY_FX_LFX])
    )

    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, ep_state_path, 0, winreg.KEY_WRITE) as ek:
        if orig_efx:
            winreg.SetValueEx(ek, "Original", 0, winreg.REG_SZ, orig_efx)
        winreg.SetValueEx(ek, "BackupJson", 0, winreg.REG_SZ, json.dumps(backup))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / f"backup_{endpoint_guid}.json").write_text(json.dumps(backup, indent=2), encoding="utf-8")
    return backup


def _register_com() -> None:
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, CLSID_KEY, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "ClipMic Direct Mic APO")
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, rf"{CLSID_KEY}\InprocServer32", 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(INSTALL_DLL))
        winreg.SetValueEx(k, "ThreadingModel", 0, winreg.REG_SZ, "Both")
    with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, APO_REG_KEY, 0, winreg.KEY_WRITE) as k:
        winreg.SetValueEx(k, "FriendlyName", 0, winreg.REG_SZ, "ClipMic Direct Mic APO")
        winreg.SetValueEx(k, "Copyright", 0, winreg.REG_SZ, "ClipMic contributors (GPLv3)")
        winreg.SetValueEx(k, "MajorVersion", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "MinorVersion", 0, winreg.REG_DWORD, 0)
        winreg.SetValueEx(k, "Flags", 0, winreg.REG_DWORD, 14)
        winreg.SetValueEx(k, "MinInputConnections", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "MaxInputConnections", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "MinOutputConnections", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "MaxOutputConnections", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "MaxInstances", 0, winreg.REG_DWORD, 0xFFFFFFFF)
        winreg.SetValueEx(k, "NumAPOInterfaces", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "APOInterface0", 0, winreg.REG_SZ, IID_IAPO)


def _merge_modes(existing: object) -> list[str]:
    modes: list[str] = []
    if isinstance(existing, list):
        modes.extend([m for m in existing if isinstance(m, str) and m.strip()])
    elif isinstance(existing, str) and existing.strip():
        modes.append(existing.strip())
    low = {m.lower() for m in modes}
    for m in APO_MODES:
        if m.lower() not in low:
            modes.append(m)
            low.add(m.lower())
    return modes


def _write_slot(endpoint_guid: str, slot: int, notes: Optional[list[str]] = None) -> str:
    fx_path = rf"{CAPTURE_ROOT}\{endpoint_guid}\FxProperties"
    with _BackupKey(fx_path, create=True) as fx:
        _backup_endpoint(endpoint_guid, fx)

        # Auto-enable Windows Audio Enhancements if currently disabled (original value is backed up)
        cur_dis = fx.get(PKEY_DISABLE_SYSFX)
        if cur_dis is not None and isinstance(cur_dis[0], int) and cur_dis[0] != 0:
            fx.set(PKEY_DISABLE_SYSFX, winreg.REG_DWORD, 0)
            if notes is not None:
                notes.append(
                    f"INFO: Auto-enabled Windows Audio Enhancements (Disable_SysFx: {cur_dis[0]} -> 0)"
                )

        # Ensure FxAssociation exists on mics that had no FxProperties key originally
        if fx.get(PKEY_FX_ASSOC) is None:
            fx.set(PKEY_FX_ASSOC, winreg.REG_SZ, FX_ASSOC_DEFAULT)

        # Clean up any leftover ClipMic references in older/other slots without clobbering vendor APOs
        for old_key in (PKEY_FX_LFX, PKEY_FX_GFX, PKEY_FX_SFX, PKEY_FX_COMP_SFX):
            cur = fx.get(old_key)
            if cur is None:
                continue
            val, rtype = cur
            if _val_contains_clsid(val):
                cleaned = _strip_clsid(val)
                if cleaned is None:
                    fx.delete(old_key)
                else:
                    fx.set(old_key, rtype, cleaned)

        if slot == EFX:
            cur_modes = fx.get(PKEY_MODES_EFX)
            modes = _merge_modes(cur_modes[0] if cur_modes else None)
            cur_comp = fx.get(PKEY_FX_COMP_EFX)
            cur_efx = fx.get(PKEY_FX_EFX)

            if cur_comp is not None and isinstance(cur_comp[0], list):
                # Windows 11 Composite EFX (REG_MULTI_SZ): append ClipMic cleanly to vendor APO chain
                chain = list(_strip_clsid(cur_comp[0]) or [])
                chain.append(CLSID_CLIPMIC)
                fx.set(PKEY_FX_COMP_EFX, winreg.REG_MULTI_SZ, chain)
                fx.set(PKEY_MODES_EFX, winreg.REG_MULTI_SZ, modes)
                return "EFX-Composite"

            if cur_efx is not None and cur_efx[1] == winreg.REG_MULTI_SZ and isinstance(cur_efx[0], list):
                chain = list(_strip_clsid(cur_efx[0]) or [])
                chain.append(CLSID_CLIPMIC)
                fx.set(PKEY_FX_EFX, winreg.REG_MULTI_SZ, chain)
                fx.set(PKEY_MODES_EFX, winreg.REG_MULTI_SZ, modes)
                return "EFX"

            fx.set(PKEY_FX_EFX, winreg.REG_SZ, CLSID_CLIPMIC)
            fx.set(PKEY_MODES_EFX, winreg.REG_MULTI_SZ, modes)
            return "EFX"

        cur_modes = fx.get(PKEY_MODES_SFX)
        fx.set(PKEY_FX_SFX, winreg.REG_SZ, CLSID_CLIPMIC)
        fx.set(PKEY_MODES_SFX, winreg.REG_MULTI_SZ, _merge_modes(cur_modes[0] if cur_modes else None))
        return "SFX"


def install_apo(
    endpoint_guid: str,
    timings_ms: Optional[dict[str, float]] = None,
    notes: Optional[list[str]] = None,
) -> str:
    if not is_admin():
        raise PermissionError("Administrator privileges required to install Direct Mic APO.")
    src_dll = find_source_dll()
    if src_dll is None:
        raise FileNotFoundError("clipmic_apo.dll not found. Reinstall ClipMic or build native/directmic/clipmic_apo.dll.")

    _enable_privileges("SeBackupPrivilege", "SeRestorePrivilege")
    t_total = time.perf_counter()

    t0 = time.perf_counter()
    up_to_date = _is_install_up_to_date(src_dll)
    if timings_ms is not None:
        timings_ms["hash_check_ms"] = (time.perf_counter() - t0) * 1000.0

    if not up_to_date:
        t0 = time.perf_counter()
        _ensure_data_dir_and_ring()
        if timings_ms is not None:
            timings_ms["ensure_dir_ring_ms"] = (time.perf_counter() - t0) * 1000.0
    elif timings_ms is not None:
        timings_ms["ensure_dir_ring_ms"] = 0.0

    # Stop Audiosrv BEFORE copying DLL (if needed) and editing FxProperties
    slot_used = "EFX"
    try:
        t0 = time.perf_counter()
        _stop_audiosrv()
        if timings_ms is not None:
            timings_ms["stop_audiosrv_ms"] = (time.perf_counter() - t0) * 1000.0

        if not up_to_date:
            t0 = time.perf_counter()
            if src_dll.resolve() != INSTALL_DLL.resolve():
                shutil.copy2(src_dll, INSTALL_DLL)
            if timings_ms is not None:
                timings_ms["copy_dll_ms"] = (time.perf_counter() - t0) * 1000.0

            t0 = time.perf_counter()
            _harden_dll_acls()
            if timings_ms is not None:
                timings_ms["icacls_dll_ms"] = (time.perf_counter() - t0) * 1000.0
        elif timings_ms is not None:
            timings_ms["copy_dll_ms"] = 0.0
            timings_ms["icacls_dll_ms"] = 0.0

        t0 = time.perf_counter()
        _register_com()
        if timings_ms is not None:
            timings_ms["register_com_ms"] = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        slot_used = _write_slot(endpoint_guid, EFX, notes=notes)
        if timings_ms is not None:
            timings_ms["write_slot_ms"] = (time.perf_counter() - t0) * 1000.0
    finally:
        t0 = time.perf_counter()
        _start_audiosrv()
        if timings_ms is not None:
            timings_ms["start_audiosrv_ms"] = (time.perf_counter() - t0) * 1000.0
            timings_ms["total_ms"] = (time.perf_counter() - t_total) * 1000.0

    return slot_used


def _delete_tree(root, subkey: str) -> None:
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            children: list[str] = []
            i = 0
            while True:
                try:
                    children.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
            for c in children:
                _delete_tree(root, rf"{subkey}\{c}")
        winreg.DeleteKey(root, subkey)
    except OSError:
        pass


def _delete_key_if_empty(subkey: str) -> None:
    try:
        with _BackupKey(subkey, create=False) as k:
            n_sub, n_val, _ = winreg.QueryInfoKey(k.h)
        if n_sub == 0 and n_val == 0:
            advapi = ctypes.WinDLL("advapi32", use_last_error=True)
            fn = advapi.RegDeleteKeyW
            fn.argtypes = [wt.HKEY, wt.LPCWSTR]
            fn.restype = wt.LONG
            fn(winreg.HKEY_LOCAL_MACHINE, subkey)
    except OSError:
        pass


def _collect_all_affected_endpoints() -> list[str]:
    """Find every endpoint GUID with a registry backup, JSON backup, or live ClipMic APO reference."""
    guids: set[str] = set()
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ENDPOINTS_KEY, 0, winreg.KEY_READ) as ek:
            i = 0
            while True:
                try:
                    guids.add(winreg.EnumKey(ek, i))
                    i += 1
                except OSError:
                    break
    except OSError:
        pass

    if DATA_DIR.is_dir():
        for p in DATA_DIR.glob("backup_*.json"):
            g = p.stem.removeprefix("backup_")
            if g:
                guids.add(g)

    # Scan all capture endpoints (including unplugged/not-present mics) for any lingering ClipMic CLSID
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, CAPTURE_ROOT, 0, winreg.KEY_READ) as root:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(root, i)
                    i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(root, rf"{guid}\FxProperties", 0, winreg.KEY_READ) as fx:
                        for pid in (EFX, COMP_EFX, SFX, COMP_SFX, LFX, GFX):
                            existed, _, val = _read_reg_value(fx, FX_FMT % pid)
                            if existed and _val_contains_clsid(val):
                                guids.add(guid)
                                break
                except OSError:
                    continue
    except OSError:
        pass

    return sorted(guids)


def _load_backup(guid: str) -> dict:
    ep_state_path = rf"{ENDPOINTS_KEY}\{guid}"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ep_state_path, 0, winreg.KEY_READ) as ek:
            raw_json, _ = winreg.QueryValueEx(ek, "BackupJson")
            data = json.loads(raw_json)
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass

    bak_file = DATA_DIR / f"backup_{guid}.json"
    if bak_file.is_file():
        try:
            data = json.loads(bak_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def uninstall_apo(
    endpoint_guid: Optional[str] = None,
    *,
    restore_all: bool = False,
    timings_ms: Optional[dict[str, float]] = None,
) -> int:
    if not is_admin():
        raise PermissionError("Administrator privileges required to restore microphone settings.")

    _enable_privileges("SeBackupPrivilege", "SeRestorePrivilege")
    t_total = time.perf_counter()
    all_affected = _collect_all_affected_endpoints()
    if restore_all or not endpoint_guid:
        targets = all_affected
    else:
        targets = [endpoint_guid]

    restored_count = 0
    try:
        t0 = time.perf_counter()
        _stop_audiosrv()
        if timings_ms is not None:
            timings_ms["stop_audiosrv_ms"] = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        for guid in targets:
            ep_state_path = rf"{ENDPOINTS_KEY}\{guid}"
            backup = _load_backup(guid)
            fx_path = rf"{CAPTURE_ROOT}\{guid}\FxProperties"
            try:
                with _BackupKey(fx_path, create=False) as fx:
                    ours_present = any(
                        (cur := fx.get(FX_FMT % p)) is not None and _val_contains_clsid(cur[0])
                        for p in (EFX, COMP_EFX, SFX, COMP_SFX, LFX, GFX)
                    )
                    if ours_present and backup:
                        for prop, info in backup.items():
                            if prop.startswith("__"):
                                continue
                            if info.get("existed"):
                                fx.set(prop, int(info["type"]), info["value"])
                            else:
                                fx.delete(prop)
                        restored_count += 1
                    elif ours_present:
                        # No backup available (e.g. registry state missing): strip ClipMic CLSID safely
                        for p in (EFX, COMP_EFX, SFX, COMP_SFX, LFX, GFX):
                            prop = FX_FMT % p
                            cur = fx.get(prop)
                            if cur is not None and _val_contains_clsid(cur[0]):
                                cleaned = _strip_clsid(cur[0])
                                if cleaned is None:
                                    fx.delete(prop)
                                else:
                                    fx.set(prop, cur[1], cleaned)
                        restored_count += 1
                    elif backup:
                        dis_info = backup.get(PKEY_DISABLE_SYSFX)
                        if isinstance(dis_info, dict) and dis_info.get("existed"):
                            orig_val = dis_info.get("value")
                            cur_dis = fx.get(PKEY_DISABLE_SYSFX)
                            if (
                                isinstance(orig_val, int)
                                and orig_val != 0
                                and cur_dis is not None
                                and cur_dis[0] == 0
                            ):
                                fx.set(PKEY_DISABLE_SYSFX, int(dis_info["type"]), orig_val)
                                restored_count += 1
                if backup.get("__fx_created__", {}).get("value"):
                    _delete_key_if_empty(fx_path)
            except OSError:
                pass

            _delete_tree(winreg.HKEY_LOCAL_MACHINE, ep_state_path)
            bak_file = DATA_DIR / f"backup_{guid}.json"
            if bak_file.is_file():
                try:
                    bak_file.unlink()
                except OSError:
                    pass

        remaining = [g for g in all_affected if g not in targets]
        if restore_all or not endpoint_guid or not remaining:
            _delete_tree(winreg.HKEY_LOCAL_MACHINE, CLSID_KEY)
            _delete_tree(winreg.HKEY_LOCAL_MACHINE, APO_REG_KEY)
            _delete_tree(winreg.HKEY_LOCAL_MACHINE, STATE_ROOT)
            if INSTALL_DLL.exists():
                try:
                    INSTALL_DLL.unlink()
                except OSError:
                    ctypes.windll.kernel32.MoveFileExW(str(INSTALL_DLL), None, 0x4)
        if timings_ms is not None:
            timings_ms["restore_reg_ms"] = (time.perf_counter() - t0) * 1000.0
    finally:
        t0 = time.perf_counter()
        _start_audiosrv()
        if timings_ms is not None:
            timings_ms["start_audiosrv_ms"] = (time.perf_counter() - t0) * 1000.0
            timings_ms["total_ms"] = (time.perf_counter() - t_total) * 1000.0
    return restored_count


def restore_all_mics(timings_ms: Optional[dict[str, float]] = None) -> int:
    """Safety fallback: restore every backed-up or modified microphone (even if unplugged/renamed)."""
    return uninstall_apo(None, restore_all=True, timings_ms=timings_ms)


def launch_elevated(action: str, endpoint_guid: Optional[str] = None) -> tuple[Optional[int], bool]:
    """Trigger silent UAC elevation via ShellExecuteExW and return (hProcess, cancelled)."""
    log_path = setup_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if log_path.exists():
            log_path.unlink()
    except OSError:
        pass

    flag_map = {
        "install": "--apo-install",
        "uninstall": "--apo-uninstall",
        "restore-all": "--apo-restore-all",
    }
    flag = flag_map.get(action.lower())
    if not flag:
        raise ValueError(f"Unsupported APO action: {action}")

    cli_args: list[str] = [flag]
    if endpoint_guid and action.lower() in ("install", "uninstall"):
        cli_args.append(endpoint_guid)
    cli_args.extend(["--apo-log", str(log_path)])

    frozen = _paths.is_frozen() if _paths is not None else bool(getattr(sys, "frozen", False))
    if frozen:
        launcher = str(Path(sys.executable).resolve())
        args = cli_args
    else:
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe")
        launcher = str(pyw) if pyw.is_file() else str(exe)
        main_py = (_paths.exe_dir() if _paths is not None else Path(__file__).resolve().parents[2]) / "main.py"
        args = [str(main_py), *cli_args]

    params = subprocess.list2cmdline(args)
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = _SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = "runas"
    sei.lpFile = launcher
    sei.lpParameters = params
    sei.nShow = 0

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wt.BOOL

    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        if err == _ERROR_CANCELLED:
            return None, True
        raise ctypes.WinError(err)

    return (int(sei.hProcess) if sei.hProcess else None), False


def _format_timings(timings_ms: dict[str, float]) -> str:
    if not timings_ms:
        return ""
    parts = [f"{k}={v:.1f}ms" for k, v in timings_ms.items()]
    return "TIMING: " + " ".join(parts)


def run_cli_action(action: str, endpoint_guid: Optional[str] = None, log_file: Optional[Path] = None) -> int:
    """Execute an elevated APO action and write a concise result line to log_file."""
    log_path = log_file or setup_log_path()
    cmd = action.lower()
    if cmd == "status":
        print(json.dumps(get_status(endpoint_guid), indent=2))
        return 0
    timings_ms: dict[str, float] = {}
    notes: list[str] = []
    try:
        if cmd == "install":
            if not endpoint_guid:
                raise ValueError("endpoint_guid required for install")
            slot = install_apo(endpoint_guid, timings_ms=timings_ms, notes=notes)
            msg = f"OK: Installed ClipMic APO ({slot}) on {endpoint_guid}"
        elif cmd == "uninstall":
            uninstall_apo(endpoint_guid, timings_ms=timings_ms)
            msg = "OK: Restored microphone settings and removed ClipMic APO"
        elif cmd == "restore-all":
            count = restore_all_mics(timings_ms=timings_ms)
            msg = f"OK: Restored all microphones ({count} endpoint(s)) and removed ClipMic APO"
        else:
            raise ValueError(f"Unknown APO command: {cmd}")
        timing_line = _format_timings(timings_ms)
        log_lines = [msg, *notes]
        if timing_line:
            log_lines.append(timing_line)
        log_body = "\n".join(log_lines) + "\n"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(log_body, encoding="utf-8")
        for line in log_lines:
            print(line)
        return 0
    except Exception as exc:
        import traceback
        try:
            timing_line = _format_timings(timings_ms)
            extra = f"\n{timing_line}" if timing_line else ""
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"ERROR: {exc}{extra}\n{traceback.format_exc()}", encoding="utf-8")
        except OSError:
            pass
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: apo_setup.py <install|uninstall|restore-all|status> [endpoint_guid]")
        return 2
    cmd = sys.argv[1].lower()
    guid = sys.argv[2] if len(sys.argv) > 2 else None
    return run_cli_action(cmd, guid)


if __name__ == "__main__":
    raise SystemExit(main())
