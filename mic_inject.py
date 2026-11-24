from __future__ import annotations

import ctypes
import logging
import mmap
import os
import struct
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

from effects import read_wav
from native.directmic import apo_setup

_log = logging.getLogger("clipmic.mic_inject")

RING_MAGIC = 0x434D5043  # "CPMC"
RING_VERSION = 1
RING_RATE = 48000
RING_CAPACITY = 65536  # power of two (~1.36s at 48 kHz)
HEADER_BYTES = 4096
HEADER_FMT = "<IIIIQQIIffIIII"
MODE_ADD = 0
MODE_REPLACE = 1


def _tick64() -> int:
    return int(ctypes.windll.kernel32.GetTickCount64())


def _match_endpoint_guid(dev_name: str, endpoints: list[dict]) -> Optional[str]:
    low = dev_name.lower()
    for ep in endpoints:
        if ep["name"].lower() == low:
            return ep["guid"]
    for ep in endpoints:
        if ep["friendly"] and ep["friendly"].lower() in low:
            return ep["guid"]
    for ep in endpoints:
        if ep["desc"] and ep["desc"].lower() in low:
            return ep["guid"]
    return endpoints[0]["guid"] if len(endpoints) == 1 else None


def list_microphones() -> list[dict]:
    """List Windows WASAPI input devices (microphones), putting the default mic first."""
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception as exc:
        _log.warning("Failed to query audio devices: %s", exc)
        return []
    wasapi_idx = next(
        (i for i, h in enumerate(hostapis) if "WASAPI" in h.get("name", "").upper()),
        None,
    )
    default_in_idx = None
    if wasapi_idx is not None:
        default_in_idx = hostapis[wasapi_idx].get("default_input_device")
    endpoints = apo_setup.list_capture_endpoints()
    mics: list[dict] = []
    for idx, dev in enumerate(devices):
        if int(dev.get("max_input_channels", 0)) <= 0:
            continue
        if wasapi_idx is not None and dev.get("hostapi") != wasapi_idx:
            continue
        name = str(dev.get("name", f"Device {idx}"))
        if "(Loopback)" in name or "[Loopback]" in name:
            continue
        mics.append({
            "index": idx,
            "name": name,
            "guid": _match_endpoint_guid(name, endpoints),
            "is_default": idx == default_in_idx,
        })
    mics.sort(key=lambda m: (not m["is_default"], "steam streaming" in m["name"].lower()))
    return mics


class DirectMicRing:
    """Memory-mapped ring buffer writer for ClipMic Direct Mic APO (ring.bin)."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self._dir = data_dir or apo_setup.DATA_DIR
        self._path = self._dir / "ring.bin"
        self._file = None
        self._mm: Optional[mmap.mmap] = None
        self._write_pos = 0
        self._mask = RING_CAPACITY - 1
        self._lock = threading.RLock()

    def ensure_open(self) -> bool:
        with self._lock:
            if self._mm is not None:
                return True
            try:
                total_bytes = HEADER_BYTES + RING_CAPACITY * 4
                exists = self._path.is_file() and self._path.stat().st_size == total_bytes
                if not exists:
                    # Only attempt creating ring.bin if we have directory write permission (admin/dev)
                    self._dir.mkdir(parents=True, exist_ok=True)
                    self._file = open(self._path, "w+b")
                    self._file.truncate(total_bytes)
                else:
                    self._file = open(self._path, "r+b")
                self._mm = mmap.mmap(self._file.fileno(), total_bytes, access=mmap.ACCESS_WRITE)
                if not exists:
                    self._mm[:HEADER_BYTES] = b"\x00" * HEADER_BYTES
                    self._write_pos = 0
                    self._write_header(enabled=0, mode=MODE_ADD, gain=1.0, mic_gain=1.0)
                else:
                    hdr = struct.unpack_from(HEADER_FMT, self._mm, 0)
                    if hdr[0] != RING_MAGIC or hdr[1] != RING_VERSION or hdr[3] != RING_CAPACITY:
                        self._mm[:HEADER_BYTES] = b"\x00" * HEADER_BYTES
                        self._write_pos = 0
                        self._write_header(enabled=0, mode=MODE_ADD, gain=1.0, mic_gain=1.0)
                    else:
                        self._write_pos = int(hdr[4])
                return True
            except OSError as exc:
                _log.warning("Could not open Direct Mic ring %s: %s", self._path, exc)
                self.close()
                return False

    def _write_header(self, *, enabled: int, mode: int, gain: float, mic_gain: float) -> None:
        if self._mm is None:
            return
        # Pack offsets 0..52 so we never clobber effect_version (offset 52) written by the APO
        struct.pack_into(
            "<IIIIQQIIffI",
            self._mm,
            0,
            RING_MAGIC,
            RING_VERSION,
            RING_RATE,
            RING_CAPACITY,
            self._write_pos,
            _tick64(),
            enabled,
            mode,
            float(gain),
            float(mic_gain),
            0,  # default lead
        )
        struct.pack_into("<I", self._mm, 56, os.getpid())

    def write_samples(self, mono_f32: np.ndarray, *, enabled: int = 1, gain: float = 1.0, mode: int = MODE_ADD) -> None:
        with self._lock:
            if self._mm is None or mono_f32.size == 0:
                return
            arr = np.ascontiguousarray(mono_f32, dtype="<f4")
            n = int(arr.size)
            pos = self._write_pos & self._mask
            first = min(n, RING_CAPACITY - pos)
            off = HEADER_BYTES + pos * 4
            self._mm[off:off + first * 4] = arr[:first].tobytes()
            if first < n:
                rem = n - first
                self._mm[HEADER_BYTES:HEADER_BYTES + rem * 4] = arr[first:].tobytes()
            self._write_pos += n
            self._write_header(enabled=enabled, mode=mode, gain=gain, mic_gain=1.0)

    def set_idle(self) -> None:
        with self._lock:
            if self._mm is not None:
                self._write_header(enabled=0, mode=MODE_ADD, gain=1.0, mic_gain=1.0)

    def flush_and_idle(self) -> None:
        """Immediately disable APO mixing and zero all queued sample data in the ring."""
        with self._lock:
            if self._mm is not None:
                self._write_header(enabled=0, mode=MODE_ADD, gain=0.0, mic_gain=1.0)
                sample_bytes = RING_CAPACITY * 4
                self._mm[HEADER_BYTES:HEADER_BYTES + sample_bytes] = b"\x00" * sample_bytes
                self._write_header(enabled=0, mode=MODE_ADD, gain=1.0, mic_gain=1.0)

    def close(self) -> None:
        with self._lock:
            if self._mm is not None:
                try:
                    self._write_header(enabled=0, mode=MODE_ADD, gain=1.0, mic_gain=1.0)
                    self._mm.close()
                except Exception:
                    pass
                self._mm = None
            if self._file is not None:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None


def _to_48k_mono(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    arr = np.asarray(audio)
    if np.issubdtype(arr.dtype, np.integer):
        arr = arr.astype(np.float32) / 32768.0
    else:
        arr = arr.astype(np.float32, copy=False)
    mono = arr.mean(axis=1) if arr.ndim > 1 else arr
    if int(sample_rate) == RING_RATE or mono.size == 0:
        return np.ascontiguousarray(mono, dtype=np.float32)
    out_len = max(1, int(round(mono.size * (RING_RATE / float(sample_rate)))))
    x_old = np.linspace(0.0, 1.0, mono.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, out_len, endpoint=False)
    return np.interp(x_new, x_old, mono).astype(np.float32)


class MicInjector:
    """Audio playback & Windows Direct-Mic APO bridge (with safe speaker fallback)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._play_lock = threading.Lock()
        self._stream: Optional[sd.OutputStream] = None
        self._apo_thread: Optional[threading.Thread] = None
        self._playing = False
        self._play_gen = 0
        self._stop_event = threading.Event()
        self._on_done: Optional[Callable[[], None]] = None
        self._endpoint_guid: Optional[str] = None
        self._ring = DirectMicRing()
        self._status_cache: dict = apo_setup.get_status(None)

    def set_target_endpoint(self, endpoint_guid: Optional[str]) -> None:
        self._endpoint_guid = endpoint_guid
        self.refresh_apo_status()

    def refresh_apo_status(self) -> dict:
        self._status_cache = apo_setup.get_status(self._endpoint_guid)
        if self._status_cache.get("active"):
            self._ring.ensure_open()
        return self._status_cache

    def ensure_ring_ready(self) -> bool:
        return bool(self._ring.ensure_open())

    def apo_status(self) -> dict:
        return dict(self._status_cache)

    @property
    def apo_installed(self) -> bool:
        return bool(self._status_cache.get("active", False))

    @property
    def is_playing(self) -> bool:
        return self._playing

    def status_label(self) -> str:
        return "Direct-Mic APO Active" if self.apo_installed else "Speaker Preview Mode (No APO)"

    def stop_all(self) -> bool:
        """Immediately abort active mic/monitor playback, stop sounddevice, and flush DirectMicRing.

        Returns True if playback was active when called, False if already idle.
        """
        with self._lock:
            stream = self._stream
            apo_t = self._apo_thread
            was_playing = bool(
                self._playing
                or stream is not None
                or (apo_t is not None and apo_t.is_alive())
            )
            self._play_gen += 1
            old_stop = self._stop_event
            self._stop_event = threading.Event()
            self._stream = None
            self._apo_thread = None
            self._playing = False
            self._on_done = None
        old_stop.set()
        self._ring.flush_and_idle()
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        try:
            sd.stop()
        except Exception:
            pass
        if apo_t is not None and apo_t is not threading.current_thread():
            apo_t.join(timeout=0.35)
        self._ring.flush_and_idle()
        return was_playing

    def stop(self) -> None:
        self.stop_all()

    def close(self) -> None:
        """Stop all playback and release the shared Direct Mic ring buffer."""
        self.stop()
        self._ring.close()

    def play_wav(
        self,
        wav_path: str | Path,
        *,
        volume: float = 1.0,
        mic_volume: Optional[float] = None,
        send_to_mic: bool = False,
        monitor_enabled: bool = True,
        start_sec: float = 0.0,
        on_done: Optional[Callable[[], None]] = None,
    ) -> bool:
        data, sr = read_wav(wav_path)
        return self.play_array(
            data,
            sr,
            volume=volume,
            mic_volume=mic_volume,
            send_to_mic=send_to_mic,
            monitor_enabled=monitor_enabled,
            start_sec=start_sec,
            on_done=on_done,
        )

    def play_array(
        self,
        audio: np.ndarray,
        sample_rate: int,
        *,
        volume: float = 1.0,
        mic_volume: Optional[float] = None,
        send_to_mic: bool = False,
        monitor_enabled: bool = True,
        start_sec: float = 0.0,
        on_done: Optional[Callable[[], None]] = None,
    ) -> bool:
        call_done_immediately = False
        started = False
        with self._play_lock:
            self.stop()
            with self._lock:
                base_gen = self._play_gen
            sr = int(sample_rate)
            arr = np.asarray(audio)
            if arr.size == 0 or sr <= 0:
                call_done_immediately = True
            else:
                start_frame = int(round(max(0.0, float(start_sec)) * sr)) if start_sec > 0.0 else 0
                if start_frame >= len(arr):
                    call_done_immediately = True
                else:
                    fade_in = start_frame > 0
                    if start_frame > 0:
                        arr = arr[start_frame:]

                    use_apo = bool(send_to_mic and self.apo_installed and self._ring.ensure_open())
                    play_speaker = bool(monitor_enabled or (send_to_mic and not use_apo))
                    if not use_apo and not play_speaker:
                        call_done_immediately = True
                    else:
                        stop_evt = threading.Event()
                        aborted = False
                        with self._lock:
                            if self._play_gen != base_gen:
                                aborted = True
                            else:
                                self._play_gen += 1
                                gen = self._play_gen
                                self._stop_event = stop_evt
                                self._on_done = on_done
                        if not aborted:
                            started = True
                            pending = {"count": (1 if use_apo else 0) + (1 if play_speaker else 0)}

                            def mark_done():
                                cb = None
                                with self._lock:
                                    if gen != self._play_gen or stop_evt.is_set():
                                        return
                                    pending["count"] -= 1
                                    if pending["count"] <= 0:
                                        self._playing = False
                                        cb = self._on_done
                                        self._on_done = None
                                if cb:
                                    try:
                                        cb()
                                    except Exception:
                                        pass

                            if use_apo:
                                mono_48k = _to_48k_mono(arr, sr)
                                if fade_in and mono_48k.size > 1:
                                    mono_48k = mono_48k.copy()
                                    fn = min(mono_48k.size, 120)
                                    mono_48k[:fn] *= np.linspace(0.0, 1.0, fn, dtype=np.float32)
                                apo_gain = float(np.clip(volume if mic_volume is None else mic_volume, 0.0, 2.0))
                                self._start_apo_stream(
                                    mono_48k, gain=apo_gain, gen=gen, stop_evt=stop_evt, on_done=mark_done
                                )

                            if play_speaker:
                                self._start_speaker_stream(
                                    arr,
                                    sr,
                                    volume=float(np.clip(volume, 0.0, 2.0)),
                                    fade_in=fade_in,
                                    gen=gen,
                                    stop_evt=stop_evt,
                                    on_done=mark_done,
                                )

        if call_done_immediately and on_done:
            on_done()
        return started

    def _start_apo_stream(
        self,
        mono_48k: np.ndarray,
        *,
        gain: float,
        gen: int,
        stop_evt: threading.Event,
        on_done: Callable[[], None],
    ) -> None:
        holder: dict[str, Optional[threading.Thread]] = {"t": None}

        def worker():
            try:
                chunk = 480  # 10 ms at 48 kHz
                lead_frames = 2400  # 50 ms pre-fill so APO starts immediately on 10ms or 20ms engines
                pos = 0
                total = int(mono_48k.size)
                t0 = time.perf_counter()
                written = 0

                first = min(total, lead_frames)
                if first > 0 and not stop_evt.is_set():
                    self._ring.write_samples(mono_48k[:first], enabled=1, gain=gain)
                    pos = first
                    written = first

                while pos < total and not stop_evt.is_set():
                    target_t = t0 + max(0.0, (written - lead_frames) / float(RING_RATE))
                    wait = target_t - time.perf_counter()
                    if wait > 0.001:
                        stop_evt.wait(min(wait, 0.015))
                        if stop_evt.is_set():
                            break
                    end = min(total, pos + chunk)
                    self._ring.write_samples(mono_48k[pos:end], enabled=1, gain=gain)
                    written += end - pos
                    pos = end

                if not stop_evt.is_set():
                    # Flush 30 ms tail silence so APO plays the end cleanly & fades out
                    self._ring.write_samples(np.zeros(lead_frames, dtype=np.float32), enabled=1, gain=gain)
                    stop_evt.wait(0.035)
            finally:
                with self._lock:
                    is_current = gen == self._play_gen
                    if self._apo_thread is holder["t"]:
                        self._apo_thread = None
                if is_current:
                    self._ring.set_idle()
                on_done()

        t = threading.Thread(target=worker, name="ClipMic-APO-Feed", daemon=True)
        holder["t"] = t
        with self._lock:
            if gen != self._play_gen or stop_evt.is_set():
                return
            self._apo_thread = t
            self._playing = True
        t.start()

    def _start_speaker_stream(
        self,
        audio: np.ndarray,
        sample_rate: int,
        *,
        volume: float,
        fade_in: bool = False,
        gen: int,
        stop_evt: threading.Event,
        on_done: Callable[[], None],
    ) -> None:
        arr = np.asarray(audio)
        if np.issubdtype(arr.dtype, np.integer):
            arr = arr.astype(np.float32) / 32768.0
        else:
            arr = arr.astype(np.float32, copy=True)
        if arr.ndim == 1:
            arr = arr[:, None]
        if arr.shape[1] > 2:
            arr = arr[:, :2].copy()
        if volume != 1.0:
            arr = np.clip(arr * volume, -1.0, 1.0)
        if fade_in and len(arr) > 1:
            fade_n = min(len(arr), max(1, int(round(sample_rate * 0.0025))))
            ramp = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)[:, None]
            arr[:fade_n] *= ramp

        state = {"pos": 0}
        total = len(arr)
        channels = arr.shape[1]
        holder: dict[str, Optional[sd.OutputStream]] = {"stream": None}

        def callback(outdata, frames, _time, _status):
            if stop_evt.is_set():
                outdata.fill(0)
                raise sd.CallbackStop()
            pos = state["pos"]
            rem = total - pos
            if rem <= 0:
                outdata.fill(0)
                raise sd.CallbackStop()
            take = min(frames, rem)
            outdata[:take] = arr[pos:pos + take]
            if take < frames:
                outdata[take:].fill(0)
            state["pos"] = pos + take
            if state["pos"] >= total:
                raise sd.CallbackStop()

        def finished():
            my_stream = holder["stream"]
            with self._lock:
                if self._stream is my_stream:
                    self._stream = None
            if my_stream is not None and not stop_evt.is_set():
                try:
                    my_stream.close()
                except Exception:
                    pass
            on_done()

        try:
            stream = sd.OutputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype="float32",
                callback=callback,
                finished_callback=finished,
            )
            holder["stream"] = stream
            with self._lock:
                if gen != self._play_gen or stop_evt.is_set():
                    try:
                        stream.close()
                    except Exception:
                        pass
                    return
                self._stream = stream
                self._playing = True
            stream.start()
        except Exception as exc:
            _log.warning("Speaker preview playback failed (%d Hz, %dch): %s", sample_rate, channels, exc)
            on_done()
