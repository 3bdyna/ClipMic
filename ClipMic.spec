# -*- mode: python ; coding: utf-8 -*-
import os
import shutil
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules


def _resolve_ffmpeg_binary() -> list[tuple[str, str]]:
    """Locate ffmpeg.exe from repo root, PATH, or WinGet so builds are reproducible."""
    candidates: list[Path] = [Path("ffmpeg.exe")]
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(Path(found))
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        candidates.append(Path(local_app) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")
        winget_pkgs = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.is_dir():
            candidates.extend(winget_pkgs.glob("*FFmpeg*/**/ffmpeg.exe"))
    candidates.extend([Path("C:/ffmpeg/bin/ffmpeg.exe"), Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe")])
    for p in candidates:
        if p.is_file():
            return [(str(p.resolve()), ".")]
    return []


datas = [
    ('clipmic.ico', '.'),
    ('LICENSE', '.'),
    ('THIRD_PARTY_NOTICES.txt', '.'),
]
binaries = [
    ('native\\directmic\\clipmic_apo.dll', 'native\\directmic'),
    ('native\\procloopback\\clipmic_procloop.dll', 'native\\procloopback'),
] + _resolve_ffmpeg_binary()
hiddenimports = ['comtypes', 'psutil', 'native.directmic.apo_setup']
binaries += collect_dynamic_libs('pyaudiowpatch')
hiddenimports += collect_submodules('numpy._core')
tmp_ret = collect_all('sounddevice')
datas += tmp_ret[0]
binaries += tmp_ret[1]
hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'unittest', 'pydoc', 'doctest',
        'win32com', 'win32api', 'win32con', 'win32gui', 'pywintypes', 'pythoncom',
        'Pythonwin', 'pythonnet', 'clr_loader', 'yaml', 'markupsafe',
        'charset_normalizer', 'setuptools', 'pkg_resources', 'wheel',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
        'PySide6.QtSql', 'PySide6.Qt3DCore', 'PySide6.QtPdf',
        'PySide6.QtNetwork', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
        'PySide6.QtDBus', 'PySide6.QtTest', 'PySide6.QtPrintSupport',
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ClipMic',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['clipmic.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ClipMic',
)
