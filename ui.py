from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import gc
import sys
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap, QPixmapCache
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProxyStyle,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

import paths
from audio_buffer import CaptureSnapshot, ChannelClip, list_loopback_devices
from effects import (
    DEFAULT_PARAMS,
    QUICK_PICK_IDS,
    SUPPORTED_AUDIO_EXTS,
    SUPPORTED_MEDIA_EXTS,
    SUPPORTED_VIDEO_EXTS,
    add_library_effect,
    available_library_effects,
    compute_waveform_peaks,
    decode_audio_to_wav,
    delete_effect,
    export_wav_to_mp3,
    is_supported_audio_file,
    is_video_file,
    load_effects,
    make_preview_sample_wav,
    normalize_params,
    process_clip,
    read_wav,
    resolve_effect_filter,
    restore_default_effects,
    sanitize_filename,
    upsert_effect,
    write_wav,
)
from native.directmic import apo_setup
from hotkeys import (
    DEFAULT_BUFFER_SECONDS,
    MAX_BUFFER_SECONDS,
    MIN_BUFFER_SECONDS,
    ClipEntry,
    clamp_buffer_seconds,
    parse_hotkey,
    resolve_clip_path,
    save_config,
)
from i18n import get_lang, is_rtl, localize_effect_name, ltr, set_lang, t
from mic_inject import MicInjector, list_microphones

DARK_QSS = """
QWidget {
    background-color: #0B0B0C;
    color: #F2F2F4;
    font-family: "Segoe UI", sans-serif;
    font-size: 13px;
}
QLabel, QCheckBox {
    background-color: transparent;
}
QCheckBox {
    spacing: 6px;
    color: #D4D4D8;
}
QFrame#Card {
    background-color: #141417;
    border: 1px solid #22222A;
    border-radius: 10px;
}
QPushButton#SectionToggle {
    background-color: transparent;
    color: #9CA3AF;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
}
QPushButton#SectionToggle:hover {
    background-color: #141419;
    border-color: #22222A;
    color: #E4E4E7;
}
QFrame#EmptyStateCard {
    background-color: #111115;
    border: 1px dashed #2A2A34;
    border-radius: 10px;
}
QPushButton {
    background-color: #1D1D22;
    color: #EAEAEF;
    border: 1px solid #2E2E36;
    border-radius: 7px;
    padding: 6px 12px;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #27272E;
    border-color: #3F3F4A;
}
QPushButton:pressed {
    background-color: #18181C;
}
QPushButton:disabled {
    color: #63636D;
    background-color: #151518;
    border-color: #222227;
}
QPushButton#Primary {
    background-color: #F4F4F5;
    border-color: #F4F4F5;
    color: #0B0B0C;
}
QPushButton#Primary:hover {
    background-color: #E2E2E6;
    border-color: #E2E2E6;
}
QPushButton#Accent {
    background-color: #18231F;
    border-color: #2A4037;
    color: #89C2A4;
}
QPushButton#Accent:hover {
    background-color: #1F2E28;
    border-color: #3A594C;
    color: #A3D4BA;
}
QPushButton#Danger {
    background-color: #251B1D;
    border-color: #462D32;
    color: #D48E95;
}
QPushButton#Danger:hover {
    background-color: #302124;
    border-color: #5C3A41;
    color: #E2A8AE;
}
QPushButton#StopIdle, QPushButton#StopIdle:disabled {
    background-color: #111114;
    border: 1px dashed #25252D;
    color: #55555F;
    font-weight: 500;
}
QPushButton#ExportCard {
    background-color: #141417;
    color: #C8C8D0;
    border: 1px solid #22222A;
    border-radius: 10px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
}
QWidget#ClipRow QPushButton {
    padding: 4px 10px;
}
QPushButton#ExportCard:hover {
    background-color: #1C1C22;
    border-color: #343440;
    color: #F2F2F4;
}
QPushButton#ExportCard:pressed {
    background-color: #111115;
    border-color: #2A2A34;
}
QMenu {
    background-color: #141417;
    color: #F2F2F4;
    border: 1px solid #2E2E36;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 14px;
    border-radius: 4px;
}
QMenu::item:selected {
    background-color: #27272E;
    color: #FFFFFF;
}
QMenu::separator {
    height: 1px;
    background: #25252B;
    margin: 4px 6px;
}
QPushButton#PresetBtn {
    background-color: #18181C;
    border: 1px solid #292930;
    padding: 6px 10px;
    font-weight: 500;
    color: #D4D4D8;
}
QPushButton#PresetBtn:checked {
    background-color: #18231F;
    border: 1px solid #2A4037;
    color: #89C2A4;
    font-weight: 700;
}
QPushButton#ChangeChannelsBtn {
    background-color: #1D2B3D;
    border: 1px solid #3B6BA5;
    color: #DBEAFE;
    padding: 6px 12px;
    font-weight: 700;
}
QPushButton#ChangeChannelsBtn:hover {
    background-color: #253A54;
    border-color: #60A5FA;
    color: #FFFFFF;
}
QPushButton#ChangeChannelsBtn:pressed {
    background-color: #172333;
}
QPushButton#BufPresetBtn {
    background-color: #16161B;
    border: 1px solid #2A2A32;
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 12px;
    font-weight: 600;
    color: #A1A1AA;
}
QPushButton#BufPresetBtn:hover {
    background-color: #22222A;
    border-color: #3F3F4A;
    color: #F4F4F5;
}
QPushButton#BufPresetBtn:checked {
    background-color: #18231F;
    border-color: #2A4037;
    color: #89C2A4;
}
QFrame#DropZone {
    background-color: #111115;
    border: 1px dashed #32323D;
    border-radius: 8px;
}
QFrame#DropZone:hover {
    background-color: #16161C;
    border-color: #4A4A5A;
}
QFrame#DropZone[dragOver="true"] {
    background-color: #192638;
    border: 1px dashed #60A5FA;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: #101013;
    border: 1px solid #2B2B33;
    border-radius: 6px;
    padding: 6px 10px;
    color: #F2F2F4;
}
QSpinBox {
    padding: 3px 6px;
    font-size: 12px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border-color: #71717A;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
    background: transparent;
}
QComboBox QAbstractItemView {
    background-color: #141417;
    color: #F2F2F4;
    border: 1px solid #32323B;
    selection-background-color: #2A2A32;
    selection-color: #FFFFFF;
    outline: 0;
    padding: 4px;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #27272E;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #E4E4E7;
    width: 13px;
    height: 13px;
    margin: -5px 0;
    border-radius: 6px;
}
QScrollArea {
    border: none;
    background: transparent;
}
QToolTip {
    background-color: #18181D;
    color: #F4F4F5;
    border: 1px solid #3A3A46;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}
"""


class _ClipMicStyle(QProxyStyle):
    """Draw crisp dark checkboxes with a clear border and vector checkmark."""

    def pixelMetric(self, metric, option=None, widget=None) -> int:
        if metric in (
            QStyle.PixelMetric.PM_IndicatorWidth,
            QStyle.PixelMetric.PM_IndicatorHeight,
        ):
            return 16
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None) -> None:
        if element == QStyle.PrimitiveElement.PE_IndicatorCheckBox:
            rect = QRectF(option.rect).adjusted(1.0, 1.0, -1.0, -1.0)
            enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
            checked = bool(option.state & QStyle.StateFlag.State_On)
            hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if not enabled:
                fill = QColor("#151518")
                border = QColor("#25252C")
            elif checked:
                fill = QColor("#18231F")
                border = QColor("#66A686" if hovered else "#2A4037")
            else:
                fill = QColor("#1C1C22" if hovered else "#161619")
                border = QColor("#565666" if hovered else "#3A3A44")

            painter.setBrush(fill)
            painter.setPen(QPen(border, 1.4 if checked else 1.1))
            painter.drawRoundedRect(rect, 4.0, 4.0)

            if checked:
                ck_pen = QPen(QColor("#89C2A4" if enabled else "#6B7280"), 1.8)
                ck_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                ck_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(ck_pen)
                x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
                painter.drawLine(
                    QPointF(x + w * 0.24, y + h * 0.52),
                    QPointF(x + w * 0.44, y + h * 0.72),
                )
                painter.drawLine(
                    QPointF(x + w * 0.44, y + h * 0.72),
                    QPointF(x + w * 0.78, y + h * 0.30),
                )
            painter.restore()
            return
        super().drawPrimitive(element, option, painter, widget)


def _install_app_style() -> None:
    app = QApplication.instance()
    if app is not None and not isinstance(app.style(), _ClipMicStyle):
        app.setStyle(_ClipMicStyle(app.style()))


def _trim_working_set() -> None:
    """Ask Windows to trim unused working-set pages while hidden in the system tray."""
    if sys.platform != "win32":
        return
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wt.HANDLE
        hproc = kernel32.GetCurrentProcess()
        empty_ws = getattr(kernel32, "K32EmptyWorkingSet", None)
        if empty_ws is None:
            empty_ws = ctypes.windll.psapi.EmptyWorkingSet
        empty_ws.argtypes = [wt.HANDLE]
        empty_ws.restype = wt.BOOL
        empty_ws(hproc)
    except Exception:
        pass


def apply_dark_titlebar(widget: QWidget, bg_hex: str = "#0B0B0C") -> None:
    """Apply Windows 10/11 dark native title bar matching ClipMic's background."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.c_void_p(int(widget.winId()))
        if not hwnd.value:
            return
        dwmapi = ctypes.windll.dwmapi
        dark = ctypes.c_int(1)
        # DWMWA_USE_IMMERSIVE_DARK_MODE: 20 (Win10 20H1+ / Win11), fallback 19 (older Win10)
        hr = dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
        if hr != 0:
            dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(dark), ctypes.sizeof(dark))

        hx = bg_hex.lstrip("#")
        if len(hx) == 6:
            r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
            caption_color = ctypes.c_uint32((b << 16) | (g << 8) | r)
            # DWMWA_CAPTION_COLOR = 35 (Windows 11)
            dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption_color), ctypes.sizeof(caption_color))

        # DWMWA_TEXT_COLOR = 36 (#EAEAEF -> 0x00EFEAEA)
        text_color = ctypes.c_uint32(0x00EFEAEA)
        dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text_color), ctypes.sizeof(text_color))
    except Exception:
        pass


def _text_align() -> Qt.AlignmentFlag:
    """Force explicit absolute horizontal alignment so Latin text aligns flush-right in Arabic RTL."""
    if is_rtl():
        return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter
    return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignAbsolute | Qt.AlignmentFlag.AlignVCenter


def ensure_on_screen(widget: QWidget) -> None:
    """Clamp widget size and position to an available monitor so it never spawns off-screen."""
    app = QApplication.instance()
    if app is None:
        return
    screen = widget.screen()
    if screen is None:
        screen = app.screenAt(widget.frameGeometry().center())
    if screen is None:
        screen = app.primaryScreen()
    if screen is None:
        return
    avail = screen.availableGeometry()
    max_w = max(420, int(avail.width() * 0.94))
    max_h = max(320, int(avail.height() * 0.92))
    w = min(widget.width(), max_w)
    h = min(widget.height(), max_h)
    if w != widget.width() or h != widget.height():
        widget.resize(w, h)
    geo = widget.frameGeometry()
    if not avail.intersects(geo) or geo.top() < avail.top() or geo.left() < avail.left():
        nx = avail.left() + max(0, (avail.width() - w) // 2)
        ny = avail.top() + max(0, (avail.height() - h) // 2)
        widget.move(nx, ny)
    else:
        nx = min(max(geo.left(), avail.left()), max(avail.left(), avail.right() - w + 1))
        ny = min(max(geo.top(), avail.top()), max(avail.top(), avail.bottom() - h + 1))
        if nx != geo.left() or ny != geo.top():
            widget.move(nx, ny)


def make_app_icon() -> QIcon:
    ico = paths.bundled_asset("clipmic.ico")
    if ico.is_file():
        icon = QIcon(str(ico))
        if not icon.isNull():
            return icon
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#2563EB"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setBrush(QColor("#38BDF8"))
    for idx, h in enumerate((16, 30, 38, 24, 14)):
        x = 14 + idx * 8
        y = (64 - h) // 2
        p.drawRoundedRect(x, y, 5, h, 2, 2)
    p.end()
    return QIcon(pm)


class HotkeyCaptureButton(QPushButton):
    """Click and press a key combo to assign a global hotkey."""

    hotkey_changed = Signal(str)

    def __init__(self, initial: str = "", allow_empty: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._hotkey = initial.strip()
        self._allow_empty = allow_empty
        self._capturing = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._start_capture)
        self._refresh_text()

    def hotkey(self) -> str:
        return self._hotkey

    def set_hotkey(self, hk: str) -> None:
        self._hotkey = hk.strip()
        self._capturing = False
        self._refresh_text()

    def _refresh_text(self) -> None:
        if self._capturing:
            self.setText(t("hotkey.press"))
        elif self._hotkey:
            self.setText(ltr(self._hotkey))
        else:
            self.setText(t("hotkey.set"))

    def retranslate_ui(self) -> None:
        self._refresh_text()

    def _start_capture(self) -> None:
        self._capturing = True
        self._refresh_text()
        self.setFocus()

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._capturing = False
            self._refresh_text()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            return

        if key == Qt.Key.Key_Escape:
            self._capturing = False
            self._refresh_text()
            return

        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete) and self._allow_empty:
            self._hotkey = ""
            self._capturing = False
            self._refresh_text()
            self.hotkey_changed.emit("")
            return

        mods = event.modifiers()
        parts: list[str] = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")

        key_name = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)
        if not key_name:
            return
        shifted_map = {
            "!": "1", "@": "2", "#": "3", "$": "4", "%": "5",
            "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
            "_": "-", "+": "=",
        }
        key_name = shifted_map.get(key_name, key_name).upper()
        if (mods & Qt.KeyboardModifier.KeypadModifier) and len(key_name) == 1 and "0" <= key_name <= "9":
            key_name = f"NUMPAD{key_name}"
        parts.append(key_name)
        candidate = "+".join(parts)

        if parse_hotkey(candidate) is None:
            return

        self._hotkey = candidate
        self._capturing = False
        self._refresh_text()
        self.hotkey_changed.emit(candidate)


class WaveformWidget(QWidget):
    """Interactive HiDPI audio waveform with cached peak pixmaps, trim handles, and draggable playhead."""

    range_changed = Signal(float, float)
    playhead_scrubbed = Signal(float)
    playhead_scrub_finished = Signal(float)

    _BG_COLOR = QColor("#0F0F12")
    _BORDER_PEN = QPen(QColor("#26262C"), 1)
    _BASE_PEN = QPen(QColor("#222228"), 1)
    _BAR_DIM = QColor("#3A3A44")
    _BAR_LIT = QColor("#E4E4E7")
    _SEL_FILL = QColor(244, 244, 245, 22)
    _START_COLOR = QColor("#66A686")
    _END_COLOR = QColor("#C2A878")
    _START_PEN = QPen(_START_COLOR, 2)
    _END_PEN = QPen(_END_COLOR, 2)
    _PLAYHEAD_COLOR = QColor("#FFFFFF")
    _PLAYHEAD_PEN = QPen(_PLAYHEAD_COLOR, 2)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setMinimumHeight(120)
        self.setMouseTracking(True)
        self._peaks = np.zeros(220, dtype=np.float32)
        self._duration = 0.0
        self._start_s = 0.0
        self._end_s = 0.0
        self._playhead_s: Optional[float] = None
        self._dragging: Optional[str] = None
        self._pad_x = 14
        self._cache_dim: Optional[QPixmap] = None
        self._cache_lit: Optional[QPixmap] = None
        self._cache_key: Optional[tuple[int, int, int]] = None

    def set_playhead(self, sec: Optional[float]) -> None:
        self._playhead_s = sec
        self.update()

    def playhead(self) -> Optional[float]:
        return self._playhead_s

    def is_scrubbing(self) -> bool:
        return self._dragging == "playhead"

    def _invalidate_cache(self) -> None:
        self._cache_dim = None
        self._cache_lit = None
        self._cache_key = None

    def release_cache(self) -> None:
        self._invalidate_cache()

    def set_audio(self, peaks: np.ndarray, duration_s: float) -> None:
        self._peaks = np.asarray(peaks, dtype=np.float32)
        self._duration = max(0.01, float(duration_s))
        self._start_s = 0.0
        self._end_s = self._duration
        self._invalidate_cache()
        self.update()
        self.range_changed.emit(self._start_s, self._end_s)

    def _ensure_peak_cache(self) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        dpr = max(1.0, float(self.devicePixelRatioF()))
        key = (w, h, int(round(dpr * 100)))
        if self._cache_key == key and self._cache_dim is not None and self._cache_lit is not None:
            return

        pw = max(1, int(round(w * dpr)))
        ph = max(1, int(round(h * dpr)))

        dim_pm = QPixmap(pw, ph)
        dim_pm.setDevicePixelRatio(dpr)
        dim_pm.fill(Qt.GlobalColor.transparent)

        lit_pm = QPixmap(pw, ph)
        lit_pm.setDevicePixelRatio(dpr)
        lit_pm.fill(Qt.GlobalColor.transparent)

        usable_w = max(1, w - 2 * self._pad_x)
        mid_y = h / 2.0

        p_dim = QPainter(dim_pm)
        p_dim.setRenderHint(QPainter.RenderHint.Antialiasing)
        p_dim.setBrush(self._BG_COLOR)
        p_dim.setPen(self._BORDER_PEN)
        p_dim.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 8.0, 8.0)
        p_dim.setPen(self._BASE_PEN)
        p_dim.drawLine(self._pad_x, int(round(mid_y)), w - self._pad_x, int(round(mid_y)))

        p_lit = QPainter(lit_pm)
        p_lit.setRenderHint(QPainter.RenderHint.Antialiasing)

        n = len(self._peaks)
        if n > 0:
            step = usable_w / n
            bar_w = max(1.5, step * 0.65)
            max_amp = max(4.0, (h - 24) / 2.0)
            p_dim.setBrush(self._BAR_DIM)
            p_dim.setPen(Qt.PenStyle.NoPen)
            p_lit.setBrush(self._BAR_LIT)
            p_lit.setPen(Qt.PenStyle.NoPen)
            for i in range(n):
                bx = self._pad_x + i * step
                bh = max(2.0, float(self._peaks[i]) * max_amp * 2.0)
                rect = QRectF(bx, mid_y - bh / 2.0, bar_w, bh)
                p_dim.drawRoundedRect(rect, 1.0, 1.0)
                p_lit.drawRoundedRect(rect, 1.0, 1.0)

        p_dim.end()
        p_lit.end()

        self._cache_dim = dim_pm
        self._cache_lit = lit_pm
        self._cache_key = key

    def set_range(self, start_s: float, end_s: float) -> None:
        self._start_s = max(0.0, min(start_s, self._duration - 0.1))
        self._end_s = max(self._start_s + 0.1, min(end_s, self._duration))
        if self._playhead_s is not None:
            self._playhead_s = max(self._start_s, min(self._end_s, self._playhead_s))
        self.update()
        self.range_changed.emit(self._start_s, self._end_s)

    def trim_range(self) -> tuple[float, float]:
        return self._start_s, self._end_s

    def _s_to_x(self, sec: float) -> float:
        usable_w = max(1, self.width() - 2 * self._pad_x)
        return self._pad_x + (sec / self._duration) * usable_w

    def _x_to_s(self, x: float) -> float:
        usable_w = max(1, self.width() - 2 * self._pad_x)
        frac = (x - self._pad_x) / usable_w
        return max(0.0, min(self._duration, frac * self._duration))

    def _scrub_to_x(self, x: float) -> None:
        sec = max(self._start_s, min(self._end_s, self._x_to_s(x)))
        self._playhead_s = sec
        self.update()
        self.playhead_scrubbed.emit(sec)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        sx = self._s_to_x(self._start_s)
        ex = self._s_to_x(self._end_s)
        # Start/End trim handle hit-testing always takes highest priority
        if abs(x - sx) <= 12 and abs(x - sx) <= abs(x - ex):
            self._dragging = "start"
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif abs(x - ex) <= 12:
            self._dragging = "end"
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self._dragging = "playhead"
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._scrub_to_x(x)

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self._dragging in ("start", "end"):
            self._move_handle(x)
            return
        if self._dragging == "playhead":
            self._scrub_to_x(x)
            return
        sx = self._s_to_x(self._start_s)
        ex = self._s_to_x(self._end_s)
        if abs(x - sx) <= 12 or abs(x - ex) <= 12:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._playhead_s is not None and abs(x - self._s_to_x(self._playhead_s)) <= 8:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        was = self._dragging
        self._dragging = None
        if was == "playhead":
            sec = self._playhead_s if self._playhead_s is not None else self._start_s
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.playhead_scrub_finished.emit(sec)

    def _move_handle(self, x: float) -> None:
        sec = self._x_to_s(x)
        min_gap = min(0.15, self._duration * 0.1)
        if self._dragging == "start":
            self._start_s = max(0.0, min(sec, self._end_s - min_gap))
        elif self._dragging == "end":
            self._end_s = min(self._duration, max(sec, self._start_s + min_gap))
        if self._playhead_s is not None:
            self._playhead_s = max(self._start_s, min(self._end_s, self._playhead_s))
        self.update()
        self.range_changed.emit(self._start_s, self._end_s)

    def paintEvent(self, event) -> None:
        self._ensure_peak_cache()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        h = self.height()

        if self._cache_dim is not None:
            p.drawPixmap(0, 0, self._cache_dim)

        sx = self._s_to_x(self._start_s)
        ex = self._s_to_x(self._end_s)
        sel_w = max(1.0, ex - sx)

        # Highlighted selection region + clipped lit waveform bars (zero per-bar redraw on drag)
        p.fillRect(QRectF(sx, 2, sel_w, h - 4), self._SEL_FILL)
        if self._cache_lit is not None:
            p.save()
            p.setClipRect(QRectF(sx, 0, sel_w, h))
            p.drawPixmap(0, 0, self._cache_lit)
            p.restore()

        # Start & End handles (matte muted sage & muted sand)
        for hx, pen, col in (
            (sx, self._START_PEN, self._START_COLOR),
            (ex, self._END_PEN, self._END_COLOR),
        ):
            p.setPen(pen)
            p.drawLine(int(round(hx)), 4, int(round(hx)), h - 4)
            p.setBrush(col)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(hx - 5, 4, 10, 16), 3, 3)

        # Moving playhead line
        if self._playhead_s is not None:
            px = self._s_to_x(max(0.0, min(self._duration, self._playhead_s)))
            p.setPen(self._PLAYHEAD_PEN)
            p.drawLine(int(round(px)), 3, int(round(px)), h - 3)
            p.setBrush(self._PLAYHEAD_COLOR)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(px - 3.5, h - 10, 7, 7))


class _RenderWorker(QObject):
    finished = Signal(str, str)  # wav_path, error

    def __init__(
        self,
        src: Path,
        dst: Path,
        preset: str,
        start_s: float,
        end_s: Optional[float],
        *,
        params: Optional[dict] = None,
        custom_filter: Optional[str] = None,
        effects_list: Optional[list[dict]] = None,
    ) -> None:
        super().__init__()
        self.src = src
        self.dst = dst
        self.preset = preset
        self.start_s = start_s
        self.end_s = end_s
        self.params = params
        self.custom_filter = custom_filter
        self.effects_list = effects_list

    def run(self) -> None:
        try:
            process_clip(
                self.src,
                self.dst,
                preset=self.preset,
                start_s=self.start_s,
                end_s=self.end_s,
                params=self.params,
                custom_filter=self.custom_filter,
                effects_list=self.effects_list,
            )
            self.finished.emit(str(self.dst), "")
        except Exception as exc:
            self.finished.emit("", str(exc))


def _describe_effect(effect: dict) -> str:
    """Short localized summary of an effect's settings for the Manage Effects list."""
    if effect.get("id") == "normal":
        return t("fx.desc_unmodified")
    custom = str(effect.get("custom_filter", "") or "").strip()
    if custom:
        chain = f"{custom[:46]}{'…' if len(custom) > 46 else ''}"
        return t("fx.desc_custom_ffmpeg", chain=ltr(chain))
    p = normalize_params(effect.get("params"))
    parts: list[str] = []
    if abs(p["pitch"] - 1.0) >= 0.01:
        parts.append(t("fx.desc_pitch", val=p["pitch"]))
    if abs(p["speed"] - 1.0) >= 0.01:
        parts.append(t("fx.desc_speed", val=p["speed"]))
    if p["wobble"] > 0:
        parts.append(t("fx.desc_wobble", val=p["wobble"]))
    if p["tone"] != "normal":
        tone_keys = {
            "feminine": "builder.tone_feminine",
            "nasal": "builder.tone_nasal",
            "bad_mic": "builder.tone_bad_mic",
            "underwater": "builder.tone_underwater",
        }
        tk = tone_keys.get(p["tone"])
        parts.append(t(tk) if tk else str(p["tone"]))
    if p["crunch"] > 0:
        parts.append(t("fx.desc_crunch", val=p["crunch"]))
    if p["echo"] != "none":
        echo_keys = {"room": "builder.echo_room", "cathedral": "builder.echo_cathedral"}
        ek = echo_keys.get(p["echo"])
        parts.append(t("fx.desc_echo", echo=t(ek) if ek else str(p["echo"]).title()))
    if p["robot"]:
        parts.append(t("fx.desc_robot"))
    if p["reverse"]:
        parts.append(t("fx.desc_reverse"))
    return " • ".join(parts) if parts else t("fx.desc_neutral")


class EffectBuilderDialog(QDialog):
    """Visual slider/toggle Effect Builder with live clip preview."""

    playback_done = Signal(int)

    _TONE_ITEMS = (
        ("builder.tone_normal", "normal"),
        ("builder.tone_feminine", "feminine"),
        ("builder.tone_nasal", "nasal"),
        ("builder.tone_bad_mic", "bad_mic"),
        ("builder.tone_underwater", "underwater"),
    )
    _ECHO_ITEMS = (
        ("builder.echo_none", "none"),
        ("builder.echo_room", "room"),
        ("builder.echo_cathedral", "cathedral"),
    )

    def __init__(
        self,
        injector: MicInjector,
        get_volumes: Callable[[], tuple[bool, float, float]],
        *,
        preview_wav: Optional[Path] = None,
        preview_range: tuple[float, Optional[float]] = (0.0, None),
        effect: Optional[dict] = None,
        duplicate: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._injector = injector
        self._get_volumes = get_volumes
        self._preview_wav = preview_wav if (preview_wav and preview_wav.is_file()) else None
        self._preview_range = preview_range
        self._editing_id: Optional[str] = None
        self._editing_builtin = False
        self.saved_effect: Optional[dict] = None

        self._tmp_dir = paths.temp_dir()
        self._created_temps: list[Path] = []
        self._sample_wav: Optional[Path] = None
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[_RenderWorker] = None

        if effect and not duplicate:
            self._editing_id = str(effect.get("id", "") or "") or None
            self._editing_builtin = bool(effect.get("builtin", False))
            title_txt = t("builder.title_edit")
            init_name = str(effect.get("name", ""))
            disp_name = localize_effect_name(self._editing_id, init_name)
            hdr_txt = f"{title_txt} — {disp_name}" if disp_name and disp_name != init_name else title_txt
        elif effect and duplicate:
            title_txt = t("builder.title_duplicate")
            src_disp = localize_effect_name(
                str(effect.get("id", "") or "") or None,
                str(effect.get("name", "Effect")),
            )
            init_name = t("builder.copy_suffix", name=src_disp)
            hdr_txt = title_txt
        else:
            title_txt = t("builder.title_new")
            init_name = ""
            hdr_txt = title_txt

        init_params = normalize_params(effect.get("params") if effect else None)
        init_custom = str(effect.get("custom_filter", "") or "") if effect else ""

        self.setWindowTitle(t("builder.window_title", title=title_txt))
        self.setWindowIcon(make_app_icon())
        self.resize(470, 430)
        self.setMinimumWidth(420)
        self.setStyleSheet(DARK_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hdr = QLabel(hdr_txt)
        hdr.setStyleSheet("font-size: 15px; font-weight: 700; color: #F9FAFB;")
        root.addWidget(hdr)

        # Name input
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        name_row.addWidget(QLabel(t("builder.name_label")))
        self._name_edit = QLineEdit(init_name)
        self._name_edit.setPlaceholderText(t("builder.name_placeholder"))
        name_row.addWidget(self._name_edit, 1)
        root.addLayout(name_row)

        # Sliders & toggles card
        card = QFrame()
        card.setObjectName("Card")
        c_lay = QVBoxLayout(card)
        c_lay.setContentsMargins(12, 12, 12, 12)
        c_lay.setSpacing(10)

        def _make_slider_row(
            label: str,
            lo: int,
            hi: int,
            val: int,
            fmt: Callable[[int], str],
            tip: str,
        ) -> tuple[QSlider, QLabel]:
            row = QHBoxLayout()
            row.setSpacing(8)
            lbl = QLabel(label)
            lbl.setMinimumWidth(68)
            lbl.setStyleSheet("color: #D4D4D8;")
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(val)
            sl.setToolTip(tip)
            val_lbl = QLabel(ltr(fmt(val)))
            val_lbl.setMinimumWidth(48)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            val_lbl.setStyleSheet("color: #A1A1AA; font-family: Consolas, monospace; font-size: 12px;")
            sl.valueChanged.connect(lambda v, f=fmt, out=val_lbl: out.setText(ltr(f(v))))
            row.addWidget(lbl)
            row.addWidget(sl, 1)
            row.addWidget(val_lbl)
            c_lay.addLayout(row)
            return sl, val_lbl

        self._pitch_sl, _ = _make_slider_row(
            t("builder.pitch_label"),
            60,
            180,
            int(round(init_params["pitch"] * 100)),
            lambda v: f"{v / 100.0:.2f}x",
            t("builder.pitch_tooltip"),
        )
        self._speed_sl, _ = _make_slider_row(
            t("builder.speed_label"),
            55,
            180,
            int(round(init_params["speed"] * 100)),
            lambda v: f"{v / 100.0:.2f}x",
            t("builder.speed_tooltip"),
        )
        self._wobble_sl, _ = _make_slider_row(
            t("builder.wobble_label"),
            0,
            100,
            int(init_params["wobble"]),
            lambda v: f"{v}%",
            t("builder.wobble_tooltip"),
        )
        self._crunch_sl, _ = _make_slider_row(
            t("builder.crunch_label"),
            0,
            100,
            int(init_params["crunch"]),
            lambda v: f"{v}%",
            t("builder.crunch_tooltip"),
        )

        # Tone & Echo dropdowns
        drop_row = QHBoxLayout()
        drop_row.setSpacing(10)

        self._tone_combo = QComboBox()
        self._tone_combo.setToolTip(t("builder.tone_tooltip"))
        for label_key, key in self._TONE_ITEMS:
            self._tone_combo.addItem(t(label_key), key)
        tidx = self._tone_combo.findData(init_params["tone"])
        if tidx >= 0:
            self._tone_combo.setCurrentIndex(tidx)

        self._echo_combo = QComboBox()
        self._echo_combo.setToolTip(t("builder.echo_tooltip"))
        for label_key, key in self._ECHO_ITEMS:
            self._echo_combo.addItem(t(label_key), key)
        eidx = self._echo_combo.findData(init_params["echo"])
        if eidx >= 0:
            self._echo_combo.setCurrentIndex(eidx)

        drop_row.addWidget(QLabel(t("builder.tone_label")))
        drop_row.addWidget(self._tone_combo, 1)
        drop_row.addSpacing(6)
        drop_row.addWidget(QLabel(t("builder.echo_label")))
        drop_row.addWidget(self._echo_combo, 1)
        c_lay.addLayout(drop_row)

        # Toggles
        tog_row = QHBoxLayout()
        tog_row.setSpacing(16)
        self._robot_chk = QCheckBox(t("builder.robot_chk"))
        self._robot_chk.setToolTip(t("builder.robot_tooltip"))
        self._robot_chk.setChecked(bool(init_params["robot"]))
        self._reverse_chk = QCheckBox(t("builder.reverse_chk"))
        self._reverse_chk.setToolTip(t("builder.reverse_tooltip"))
        self._reverse_chk.setChecked(bool(init_params["reverse"]))
        tog_row.addWidget(self._robot_chk)
        tog_row.addWidget(self._reverse_chk)
        tog_row.addStretch()
        c_lay.addLayout(tog_row)

        # Optional advanced FFmpeg filter override (collapsed by default)
        self._adv_chk = QCheckBox(t("builder.adv_chk"))
        self._adv_chk.setToolTip(t("builder.adv_tooltip"))
        self._adv_chk.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        self._custom_edit = QLineEdit(init_custom)
        self._custom_edit.setPlaceholderText(t("builder.adv_placeholder"))
        has_custom = bool(init_custom.strip())
        self._adv_chk.setChecked(has_custom)
        self._custom_edit.setVisible(has_custom)
        self._adv_chk.toggled.connect(self._on_adv_toggled)
        c_lay.addWidget(self._adv_chk)
        c_lay.addWidget(self._custom_edit)

        root.addWidget(card)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        root.addWidget(self._status_lbl)

        # Preview & Save bar
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self._preview_btn = QPushButton(t("common.preview"))
        self._preview_btn.setToolTip(
            t("builder.preview_clip_tooltip")
            if self._preview_wav
            else t("builder.preview_sample_tooltip")
        )
        self._preview_btn.clicked.connect(self._on_preview)

        self._stop_btn = QPushButton(t("common.stop"))
        self._stop_btn.setToolTip(t("builder.stop_tooltip"))
        self._stop_btn.clicked.connect(self._on_stop)

        self._reset_btn = QPushButton(t("common.reset"))
        self._reset_btn.setToolTip(t("builder.reset_tooltip"))
        self._reset_btn.clicked.connect(self._on_reset)

        cancel_btn = QPushButton(t("common.cancel"))
        cancel_btn.clicked.connect(self.reject)

        self._save_btn = QPushButton(t("builder.save_btn"))
        self._save_btn.setObjectName("Primary")
        self._save_btn.setToolTip(t("builder.save_tooltip"))
        self._save_btn.clicked.connect(self._on_save)

        btn_row.addWidget(self._preview_btn)
        btn_row.addWidget(self._stop_btn)
        btn_row.addWidget(self._reset_btn)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._save_btn)
        root.addLayout(btn_row)

        self._play_seq = 0
        self._preview_cancelled = False
        self.playback_done.connect(self._on_playback_done)

    def _on_playback_done(self, seq: int) -> None:
        if seq != self._play_seq:
            return
        self._status_lbl.setText("")

    def _on_adv_toggled(self, checked: bool) -> None:
        self._custom_edit.setVisible(bool(checked))
        if not checked:
            self._custom_edit.clear()
        self.adjustSize()
        ensure_on_screen(self)

    def _current_params(self) -> dict:
        return normalize_params(
            {
                "pitch": self._pitch_sl.value() / 100.0,
                "speed": self._speed_sl.value() / 100.0,
                "wobble": self._wobble_sl.value(),
                "echo": self._echo_combo.currentData(),
                "tone": self._tone_combo.currentData(),
                "crunch": self._crunch_sl.value(),
                "robot": self._robot_chk.isChecked(),
                "reverse": self._reverse_chk.isChecked(),
            }
        )

    def _current_custom_filter(self) -> str:
        if not self._adv_chk.isChecked():
            return ""
        return self._custom_edit.text().strip()

    def _on_reset(self) -> None:
        p = DEFAULT_PARAMS
        self._pitch_sl.setValue(int(round(p["pitch"] * 100)))
        self._speed_sl.setValue(int(round(p["speed"] * 100)))
        self._wobble_sl.setValue(int(p["wobble"]))
        self._crunch_sl.setValue(int(p["crunch"]))
        self._tone_combo.setCurrentIndex(0)
        self._echo_combo.setCurrentIndex(0)
        self._robot_chk.setChecked(False)
        self._reverse_chk.setChecked(False)
        self._adv_chk.setChecked(False)
        self._custom_edit.clear()

    def _get_preview_source(self) -> tuple[Path, float, Optional[float]]:
        if self._preview_wav and self._preview_wav.is_file():
            return self._preview_wav, self._preview_range[0], self._preview_range[1]
        if self._sample_wav is None or not self._sample_wav.is_file():
            self._sample_wav = self._tmp_dir / f"fx_sample_{uuid.uuid4().hex[:8]}.wav"
            make_preview_sample_wav(self._sample_wav)
            self._created_temps.append(self._sample_wav)
        return self._sample_wav, 0.0, None

    def _on_preview(self) -> None:
        if self._worker_thread and self._worker_thread.isRunning():
            return
        self._preview_cancelled = False
        self._injector.stop()
        src, start_s, end_s = self._get_preview_source()
        out_wav = self._tmp_dir / f"fx_prev_{uuid.uuid4().hex[:8]}.wav"
        self._created_temps.append(out_wav)
        self._preview_btn.setDisabled(True)
        self._save_btn.setDisabled(True)
        self._status_lbl.setText(t("builder.status_rendering"))

        thread = QThread(self)
        worker = _RenderWorker(
            src,
            out_wav,
            "Custom Preview",
            start_s,
            end_s,
            params=self._current_params(),
            custom_filter=self._current_custom_filter(),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_preview_rendered, Qt.ConnectionType.QueuedConnection)
        self._worker_thread = thread
        self._worker = worker
        thread.start()

    @Slot(str, str)
    def _on_preview_rendered(self, wav_str: str, err: str) -> None:
        thread = self._worker_thread
        if thread is not None:
            thread.quit()
            thread.wait(2000)
        self._worker_thread = None
        self._worker = None
        self._preview_btn.setDisabled(False)
        self._save_btn.setDisabled(False)
        if self._preview_cancelled:
            self._preview_cancelled = False
            return
        if err:
            self._status_lbl.setText("")
            QMessageBox.warning(self, t("builder.err_preview_title"), err)
            return
        _mon, mon_vol, _mic_vol = self._get_volumes()
        self._play_seq += 1
        seq = self._play_seq
        self._status_lbl.setText(t("builder.status_playing"))
        self._injector.play_wav(
            Path(wav_str),
            volume=mon_vol,
            send_to_mic=False,
            monitor_enabled=True,
            on_done=lambda s=seq: self.playback_done.emit(s),
        )

    def _on_stop(self) -> None:
        self._preview_cancelled = True
        self._play_seq += 1
        self._injector.stop()
        self._status_lbl.setText(t("common.stopped"))

    def _on_save(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, t("builder.err_name_required_title"), t("builder.err_name_required_body"))
            self._name_edit.setFocus()
            return
        if name.lower() == "normal":
            QMessageBox.warning(self, t("builder.err_reserved_title"), t("builder.err_reserved_body"))
            self._name_edit.setFocus()
            return

        payload = {
            "id": self._editing_id or "",
            "name": name,
            "builtin": self._editing_builtin,
            "params": self._current_params(),
            "custom_filter": self._current_custom_filter(),
        }
        try:
            saved, _all = upsert_effect(payload)
        except Exception as exc:
            QMessageBox.warning(self, t("builder.err_save_title"), str(exc))
            return
        self.saved_effect = saved
        self.accept()

    def _cleanup_temps(self) -> None:
        for p in self._created_temps:
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        self._created_temps.clear()
        self._sample_wav = None

    def _shutdown_worker(self) -> None:
        self._injector.stop()
        if self._worker_thread and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(1500)
        self._worker_thread = None
        self._worker = None
        self._cleanup_temps()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_dark_titlebar_applied", False):
            self._dark_titlebar_applied = True
            apply_dark_titlebar(self)

    def closeEvent(self, event) -> None:
        self._shutdown_worker()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._shutdown_worker()
        super().done(result)


class ManageEffectsDialog(QDialog):
    """Compact manager to create, edit, duplicate, delete, or restore voice effects."""

    effects_changed = Signal()

    def __init__(
        self,
        injector: MicInjector,
        get_volumes: Callable[[], tuple[bool, float, float]],
        *,
        preview_wav: Optional[Path] = None,
        preview_range: tuple[float, Optional[float]] = (0.0, None),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._injector = injector
        self._get_volumes = get_volumes
        self._preview_wav = preview_wav
        self._preview_range = preview_range
        self.last_saved_id: Optional[str] = None

        self.setWindowTitle(t("manage_fx.window_title"))
        self.setWindowIcon(make_app_icon())
        self.resize(520, 480)
        self.setMinimumSize(440, 360)
        self.setStyleSheet(DARK_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        hdr = QHBoxLayout()
        title = QLabel(t("manage_fx.title"))
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #F9FAFB;")
        new_btn = QPushButton(t("manage_fx.new_btn"))
        new_btn.setObjectName("Primary")
        new_btn.setToolTip(t("manage_fx.new_tooltip"))
        new_btn.clicked.connect(self._on_new)
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(new_btn)
        root.addLayout(hdr)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._host = QWidget()
        self._list_lay = QVBoxLayout(self._host)
        self._list_lay.setContentsMargins(0, 0, 4, 0)
        self._list_lay.setSpacing(6)
        self._list_lay.addStretch()
        self._scroll.setWidget(self._host)
        root.addWidget(self._scroll, 1)

        footer = QHBoxLayout()
        restore_btn = QPushButton(t("manage_fx.restore_btn"))
        restore_btn.setToolTip(t("manage_fx.restore_tooltip"))
        restore_btn.clicked.connect(self._on_restore_defaults)
        close_btn = QPushButton(t("common.done"))
        close_btn.clicked.connect(self.accept)
        footer.addWidget(restore_btn)
        footer.addStretch()
        footer.addWidget(close_btn)
        root.addLayout(footer)

        self._rebuild_list()

    def _rebuild_list(self) -> None:
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        effects_list = load_effects()
        insert_pos = 0

        active_hdr = QLabel(t("manage_fx.active_hdr", n=len(effects_list)))
        active_hdr.setAlignment(_text_align())
        active_hdr.setStyleSheet("color: #9CA3AF; font-size: 11px; font-weight: 700; padding: 2px 0;")
        self._list_lay.insertWidget(insert_pos, active_hdr)
        insert_pos += 1

        for fx in effects_list:
            card = QFrame()
            card.setObjectName("Card")
            row = QHBoxLayout(card)
            row.setContentsMargins(10, 8, 10, 8)
            row.setSpacing(8)

            info = QVBoxLayout()
            info.setSpacing(2)
            top_line = QHBoxLayout()
            top_line.setSpacing(6)
            disp_name = localize_effect_name(fx["id"], fx["name"])
            name_lbl = QLabel(disp_name)
            name_lbl.setAlignment(_text_align())
            name_lbl.setStyleSheet("font-weight: 700; font-size: 13px; color: #F4F4F5;")
            tag_txt = (
                t("manage_fx.tag_locked")
                if fx["id"] == "normal"
                else (t("manage_fx.tag_builtin") if fx.get("builtin") else t("manage_fx.tag_custom"))
            )
            tag_col = "#71717A" if fx.get("builtin") else "#89C2A4"
            tag_lbl = QLabel(tag_txt)
            tag_lbl.setStyleSheet(
                f"color: {tag_col}; background: #1B1B20; border: 1px solid #2A2A32; "
                "border-radius: 5px; padding: 1px 6px; font-size: 10px; font-weight: 600;"
            )
            top_line.addWidget(name_lbl)
            top_line.addWidget(tag_lbl)
            top_line.addStretch()

            desc_lbl = QLabel(_describe_effect(fx))
            desc_lbl.setAlignment(_text_align())
            desc_lbl.setStyleSheet("color: #9A9AA2; font-size: 11px;")
            info.addLayout(top_line)
            info.addWidget(desc_lbl)
            row.addLayout(info, 1)

            if fx["id"] != "normal":
                edit_btn = QPushButton(t("common.edit"))
                edit_btn.setToolTip(t("manage_fx.edit_tooltip"))
                edit_btn.clicked.connect(lambda _=False, e=fx: self._on_edit(e))
                dup_btn = QPushButton(t("common.copy"))
                dup_btn.setToolTip(t("manage_fx.copy_tooltip"))
                dup_btn.clicked.connect(lambda _=False, e=fx: self._on_duplicate(e))
                del_btn = QPushButton("✕")
                del_btn.setObjectName("Danger")
                del_btn.setMinimumWidth(32)
                del_btn.setToolTip(t("manage_fx.delete_tooltip", name=disp_name))
                del_btn.clicked.connect(lambda _=False, e=fx: self._on_delete(e))
                row.addWidget(edit_btn)
                row.addWidget(dup_btn)
                row.addWidget(del_btn)

            self._list_lay.insertWidget(insert_pos, card)
            insert_pos += 1

        extras = available_library_effects(effects_list)
        if extras:
            lib_hdr = QLabel(t("manage_fx.more_hdr", n=len(extras)))
            lib_hdr.setAlignment(_text_align())
            lib_hdr.setStyleSheet("color: #9CA3AF; font-size: 11px; font-weight: 700; padding: 10px 0 2px 0;")
            self._list_lay.insertWidget(insert_pos, lib_hdr)
            insert_pos += 1

            for fx in extras:
                card = QFrame()
                card.setObjectName("Card")
                row = QHBoxLayout(card)
                row.setContentsMargins(10, 8, 10, 8)
                row.setSpacing(8)

                info = QVBoxLayout()
                info.setSpacing(2)
                disp_name = localize_effect_name(fx["id"], fx["name"])
                name_lbl = QLabel(disp_name)
                name_lbl.setAlignment(_text_align())
                name_lbl.setStyleSheet("font-weight: 600; font-size: 13px; color: #D4D4D8;")
                desc_lbl = QLabel(_describe_effect(fx))
                desc_lbl.setAlignment(_text_align())
                desc_lbl.setStyleSheet("color: #8B8B96; font-size: 11px;")
                info.addWidget(name_lbl)
                info.addWidget(desc_lbl)
                row.addLayout(info, 1)

                add_btn = QPushButton(t("manage_fx.add_btn"))
                add_btn.setObjectName("Accent")
                add_btn.setToolTip(t("manage_fx.add_tooltip", name=disp_name))
                add_btn.clicked.connect(lambda _=False, eid=fx["id"]: self._on_add_library(eid))
                row.addWidget(add_btn)

                self._list_lay.insertWidget(insert_pos, card)
                insert_pos += 1

    def _on_add_library(self, effect_id: str) -> None:
        try:
            saved, _all = add_library_effect(effect_id)
        except Exception as exc:
            QMessageBox.warning(self, t("manage_fx.err_add_title"), str(exc))
            return
        self.last_saved_id = saved["id"]
        self._rebuild_list()
        self.effects_changed.emit()

    def _open_builder(self, effect: Optional[dict] = None, duplicate: bool = False) -> None:
        dlg = EffectBuilderDialog(
            self._injector,
            self._get_volumes,
            preview_wav=self._preview_wav,
            preview_range=self._preview_range,
            effect=effect,
            duplicate=duplicate,
            parent=self,
        )
        ensure_on_screen(dlg)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.saved_effect:
            self.last_saved_id = dlg.saved_effect["id"]
            self._rebuild_list()
            self.effects_changed.emit()

    def _on_new(self) -> None:
        self._open_builder(effect=None, duplicate=False)

    def _on_edit(self, fx: dict) -> None:
        self._open_builder(effect=fx, duplicate=False)

    def _on_duplicate(self, fx: dict) -> None:
        self._open_builder(effect=fx, duplicate=True)

    def _on_delete(self, fx: dict) -> None:
        disp_name = localize_effect_name(fx["id"], fx["name"])
        ans = QMessageBox.question(
            self,
            t("manage_fx.delete_confirm_title"),
            t("manage_fx.delete_confirm_body", name=disp_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_effect(fx["id"])
        except Exception as exc:
            QMessageBox.warning(self, t("manage_fx.err_delete_title"), str(exc))
            return
        self._rebuild_list()
        self.effects_changed.emit()

    def _on_restore_defaults(self) -> None:
        restore_default_effects()
        self._rebuild_list()
        self.effects_changed.emit()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_dark_titlebar_applied", False):
            self._dark_titlebar_applied = True
            apply_dark_titlebar(self)


class MiniWaveformWidget(QWidget):
    """Compact HiDPI mini-waveform for a channel row with interactive draggable playhead scrubbing."""

    seek_started = Signal(float)
    seek_moved = Signal(float)
    seek_released = Signal(float)

    _BG = QColor("#0F0F12")
    _BORDER = QPen(QColor("#26262C"), 1)
    _MID = QPen(QColor("#1E1E24"), 1)
    _BAR_ACTIVE = QColor("#D4D4D8")
    _BAR_MUTED = QColor("#4A4A54")
    _PLAYHEAD_COLOR = QColor("#FFFFFF")
    _PLAYHEAD = QPen(_PLAYHEAD_COLOR, 2)

    def __init__(self, peaks: np.ndarray, checked: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setFixedHeight(36)
        self.setMinimumWidth(160)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._peaks = np.asarray(peaks, dtype=np.float32)
        self._checked = bool(checked)
        self._play_frac: Optional[float] = None
        self._dragging = False
        self._pad_x = 6
        self._cache: Optional[QPixmap] = None
        self._cache_key: Optional[tuple[int, int, int, bool]] = None

    def set_checked_style(self, checked: bool) -> None:
        if self._checked != bool(checked):
            self._checked = bool(checked)
            self._cache = None
            self.update()

    def release_cache(self) -> None:
        self._cache = None
        self._cache_key = None

    def set_playhead_fraction(self, frac: Optional[float]) -> None:
        self._play_frac = frac
        self.update()

    def playhead_fraction(self) -> Optional[float]:
        return self._play_frac

    def is_scrubbing(self) -> bool:
        return self._dragging

    def _x_to_frac(self, x: float) -> float:
        usable_w = max(1, self.width() - 2 * self._pad_x)
        return max(0.0, min(1.0, (x - self._pad_x) / usable_w))

    def _frac_to_x(self, frac: float) -> float:
        usable_w = max(1, self.width() - 2 * self._pad_x)
        return self._pad_x + max(0.0, min(1.0, frac)) * usable_w

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        frac = self._x_to_frac(event.position().x())
        self._play_frac = frac
        self.update()
        self.seek_started.emit(frac)

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self._dragging:
            frac = self._x_to_frac(x)
            self._play_frac = frac
            self.update()
            self.seek_moved.emit(frac)
            return
        if self._play_frac is not None and abs(x - self._frac_to_x(self._play_frac)) <= 7:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        frac = self._x_to_frac(event.position().x())
        self._play_frac = frac
        self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.seek_released.emit(frac)

    def _ensure_cache(self) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        dpr = max(1.0, float(self.devicePixelRatioF()))
        key = (w, h, int(round(dpr * 100)), self._checked)
        if self._cache_key == key and self._cache is not None:
            return
        pm = QPixmap(max(1, int(round(w * dpr))), max(1, int(round(h * dpr))))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(self._BG)
        p.setPen(self._BORDER)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1.0, h - 1.0), 6.0, 6.0)
        mid_y = h / 2.0
        pad_x = self._pad_x
        p.setPen(self._MID)
        p.drawLine(pad_x, int(round(mid_y)), w - pad_x, int(round(mid_y)))
        n = len(self._peaks)
        usable_w = max(1, w - 2 * pad_x)
        if n > 0:
            step = usable_w / n
            bar_w = max(1.2, step * 0.65)
            max_amp = max(2.0, (h - 10) / 2.0)
            p.setBrush(self._BAR_ACTIVE if self._checked else self._BAR_MUTED)
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(n):
                bx = pad_x + i * step
                bh = max(2.0, float(self._peaks[i]) * max_amp * 2.0)
                p.drawRoundedRect(QRectF(bx, mid_y - bh / 2.0, bar_w, bh), 1.0, 1.0)
        p.end()
        self._cache = pm
        self._cache_key = key

    def paintEvent(self, event) -> None:
        self._ensure_cache()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._cache is not None:
            p.drawPixmap(0, 0, self._cache)
        if self._play_frac is not None:
            px = self._frac_to_x(self._play_frac)
            p.setPen(self._PLAYHEAD)
            p.drawLine(int(round(px)), 3, int(round(px)), self.height() - 3)
            p.setBrush(self._PLAYHEAD_COLOR)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(px - 3.0, self.height() - 8.0, 6.0, 6.0))


class ChannelPickerDialog(QDialog):
    """Fast F9 Channel Picker Dashboard: multi-select audible apps/mic, preview, and jump to Trim."""

    channels_confirmed = Signal(list)  # list[str] of selected channel keys
    preview_done = Signal(int)

    _BADGE_STYLES: dict[str, tuple[str, str]] = {
        "game": ("picker.badge_game", "background: #18231F; color: #89C2A4; border: 1px solid #2A4037;"),
        "app": ("picker.badge_app", "background: #1C1C22; color: #A1A1AA; border: 1px solid #2D2D36;"),
        "mic": ("picker.badge_mic", "background: #2E2417; color: #E2C08D; border: 1px solid #4F3D24;"),
        "system": ("picker.badge_system", "background: #172554; color: #93C5FD; border: 1px solid #1E3A8A;"),
    }

    def __init__(
        self,
        snapshot: CaptureSnapshot,
        injector: MicInjector,
        get_volumes: Callable[[], tuple[bool, float, float]],
        *,
        initial_selected: Optional[list[str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._snapshot = snapshot
        self._injector = injector
        self._get_volumes = get_volumes

        self.setWindowTitle(t("picker.window_title"))
        self.setWindowIcon(make_app_icon())
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setStyleSheet(DARK_QSS)

        self._rows: dict[str, tuple[QCheckBox, MiniWaveformWidget, QPushButton, ChannelClip]] = {}
        self._playing_key: Optional[str] = None
        self._play_seq = 0
        self._play_started_at = 0.0
        self._play_dur_s = 0.0
        self._last_scrub_ts = 0.0

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(33)
        self._play_timer.timeout.connect(self._tick_playhead)
        self.preview_done.connect(self._on_preview_done)

        valid_keys = {c.key for c in snapshot.channels}
        if initial_selected is not None:
            prechecked = [k for k in initial_selected if k in valid_keys]
            if not prechecked:
                prechecked = snapshot.default_selected_keys()
        else:
            prechecked = snapshot.default_selected_keys()
        prechecked_set = set(prechecked)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        title = QLabel(t("picker.title"))
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #F9FAFB;")
        n_ch = len(snapshot.channels)
        sub_txt = (
            t("picker.subtitle_one", dur=snapshot.duration_s)
            if n_ch == 1
            else t("picker.subtitle_many", dur=snapshot.duration_s, n=n_ch)
        )
        sub = QLabel(sub_txt)
        sub.setAlignment(_text_align())
        sub.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(sub)
        root.addLayout(hdr)

        # Channel list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        list_lay = QVBoxLayout(host)
        list_lay.setContentsMargins(0, 0, 4, 0)
        list_lay.setSpacing(8)

        for ch in snapshot.channels:
            is_checked = ch.key in prechecked_set
            card = QFrame()
            card.setObjectName("Card")
            row = QHBoxLayout(card)
            row.setContentsMargins(12, 8, 12, 8)
            row.setSpacing(10)

            chk = QCheckBox()
            chk.setChecked(is_checked)
            chk.setToolTip(t("picker.chk_tooltip", name=ch.name))

            name_col = QVBoxLayout()
            name_col.setSpacing(3)
            name_lbl = QLabel(ch.name)
            name_lbl.setAlignment(_text_align())
            name_lbl.setStyleSheet("font-weight: 700; font-size: 13px; color: #F4F4F5;")
            name_lbl.setMinimumWidth(112)
            name_lbl.setMaximumWidth(155)

            badge_key, badge_css = self._BADGE_STYLES.get(ch.kind, self._BADGE_STYLES["app"])
            badge = QLabel(t(badge_key))
            badge.setFixedWidth(52)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"{badge_css} border-radius: 5px; padding: 1px 6px; font-size: 10px; font-weight: 600;")
            name_col.addWidget(name_lbl)
            name_col.addWidget(badge)

            mini = MiniWaveformWidget(ch.mini_peaks, checked=is_checked)
            mini.setToolTip(t("picker.wave_tooltip"))
            mini.seek_started.connect(lambda f, k=ch.key: self._on_mini_seek_started(k, f))
            mini.seek_moved.connect(lambda f, k=ch.key: self._on_mini_seek_moved(k, f))
            mini.seek_released.connect(lambda f, k=ch.key: self._on_mini_seek_released(k, f))

            prev_btn = QPushButton(t("common.preview"))
            prev_btn.setFixedWidth(92)
            prev_btn.setToolTip(t("picker.preview_tooltip"))
            prev_btn.clicked.connect(lambda _=False, k=ch.key: self._toggle_preview(k))

            chk.toggled.connect(lambda state, m=mini: (m.set_checked_style(state), self._update_confirm_btn()))

            row.addWidget(chk)
            row.addLayout(name_col)
            row.addWidget(mini, 1)
            row.addWidget(prev_btn)
            list_lay.addWidget(card)

            self._rows[ch.key] = (chk, mini, prev_btn, ch)

        list_lay.addStretch()
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        # Footer
        footer = QHBoxLayout()
        footer.setSpacing(8)
        hint = QLabel(t("picker.hint"))
        hint.setAlignment(_text_align())
        hint.setStyleSheet("color: #71717A; font-size: 11px;")
        cancel_btn = QPushButton(t("common.cancel"))
        cancel_btn.clicked.connect(self.reject)
        self._confirm_btn = QPushButton()
        self._confirm_btn.setObjectName("Primary")
        self._confirm_btn.setToolTip(t("picker.confirm_tooltip"))
        self._confirm_btn.setDefault(True)
        self._confirm_btn.setAutoDefault(True)
        self._confirm_btn.setMinimumHeight(34)
        self._confirm_btn.clicked.connect(self._on_confirm)

        footer.addWidget(hint)
        footer.addStretch()
        footer.addWidget(cancel_btn)
        footer.addWidget(self._confirm_btn)
        root.addLayout(footer)

        est_h = min(560, max(210, 126 + n_ch * 68))
        self.resize(660, est_h)
        self.setMinimumWidth(520)
        self._update_confirm_btn()
        self._confirm_btn.setFocus()

    def selected_keys(self) -> list[str]:
        return [k for k, (chk, _m, _b, _c) in self._rows.items() if chk.isChecked()]

    def _update_confirm_btn(self) -> None:
        n = len(self.selected_keys())
        self._confirm_btn.setText(t("picker.confirm_btn", n=n))
        self._confirm_btn.setEnabled(n > 0)

    def _start_channel_preview(self, key: str, start_frac: float = 0.0, *, start_timer: bool = True) -> None:
        if self._playing_key and self._playing_key != key:
            self._reset_preview_ui()
        row = self._rows.get(key)
        if row is None:
            return
        _chk, mini, btn, ch = row
        _mon, mon_vol, _mic_vol = self._get_volumes()
        dur = max(0.05, len(ch.audio) / float(ch.sample_rate))
        frac = max(0.0, min(1.0, float(start_frac)))
        start_sec = min(max(0.0, dur - 0.02), frac * dur)
        self._play_seq += 1
        seq = self._play_seq
        self._playing_key = key
        self._play_dur_s = dur
        self._play_started_at = time.monotonic() - start_sec
        if btn.objectName() != "Danger":
            btn.setText(t("common.stop"))
            btn.setObjectName("Danger")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        mini.set_playhead_fraction(frac)
        if start_timer:
            self._play_timer.start()
        else:
            self._play_timer.stop()
        self._injector.play_array(
            ch.audio,
            ch.sample_rate,
            volume=mon_vol,
            send_to_mic=False,
            monitor_enabled=True,
            start_sec=start_sec,
            on_done=lambda s=seq: self.preview_done.emit(s),
        )

    def _toggle_preview(self, key: str) -> None:
        if self._playing_key == key:
            self.stop_preview()
            return
        self._start_channel_preview(key, 0.0, start_timer=True)

    def _on_mini_seek_started(self, key: str, frac: float) -> None:
        self._last_scrub_ts = time.monotonic()
        self._start_channel_preview(key, frac, start_timer=False)

    def _on_mini_seek_moved(self, key: str, frac: float) -> None:
        self._play_timer.stop()
        now = time.monotonic()
        if self._playing_key != key or (now - self._last_scrub_ts) >= 0.09:
            self._last_scrub_ts = now
            self._start_channel_preview(key, frac, start_timer=False)

    def _on_mini_seek_released(self, key: str, frac: float) -> None:
        self._last_scrub_ts = time.monotonic()
        self._start_channel_preview(key, frac, start_timer=True)

    def _tick_playhead(self) -> None:
        if not self._playing_key or self._playing_key not in self._rows:
            self._play_timer.stop()
            return
        _chk, mini, _btn, _ch = self._rows[self._playing_key]
        if mini.is_scrubbing():
            return
        elapsed = time.monotonic() - self._play_started_at
        frac = min(1.0, max(0.0, elapsed / self._play_dur_s)) if self._play_dur_s > 0 else 1.0
        mini.set_playhead_fraction(frac)
        if frac >= 1.0:
            self._play_timer.stop()

    def _reset_preview_ui(self) -> None:
        self._play_timer.stop()
        key = self._playing_key
        self._playing_key = None
        if key and key in self._rows:
            _chk, mini, btn, _ch = self._rows[key]
            mini.set_playhead_fraction(None)
            btn.setText(t("common.preview"))
            btn.setObjectName("")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def stop_preview(self) -> None:
        self._play_seq += 1
        self._injector.stop()
        self._reset_preview_ui()

    def _on_preview_done(self, seq: int) -> None:
        if seq != self._play_seq:
            return
        self._reset_preview_ui()

    def _on_confirm(self) -> None:
        keys = self.selected_keys()
        if not keys:
            return
        self.stop_preview()
        self.channels_confirmed.emit(keys)
        self.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self._confirm_btn.isEnabled():
                self._on_confirm()
                return
        super().keyPressEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_dark_titlebar_applied", False):
            self._dark_titlebar_applied = True
            apply_dark_titlebar(self)

    def _release_waveform_caches(self) -> None:
        for _chk, mini, _btn, _ch in self._rows.values():
            mini.release_cache()
        QPixmapCache.clear()
        gc.collect()

    def closeEvent(self, event) -> None:
        self.stop_preview()
        self._release_waveform_caches()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self.stop_preview()
        self._release_waveform_caches()
        super().done(result)


class TrimDialog(QDialog):
    """Instant capture / edit popup for trimming, previewing effects, sending to mic, and saving."""

    clip_saved = Signal(object)  # ClipEntry
    playback_done = Signal(int)

    def __init__(
        self,
        source_wav: Path,
        injector: MicInjector,
        get_volumes: Callable[[], tuple[bool, float, float]],
        existing_clip: Optional[ClipEntry] = None,
        on_change_channels: Optional[Callable[[], None]] = None,
        channel_summary: Optional[str] = None,
        channel_count: int = 0,
        default_name: Optional[str] = None,
        buffer_seconds: Optional[int] = None,
        import_name: Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("trim.window_title"))
        self.setWindowIcon(make_app_icon())
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(680, 440)
        self.setStyleSheet(DARK_QSS)

        self._source_wav = source_wav
        self._injector = injector
        self._get_volumes = get_volumes
        self._existing = existing_clip
        self._on_change_channels = on_change_channels
        self._channel_count = int(channel_count)

        self._tmp_dir = paths.temp_dir()
        self._created_temps: list[Path] = []
        self._cache_key: Optional[tuple[int, int, str, str]] = None
        self._rendered_wav: Optional[Path] = None
        self._rendered_dur_s: Optional[float] = None
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[_RenderWorker] = None
        self._pending_key: Optional[tuple[int, int, str, str]] = None
        self._pending_then: Optional[Callable[[Path], None]] = None
        self._queued_then: Optional[Callable[[Path], None]] = None
        self._effects: list[dict] = []
        self._current_effect_id = "normal"

        audio, sr = read_wav(source_wav)
        self._duration_s = max(0.1, len(audio) / float(sr))
        peaks = compute_waveform_peaks(audio, bins=220)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        self._change_channels_btn: Optional[QPushButton] = None
        if on_change_channels is not None:
            btn_txt = (
                t("trim.change_channels_count", n=self._channel_count)
                if self._channel_count > 0
                else t("trim.change_channels")
            )
            self._change_channels_btn = QPushButton(btn_txt)
            self._change_channels_btn.setObjectName("ChangeChannelsBtn")
            buf_hint = t("main.buffer_preset", n=int(buffer_seconds) if buffer_seconds else int(round(self._duration_s)))
            self._change_channels_btn.setToolTip(t("trim.change_channels_tooltip", dur=buf_hint))
            self._change_channels_btn.clicked.connect(self._handle_change_channels)
            hdr.addWidget(self._change_channels_btn)

        if existing_clip:
            title_txt = t("trim.title_edit")
        elif import_name:
            title_txt = t("trim.title_import", name=import_name)
        elif channel_summary:
            title_txt = t("trim.title_trim", summary=channel_summary)
        else:
            buf_lbl = int(buffer_seconds) if buffer_seconds else int(round(self._duration_s))
            title_txt = t("trim.title_capture", n=buf_lbl)
        title = QLabel(title_txt)
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #F9FAFB;")
        self._range_lbl = QLabel()
        self._range_lbl.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._range_lbl.setStyleSheet("color: #9CA3AF; font-family: Consolas, monospace;")
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(self._range_lbl)
        root.addLayout(hdr)

        # Waveform
        self._waveform = WaveformWidget()
        self._waveform.setToolTip(t("trim.waveform_tooltip"))
        self._waveform.range_changed.connect(self._on_range_changed)
        self._waveform.playhead_scrubbed.connect(self._on_waveform_scrubbed)
        self._waveform.playhead_scrub_finished.connect(self._on_waveform_scrub_finished)
        self._waveform.set_audio(peaks, self._duration_s)
        root.addWidget(self._waveform)

        # Voice Effect selector (clean dropdown + quick-picks + New / Manage)
        fx_hdr = QHBoxLayout()
        fx_hdr.setSpacing(8)
        fx_lbl = QLabel(t("trim.fx_section"))
        fx_lbl.setStyleSheet("color: #9CA3AF; font-size: 12px; font-weight: 600;")
        fx_hdr.addWidget(fx_lbl)
        fx_hdr.addStretch()
        root.addLayout(fx_hdr)

        fx_sel_row = QHBoxLayout()
        fx_sel_row.setSpacing(8)
        self._effect_combo = QComboBox()
        self._effect_combo.setMinimumWidth(220)
        self._effect_combo.view().setMinimumWidth(280)
        self._effect_combo.setToolTip(t("trim.fx_combo_tooltip"))
        self._effect_combo.currentIndexChanged.connect(self._on_effect_combo_changed)

        self._new_fx_btn = QPushButton(t("trim.new_fx_btn"))
        self._new_fx_btn.setToolTip(t("trim.new_fx_tooltip"))
        self._new_fx_btn.clicked.connect(self._on_new_effect)

        self._manage_fx_btn = QPushButton(t("trim.manage_fx_btn"))
        self._manage_fx_btn.setToolTip(t("trim.manage_fx_tooltip"))
        self._manage_fx_btn.clicked.connect(self._on_manage_effects)

        fx_sel_row.addWidget(self._effect_combo, 1)
        fx_sel_row.addWidget(self._new_fx_btn)
        fx_sel_row.addWidget(self._manage_fx_btn)
        root.addLayout(fx_sel_row)

        self._quick_row = QHBoxLayout()
        self._quick_row.setSpacing(6)
        self._preset_btns: dict[str, QPushButton] = {}
        root.addLayout(self._quick_row)

        # When editing an already-processed WAV, default to "normal" to avoid double-applying effects.
        self.reload_effects(select_id="normal")

        # Playback actions
        play_row = QHBoxLayout()
        self._play_btn = QPushButton(t("trim.play_preview_btn"))
        self._play_btn.setToolTip(t("trim.play_preview_tooltip"))
        self._play_btn.clicked.connect(self._on_play_preview)
        self._send_btn = QPushButton(t("trim.send_mic_btn"))
        self._send_btn.setObjectName("Accent")
        self._send_btn.setToolTip(t("trim.send_mic_tooltip"))
        self._send_btn.clicked.connect(self._on_send_to_mic)
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet("color: #9CA3AF; font-size: 12px;")

        play_row.addWidget(self._play_btn)
        play_row.addWidget(self._send_btn)
        play_row.addSpacing(8)
        play_row.addWidget(self._status_lbl, 1)
        root.addLayout(play_row)

        # Save bar
        save_card = QFrame()
        save_card.setObjectName("Card")
        save_lay = QHBoxLayout(save_card)
        save_lay.setContentsMargins(10, 10, 10, 10)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(t("trim.name_placeholder"))
        if existing_clip:
            self._name_edit.setText(existing_clip.name)
        elif default_name:
            self._name_edit.setText(default_name)
        else:
            self._name_edit.setText(f"Clip {time.strftime('%H-%M-%S')}")

        self._hotkey_btn = HotkeyCaptureButton(existing_clip.hotkey if existing_clip else "")
        self._hotkey_btn.setToolTip(t("trim.hotkey_tooltip"))
        self._save_btn = QPushButton(t("trim.save_btn"))
        self._save_btn.setObjectName("Primary")
        self._save_btn.setToolTip(t("trim.save_tooltip"))
        self._save_btn.clicked.connect(self._on_save)

        save_lay.addWidget(QLabel(t("trim.name_label")))
        save_lay.addWidget(self._name_edit, 1)
        save_lay.addWidget(QLabel(t("trim.hotkey_label")))
        save_lay.addWidget(self._hotkey_btn)
        save_lay.addWidget(self._save_btn)
        root.addWidget(save_card)

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(30)
        self._play_timer.timeout.connect(self._tick_playhead)
        self._play_started_at = 0.0
        self._play_start_s = 0.0
        self._play_end_s = 0.0
        self._play_dur_s = 0.0
        self._play_seq = 0
        self._is_playing = False
        self._is_preview_playing = False
        self._scrub_was_playing = False
        self._last_scrub_seek_ts = 0.0
        self.playback_done.connect(self._on_playback_done)

    def _handle_change_channels(self) -> None:
        self._on_stop()
        if self._on_change_channels is not None:
            self._on_change_channels()

    def _on_range_changed(self, start_s: float, end_s: float) -> None:
        dur = max(0.0, end_s - start_s)
        self._range_lbl.setText(ltr(t("trim.range_label", start=start_s, end=end_s, dur=dur)))

    def _current_effect(self) -> dict:
        for fx in self._effects:
            if fx["id"] == self._current_effect_id:
                return fx
        return self._effects[0]

    def reload_effects(self, select_id: Optional[str] = None) -> None:
        """Reload effects.json and refresh dropdown + quick-pick pills live."""
        self._effects = load_effects()
        target_id = select_id if select_id is not None else self._current_effect_id
        if not any(fx["id"] == target_id for fx in self._effects):
            target_id = "normal"
        self._current_effect_id = target_id

        self._effect_combo.blockSignals(True)
        self._effect_combo.clear()
        for fx in self._effects:
            self._effect_combo.addItem(localize_effect_name(fx["id"], fx["name"]), fx["id"])
        cidx = self._effect_combo.findData(target_id)
        self._effect_combo.setCurrentIndex(cidx if cidx >= 0 else 0)
        self._effect_combo.blockSignals(False)

        while self._quick_row.count():
            item = self._quick_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._preset_btns.clear()

        by_id = {fx["id"]: fx for fx in self._effects}
        picks: list[dict] = [by_id[eid] for eid in QUICK_PICK_IDS if eid in by_id]
        for fx in self._effects:
            if len(picks) >= 5:
                break
            if fx not in picks:
                picks.append(fx)
        for fx in picks[:5]:
            btn = QPushButton(localize_effect_name(fx["id"], fx["name"]))
            btn.setObjectName("PresetBtn")
            btn.setCheckable(True)
            btn.setChecked(fx["id"] == self._current_effect_id)
            btn.clicked.connect(lambda _=False, eid=fx["id"]: self._select_effect(eid))
            self._preset_btns[fx["id"]] = btn
            self._quick_row.addWidget(btn)
        self._quick_row.addStretch()

    def _select_effect(self, effect_id: str) -> None:
        self._current_effect_id = effect_id
        self._effect_combo.blockSignals(True)
        idx = self._effect_combo.findData(effect_id)
        if idx >= 0:
            self._effect_combo.setCurrentIndex(idx)
        self._effect_combo.blockSignals(False)
        for eid, btn in self._preset_btns.items():
            btn.setChecked(eid == effect_id)
        self._restart_preview()

    def _on_effect_combo_changed(self) -> None:
        eid = self._effect_combo.currentData()
        if eid:
            self._select_effect(str(eid))

    def _on_new_effect(self) -> None:
        self._on_stop()
        s, e = self._waveform.trim_range()
        dlg = EffectBuilderDialog(
            self._injector,
            self._get_volumes,
            preview_wav=self._source_wav,
            preview_range=(s, e),
            parent=self,
        )
        ensure_on_screen(dlg)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.saved_effect:
            self.reload_effects(select_id=dlg.saved_effect["id"])

    def _on_manage_effects(self) -> None:
        self._on_stop()
        s, e = self._waveform.trim_range()
        dlg = ManageEffectsDialog(
            self._injector,
            self._get_volumes,
            preview_wav=self._source_wav,
            preview_range=(s, e),
            parent=self,
        )
        dlg.effects_changed.connect(lambda: self.reload_effects(select_id=dlg.last_saved_id))
        ensure_on_screen(dlg)
        dlg.exec()
        self.reload_effects(select_id=dlg.last_saved_id)

    def _current_key(self) -> tuple[int, int, str, str]:
        s, e = self._waveform.trim_range()
        fx = self._current_effect()
        sig = resolve_effect_filter(fx, 48000) or ""
        return (int(round(s * 1000)), int(round(e * 1000)), fx["id"], sig)

    def _ensure_rendered(self, then: Callable[[Path], None]) -> None:
        key = self._current_key()
        if key == self._cache_key and self._rendered_wav and self._rendered_wav.is_file():
            then(self._rendered_wav)
            return
        if self._worker_thread and self._worker_thread.isRunning():
            self._queued_then = then
            return

        s, e = self._waveform.trim_range()
        out_wav = self._tmp_dir / f"preview_{uuid.uuid4().hex[:8]}.wav"
        self._created_temps.append(out_wav)
        self._set_busy(True, t("trim.status_processing"))

        self._pending_key = key
        self._pending_then = then

        thread = QThread(self)
        worker = _RenderWorker(
            self._source_wav,
            out_wav,
            self._current_effect_id,
            s,
            e,
            effects_list=self._effects,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_render_finished, Qt.ConnectionType.QueuedConnection)
        self._worker_thread = thread
        self._worker = worker
        thread.start()

    @Slot(str, str)
    def _on_render_finished(self, path_str: str, err: str) -> None:
        thread = self._worker_thread
        if thread is not None:
            thread.quit()
            thread.wait(2000)
        self._worker_thread = None
        self._worker = None
        then = self._pending_then
        key = self._pending_key
        queued = self._queued_then
        self._pending_then = None
        self._pending_key = None
        self._queued_then = None
        self._set_busy(False, "")
        if err:
            if queued is not None:
                self._ensure_rendered(queued)
                return
            QMessageBox.warning(self, t("trim.err_ffmpeg_title"), err)
            return
        self._cache_key = key
        prev_wav = self._rendered_wav
        self._rendered_wav = Path(path_str)
        self._rendered_dur_s = None
        if prev_wav and prev_wav != self._rendered_wav and prev_wav.is_file():
            try:
                prev_wav.unlink()
            except OSError:
                pass
        if queued is not None:
            self._ensure_rendered(queued)
            return
        if then is not None:
            then(self._rendered_wav)

    def _set_busy(self, busy: bool, msg: str) -> None:
        self._play_btn.setDisabled(busy)
        self._send_btn.setDisabled(busy)
        self._save_btn.setDisabled(busy)
        self._new_fx_btn.setDisabled(busy)
        self._manage_fx_btn.setDisabled(busy)
        self._status_lbl.setText(msg)

    def _wav_duration_s(self, wav: Path) -> float:
        if wav == self._rendered_wav and self._rendered_dur_s is not None:
            return self._rendered_dur_s
        start_s, end_s = self._waveform.trim_range()
        try:
            audio, sr = read_wav(wav)
            dur = max(0.05, len(audio) / float(sr))
        except Exception:
            dur = max(0.05, end_s - start_s)
        if wav == self._rendered_wav:
            self._rendered_dur_s = dur
        return dur

    def _map_waveform_sec_to_rendered_offset(self, sec: float, rendered_dur: float) -> float:
        start_s, end_s = self._waveform.trim_range()
        span = max(1e-6, end_s - start_s)
        clamped = max(start_s, min(end_s, float(sec)))
        frac = (clamped - start_s) / span
        return max(0.0, min(max(0.0, rendered_dur - 0.02), frac * rendered_dur))

    def _start_playhead(self, wav: Path, *, offset_s: float = 0.0, start_timer: bool = True) -> None:
        start_s, end_s = self._waveform.trim_range()
        dur = self._wav_duration_s(wav)
        clamped_off = max(0.0, min(dur, float(offset_s)))
        frac = clamped_off / dur if dur > 0 else 0.0
        self._play_start_s = start_s
        self._play_end_s = end_s
        self._play_dur_s = dur
        self._play_started_at = time.monotonic() - clamped_off
        self._waveform.set_playhead(start_s + frac * (end_s - start_s))
        if start_timer:
            self._play_timer.start()
        else:
            self._play_timer.stop()

    def _tick_playhead(self) -> None:
        if self._waveform.is_scrubbing():
            return
        elapsed = time.monotonic() - self._play_started_at
        frac = min(1.0, max(0.0, elapsed / self._play_dur_s)) if self._play_dur_s > 0 else 1.0
        pos = self._play_start_s + frac * (self._play_end_s - self._play_start_s)
        self._waveform.set_playhead(pos)
        if frac >= 1.0:
            self._play_timer.stop()

    def _stop_playhead(self) -> None:
        self._play_timer.stop()
        self._waveform.set_playhead(None)

    def _sync_play_button(self) -> None:
        if self._is_playing:
            self._play_btn.setText(t("common.stop"))
            self._play_btn.setToolTip(t("trim.stop_tooltip"))
            self._play_btn.setObjectName("Danger")
        else:
            self._play_btn.setText(t("trim.play_preview_btn"))
            self._play_btn.setToolTip(t("trim.play_preview_tooltip"))
            self._play_btn.setObjectName("")
        self._play_btn.style().unpolish(self._play_btn)
        self._play_btn.style().polish(self._play_btn)

    def _on_playback_done(self, seq: int) -> None:
        if seq != self._play_seq:
            return
        self._is_playing = False
        self._is_preview_playing = False
        self._stop_playhead()
        self._sync_play_button()
        self._status_lbl.setText("")

    def _play_preview_from(self, target_sec: float, *, start_timer: bool = True) -> None:
        def play(wav: Path) -> None:
            _mon, mon_vol, _mic_vol = self._get_volumes()
            dur = self._wav_duration_s(wav)
            offset = self._map_waveform_sec_to_rendered_offset(target_sec, dur)
            self._play_seq += 1
            seq = self._play_seq
            self._is_playing = True
            self._is_preview_playing = True
            self._sync_play_button()
            self._status_lbl.setText(t("trim.status_playing"))
            self._start_playhead(wav, offset_s=offset, start_timer=start_timer)
            self._injector.play_wav(
                wav,
                volume=mon_vol,
                send_to_mic=False,
                monitor_enabled=True,
                start_sec=offset,
                on_done=lambda s=seq: self.playback_done.emit(s),
            )

        self._ensure_rendered(play)

    def _restart_preview(self) -> None:
        if self._is_playing:
            self._play_seq += 1
            self._is_playing = False
            self._is_preview_playing = False
            self._scrub_was_playing = False
            self._injector.stop()
            self._stop_playhead()
            self._sync_play_button()
        start_s, _end_s = self._waveform.trim_range()
        self._play_preview_from(start_s, start_timer=True)

    def _on_waveform_scrubbed(self, sec: float) -> None:
        self._play_timer.stop()
        if self._is_preview_playing:
            self._scrub_was_playing = True
        self._status_lbl.setText(t("trim.status_scrubbing", sec=sec))
        if self._scrub_was_playing and self._current_key() == self._cache_key and self._rendered_wav and self._rendered_wav.is_file():
            now = time.monotonic()
            if (now - self._last_scrub_seek_ts) >= 0.09:
                self._last_scrub_seek_ts = now
                self._play_preview_from(sec, start_timer=False)

    def _on_waveform_scrub_finished(self, sec: float) -> None:
        self._scrub_was_playing = False
        self._last_scrub_seek_ts = time.monotonic()
        self._play_preview_from(sec, start_timer=True)

    def _on_play_preview(self) -> None:
        if self._is_playing:
            self._on_stop()
            return
        start_s, _end_s = self._waveform.trim_range()
        self._play_preview_from(start_s, start_timer=True)

    def _on_send_to_mic(self) -> None:
        def send(wav: Path) -> None:
            mon, mon_vol, mic_vol = self._get_volumes()
            mode = t("trim.status_sending") if self._injector.apo_installed else t("trim.status_speaker_mode")
            self._play_seq += 1
            seq = self._play_seq
            self._is_playing = True
            self._is_preview_playing = False
            self._sync_play_button()
            self._status_lbl.setText(mode)
            self._start_playhead(wav)
            self._injector.play_wav(
                wav,
                volume=mon_vol,
                mic_volume=mic_vol,
                send_to_mic=True,
                monitor_enabled=mon,
                on_done=lambda s=seq: self.playback_done.emit(s),
            )

        self._ensure_rendered(send)

    def _on_stop(self) -> None:
        self._play_seq += 1
        self._pending_then = None
        self._pending_key = None
        self._queued_then = None
        self._is_playing = False
        self._is_preview_playing = False
        self._scrub_was_playing = False
        self._injector.stop()
        self._stop_playhead()
        self._sync_play_button()
        self._status_lbl.setText(t("common.stopped"))

    def _on_save(self) -> None:
        name = self._name_edit.text().strip() or t("trim.untitled_clip")

        def save_rendered(wav: Path) -> None:
            clips_root = paths.clips_dir()
            clip_id = self._existing.id if self._existing else uuid.uuid4().hex[:8]
            safe_slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "clip"
            dest = clips_root / f"{safe_slug[:28]}_{clip_id}.wav"
            dest.write_bytes(wav.read_bytes())
            if self._existing:
                old_path = resolve_clip_path(self._existing.file)
                if old_path != dest and old_path.is_file():
                    try:
                        old_path.unlink()
                    except OSError:
                        pass

            audio, sr = read_wav(dest)
            dur = len(audio) / float(sr)
            fx_name = self._current_effect()["name"]
            preset_label = (
                self._existing.preset
                if self._existing and self._current_effect_id == "normal"
                else fx_name
            )
            entry = ClipEntry(
                id=clip_id,
                name=name,
                file=str(dest.relative_to(paths.user_data_dir())),
                hotkey=self._hotkey_btn.hotkey(),
                preset=preset_label,
                duration=round(dur, 2),
            )
            self.clip_saved.emit(entry)
            self.accept()

        self._ensure_rendered(save_rendered)

    def _cleanup_temp_previews(self) -> None:
        for p in self._created_temps:
            if p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
        self._created_temps.clear()
        self._rendered_wav = None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not getattr(self, "_dark_titlebar_applied", False):
            self._dark_titlebar_applied = True
            apply_dark_titlebar(self)

    def _release_waveform_cache(self) -> None:
        if hasattr(self, "_waveform") and self._waveform is not None:
            self._waveform.release_cache()
        QPixmapCache.clear()
        gc.collect()

    def closeEvent(self, event) -> None:
        self._stop_playhead()
        self._injector.stop()
        if self._worker_thread and self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(1000)
        self._cleanup_temp_previews()
        self._release_waveform_cache()
        super().closeEvent(event)

    def done(self, result: int) -> None:
        self._stop_playhead()
        self._injector.stop()
        self._cleanup_temp_previews()
        self._release_waveform_cache()
        super().done(result)


class _DecodeWorker(QObject):
    """Background FFmpeg decoder for drag-and-drop / browsed audio files."""

    finished = Signal(str, str, str)  # (wav_path, display_name, error)

    def __init__(self, src_path: Path, dst_wav: Path) -> None:
        super().__init__()
        self.src_path = src_path
        self.dst_wav = dst_wav

    def run(self) -> None:
        try:
            out = decode_audio_to_wav(self.src_path, self.dst_wav)
            self.finished.emit(str(out), self.src_path.name, "")
        except Exception as exc:
            self.finished.emit("", self.src_path.name, str(exc))


def _first_supported_media_url(mime_data) -> Optional[str]:
    """Return the first local file path in mime_data with a supported audio or video extension."""
    if not mime_data or not mime_data.hasUrls():
        return None
    for url in mime_data.urls():
        if url.isLocalFile():
            local_path = url.toLocalFile()
            if Path(local_path).suffix.lower() in SUPPORTED_MEDIA_EXTS:
                return local_path
    return None


class _ExportWorker(QThread):
    """Background FFmpeg WAV-to-MP3 exporter."""

    export_finished = Signal(str, str)  # (dst_mp3_path, error_message)

    def __init__(
        self,
        src_wav: Path,
        dst_mp3: Path,
        title: Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.src_wav = Path(src_wav)
        self.dst_mp3 = Path(dst_mp3)
        self.title = title

    def run(self) -> None:
        try:
            out_path = export_wav_to_mp3(self.src_wav, self.dst_mp3, title=self.title)
            self.export_finished.emit(out_path, "")
        except Exception as exc:
            self.export_finished.emit(str(self.dst_mp3), str(exc))


class _ApoProcessWorker(QThread):
    """Waits on the elevated APO setup process handle and reads its log on exit."""

    finished_apo = Signal(int, str)  # (exit_code, log_text)

    def __init__(self, h_process: int, log_path: Path, parent=None) -> None:
        super().__init__(parent)
        self._h_process = int(h_process)
        self._log_path = Path(log_path)

    def run(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        kernel32.WaitForSingleObject.restype = wt.DWORD
        kernel32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        kernel32.GetExitCodeProcess.restype = wt.BOOL
        kernel32.CloseHandle.argtypes = [wt.HANDLE]
        kernel32.CloseHandle.restype = wt.BOOL

        exit_code = -1
        handle = wt.HANDLE(self._h_process)
        try:
            if kernel32.WaitForSingleObject(handle, 60000) == 0:
                code = wt.DWORD()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    exit_code = int(code.value)
        finally:
            kernel32.CloseHandle(handle)

        log_text = ""
        if self._log_path.is_file():
            try:
                log_text = self._log_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                pass
        self.finished_apo.emit(exit_code, log_text)


class _AudioDropZone(QFrame):
    """Low-profile drag-and-drop & click-to-browse media import bar."""

    file_dropped = Signal(str)
    browse_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setFixedHeight(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("dragOver", False)
        self._custom_status = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 2, 12, 2)
        self._label = QLabel(t("main.drop_default"))
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #9CA3AF; font-size: 12px; font-weight: 500;")
        lay.addWidget(self._label)

    def retranslate_ui(self) -> None:
        if not self._custom_status:
            if self.property("dragOver"):
                self._label.setText(t("main.drop_hover"))
            else:
                self._label.setText(t("main.drop_default"))

    def set_drag_over(self, active: bool) -> None:
        val = bool(active)
        if self.property("dragOver") != val:
            self.setProperty("dragOver", val)
            self.style().unpolish(self)
            self.style().polish(self)
            if val:
                self._label.setText(t("main.drop_hover"))
                self._label.setStyleSheet("color: #DBEAFE; font-size: 12px; font-weight: 600;")
            elif not self._custom_status:
                self._label.setText(t("main.drop_default"))
                self._label.setStyleSheet("color: #9CA3AF; font-size: 12px; font-weight: 500;")

    def set_status_text(self, text: Optional[str]) -> None:
        if text:
            self._custom_status = True
            self._label.setText(text)
            self._label.setStyleSheet("color: #DBEAFE; font-size: 12px; font-weight: 600;")
        else:
            self._custom_status = False
            self._label.setText(t("main.drop_default"))
            self._label.setStyleSheet("color: #9CA3AF; font-size: 12px; font-weight: 500;")

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.browse_clicked.emit()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if _first_supported_media_url(event.mimeData()) is not None:
            self.set_drag_over(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self.set_drag_over(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self.set_drag_over(False)
        media_path = _first_supported_media_url(event.mimeData())
        if media_path is not None:
            event.acceptProposedAction()
            self.file_dropped.emit(media_path)
            return
        event.ignore()


class _DeviceScanWorker(QObject):
    """Background audio device enumerator so MainWindow opens instantly."""

    finished = Signal(object, object)  # (mics: list[dict], loopbacks: list[str])

    def __init__(self, include_loopbacks: bool = True) -> None:
        super().__init__()
        self._include_loopbacks = bool(include_loopbacks)

    def run(self) -> None:
        try:
            mics = list_microphones()
        except Exception:
            mics = []
        loopbacks: list[str] = []
        if self._include_loopbacks:
            try:
                loopbacks = list_loopback_devices()
            except Exception:
                loopbacks = []
        self.finished.emit(mics, loopbacks)


class MainWindow(QMainWindow):
    """Minimal dark soundboard & capture controller."""

    capture_requested = Signal()
    play_clip_requested = Signal(str)
    stop_audio_requested = Signal()
    clip_playback_done = Signal(int)
    loopback_status_changed = Signal(str, str)
    buffer_duration_changed = Signal(int)
    language_changed = Signal(str)

    _BUFFER_PRESETS: tuple[int, ...] = (10, 15, 30, 60, 120)

    def __init__(
        self,
        ring_buffer,
        injector: MicInjector,
        config: dict,
        clips: list[ClipEntry],
        on_save_state: Callable[[dict, list[ClipEntry]], list[str]],
    ) -> None:
        super().__init__()
        self._ring = ring_buffer
        self._injector = injector
        self._config = config
        self._config["buffer_seconds"] = clamp_buffer_seconds(
            self._config.get("buffer_seconds", DEFAULT_BUFFER_SECONDS)
        )
        self._clips = clips
        self._on_save_state = on_save_state
        self._active_popup: Optional[TrimDialog] = None
        self._active_picker: Optional[ChannelPickerDialog] = None
        self._active_snapshot: Optional[CaptureSnapshot] = None
        self._active_selected_keys: list[str] = []
        self._active_capture_wav: Optional[Path] = None
        self._transitioning_session: bool = False
        self._toast_label: Optional[QLabel] = None
        self._tmp_capture_dir = paths.temp_dir()
        self._scan_thread: Optional[QThread] = None
        self._scan_worker: Optional[_DeviceScanWorker] = None
        self._decode_thread: Optional[QThread] = None
        self._decode_worker: Optional[_DecodeWorker] = None
        self._pending_import_wav: Optional[Path] = None
        self._last_export_dir: Optional[Path] = None
        self._export_workers: list[_ExportWorker] = []
        self._apo_worker: Optional[_ApoProcessWorker] = None
        self._devices_scanned: bool = False
        self._default_mic_name: Optional[str] = None
        self._clip_play_seq: int = 0
        self._playing_clip_id: Optional[str] = None
        self._playing_clip_mode: Optional[str] = None  # "mic" or "preview"
        self._clip_row_buttons: dict[str, tuple[QPushButton, QPushButton, QPushButton]] = {}
        self._status_override_until: float = 0.0
        self._status_restore_timer = QTimer(self)
        self._status_restore_timer.setSingleShot(True)
        self._status_restore_timer.timeout.connect(self._on_status_override_expired)
        self._tray_trim_timer = QTimer(self)
        self._tray_trim_timer.setSingleShot(True)
        self._tray_trim_timer.setInterval(250)
        self._tray_trim_timer.timeout.connect(self._on_tray_trim_timeout)

        self.setWindowTitle(t("app.title"))
        self.setWindowIcon(make_app_icon())
        self.resize(780, 700)
        self.setMinimumSize(700, 580)
        self.setAcceptDrops(True)
        _install_app_style()
        self.setStyleSheet(DARK_QSS)

        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._content_container = QWidget()
        self._content_container.setMaximumWidth(920)
        self._content_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addStretch(0)
        outer.addWidget(self._content_container, 1)
        outer.addStretch(0)

        root = QVBoxLayout(self._content_container)
        root.setContentsMargins(18, 16, 18, 12)
        root.setSpacing(10)

        # Header & Live Status Badge
        top = QHBoxLayout()
        brand = QLabel(t("app.title"))
        brand.setStyleSheet("font-size: 20px; font-weight: 800; color: #F9FAFB;")
        self._status_pill = QLabel()
        self._status_pill.setStyleSheet(
            "background: #172554; color: #93C5FD; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 600;"
        )
        top.addWidget(brand)
        top.addSpacing(8)
        top.addWidget(self._status_pill)
        top.addStretch()
        self._effects_btn = QPushButton(t("main.effects_btn"))
        self._effects_btn.setToolTip(t("main.effects_tooltip"))
        self._effects_btn.clicked.connect(self._open_manage_effects)
        top.addWidget(self._effects_btn)
        self._lang_btn = QPushButton(t("lang.toggle_btn"))
        self._lang_btn.setMinimumWidth(48)
        self._lang_btn.setToolTip(t("lang.tooltip"))
        self._lang_btn.clicked.connect(self._on_toggle_language)
        top.addWidget(self._lang_btn)
        root.addLayout(top)

        # Instant Capture & Global Stop Bar
        cap_card = QFrame()
        cap_card.setObjectName("Card")
        cap_lay = QHBoxLayout(cap_card)
        cap_lay.setContentsMargins(12, 10, 12, 10)
        cap_lay.setSpacing(8)

        cap_hk = str(self._config.get("capture_hotkey", "F9") or "F9")
        buf_s = self._buffer_seconds()
        self._capture_btn = QPushButton()
        self._capture_btn.setObjectName("Primary")
        self._capture_btn.setMinimumHeight(38)
        self._capture_btn.clicked.connect(self.capture_last_30s)

        self._cap_hotkey_btn = HotkeyCaptureButton(cap_hk, allow_empty=False)
        self._cap_hotkey_btn.hotkey_changed.connect(self._on_capture_hotkey_changed)

        stop_hk = str(self._config.get("stop_hotkey", "F10") or "")
        self._stop_btn = QPushButton()
        self._stop_btn.setObjectName("StopIdle")
        self._stop_btn.setEnabled(False)
        self._stop_btn.setMinimumHeight(38)
        self._stop_btn.clicked.connect(self.stop_all_audio)

        self._stop_hotkey_btn = HotkeyCaptureButton(stop_hk, allow_empty=True)
        self._stop_hotkey_btn.hotkey_changed.connect(self._on_stop_hotkey_changed)

        self._clip_hk_lbl = QLabel(t("main.clip_hk_label"))
        self._stop_hk_lbl = QLabel(t("main.stop_hk_label"))
        cap_lay.addWidget(self._capture_btn, 1)
        cap_lay.addWidget(self._clip_hk_lbl)
        cap_lay.addWidget(self._cap_hotkey_btn)
        cap_lay.addSpacing(4)
        cap_lay.addWidget(self._stop_btn)
        cap_lay.addWidget(self._stop_hk_lbl)
        cap_lay.addWidget(self._stop_hotkey_btn)
        root.addWidget(cap_card)

        # Collapsible Audio & Capture Settings Toggle Bar
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 0, 0, 0)
        self._setup_toggle_btn = QPushButton(t("main.setup_toggle_expanded"))
        self._setup_toggle_btn.setObjectName("SectionToggle")
        self._setup_toggle_btn.setCheckable(True)
        self._setup_toggle_btn.setChecked(True)
        self._setup_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._setup_toggle_btn.setToolTip(t("main.setup_toggle_tooltip"))
        self._setup_toggle_btn.toggled.connect(self._on_setup_section_toggled)
        toggle_row.addWidget(self._setup_toggle_btn)
        toggle_row.addStretch()
        root.addLayout(toggle_row)

        self._setup_section = QWidget()
        setup_lay = QVBoxLayout(self._setup_section)
        setup_lay.setContentsMargins(0, 0, 0, 0)
        setup_lay.setSpacing(10)

        # Microphone, Direct Mic APO & Dual Volume Mixer
        ctrl_card = QFrame()
        ctrl_card.setObjectName("Card")
        ctrl_outer = QVBoxLayout(ctrl_card)
        ctrl_outer.setContentsMargins(12, 10, 12, 10)
        ctrl_outer.setSpacing(8)

        mic_row = QHBoxLayout()
        mic_row.setSpacing(8)
        self._mic_combo = QComboBox()
        self._mic_combo.setMinimumWidth(180)
        self._mic_combo.view().setMinimumWidth(340)
        self._mic_combo.setToolTip(t("main.mic_combo_tooltip"))
        self._mic_guids: dict[Optional[str], Optional[str]] = {None: None}
        saved_mic = self._config.get("mic_device")
        self._mic_combo.addItem(t("main.mic_default_scanning"), None)
        if saved_mic:
            self._mic_combo.addItem(str(saved_mic), saved_mic)
            self._mic_combo.setCurrentIndex(1)
        self._mic_combo.currentIndexChanged.connect(self._on_settings_changed)

        self._refresh_dev_btn = QPushButton("↻")
        self._refresh_dev_btn.setToolTip(t("main.refresh_devices_tooltip"))
        self._refresh_dev_btn.setMinimumWidth(34)
        self._refresh_dev_btn.clicked.connect(self._start_device_scan)

        self._apo_btn = QPushButton(t("main.enable_direct_mic"))
        self._apo_btn.setToolTip(t("main.enable_direct_mic_tooltip"))
        self._apo_btn.clicked.connect(self._on_toggle_apo)

        self._mic_lbl = QLabel(t("main.mic_label"))
        mic_row.addWidget(self._mic_lbl)
        mic_row.addWidget(self._mic_combo, 1)
        mic_row.addWidget(self._refresh_dev_btn)
        mic_row.addWidget(self._apo_btn)
        ctrl_outer.addLayout(mic_row)

        mix_row = QHBoxLayout()
        mix_row.setSpacing(8)

        mic_vol_pct = int(round(float(self._config.get("mic_volume", 1.0)) * 100))
        self._mic_vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._mic_vol_slider.setRange(0, 150)
        self._mic_vol_slider.setValue(mic_vol_pct)
        self._mic_vol_slider.setMinimumWidth(80)
        self._mic_vol_slider.setToolTip(t("main.mic_vol_tooltip"))
        self._mic_vol_slider.valueChanged.connect(self._on_settings_changed)

        self._mic_vol_lbl = QLabel(ltr(f"{mic_vol_pct}%"))
        self._mic_vol_lbl.setMinimumWidth(40)
        self._mic_vol_lbl.setStyleSheet("color: #9A9AA2; font-size: 11px;")
        self._mic_vol_lbl.setToolTip(self._mic_vol_slider.toolTip())

        self._monitor_chk = QCheckBox(t("main.monitor_chk"))
        self._monitor_chk.setToolTip(t("main.monitor_chk_tooltip"))
        self._monitor_chk.setChecked(bool(self._config.get("monitor_enabled", True)))
        self._monitor_chk.toggled.connect(self._on_settings_changed)

        vol_pct = int(round(float(self._config.get("monitor_volume", 0.85)) * 100))
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 150)
        self._vol_slider.setValue(vol_pct)
        self._vol_slider.setMinimumWidth(80)
        self._vol_slider.setEnabled(self._monitor_chk.isChecked())
        self._vol_slider.setToolTip(t("main.monitor_vol_tooltip"))
        self._vol_slider.valueChanged.connect(self._on_settings_changed)

        self._vol_lbl = QLabel(ltr(f"{vol_pct}%"))
        self._vol_lbl.setMinimumWidth(40)
        self._vol_lbl.setStyleSheet("color: #9A9AA2; font-size: 11px;")
        self._vol_lbl.setToolTip(self._vol_slider.toolTip())

        self._mic_vol_title_lbl = QLabel(t("main.mic_vol_label"))
        mix_row.addWidget(self._mic_vol_title_lbl)
        mix_row.addWidget(self._mic_vol_slider, 1)
        mix_row.addWidget(self._mic_vol_lbl)
        mix_row.addSpacing(10)
        mix_row.addWidget(self._monitor_chk)
        mix_row.addWidget(self._vol_slider, 1)
        mix_row.addWidget(self._vol_lbl)
        ctrl_outer.addLayout(mix_row)
        setup_lay.addWidget(ctrl_card)

        # Listening Source & Buffer Duration
        listen_card = QFrame()
        listen_card.setObjectName("Card")
        listen_outer = QVBoxLayout(listen_card)
        listen_outer.setContentsMargins(12, 10, 12, 10)
        listen_outer.setSpacing(8)

        listen_row = QHBoxLayout()
        listen_row.setSpacing(8)
        self._loopback_combo = QComboBox()
        self._loopback_combo.view().setMinimumWidth(340)
        self._loopback_combo.setToolTip(t("main.loopback_tooltip"))
        self._loopback_combo.addItem(t("main.loopback_default"), None)
        saved_lb = self._config.get("loopback_device")
        if saved_lb:
            self._loopback_combo.addItem(str(saved_lb).replace(" [Loopback]", ""), saved_lb)
            self._loopback_combo.setCurrentIndex(1)
        self._loopback_combo.currentIndexChanged.connect(self._on_loopback_changed)

        self._capture_lbl = QLabel(t("main.capture_label"))
        listen_row.addWidget(self._capture_lbl)
        listen_row.addWidget(self._loopback_combo, 1)
        listen_outer.addLayout(listen_row)

        # Rolling buffer duration selector (presets + custom 5–180s spinbox)
        buf_row = QHBoxLayout()
        buf_row.setSpacing(8)
        self._buf_lbl = QLabel(t("main.buffer_label"))
        self._buf_lbl.setStyleSheet("color: #9CA3AF; font-size: 13px;")
        buf_row.addWidget(self._buf_lbl)
        self._buf_preset_btns: dict[int, QPushButton] = {}
        for sec in self._BUFFER_PRESETS:
            pbtn = QPushButton(t("main.buffer_preset", n=sec))
            pbtn.setObjectName("BufPresetBtn")
            pbtn.setCheckable(True)
            pbtn.setChecked(sec == buf_s)
            pbtn.setToolTip(t("main.buffer_preset_tooltip", n=sec))
            pbtn.clicked.connect(lambda _=False, s=sec: self._on_buffer_seconds_changed(s))
            self._buf_preset_btns[sec] = pbtn
            buf_row.addWidget(pbtn)

        self._buf_spin = QSpinBox()
        self._buf_spin.setRange(MIN_BUFFER_SECONDS, MAX_BUFFER_SECONDS)
        self._buf_spin.setValue(buf_s)
        self._buf_spin.setSuffix(t("main.buffer_spin_suffix"))
        self._buf_spin.setFixedWidth(82)
        self._buf_spin.setMinimumHeight(28)
        self._buf_spin.setKeyboardTracking(False)
        self._buf_spin.setToolTip(t("main.buffer_spin_tooltip", min=MIN_BUFFER_SECONDS, max=MAX_BUFFER_SECONDS))
        self._buf_spin.valueChanged.connect(self._on_buffer_seconds_changed)
        buf_row.addSpacing(2)
        buf_row.addWidget(self._buf_spin)
        buf_row.addStretch()
        listen_outer.addLayout(buf_row)

        self._listening_label = QLabel(t("main.listening_checking"))
        self._listening_label.setAlignment(_text_align())
        self._listening_label.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        listen_outer.addWidget(self._listening_label)
        setup_lay.addWidget(listen_card)
        root.addWidget(self._setup_section)

        # Compact Drag-and-Drop Audio/Video File Import Zone
        self._drop_zone = _AudioDropZone()
        self._drop_zone.setToolTip(t("main.drop_tooltip"))
        self._drop_zone.file_dropped.connect(self.import_audio_file)
        self._drop_zone.browse_clicked.connect(self._browse_import_audio)
        root.addWidget(self._drop_zone)

        # Saved Clips Header
        list_hdr = QHBoxLayout()
        self._saved_clips_lbl = QLabel(t("main.saved_clips_header"))
        self._saved_clips_lbl.setAlignment(_text_align())
        self._saved_clips_lbl.setStyleSheet("font-size: 14px; font-weight: 700; color: #D1D5DB;")
        self._count_lbl = QLabel()
        self._count_lbl.setStyleSheet("color: #6B7280; font-size: 12px;")
        list_hdr.addWidget(self._saved_clips_lbl)
        list_hdr.addStretch()
        list_hdr.addWidget(self._count_lbl)
        root.addLayout(list_hdr)

        # Saved Clips Scroll List
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 4, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.addStretch()
        self._scroll.setWidget(self._list_host)
        root.addWidget(self._scroll, 1)

        # Compact Tray Preferences & Live Status Footer
        footer_row = QHBoxLayout()
        footer_row.setSpacing(14)
        self._startup_chk = QCheckBox(t("main.startup_chk"))
        self._startup_chk.setToolTip(t("main.startup_tooltip"))
        self._startup_chk.setChecked(paths.is_startup_enabled())
        self._startup_chk.toggled.connect(self._on_startup_toggled)

        self._tray_close_chk = QCheckBox(t("main.tray_close_chk"))
        self._tray_close_chk.setToolTip(t("main.tray_close_tooltip"))
        self._tray_close_chk.setChecked(bool(self._config.get("minimize_to_tray", True)))
        self._tray_close_chk.toggled.connect(self._on_tray_close_toggled)

        footer_row.addWidget(self._startup_chk)
        footer_row.addWidget(self._tray_close_chk)
        footer_row.addStretch()
        root.addLayout(footer_row)

        self._status_bar_lbl = QLabel("")
        self._status_bar_lbl.setAlignment(_text_align())
        self._status_bar_lbl.setStyleSheet(
            "color: #8B8B96; font-size: 11px; padding-top: 2px; border-top: 1px solid #1E1E24;"
        )
        root.addWidget(self._status_bar_lbl)

        self.capture_requested.connect(self.capture_last_30s)
        self.play_clip_requested.connect(self.play_clip_by_id)
        self.stop_audio_requested.connect(self.stop_all_audio)
        self.clip_playback_done.connect(self._on_clip_playback_done)
        self.loopback_status_changed.connect(self._on_loopback_status_changed)
        self.retranslate_ui()

        self._session_timer = QTimer(self)
        self._session_timer.setInterval(1500)
        self._session_timer.timeout.connect(self._refresh_listening)
        QTimer.singleShot(0, self._start_device_scan)

    def _on_toggle_language(self) -> None:
        new_lang = "ar" if get_lang() == "en" else "en"
        set_lang(new_lang)
        self._config["language"] = new_lang
        save_config(self._config)
        direction = Qt.LayoutDirection.RightToLeft if is_rtl() else Qt.LayoutDirection.LeftToRight
        app = QApplication.instance()
        if app is not None:
            app.setLayoutDirection(direction)
        self.setLayoutDirection(direction)
        self.retranslate_ui()
        self.language_changed.emit(new_lang)

    def _on_setup_section_toggled(self, expanded: bool) -> None:
        self._setup_section.setVisible(bool(expanded))
        self._setup_toggle_btn.setText(
            t("main.setup_toggle_expanded") if expanded else t("main.setup_toggle_collapsed")
        )

    def _default_mic_item_text(self) -> str:
        if not self._devices_scanned:
            return t("main.mic_default_scanning")
        if self._default_mic_name:
            return t("main.mic_default_named", name=self._default_mic_name)
        return t("main.mic_default")

    def retranslate_ui(self) -> None:
        """Re-apply all localized labels, tooltips, and dynamic text in MainWindow live."""
        self.setWindowTitle(t("app.title"))
        self._effects_btn.setText(t("main.effects_btn"))
        self._effects_btn.setToolTip(t("main.effects_tooltip"))
        self._lang_btn.setText(t("lang.toggle_btn"))
        self._lang_btn.setToolTip(t("lang.tooltip"))

        self._clip_hk_lbl.setText(t("main.clip_hk_label"))
        self._stop_hk_lbl.setText(t("main.stop_hk_label"))
        stop_hk = str(self._config.get("stop_hotkey", "") or "").strip()
        self._stop_btn.setText(t("main.stop_btn", hk=ltr(stop_hk)) if stop_hk else t("main.stop_btn_no_hk"))
        self._stop_btn.setToolTip(t("main.stop_tooltip"))
        self._stop_hotkey_btn.setToolTip(t("main.stop_hk_tooltip"))
        self._cap_hotkey_btn.retranslate_ui()
        self._stop_hotkey_btn.retranslate_ui()

        self._setup_toggle_btn.setText(
            t("main.setup_toggle_expanded")
            if self._setup_toggle_btn.isChecked()
            else t("main.setup_toggle_collapsed")
        )
        self._setup_toggle_btn.setToolTip(t("main.setup_toggle_tooltip"))

        self._mic_lbl.setText(t("main.mic_label"))
        self._mic_combo.setToolTip(t("main.mic_combo_tooltip"))
        if self._mic_combo.count() > 0:
            self._mic_combo.setItemText(0, self._default_mic_item_text())
        self._refresh_dev_btn.setToolTip(t("main.refresh_devices_tooltip"))

        self._mic_vol_title_lbl.setText(t("main.mic_vol_label"))
        self._mic_vol_slider.setToolTip(t("main.mic_vol_tooltip"))
        self._mic_vol_lbl.setText(ltr(f"{self._mic_vol_slider.value()}%"))
        self._mic_vol_lbl.setToolTip(self._mic_vol_slider.toolTip())
        self._monitor_chk.setText(t("main.monitor_chk"))
        self._monitor_chk.setToolTip(t("main.monitor_chk_tooltip"))
        self._vol_slider.setToolTip(t("main.monitor_vol_tooltip"))
        self._vol_lbl.setText(ltr(f"{self._vol_slider.value()}%"))
        self._vol_lbl.setToolTip(self._vol_slider.toolTip())

        self._capture_lbl.setText(t("main.capture_label"))
        self._loopback_combo.setToolTip(t("main.loopback_tooltip"))
        if self._loopback_combo.count() > 0:
            self._loopback_combo.setItemText(0, t("main.loopback_default"))

        self._buf_lbl.setText(t("main.buffer_label"))
        for sec, pbtn in self._buf_preset_btns.items():
            pbtn.setText(t("main.buffer_preset", n=sec))
            pbtn.setToolTip(t("main.buffer_preset_tooltip", n=sec))
        self._buf_spin.setSuffix(t("main.buffer_spin_suffix"))
        self._buf_spin.setToolTip(t("main.buffer_spin_tooltip", min=MIN_BUFFER_SECONDS, max=MAX_BUFFER_SECONDS))

        self._drop_zone.setToolTip(t("main.drop_tooltip"))
        self._drop_zone.retranslate_ui()

        self._saved_clips_lbl.setText(t("main.saved_clips_header"))
        align = _text_align()
        self._saved_clips_lbl.setAlignment(align)
        self._listening_label.setAlignment(align)
        self._status_bar_lbl.setAlignment(align)
        self._startup_chk.setText(t("main.startup_chk"))
        self._startup_chk.setToolTip(t("main.startup_tooltip"))
        self._tray_close_chk.setText(t("main.tray_close_chk"))
        self._tray_close_chk.setToolTip(t("main.tray_close_tooltip"))

        self._update_duration_labels()
        self._refresh_status()
        self._rebuild_clip_list()

    def _start_device_scan(self) -> None:
        if self._scan_thread is not None and self._scan_thread.isRunning():
            return
        self._refresh_dev_btn.setEnabled(False)
        thread = QThread(self)
        include_loopbacks = getattr(self._ring, "mode", "") != "procloop"
        worker = _DeviceScanWorker(include_loopbacks=include_loopbacks)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_devices_scanned, Qt.ConnectionType.QueuedConnection)
        self._scan_thread = thread
        self._scan_worker = worker
        thread.start()

    @Slot(object, object)
    def _on_devices_scanned(self, mics: list[dict], loopbacks: list[str]) -> None:
        thread = self._scan_thread
        if thread is not None:
            thread.quit()
            thread.wait(1500)
        self._scan_thread = None
        self._scan_worker = None
        self._refresh_dev_btn.setEnabled(True)

        # Populate microphones without triggering redundant restarts/saves
        selected_mic = self._config.get("mic_device")
        self._mic_combo.blockSignals(True)
        self._mic_combo.clear()
        self._mic_guids = {}
        default_mic = next((m for m in mics if m.get("is_default")), mics[0] if mics else None)
        self._devices_scanned = True
        self._default_mic_name = default_mic["name"] if default_mic else None
        self._mic_combo.addItem(self._default_mic_item_text(), None)
        self._mic_guids[None] = default_mic.get("guid") if default_mic else None
        for m in mics:
            self._mic_combo.addItem(m["name"], m["name"])
            self._mic_guids[m["name"]] = m.get("guid")
        if selected_mic:
            idx = self._mic_combo.findData(selected_mic)
            if idx >= 0:
                self._mic_combo.setCurrentIndex(idx)
        self._mic_combo.blockSignals(False)

        # Populate WASAPI loopback playback devices
        selected_lb = self._config.get("loopback_device")
        self._loopback_combo.blockSignals(True)
        self._loopback_combo.clear()
        self._loopback_combo.addItem(t("main.loopback_default"), None)
        for dev_name in loopbacks:
            clean = dev_name.replace(" [Loopback]", "")
            self._loopback_combo.addItem(clean, dev_name)
        if selected_lb:
            lidx = self._loopback_combo.findData(selected_lb)
            if lidx >= 0:
                self._loopback_combo.setCurrentIndex(lidx)
        self._loopback_combo.blockSignals(False)

        self._injector.set_target_endpoint(self._current_mic_guid())
        if hasattr(self._ring, "set_mic_device"):
            self._ring.set_mic_device(self._mic_combo.currentData())
        self._refresh_status()

    def _pause_ui_timers(self) -> None:
        if self._session_timer.isActive():
            self._session_timer.stop()

    def _resume_ui_timers(self) -> None:
        if not self.isVisible() or self.isMinimized():
            return
        self._refresh_listening()
        self._refresh_status()
        if not self._session_timer.isActive():
            self._session_timer.start()

    def _on_tray_trim_timeout(self) -> None:
        if self.isVisible() and not self.isMinimized():
            return
        if self._active_picker is not None and self._active_picker.isVisible():
            return
        if self._active_popup is not None and self._active_popup.isVisible():
            return
        QPixmapCache.clear()
        gc.collect()
        _trim_working_set()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._tray_trim_timer.stop()
        if not getattr(self, "_dark_titlebar_applied", False):
            self._dark_titlebar_applied = True
            apply_dark_titlebar(self)
        self._resume_ui_timers()

    def hideEvent(self, event) -> None:
        self._pause_ui_timers()
        self._tray_trim_timer.start()
        super().hideEvent(event)

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                self._pause_ui_timers()
                self._tray_trim_timer.start()
            elif self.isVisible():
                self._tray_trim_timer.stop()
                self._resume_ui_timers()

    def shutdown(self) -> None:
        """Stop timers, background scan/decode threads, and any open capture/trim popups cleanly."""
        self._status_restore_timer.stop()
        self._tray_trim_timer.stop()
        self._session_timer.stop()
        if self._toast_label is not None:
            try:
                self._toast_label.close()
            except Exception:
                pass
            self._toast_label = None
        self._release_capture_session()
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._scan_thread.quit()
            self._scan_thread.wait(350)
        self._scan_thread = None
        self._scan_worker = None
        if self._decode_thread is not None and self._decode_thread.isRunning():
            self._decode_thread.quit()
            self._decode_thread.wait(350)
        self._decode_thread = None
        self._decode_worker = None
        for worker in list(self._export_workers):
            if worker.isRunning():
                worker.wait(350)
        self._export_workers.clear()
        if self._apo_worker is not None and self._apo_worker.isRunning():
            self._apo_worker.wait(500)
        self._apo_worker = None
        if self._pending_import_wav is not None and self._pending_import_wav.is_file():
            try:
                self._pending_import_wav.unlink()
            except OSError:
                pass
            self._pending_import_wav = None

    def _buffer_seconds(self) -> int:
        return clamp_buffer_seconds(self._config.get("buffer_seconds", DEFAULT_BUFFER_SECONDS))

    def _update_duration_labels(self) -> None:
        secs = self._buffer_seconds()
        cap_hk = str(self._config.get("capture_hotkey", "F9") or "F9").strip()
        if cap_hk:
            self._capture_btn.setText(t("main.clip_last_btn", n=secs, hk=ltr(cap_hk)))
        else:
            self._capture_btn.setText(t("main.clip_last_no_hk", n=secs))
        self._capture_btn.setToolTip(t("main.clip_last_tooltip", n=secs))
        self._cap_hotkey_btn.setToolTip(t("main.clip_hk_tooltip", n=secs))
        self._refresh_listening()
        if not self._clips:
            self._rebuild_clip_list()

    def _on_buffer_seconds_changed(self, seconds: int) -> None:
        secs = clamp_buffer_seconds(seconds)
        prev = self._buffer_seconds()
        self._config["buffer_seconds"] = secs
        for preset_s, btn in self._buf_preset_btns.items():
            btn.blockSignals(True)
            btn.setChecked(preset_s == secs)
            btn.blockSignals(False)
        if self._buf_spin.value() != secs:
            self._buf_spin.blockSignals(True)
            self._buf_spin.setValue(secs)
            self._buf_spin.blockSignals(False)
        if secs != prev and hasattr(self._ring, "set_duration"):
            try:
                self._ring.set_duration(float(secs))
            except Exception as exc:
                self._show_status_toast(t("main.status_buffer_error", err=exc))
        self._update_duration_labels()
        self._on_save_state(self._config, self._clips)
        self.buffer_duration_changed.emit(secs)

    def dragEnterEvent(self, event) -> None:
        if _first_supported_media_url(event.mimeData()) is not None:
            self._drop_zone.set_drag_over(True)
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drop_zone.set_drag_over(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._drop_zone.set_drag_over(False)
        media_path = _first_supported_media_url(event.mimeData())
        if media_path is not None:
            event.acceptProposedAction()
            self.import_audio_file(media_path)
            return
        event.ignore()

    def _browse_import_audio(self) -> None:
        media_exts = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_MEDIA_EXTS))
        audio_exts = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_AUDIO_EXTS))
        video_exts = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_VIDEO_EXTS))
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            t("main.import_dialog_title"),
            "",
            (
                f"{t('main.import_filter_all_media', exts=media_exts)};;"
                f"{t('main.import_filter_audio', exts=audio_exts)};;"
                f"{t('main.import_filter_video', exts=video_exts)};;"
                f"{t('main.import_filter_any')}"
            ),
        )
        if chosen:
            self.import_audio_file(chosen)

    def import_audio_file(self, file_path: str | Path) -> None:
        src = Path(file_path)
        if not is_supported_audio_file(src):
            self._show_status_toast(t("main.import_unsupported"))
            return
        if self._decode_thread is not None and self._decode_thread.isRunning():
            self._show_status_toast(t("main.import_busy"))
            return

        dst_wav = self._tmp_capture_dir / f"import_{uuid.uuid4().hex[:8]}.wav"
        self._pending_import_wav = dst_wav
        status_msg = (
            t("main.import_extracting", name=src.name)
            if is_video_file(src)
            else t("main.import_decoding", name=src.name)
        )
        self._drop_zone.set_status_text(status_msg)
        self._status_bar_lbl.setText(status_msg)

        thread = QThread(self)
        worker = _DecodeWorker(src, dst_wav)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_import_decoded, Qt.ConnectionType.QueuedConnection)
        self._decode_thread = thread
        self._decode_worker = worker
        thread.start()

    @Slot(str, str, str)
    def _on_import_decoded(self, wav_str: str, src_name: str, err: str) -> None:
        thread = self._decode_thread
        if thread is not None:
            thread.quit()
            thread.wait(2000)
        self._decode_thread = None
        self._decode_worker = None
        pending = self._pending_import_wav
        self._pending_import_wav = None
        self._drop_zone.set_status_text(None)
        self._refresh_status()

        if err or not wav_str:
            if pending is not None and pending.is_file():
                try:
                    pending.unlink()
                except OSError:
                    pass
            self._show_status_toast(t("main.import_failed", err=err or t("main.import_decode_error")))
            return

        wav_path = Path(wav_str)
        self._release_capture_session()
        self._active_capture_wav = wav_path
        self._open_trim_dialog(
            wav_path,
            existing=None,
            on_change_channels=None,
            import_name=src_name,
            default_name=Path(src_name).stem,
        )

    @Slot(str, str)
    def _on_loopback_status_changed(self, _state: str, _detail: str) -> None:
        self._refresh_status()
        self._refresh_listening()

    def _set_status_message(self, message: str, *, hold_ms: int = 3500, is_error: bool = False) -> None:
        """Display a transient status bar message protected from background _refresh_status() overwrites."""
        self._status_override_until = time.monotonic() + max(0.0, hold_ms / 1000.0)
        color = "#D48E95" if is_error else "#D4D4D8"
        self._status_bar_lbl.setStyleSheet(
            f"color: {color}; font-size: 11px; padding-top: 2px; border-top: 1px solid #1E1E24;"
        )
        self._status_bar_lbl.setText(message)
        if hold_ms > 0:
            self._status_restore_timer.start(hold_ms)
        else:
            self._status_restore_timer.stop()

    def _on_status_override_expired(self) -> None:
        if time.monotonic() < self._status_override_until - 0.01:
            return
        self._status_override_until = 0.0
        self._status_bar_lbl.setStyleSheet(
            "color: #8B8B96; font-size: 11px; padding-top: 2px; border-top: 1px solid #1E1E24;"
        )
        self._refresh_status()

    def _repolish_widget(self, w: QWidget) -> None:
        w.style().unpolish(w)
        w.style().polish(w)

    def _sync_clip_playback_buttons(self) -> None:
        """Reflect the currently active saved-clip playback state on row buttons and top Stop button."""
        active_id = self._playing_clip_id
        active_mode = self._playing_clip_mode
        for cid, (prev_btn, mic_btn, loop_btn) in list(self._clip_row_buttons.items()):
            try:
                is_prev = cid == active_id and active_mode == "preview"
                is_mic = cid == active_id and active_mode == "mic"
                is_loop = cid == active_id and active_mode == "loop"
                prev_btn.setText(t("common.stop") if is_prev else t("main.clip_preview_btn"))
                prev_btn.setObjectName("Danger" if is_prev else "")
                self._repolish_widget(prev_btn)

                mic_btn.setText(t("common.stop") if is_mic else t("main.clip_play_btn"))
                mic_btn.setObjectName("Danger" if is_mic else "Accent")
                self._repolish_widget(mic_btn)

                loop_btn.setText(t("common.stop") if is_loop else t("main.clip_loop_btn"))
                loop_btn.setObjectName("Danger" if is_loop else "")
                self._repolish_widget(loop_btn)
            except RuntimeError:
                self._clip_row_buttons.pop(cid, None)

        is_active = active_id is not None
        self._stop_btn.setEnabled(is_active)
        self._stop_btn.setObjectName("Danger" if is_active else "StopIdle")
        self._repolish_widget(self._stop_btn)

    def _stop_other_dialog_playback(self) -> None:
        if self._active_picker is not None:
            try:
                self._active_picker.stop_preview()
            except Exception:
                pass
        if self._active_popup is not None:
            try:
                self._active_popup._on_stop()
            except Exception:
                pass

    def stop_all_audio(self, _checked: bool = False) -> None:
        clip_playing = self._playing_clip_id is not None
        picker_playing = bool(self._active_picker is not None and getattr(self._active_picker, "_playing_key", None))
        popup_playing = bool(
            self._active_popup is not None
            and (
                getattr(self._active_popup, "_is_playing", False)
                or getattr(self._active_popup, "_is_preview_playing", False)
                or getattr(self._active_popup, "_pending_then", None) is not None
            )
        )
        was_playing = self._injector.stop_all() or clip_playing or picker_playing or popup_playing
        self._clip_play_seq += 1
        self._playing_clip_id = None
        self._playing_clip_mode = None
        self._sync_clip_playback_buttons()
        self._stop_other_dialog_playback()
        self._set_status_message(
            t("main.status_audio_stopped") if was_playing else t("main.status_nothing_playing"),
            hold_ms=3500,
            is_error=False,
        )

    @Slot(int)
    def _on_clip_playback_done(self, seq: int) -> None:
        if seq != self._clip_play_seq:
            return
        self._playing_clip_id = None
        self._playing_clip_mode = None
        self._sync_clip_playback_buttons()
        self._status_override_until = 0.0
        self._status_restore_timer.stop()
        self._status_bar_lbl.setStyleSheet(
            "color: #8B8B96; font-size: 11px; padding-top: 2px; border-top: 1px solid #1E1E24;"
        )
        self._refresh_status()

    def _on_startup_toggled(self, checked: bool) -> None:
        try:
            paths.set_startup_enabled(bool(checked))
        except Exception as exc:
            self._startup_chk.blockSignals(True)
            self._startup_chk.setChecked(paths.is_startup_enabled())
            self._startup_chk.blockSignals(False)
            QMessageBox.warning(self, t("main.err_startup_title"), t("main.err_startup_body", err=exc))

    def _on_tray_close_toggled(self, checked: bool) -> None:
        self._config["minimize_to_tray"] = bool(checked)
        self._on_save_state(self._config, self._clips)

    def _on_loopback_changed(self) -> None:
        dev = self._loopback_combo.currentData()
        self._config["loopback_device"] = dev
        self._on_save_state(self._config, self._clips)
        try:
            self._ring.restart(dev)
        except Exception as exc:
            QMessageBox.warning(self, t("main.err_capture_title"), t("main.err_capture_body", err=exc))
        self._refresh_status()
        self._refresh_listening()

    def _refresh_listening(self) -> None:
        if self._ring.is_reconnecting:
            detail = f" ({self._ring.last_error})" if self._ring.last_error else ""
            self._listening_label.setText(t("main.listening_reconnecting", detail=detail))
            self._listening_label.setStyleSheet("color: #FBBF24; font-size: 12px;")
            return
        if not self._ring.is_running:
            self._listening_label.setText(t("main.listening_stopped"))
            self._listening_label.setStyleSheet("color: #D48E95; font-size: 12px;")
            return
        apps = self._ring.refresh_active_sessions()
        raw_dev = (self._ring.device_name or "").replace(" [Loopback]", "")
        if getattr(self._ring, "mode", "") == "procloop" or raw_dev == "Per-App Capture (All Outputs)":
            dev_short = t("main.mode_per_app_outputs")
        else:
            dev_short = raw_dev or t("main.loopback_default")
        active_now = self._ring.is_receiving_audio()
        dot = "●" if active_now else "○"
        color = "#89C2A4" if active_now else "#9CA3AF"
        apps_txt = ", ".join(apps[:5]) if apps else t("main.listening_waiting")
        secs = self._buffer_seconds()
        mic_tag = (
            t("main.mic_buf_on", n=secs)
            if getattr(self._ring, "mic_active", False)
            else t("main.mic_buf_unavailable")
        )
        mode_tag = t("main.mode_per_app_outputs") if getattr(self._ring, "mode", "") == "procloop" else dev_short
        self._listening_label.setText(t("main.listening_line", dot=dot, apps=apps_txt, mode=mode_tag, mic=mic_tag))
        self._listening_label.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _current_mic_guid(self) -> Optional[str]:
        return self._mic_guids.get(self._mic_combo.currentData())

    def _refresh_status(self) -> None:
        st = self._injector.refresh_apo_status()
        if self._ring.is_reconnecting:
            buf_state = t("main.buf_reconnecting")
        elif self._ring.is_running:
            buf_state = t("main.buf_per_app") if getattr(self._ring, "mode", "") == "procloop" else t("main.buf_loopback")
        else:
            buf_state = t("main.buf_off")

        enh_off = bool(st.get("enhancements_disabled"))
        if enh_off:
            self._status_pill.setText(t("main.status_pill", buf_state=buf_state, mode_state=t("main.enhancements_off")))
            self._status_pill.setToolTip(t("main.enhancements_off_tooltip"))
            self._status_pill.setStyleSheet(
                "background: #3B2711; color: #FBBF24; border: 1px solid #6B441A; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 600;"
            )
        else:
            apo_label = t("main.apo_active") if self._injector.apo_installed else t("main.apo_preview_mode")
            self._status_pill.setText(t("main.status_pill", buf_state=buf_state, mode_state=apo_label))
            self._status_pill.setToolTip("")
            if self._injector.apo_installed:
                self._status_pill.setStyleSheet(
                    "background: #18231F; color: #89C2A4; border: 1px solid #2A4037; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 600;"
                )
            else:
                self._status_pill.setStyleSheet(
                    "background: #1C1C21; color: #A1A1AA; border: 1px solid #2C2C34; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 600;"
                )

        if self._injector.apo_installed:
            self._apo_btn.setText(t("main.restore_mic"))
            self._apo_btn.setToolTip(t("main.restore_mic_tooltip"))
            self._apo_btn.setObjectName("Danger")
        elif st.get("has_backup"):
            self._apo_btn.setText(t("main.enable_direct_mic"))
            self._apo_btn.setToolTip(t("main.enable_direct_mic_backup_tooltip"))
            self._apo_btn.setObjectName("")
        else:
            self._apo_btn.setText(t("main.enable_direct_mic"))
            self._apo_btn.setToolTip(t("main.enable_direct_mic_tooltip"))
            self._apo_btn.setObjectName("")
        self._apo_btn.style().unpolish(self._apo_btn)
        self._apo_btn.style().polish(self._apo_btn)

        # Update compact live status bar at bottom
        raw_cap_dev = (self._ring.device_name or "").replace(" [Loopback]", "")
        if getattr(self._ring, "mode", "") == "procloop" or raw_cap_dev == "Per-App Capture (All Outputs)":
            cap_dev = t("main.mode_per_app_outputs")
        else:
            cap_dev = raw_cap_dev or t("main.loopback_default")
        mic_label = self._mic_combo.currentText() or t("main.mic_default")
        if len(mic_label) > 36:
            mic_label = mic_label[:35] + "…"
        mode_lbl = t("main.apo_active_short") if self._injector.apo_installed else t("main.apo_preview_short")
        if enh_off:
            mode_lbl = t("main.enhancements_off")
        if time.monotonic() >= self._status_override_until:
            self._status_bar_lbl.setStyleSheet(
                "color: #8B8B96; font-size: 11px; padding-top: 2px; border-top: 1px solid #1E1E24;"
            )
            self._status_bar_lbl.setText(t("main.status_bar", buf_state=buf_state, device=cap_dev, mic=mic_label, mode=mode_lbl))

    def restore_all_mics_action(self) -> None:
        self._start_apo_action("restore-all", None, "main.restoring", "main.apo_status_restoring")

    def _on_toggle_apo(self) -> None:
        st = self._injector.refresh_apo_status()
        if st.get("active"):
            self._start_apo_action("uninstall", self._current_mic_guid(), "main.restoring", "main.apo_status_restoring")
            return

        if not st.get("dll_built"):
            QMessageBox.warning(
                self,
                t("main.err_apo_missing_title"),
                t("main.err_apo_missing_body"),
            )
            return
        guid = self._current_mic_guid()
        if not guid:
            QMessageBox.warning(self, t("main.err_no_mic_title"), t("main.err_no_mic_body"))
            return
        if st.get("enhancements_disabled"):
            ans = QMessageBox.warning(
                self,
                t("main.err_enhancements_title"),
                t("main.err_enhancements_body"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._start_apo_action("install", guid, "main.enabling", "main.apo_status_enabling")

    def _start_apo_action(
        self,
        action: str,
        guid: Optional[str],
        btn_key: str,
        status_key: str,
    ) -> None:
        if self._apo_worker is not None and self._apo_worker.isRunning():
            return
        self._apo_btn.setDisabled(True)
        self._apo_btn.setText(t(btn_key))
        self._status_bar_lbl.setText(t(status_key))
        try:
            h_process, cancelled = apo_setup.launch_elevated(action, guid)
        except OSError as exc:
            self._apo_btn.setDisabled(False)
            self._refresh_status()
            QMessageBox.warning(self, t("main.err_apo_setup_title"), str(exc))
            return

        if cancelled:
            self._apo_btn.setDisabled(False)
            self._refresh_status()
            self._status_bar_lbl.setText(t("main.apo_uac_cancelled"))
            return

        if not h_process:
            self._apo_btn.setDisabled(False)
            self._refresh_status()
            return

        worker = _ApoProcessWorker(h_process, apo_setup.setup_log_path(), self)
        self._apo_worker = worker
        worker.finished_apo.connect(self._on_apo_finished)
        worker.start()

    @Slot(int, str)
    def _on_apo_finished(self, exit_code: int, log_text: str) -> None:
        worker = self._apo_worker
        self._apo_worker = None
        if worker is not None:
            worker.deleteLater()
        self._apo_btn.setDisabled(False)
        self._refresh_status()
        if log_text.startswith("ERROR:") or (exit_code != 0 and log_text):
            QMessageBox.warning(self, t("main.err_apo_setup_title"), log_text.splitlines()[0])
        elif exit_code != 0:
            QMessageBox.warning(
                self,
                t("main.err_apo_setup_title"),
                f"Elevated setup exited with code {exit_code}.",
            )

    def get_volumes(self) -> tuple[bool, float, float]:
        return (
            self._monitor_chk.isChecked(),
            self._vol_slider.value() / 100.0,
            self._mic_vol_slider.value() / 100.0,
        )

    def _on_capture_hotkey_changed(self, hk: str) -> None:
        self._config["capture_hotkey"] = hk
        self._update_duration_labels()
        self._persist_and_bind()

    def _on_stop_hotkey_changed(self, hk: str) -> None:
        self._config["stop_hotkey"] = hk
        self._stop_btn.setText(t("main.stop_btn", hk=ltr(hk)) if hk else t("main.stop_btn_no_hk"))
        self._persist_and_bind()

    def _on_settings_changed(self) -> None:
        mic_dev = self._mic_combo.currentData()
        self._config["mic_device"] = mic_dev
        self._config["monitor_enabled"] = self._monitor_chk.isChecked()
        self._vol_slider.setEnabled(self._monitor_chk.isChecked())
        self._vol_lbl.setText(ltr(f"{self._vol_slider.value()}%"))
        self._config["monitor_volume"] = round(self._vol_slider.value() / 100.0, 2)
        self._mic_vol_lbl.setText(ltr(f"{self._mic_vol_slider.value()}%"))
        self._config["mic_volume"] = round(self._mic_vol_slider.value() / 100.0, 2)
        self._injector.set_target_endpoint(self._current_mic_guid())
        if hasattr(self._ring, "set_mic_device"):
            self._ring.set_mic_device(mic_dev)
        self._refresh_status()
        self._on_save_state(self._config, self._clips)

    def _persist_and_bind(self) -> None:
        failed = self._on_save_state(self._config, self._clips)
        if failed:
            QMessageBox.warning(
                self,
                t("main.err_hotkey_title"),
                t("main.err_hotkey_body", hotkeys=ltr(", ".join(failed))),
            )

    def _show_status_toast(self, message: str, duration_ms: int = 2800) -> None:
        """Show a brief, non-intrusive status message without stealing focus or opening an empty picker."""
        self._status_bar_lbl.setText(message)
        if self._toast_label is not None:
            try:
                self._toast_label.close()
            except Exception:
                pass
            self._toast_label = None
        toast = QLabel(message)
        toast.setWindowFlags(
            Qt.WindowType.ToolTip
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        toast.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        toast.setStyleSheet(
            "background-color: #18181C; color: #F4F4F5; border: 1px solid #3F3F4A; "
            "border-radius: 8px; padding: 10px 16px; font-size: 13px; font-weight: 600;"
        )
        toast.adjustSize()
        app = QApplication.instance()
        screen = self.screen() if self.isVisible() else (app.primaryScreen() if app else None)
        if screen is not None:
            avail = screen.availableGeometry()
            x = avail.right() - toast.width() - 24
            y = avail.bottom() - toast.height() - 28
            toast.move(max(avail.left() + 12, x), max(avail.top() + 12, y))
        toast.show()
        self._toast_label = toast

        def _hide() -> None:
            if self._toast_label is toast:
                toast.close()
                self._toast_label = None

        QTimer.singleShot(duration_ms, _hide)

    def _unlink_capture_wav(self) -> None:
        wav = self._active_capture_wav
        self._active_capture_wav = None
        if wav is not None and wav.is_file():
            try:
                wav.unlink()
            except OSError:
                pass

    def _release_capture_session(self) -> None:
        """Close active picker/trim dialogs and free frozen multi-channel snapshot RAM & temp WAV."""
        self._transitioning_session = True
        try:
            picker = self._active_picker
            self._active_picker = None
            if picker is not None:
                try:
                    picker.close()
                except Exception:
                    pass
            popup = self._active_popup
            self._active_popup = None
            if popup is not None:
                try:
                    popup.close()
                except Exception:
                    pass
        finally:
            self._transitioning_session = False

        snap = self._active_snapshot
        self._active_snapshot = None
        self._active_selected_keys = []
        if snap is not None:
            snap.release()
        self._unlink_capture_wav()

    def capture_last_30s(self) -> None:
        self._release_capture_session()
        secs = self._buffer_seconds()

        if hasattr(self._ring, "snapshot_channels"):
            snap = self._ring.snapshot_channels()
            if snap.is_empty:
                snap.release()
                self._show_status_toast(t("main.status_no_audio", n=secs))
                return
            self._active_snapshot = snap
            self._active_selected_keys = snap.default_selected_keys()
            self._open_channel_picker()
            return

        # Legacy single-buffer fallback
        audio, sr = self._ring.snapshot()
        if audio.size == 0:
            self._show_status_toast(t("main.status_no_audio", n=secs))
            return
        snap_wav = self._tmp_capture_dir / f"capture_{uuid.uuid4().hex[:8]}.wav"
        write_wav(snap_wav, audio, sr)
        self._active_capture_wav = snap_wav
        self._open_trim_dialog(snap_wav, existing=None, buffer_seconds=secs)

    def _open_channel_picker(self) -> None:
        snap = self._active_snapshot
        if snap is None or snap.is_empty:
            self._release_capture_session()
            return
        picker = ChannelPickerDialog(
            snap,
            self._injector,
            self.get_volumes,
            initial_selected=self._active_selected_keys,
            parent=self if self.isVisible() else None,
        )
        picker.channels_confirmed.connect(self._on_channels_confirmed)
        picker.finished.connect(self._on_picker_finished)
        self._active_picker = picker
        ensure_on_screen(picker)
        picker.show()
        picker.raise_()
        picker.activateWindow()

    def _on_picker_finished(self, _res: int = 0) -> None:
        self._active_picker = None
        if not self._transitioning_session and self._active_popup is None:
            # User cancelled or closed picker directly -> free frozen snapshot
            snap = self._active_snapshot
            self._active_snapshot = None
            self._active_selected_keys = []
            if snap is not None:
                snap.release()
            self._unlink_capture_wav()

    def _on_channels_confirmed(self, selected_keys: list[str]) -> None:
        snap = self._active_snapshot
        if snap is None or not selected_keys:
            return
        self._active_selected_keys = list(selected_keys)
        mixed_i16, sr = snap.mix(self._active_selected_keys)
        if mixed_i16.size == 0:
            self._show_status_toast(t("main.status_no_audio_channels"))
            return

        self._unlink_capture_wav()
        snap_wav = self._tmp_capture_dir / f"capture_{uuid.uuid4().hex[:8]}.wav"
        write_wav(snap_wav, mixed_i16, sr)
        self._active_capture_wav = snap_wav

        names = [c.name for c in snap.channels if c.key in set(self._active_selected_keys)]
        summary = " + ".join(names[:3]) + (f" +{len(names) - 3}" if len(names) > 3 else "")

        self._transitioning_session = True
        try:
            picker = self._active_picker
            self._active_picker = None
            if picker is not None:
                picker.hide()
            self._open_trim_dialog(
                snap_wav,
                existing=None,
                on_change_channels=self._return_to_channel_picker,
                channel_summary=summary,
                channel_count=len(self._active_selected_keys),
                buffer_seconds=self._buffer_seconds(),
            )
        finally:
            self._transitioning_session = False

    def _return_to_channel_picker(self) -> None:
        if self._active_snapshot is None:
            return
        self._transitioning_session = True
        try:
            popup = self._active_popup
            self._active_popup = None
            if popup is not None:
                popup.close()
            self._open_channel_picker()
        finally:
            self._transitioning_session = False

    def _on_trim_finished(self, _res: int = 0) -> None:
        self._active_popup = None
        if not self._transitioning_session and self._active_picker is None:
            snap = self._active_snapshot
            self._active_snapshot = None
            self._active_selected_keys = []
            if snap is not None:
                snap.release()
            self._unlink_capture_wav()

    def _open_manage_effects(self) -> None:
        preview_wav: Optional[Path] = None
        for clip in self._clips:
            candidate = resolve_clip_path(clip.file)
            if candidate.is_file():
                preview_wav = candidate
                break

        dlg = ManageEffectsDialog(
            self._injector,
            self.get_volumes,
            preview_wav=preview_wav,
            parent=self,
        )

        def _sync_open_trim() -> None:
            if self._active_popup is not None:
                try:
                    self._active_popup.reload_effects(select_id=dlg.last_saved_id)
                except Exception:
                    pass

        dlg.effects_changed.connect(_sync_open_trim)
        ensure_on_screen(dlg)
        dlg.exec()
        _sync_open_trim()

    def _open_trim_dialog(
        self,
        wav_path: Path,
        existing: Optional[ClipEntry],
        *,
        on_change_channels: Optional[Callable[[], None]] = None,
        channel_summary: Optional[str] = None,
        channel_count: int = 0,
        default_name: Optional[str] = None,
        buffer_seconds: Optional[int] = None,
        import_name: Optional[str] = None,
    ) -> None:
        if self._active_popup is not None:
            self._active_popup.close()

        dlg = TrimDialog(
            wav_path,
            self._injector,
            self.get_volumes,
            existing_clip=existing,
            on_change_channels=on_change_channels,
            channel_summary=channel_summary,
            channel_count=channel_count,
            default_name=default_name,
            buffer_seconds=buffer_seconds,
            import_name=import_name,
            parent=self if self.isVisible() else None,
        )
        dlg.clip_saved.connect(self._on_clip_saved)
        dlg.finished.connect(self._on_trim_finished)
        self._active_popup = dlg
        ensure_on_screen(dlg)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _on_clip_saved(self, entry: ClipEntry) -> None:
        replaced = False
        for i, existing in enumerate(self._clips):
            if existing.id == entry.id:
                self._clips[i] = entry
                replaced = True
                break
        if not replaced:
            self._clips.insert(0, entry)
        self._rebuild_clip_list()
        self._persist_and_bind()

    def _edit_clip(self, entry: ClipEntry) -> None:
        wav_path = resolve_clip_path(entry.file)
        if not wav_path.is_file():
            QMessageBox.warning(self, t("main.err_missing_file_title"), t("main.err_missing_file_body", path=wav_path))
            return
        self._open_trim_dialog(wav_path, existing=entry)

    def _default_export_dir(self) -> Path:
        if self._last_export_dir is not None and self._last_export_dir.is_dir():
            return self._last_export_dir
        desktop = Path.home() / "Desktop"
        return desktop if desktop.is_dir() else Path.home()

    def _export_clip_mp3(self, entry: ClipEntry) -> None:
        src_wav = resolve_clip_path(entry.file)
        if not src_wav.is_file():
            self._show_status_toast(t("main.export_missing", name=entry.name))
            return

        default_name = f"{sanitize_filename(entry.name)}.mp3"
        initial_path = str(self._default_export_dir() / default_name)
        chosen, _ = QFileDialog.getSaveFileName(
            self,
            t("main.export_dialog_title"),
            initial_path,
            t("main.export_filter"),
        )
        if not chosen:
            return

        dst_path = Path(chosen)
        if dst_path.suffix.lower() != ".mp3":
            dst_path = dst_path.with_name(dst_path.name + ".mp3")
        self._last_export_dir = dst_path.parent
        self._status_bar_lbl.setText(t("main.export_in_progress", name=dst_path.name))

        worker = _ExportWorker(src_wav, dst_path, title=entry.name, parent=self)
        self._export_workers.append(worker)
        worker.export_finished.connect(self._on_export_finished, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(lambda w=worker: self._cleanup_export_worker(w))
        worker.start()

    @Slot(str, str)
    def _on_export_finished(self, dst_str: str, err: str) -> None:
        if err:
            self._show_status_toast(t("main.export_failed", err=err))
            return
        fname = Path(dst_str).name if dst_str else "MP3"
        self._show_status_toast(t("main.export_done", name=fname))

    def _cleanup_export_worker(self, worker: _ExportWorker) -> None:
        try:
            self._export_workers.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()

    def _show_clip_context_menu(self, card: QWidget, pos, entry: ClipEntry) -> None:
        menu = QMenu(self)
        preview_act = menu.addAction(t("main.ctx_preview"))
        play_act = menu.addAction(t("main.ctx_play"))
        edit_act = menu.addAction(t("main.ctx_edit"))
        export_act = menu.addAction(t("main.ctx_export"))
        menu.addSeparator()
        del_act = menu.addAction(t("main.ctx_delete"))

        chosen = menu.exec(card.mapToGlobal(pos))
        if chosen is preview_act:
            self.preview_clip_by_id(entry.id)
        elif chosen is play_act:
            self.play_clip_by_id(entry.id)
        elif chosen is edit_act:
            self._edit_clip(entry)
        elif chosen is export_act:
            self._export_clip_mp3(entry)
        elif chosen is del_act:
            self._delete_clip(entry)

    def _delete_clip(self, entry: ClipEntry) -> None:
        if self._playing_clip_id == entry.id:
            self.stop_all_audio()
        self._clips = [c for c in self._clips if c.id != entry.id]
        wav_path = resolve_clip_path(entry.file)
        if wav_path.is_file():
            try:
                wav_path.unlink()
            except OSError:
                pass
        self._rebuild_clip_list()
        self._persist_and_bind()

    def _start_saved_clip(self, clip_id: str, *, mode: str) -> None:
        entry = next((c for c in self._clips if c.id == clip_id), None)
        if not entry:
            return
        if self._playing_clip_id == clip_id and self._playing_clip_mode == mode:
            self.stop_all_audio()
            return

        # Always stop any active audio (saved clip, picker, or trim dialog) first
        self._clip_play_seq += 1
        self._playing_clip_id = None
        self._playing_clip_mode = None
        self._injector.stop_all()
        self._stop_other_dialog_playback()
        self._sync_clip_playback_buttons()

        wav_path = resolve_clip_path(entry.file)
        if not wav_path.is_file():
            self._set_status_message(t("main.clip_err_missing_wav", name=entry.name), hold_ms=4500, is_error=True)
            return

        is_loop = mode == "loop"
        send_to_mic = mode in ("mic", "loop")
        if send_to_mic:
            self._injector.refresh_apo_status()
            if not self._injector.apo_installed:
                self._set_status_message(t("main.clip_err_apo_disabled"), hold_ms=5000, is_error=True)
                return
            if not self._injector.ensure_ring_ready():
                self._set_status_message(t("main.clip_err_ring_unavailable"), hold_ms=5000, is_error=True)
                return

        mon, mon_vol, mic_vol = self.get_volumes()
        seq = self._clip_play_seq
        self._playing_clip_id = clip_id
        self._playing_clip_mode = mode
        self._sync_clip_playback_buttons()
        if is_loop:
            stop_hk = str(self._config.get("stop_hotkey", "") or "").strip()
            msg = (
                t("main.status_looping_mic", name=entry.name, hk=ltr(stop_hk))
                if stop_hk
                else t("main.status_looping_mic_no_hk", name=entry.name)
            )
            self._set_status_message(msg, hold_ms=86400000, is_error=False)
        else:
            status_key = "main.status_playing_mic" if send_to_mic else "main.status_previewing_clip"
            self._set_status_message(t(status_key, name=entry.name), hold_ms=60000, is_error=False)

        try:
            started = self._injector.play_wav(
                wav_path,
                volume=mon_vol,
                mic_volume=mic_vol,
                send_to_mic=send_to_mic,
                monitor_enabled=mon if send_to_mic else True,
                loop=is_loop,
                on_done=lambda s=seq: self.clip_playback_done.emit(s),
            )
        except Exception as exc:
            self._playing_clip_id = None
            self._playing_clip_mode = None
            self._sync_clip_playback_buttons()
            self._set_status_message(t("main.clip_err_playback_failed", err=exc), hold_ms=5000, is_error=True)
            return

        if not started:
            self._playing_clip_id = None
            self._playing_clip_mode = None
            self._sync_clip_playback_buttons()
            self._set_status_message(t("main.clip_err_playback_failed", err="Empty audio"), hold_ms=4000, is_error=True)

    def play_clip_by_id(self, clip_id: str) -> None:
        self._start_saved_clip(clip_id, mode="mic")

    def loop_clip_by_id(self, clip_id: str) -> None:
        self._start_saved_clip(clip_id, mode="loop")

    def preview_clip_by_id(self, clip_id: str) -> None:
        self._start_saved_clip(clip_id, mode="preview")

    def _on_clip_hotkey_changed(self, clip_id: str, hk: str) -> None:
        for c in self._clips:
            if c.id == clip_id:
                c.hotkey = hk
                break
        self._persist_and_bind()

    def _rebuild_clip_list(self) -> None:
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._clip_row_buttons.clear()

        n_clips = len(self._clips)
        self._count_lbl.setText(
            t("main.clip_count_one") if n_clips == 1 else t("main.clip_count_many", n=n_clips)
        )

        if not self._clips:
            cap_hk = self._config.get("capture_hotkey", "F9")
            secs = self._buffer_seconds()
            empty_card = QFrame()
            empty_card.setObjectName("EmptyStateCard")
            empty_lay = QVBoxLayout(empty_card)
            empty_lay.setContentsMargins(24, 32, 24, 32)
            empty_lay.setSpacing(6)
            empty = QLabel(t("main.empty_clips", hk=ltr(cap_hk), n=secs))
            empty.setStyleSheet("color: #8B8B96; font-size: 13px;")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_lay.addWidget(empty)
            self._list_layout.insertWidget(0, empty_card)
            return

        for idx, clip in enumerate(self._clips):
            row_container = QWidget()
            row_container.setObjectName("ClipRow")
            row_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            outer_row = QHBoxLayout(row_container)
            outer_row.setContentsMargins(0, 0, 0, 0)
            outer_row.setSpacing(8)

            export_btn = QPushButton(t("main.clip_export_btn"))
            export_btn.setObjectName("ExportCard")
            export_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
            export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            export_btn.setToolTip(t("main.clip_export_tooltip"))
            export_btn.clicked.connect(lambda _=False, c=clip: self._export_clip_mp3(c))

            card = QFrame()
            card.setObjectName("Card")
            card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            card.customContextMenuRequested.connect(
                lambda pos, c=clip, w=card: self._show_clip_context_menu(w, pos, c)
            )
            row = QHBoxLayout(card)
            row.setContentsMargins(12, 6, 12, 6)
            row.setSpacing(8)

            info = QVBoxLayout()
            info.setContentsMargins(0, 0, 0, 0)
            info.setSpacing(1)
            name_lbl = QLabel(clip.name)
            name_lbl.setAlignment(_text_align())
            name_lbl.setStyleSheet("font-weight: 700; font-size: 13px; color: #F4F4F5;")
            preset_disp = localize_effect_name(None, clip.preset)
            meta_lbl = QLabel(t("main.clip_meta", dur=clip.duration, preset=preset_disp))
            meta_lbl.setAlignment(_text_align())
            meta_lbl.setStyleSheet("color: #9A9AA2; font-size: 11px;")
            info.addWidget(name_lbl)
            info.addWidget(meta_lbl)

            hk_btn = HotkeyCaptureButton(clip.hotkey)
            hk_btn.setToolTip(t("main.clip_hk_tooltip"))
            hk_btn.setMinimumWidth(86)
            hk_btn.hotkey_changed.connect(lambda hk, cid=clip.id: self._on_clip_hotkey_changed(cid, hk))

            preview_btn = QPushButton(t("main.clip_preview_btn"))
            preview_btn.setToolTip(t("main.clip_preview_tooltip"))
            preview_btn.clicked.connect(lambda _=False, cid=clip.id: self.preview_clip_by_id(cid))

            play_btn = QPushButton(t("main.clip_play_btn"))
            play_btn.setObjectName("Accent")
            play_btn.setToolTip(t("main.clip_play_tooltip"))
            play_btn.clicked.connect(lambda _=False, cid=clip.id: self.play_clip_by_id(cid))

            loop_btn = QPushButton(t("main.clip_loop_btn"))
            loop_btn.setToolTip(t("main.clip_loop_tooltip"))
            loop_btn.clicked.connect(lambda _=False, cid=clip.id: self.loop_clip_by_id(cid))

            edit_btn = QPushButton(t("main.clip_edit_btn"))
            edit_btn.setToolTip(t("main.clip_edit_tooltip"))
            edit_btn.clicked.connect(lambda _=False, c=clip: self._edit_clip(c))

            del_btn = QPushButton("✕")
            del_btn.setObjectName("Danger")
            del_btn.setMinimumWidth(34)
            del_btn.setToolTip(t("main.clip_delete_tooltip"))
            del_btn.clicked.connect(lambda _=False, c=clip: self._delete_clip(c))

            row.addLayout(info, 1)
            row.addWidget(hk_btn)
            row.addSpacing(6)
            row.addWidget(preview_btn)
            row.addWidget(play_btn)
            row.addWidget(loop_btn)
            row.addWidget(edit_btn)
            row.addWidget(del_btn)

            outer_row.addWidget(export_btn)
            outer_row.addWidget(card, 1)
            self._clip_row_buttons[clip.id] = (preview_btn, play_btn, loop_btn)
            self._list_layout.insertWidget(idx, row_container)

        self._sync_clip_playback_buttons()
