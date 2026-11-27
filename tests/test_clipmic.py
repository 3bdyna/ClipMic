from __future__ import annotations

import os
import re
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import audio_buffer
import effects
import hotkeys
import i18n
import main as clipmic_main
import mic_inject
import paths
import updater


class TestPreviewEndLatency(unittest.TestCase):
    """Reproduce and guard against the ~1.25s post-clip lag in speaker preview playback."""

    def test_speaker_preview_48khz_fires_on_done_promptly(self) -> None:
        inj = mic_inject.MicInjector()
        try:
            dur_s = 0.40
            sr = 48000
            audio = np.zeros((int(sr * dur_s), 2), dtype=np.float32)
            done = threading.Event()
            done_ts = [0.0]

            def _cb() -> None:
                done_ts[0] = time.perf_counter()
                done.set()

            t0 = time.perf_counter()
            started = inj.play_array(
                audio, sr, volume=0.0, send_to_mic=False, monitor_enabled=True, on_done=_cb
            )
            self.assertTrue(started)
            self.assertTrue(done.wait(timeout=dur_s + 2.5), "on_done did not fire")
            overrun_ms = (done_ts[0] - t0 - dur_s) * 1000.0
            self.assertLess(
                overrun_ms,
                220.0,
                f"Preview end detection took +{overrun_ms:.1f}ms past clip end (expected < 220ms)",
            )
        finally:
            inj.close()

    def test_speaker_preview_44100hz_fires_on_done_promptly(self) -> None:
        inj = mic_inject.MicInjector()
        try:
            dur_s = 0.40
            sr = 44100
            audio = np.zeros((int(sr * dur_s), 2), dtype=np.float32)
            done = threading.Event()
            done_ts = [0.0]

            def _cb() -> None:
                done_ts[0] = time.perf_counter()
                done.set()

            t0 = time.perf_counter()
            started = inj.play_array(
                audio, sr, volume=0.0, send_to_mic=False, monitor_enabled=True, on_done=_cb
            )
            self.assertTrue(started)
            self.assertTrue(done.wait(timeout=dur_s + 2.5), "on_done did not fire")
            overrun_ms = (done_ts[0] - t0 - dur_s) * 1000.0
            self.assertLess(
                overrun_ms,
                220.0,
                f"44.1kHz preview end detection took +{overrun_ms:.1f}ms past clip end (expected < 220ms)",
            )
        finally:
            inj.close()

    def test_stop_mid_preview_does_not_fire_stale_on_done(self) -> None:
        inj = mic_inject.MicInjector()
        try:
            audio = np.zeros((48000 * 2, 2), dtype=np.float32)
            called = threading.Event()
            inj.play_array(
                audio, 48000, volume=0.0, send_to_mic=False, monitor_enabled=True, on_done=called.set
            )
            time.sleep(0.08)
            was_playing = inj.stop_all()
            self.assertTrue(was_playing)
            self.assertFalse(called.wait(timeout=0.15), "on_done should not fire after explicit stop_all()")
        finally:
            inj.close()


class TestTrimDialogPreviewUI(unittest.TestCase):
    """Verify TrimDialog preview end lag and play/stop button icon state."""

    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_trim_dialog_preview_resets_promptly_and_updates_icon(self) -> None:
        from PySide6.QtCore import QEventLoop, QTimer
        import ui

        inj = mic_inject.MicInjector()
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "short.wav"
            dur_s = 0.45
            effects.write_wav(wav, np.zeros((int(48000 * dur_s), 2), dtype=np.int16), 48000)

            dlg = ui.TrimDialog(wav, inj, lambda: (True, 0.0, 0.0))
            try:
                dlg._cache_key = dlg._current_key()
                dlg._rendered_wav = wav

                idle_icon_key = dlg._play_btn.icon().cacheKey()
                loop = QEventLoop()
                done_ts = [0.0]
                orig_done = dlg._on_playback_done

                def _on_done(seq: int) -> None:
                    done_ts[0] = time.perf_counter()
                    orig_done(seq)
                    loop.quit()

                dlg.playback_done.disconnect()
                dlg.playback_done.connect(_on_done)

                t0 = time.perf_counter()
                dlg._on_play_preview()
                playing_icon_key = dlg._play_btn.icon().cacheKey()
                self.assertTrue(dlg._is_playing)
                self.assertNotEqual(
                    playing_icon_key,
                    idle_icon_key,
                    "Play button icon should switch from Play to Stop while previewing",
                )

                QTimer.singleShot(3000, loop.quit)
                loop.exec()

                self.assertGreater(done_ts[0], 0.0, "TrimDialog playback_done did not fire")
                overrun_ms = (done_ts[0] - t0 - dur_s) * 1000.0
                self.assertLess(
                    overrun_ms,
                    250.0,
                    f"TrimDialog preview took +{overrun_ms:.1f}ms past clip end (expected < 250ms)",
                )
                self.assertFalse(dlg._is_playing)
                self.assertEqual(dlg._play_btn.icon().cacheKey(), idle_icon_key)
            finally:
                dlg.close()
                inj.close()

    def test_trim_dialog_reset_to_original(self) -> None:
        import ui

        inj = mic_inject.MicInjector()
        non_normal = [fx["id"] for fx in effects.load_effects() if fx["id"] != "normal"]
        target_fx = non_normal[0] if non_normal else "normal"
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "orig.wav"
            dur_s = 2.0
            effects.write_wav(wav, np.zeros((int(48000 * dur_s), 2), dtype=np.int16), 48000)

            dlg = ui.TrimDialog(
                wav,
                inj,
                lambda: (True, 0.0, 0.0),
                initial_start=0.4,
                initial_end=1.3,
                initial_effect_id=target_fx,
                has_raw_source=True,
            )
            try:
                s0, e0 = dlg._waveform.trim_range()
                self.assertAlmostEqual(s0, 0.4, places=2)
                self.assertAlmostEqual(e0, 1.3, places=2)
                self.assertEqual(dlg._current_effect_id, target_fx)

                dlg._cache_key = dlg._current_key()
                dlg._rendered_wav = wav
                dlg._rendered_dur_s = 0.9
                before_lbl = dlg._range_lbl.text()

                dlg._on_reset_original()
                s1, e1 = dlg._waveform.trim_range()
                self.assertAlmostEqual(s1, 0.0, places=3)
                self.assertAlmostEqual(e1, dlg._duration_s, places=3)
                self.assertEqual(dlg._current_effect_id, "normal")
                self.assertIsNone(dlg._cache_key)
                self.assertIsNone(dlg._rendered_wav)
                self.assertIsNone(dlg._rendered_dur_s)
                self.assertNotEqual(dlg._range_lbl.text(), before_lbl)
                self.assertIn("2.00", dlg._range_lbl.text())
            finally:
                dlg.close()
                inj.close()

    def test_capture_dialogs_not_pinned_stays_on_top(self) -> None:
        from PySide6.QtCore import Qt
        import audio_buffer
        import ui

        inj = mic_inject.MicInjector()
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "clip.wav"
            effects.write_wav(wav, np.zeros((48000, 2), dtype=np.int16), 48000)

            ch = audio_buffer.ChannelClip(
                key="game:fortnite",
                name="Fortnite",
                kind="game",
                audio=np.zeros((48000, 2), dtype=np.float32),
                sample_rate=48000,
                peak_db=-10.0,
                rms_db=-20.0,
                ms_since_signal=0,
                mini_peaks=np.zeros(40, dtype=np.float32),
            )
            snap = audio_buffer.CaptureSnapshot(48000, [ch])
            picker = ui.ChannelPickerDialog(snap, inj, lambda: (True, 0.0, 0.0))
            dlg = ui.TrimDialog(wav, inj, lambda: (True, 0.0, 0.0))
            try:
                self.assertFalse(bool(picker.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
                self.assertFalse(bool(dlg.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
                ui.bring_window_to_front_once(picker)
                ui.bring_window_to_front_once(dlg)
                self.assertFalse(bool(picker.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
                self.assertFalse(bool(dlg.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
            finally:
                picker.close()
                dlg.close()
                snap.release()
                inj.close()


class TestEffectsAndAudioCore(unittest.TestCase):
    def test_probe_wav_duration_reads_header_accurately(self) -> None:
        self.assertTrue(
            hasattr(effects, "probe_wav_duration"),
            "effects.probe_wav_duration helper should exist for fast header-only duration checks",
        )
        with tempfile.TemporaryDirectory() as td:
            wav = Path(td) / "probe.wav"
            effects.write_wav(wav, np.zeros((48000 * 3, 2), dtype=np.int16), 48000)
            dur = effects.probe_wav_duration(wav)
            self.assertAlmostEqual(dur, 3.0, places=3)

    def test_filter_chain_and_filename_sanitization(self) -> None:
        self.assertIsNone(effects.build_filter_from_params(effects.DEFAULT_PARAMS, 48000))
        flt = effects.build_filter_from_params({"pitch": 1.35, "echo": "room", "crunch": 40}, 48000)
        self.assertIsNotNone(flt)
        self.assertIn("asetrate=", flt)
        self.assertIn("aecho=", flt)
        self.assertIn("alimiter=", flt)
        self.assertEqual(effects.sanitize_filename("CON"), "_CON")
        self.assertEqual(effects.sanitize_filename('a<b>c:d/e\\f|g?h*i'), "abcdefghi")

    def test_hotkeys_and_config_roundtrip(self) -> None:
        self.assertIsNotNone(hotkeys.parse_hotkey("Ctrl+Shift+F9"))
        self.assertIsNone(hotkeys.parse_hotkey("Ctrl+"))
        self.assertEqual(hotkeys.clamp_buffer_seconds(2), hotkeys.MIN_BUFFER_SECONDS)
        self.assertEqual(hotkeys.clamp_buffer_seconds(999), hotkeys.MAX_BUFFER_SECONDS)
        with tempfile.TemporaryDirectory() as td:
            cfg_file = Path(td) / "config.json"
            hotkeys.save_config({"buffer_seconds": 45, "language": "ar"}, cfg_file)
            loaded = hotkeys.load_config(cfg_file)
            self.assertEqual(loaded["buffer_seconds"], 45)
            self.assertEqual(loaded["language"], "ar")

    def test_mic_ring_buffer_write_resize_and_snapshot(self) -> None:
        mic = audio_buffer.MicRingBuffer(duration_s=2.0)
        mic._buf = np.zeros((mic._capacity, 2), dtype=np.int16)
        mic._running = True
        chunk = np.full((1920, 2), 0.25, dtype=np.float32)
        for _ in range(10):
            mic._on_audio(chunk, 1920, None, None)
        data, sr, _qpc, _freq = mic.snapshot()
        self.assertEqual(sr, 48000)
        self.assertEqual(len(data), 19200)
        self.assertGreater(float(data.max()), 0.2)
        mic.set_duration(1.0)
        data2, _, _, _ = mic.snapshot()
        self.assertEqual(len(data2), 19200)

    def test_mix_float_tracks_soft_limits_without_clipping(self) -> None:
        t1 = np.full((4800, 2), 0.75, dtype=np.float32)
        t2 = np.full((4800, 2), 0.75, dtype=np.float32)
        mixed = audio_buffer.mix_float_tracks([t1, t2])
        self.assertEqual(mixed.dtype, np.int16)
        self.assertLess(int(mixed.max()), 32767)
        self.assertGreater(int(mixed.max()), 27000)

    def test_snapshot_bundle_roundtrip_and_delete(self) -> None:
        sr = 48000
        a1 = np.full((4800, 2), 0.25, dtype=np.float32)
        a2 = np.full((4800, 2), -0.5, dtype=np.float32)
        ch1 = audio_buffer.ChannelClip(
            key="game:1",
            name="Game",
            kind="game",
            audio=a1,
            sample_rate=sr,
            peak_db=-12.0,
            rms_db=-14.0,
            ms_since_signal=50,
            mini_peaks=audio_buffer._compute_mini_peaks(a1),
        )
        ch2 = audio_buffer.ChannelClip(
            key="mic:default",
            name="Mic",
            kind="mic",
            audio=a2,
            sample_rate=sr,
            peak_db=-6.0,
            rms_db=-8.0,
            ms_since_signal=10,
            mini_peaks=audio_buffer._compute_mini_peaks(a2),
        )
        snap = audio_buffer.CaptureSnapshot(sr, [ch1, ch2])
        with tempfile.TemporaryDirectory() as td:
            bundle_dir = Path(td) / "bundle_abc"
            audio_buffer.save_snapshot_bundle(snap, bundle_dir, ["game:1"])
            self.assertTrue((bundle_dir / "meta.json").is_file())

            loaded = audio_buffer.load_snapshot_bundle(bundle_dir)
            self.assertIsNotNone(loaded)
            loaded_snap, loaded_keys = loaded  # type: ignore[misc]
            self.assertEqual(loaded_snap.sample_rate, sr)
            self.assertEqual(loaded_keys, ["game:1"])
            self.assertEqual(len(loaded_snap.channels), 2)
            self.assertEqual(loaded_snap.channels[0].key, "game:1")
            self.assertEqual(loaded_snap.channels[1].kind, "mic")
            self.assertEqual(loaded_snap.channels[0].audio.shape, (4800, 2))
            self.assertTrue(np.allclose(loaded_snap.channels[0].audio, a1, atol=1e-3))
            self.assertTrue(np.allclose(loaded_snap.channels[1].audio, a2, atol=1e-3))
            self.assertEqual(len(loaded_snap.channels[0].mini_peaks), 120)

            ch0_path = bundle_dir / "ch_0.wav"
            ch0_bytes_before = ch0_path.read_bytes()
            ch0_mtime_before = ch0_path.stat().st_mtime_ns
            self.assertTrue(audio_buffer.update_snapshot_selected_keys(bundle_dir, ["game:1", "mic:default"]))
            self.assertEqual(ch0_path.read_bytes(), ch0_bytes_before)
            self.assertEqual(ch0_path.stat().st_mtime_ns, ch0_mtime_before)
            loaded2 = audio_buffer.load_snapshot_bundle(bundle_dir)
            self.assertIsNotNone(loaded2)
            self.assertEqual(loaded2[1], ["game:1", "mic:default"])  # type: ignore[index]

            audio_buffer.delete_snapshot_bundle(bundle_dir)
            self.assertFalse(bundle_dir.exists())
            self.assertIsNone(audio_buffer.load_snapshot_bundle(bundle_dir))
            self.assertFalse(audio_buffer.update_snapshot_selected_keys(bundle_dir, ["game:1"]))

    def test_clip_entry_backward_compat_and_roundtrip(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as td:
            clips_file = Path(td) / "clips.json"
            legacy_payload = [
                {
                    "id": "old123",
                    "name": "Legacy Clip",
                    "file": "clips/legacy.wav",
                    "hotkey": "Ctrl+1",
                    "preset": "Radio",
                    "duration": 1.25,
                }
            ]
            clips_file.write_text(json.dumps(legacy_payload), encoding="utf-8")
            loaded = hotkeys.load_clips(clips_file)
            self.assertEqual(len(loaded), 1)
            c = loaded[0]
            self.assertEqual(c.id, "old123")
            self.assertEqual(c.source_dir, "")
            self.assertEqual(c.selected_keys, [])
            self.assertEqual(c.trim_start, 0.0)
            self.assertEqual(c.trim_end, 0.0)
            self.assertEqual(c.effect_id, "normal")

            updated = hotkeys.ClipEntry(
                id="new456",
                name="Editable Clip",
                file="clips/new.wav",
                hotkey="F8",
                preset="Radio",
                duration=2.5,
                source_dir="clips/_sources/new456",
                selected_keys=["game:1", "mic:default"],
                trim_start=0.5,
                trim_end=3.0,
                effect_id="radio",
            )
            hotkeys.save_clips([c, updated], clips_file)
            reloaded = hotkeys.load_clips(clips_file)
            self.assertEqual(len(reloaded), 2)
            r = reloaded[1]
            self.assertEqual(r.source_dir, "clips/_sources/new456")
            self.assertEqual(r.selected_keys, ["game:1", "mic:default"])
            self.assertAlmostEqual(r.trim_start, 0.5)
            self.assertAlmostEqual(r.trim_end, 3.0)
            self.assertEqual(r.effect_id, "radio")


class TestPackagingAndI18nReadiness(unittest.TestCase):
    def test_spec_bundles_required_native_dlls_icon_and_licenses(self) -> None:
        spec_text = (ROOT / "ClipMic.spec").read_text(encoding="utf-8")
        self.assertNotIn(
            "build_ffmpeg_tmp.exe",
            spec_text,
            "ClipMic.spec references missing temporary file build_ffmpeg_tmp.exe",
        )
        self.assertIn(
            "clipmic_procloop.dll",
            spec_text,
            "ClipMic.spec must bundle native/procloopback/clipmic_procloop.dll for per-app capture",
        )
        self.assertIn(
            "clipmic_apo.dll",
            spec_text,
            "ClipMic.spec must bundle native/directmic/clipmic_apo.dll for Direct Mic APO",
        )
        self.assertIn(
            "('clipmic.ico', '.')",
            spec_text,
            "ClipMic.spec must include clipmic.ico in datas so frozen app loads the real icon",
        )
        self.assertIn("LICENSE", spec_text)
        self.assertIn("THIRD_PARTY_NOTICES.txt", spec_text)

    def test_native_dlls_exist_and_load(self) -> None:
        self.assertTrue((ROOT / "native" / "directmic" / "clipmic_apo.dll").is_file())
        self.assertTrue((ROOT / "native" / "procloopback" / "clipmic_procloop.dll").is_file())
        self.assertTrue((ROOT / "clipmic.ico").is_file())

    def test_i18n_complete_and_matching(self) -> None:
        en_keys = set(i18n.TRANSLATIONS["en"].keys())
        ar_keys = set(i18n.TRANSLATIONS["ar"].keys())
        self.assertEqual(en_keys, ar_keys)
        for pyf in ("main.py", "ui.py"):
            src = (ROOT / pyf).read_text(encoding="utf-8")
            for m in re.finditer(r'\bt\(\s*["\']([^"\']+)["\']', src):
                self.assertIn(m.group(1), en_keys, f"Missing i18n key {m.group(1)} used in {pyf}")


class TestFastClipCore(unittest.TestCase):
    def test_detect_channel_kind_recognizes_known_games(self) -> None:
        from audio_buffer import detect_channel_kind

        for exe in ("overwatch.exe", "valorant-win64-shipping.exe", "cs2.exe"):
            self.assertEqual(
                detect_channel_kind(exe, exe_path=f"C:\\CustomFolder\\{exe}", check_modules=False),
                "game",
            )
        self.assertEqual(
            detect_channel_kind("discord.exe", exe_path="C:\\CustomFolder\\discord.exe", check_modules=False),
            "app",
        )

    def test_snapshot_slice_seconds_trims_and_preserves_short(self) -> None:
        from audio_buffer import CaptureSnapshot, ChannelClip

        sr = 48000
        peaks = np.zeros(0, dtype=np.float32)
        long_audio = np.ones((sr * 10, 2), dtype=np.float32) * 0.25
        short_audio = np.ones((sr * 2, 2), dtype=np.float32) * 0.5
        snap = CaptureSnapshot(
            sr,
            [
                ChannelClip("g1", "Game", "game", long_audio, sr, -12.0, -14.0, 50, peaks),
                ChannelClip("a1", "App", "app", short_audio, sr, -6.0, -8.0, 100, peaks),
            ],
        )
        sliced = snap.slice_seconds(4.0)
        self.assertEqual(len(sliced.channel_by_key("g1").audio), sr * 4)
        self.assertEqual(len(sliced.channel_by_key("a1").audio), sr * 2)

    def test_choose_fast_clip_channels_priority(self) -> None:
        from audio_buffer import ChannelClip, choose_fast_clip_channels

        sr = 48000
        silent = np.zeros((100, 2), dtype=np.float32)
        peaks = np.zeros(0, dtype=np.float32)
        ch_mic = ChannelClip("__mic__", "Mic", "mic", silent, sr, -5.0, -8.0, 10, peaks)
        ch_sys = ChannelClip("__system__", "System", "app", silent, sr, -12.0, -15.0, 10, peaks)
        ch_app_loud = ChannelClip("app1", "Chrome", "app", silent, sr, -6.0, -10.0, 100, peaks)
        ch_game_quiet = ChannelClip("g1", "CS2", "game", silent, sr, -20.0, -25.0, 200, peaks)
        ch_game_loud = ChannelClip("g2", "Valorant", "game", silent, sr, -10.0, -12.0, 100, peaks)

        self.assertEqual(
            choose_fast_clip_channels([ch_mic, ch_sys, ch_app_loud, ch_game_quiet, ch_game_loud]),
            ["g2"],
        )
        self.assertEqual(
            choose_fast_clip_channels([ch_mic, ch_sys, ch_app_loud]),
            ["app1"],
        )
        ch_app_silent = ChannelClip("app_s", "IdleApp", "app", silent, sr, -70.0, -75.0, 100, peaks)
        self.assertEqual(
            choose_fast_clip_channels([ch_mic, ch_sys, ch_app_silent]),
            ["__system__"],
        )
        self.assertEqual(choose_fast_clip_channels([ch_mic]), ["__mic__"])
        self.assertEqual(choose_fast_clip_channels([]), [])

    def test_fast_clip_config_roundtrip_and_clamping(self) -> None:
        import hotkeys

        self.assertEqual(hotkeys.clamp_fast_clip_seconds(0), hotkeys.MIN_FAST_CLIP_SECONDS)
        self.assertEqual(hotkeys.clamp_fast_clip_seconds(999), hotkeys.MAX_FAST_CLIP_SECONDS)
        self.assertEqual(hotkeys.clamp_fast_clip_seconds("bad"), hotkeys.DEFAULT_FAST_CLIP_SECONDS)
        self.assertEqual(hotkeys.clamp_fast_clip_seconds(12), 12)

        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "config.json"
            cfg_path.write_text('{"buffer_seconds": 30}', encoding="utf-8")
            loaded = hotkeys.load_config(cfg_path)
            self.assertEqual(loaded["fast_clip_hotkey"], "F8")
            self.assertEqual(loaded["fast_clip_seconds"], hotkeys.DEFAULT_FAST_CLIP_SECONDS)
            self.assertEqual(loaded["fast_clip_effect"], "normal")

            loaded["fast_clip_hotkey"] = "Ctrl+F7"
            loaded["fast_clip_seconds"] = 150
            loaded["fast_clip_effect"] = "radio"
            hotkeys.save_config(loaded, cfg_path)
            reloaded = hotkeys.load_config(cfg_path)
            self.assertEqual(reloaded["fast_clip_hotkey"], "Ctrl+F7")
            self.assertEqual(reloaded["fast_clip_seconds"], hotkeys.MAX_FAST_CLIP_SECONDS)
            self.assertEqual(reloaded["fast_clip_effect"], "radio")


class _FakeHTTPResponse:
    """Minimal context-managed fake urllib response for offline updater tests."""

    def __init__(self, body: bytes, url: str, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self._pos = 0
        self._url = url
        self.headers = headers or {"Content-Length": str(len(body))}

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            out = self._body[self._pos :]
            self._pos = len(self._body)
            return out
        out = self._body[self._pos : self._pos + n]
        self._pos += len(out)
        return out

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        pass

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *args) -> None:
        self.close()


class TestUpdaterAndReleaseGuardrails(unittest.TestCase):
    def test_semver_parsing_and_numeric_comparison(self) -> None:
        self.assertEqual(updater.parse_semver("1.0.0"), (1, 0, 0))
        self.assertEqual(updater.parse_semver("v1.10.2"), (1, 10, 2))
        self.assertEqual(updater.parse_semver("V2.0.15"), (2, 0, 15))
        for bad in ("", "1.0", "1.0.0.0", "1.0.0-beta", "vv1.0.0", "1.a.0", "1.0.0\n", "١.٢.٣"):
            with self.assertRaises(ValueError, msg=f"Should reject {bad!r}"):
                updater.parse_semver(bad)

        self.assertTrue(updater.is_newer_version("1.10.0", "1.9.0"))
        self.assertTrue(updater.is_newer_version("v1.2.0", "1.1.9"))
        self.assertFalse(updater.is_newer_version("1.2.0", "1.2.0"))
        self.assertFalse(updater.is_newer_version("v1.2.0", "1.2.0"))
        self.assertFalse(updater.is_newer_version("1.1.9", "1.2.0"))
        self.assertFalse(updater.is_newer_version("not-a-ver", "1.0.0"))

    def test_trusted_download_url_allowlist(self) -> None:
        good_urls = [
            "https://github.com/3bdyna/clipmic/releases/download/v1.1.0/ClipMic-Setup-1.1.0.exe",
            "https://api.github.com/repos/3bdyna/clipmic/releases/latest",
            "https://objects.githubusercontent.com/github-production-release-asset/123",
            "https://release-assets.githubusercontent.com/github-production-release-asset/456",
            "https://codeload.github.com/3bdyna/clipmic/zip/refs/tags/v1.1.0",
        ]
        for url in good_urls:
            self.assertTrue(updater.is_trusted_download_url(url), f"Expected trusted: {url}")

        bad_urls = [
            "",
            "http://github.com/3bdyna/clipmic/releases/download/v1.1.0/ClipMic-Setup-1.1.0.exe",
            "file:///C:/Windows/System32/cmd.exe",
            "https://evil.com/ClipMic-Setup-1.1.0.exe",
            "https://github.com.evil.com/3bdyna/clipmic/releases/latest",
            "https://sub.github.com/3bdyna/clipmic/releases/latest",
            "https://github.com@evil.com/ClipMic-Setup-1.1.0.exe",
            "https://user:pass@github.com/3bdyna/clipmic",
            "https://github.com:8443/3bdyna/clipmic",
            "https://github.com:/3bdyna/clipmic",
            "https://github.com/3bdyna\\clipmic",
            "https://github.com/path\x7f",
            "https://github.com./3bdyna/clipmic",
            "https://github.com/path\r\nX-Injected: 1",
        ]
        for url in bad_urls:
            self.assertFalse(updater.is_trusted_download_url(url), f"Expected rejected: {url}")

        self.assertTrue(
            updater.is_trusted_release_page_url("https://github.com/3bdyna/clipmic/releases/tag/v1.1.0")
        )
        self.assertFalse(
            updater.is_trusted_release_page_url("https://objects.githubusercontent.com/3bdyna/clipmic")
        )
        self.assertFalse(
            updater.is_trusted_release_page_url("https://github.com/other/repo/releases/tag/v1.1.0")
        )

    def test_redirect_handler_blocks_untrusted_hops(self) -> None:
        import urllib.request

        handler = updater._SafeGitHubRedirectHandler()
        req = urllib.request.Request("https://github.com/3bdyna/clipmic/releases/download/v1.1.0/a.exe")
        with self.assertRaises(updater.UntrustedUrlError):
            handler.redirect_request(req, None, 302, "Found", {}, "https://evil.example/payload.exe")
        with self.assertRaises(updater.UntrustedUrlError):
            handler.redirect_request(req, None, 302, "Found", {}, "//evil.example/payload.exe")
        with self.assertRaises(updater.UntrustedUrlError):
            handler.redirect_request(req, None, 302, "Found", {}, "http://objects.githubusercontent.com/a.exe")
        allowed = handler.redirect_request(
            req, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/a.exe"
        )
        self.assertIsNotNone(allowed)
        self.assertEqual(allowed.full_url, "https://release-assets.githubusercontent.com/a.exe")

    def test_parse_sha256sums_and_verify_file_sha256(self) -> None:
        import hashlib

        payload = b"clipmic-installer-test-payload-bytes"
        expected = hashlib.sha256(payload).hexdigest()
        sums_two_spaces = f"\ufeff# comment\n{expected.upper()}  ClipMic-Setup-1.1.0.exe\n"
        sums_star = f"{expected} *ClipMic-Setup-1.1.0.exe\n"
        sums_wrong_path = f"{expected}  dist/ClipMic-Setup-1.1.0.exe\n"
        sums_bad_hex = f"{'z' * 64}  ClipMic-Setup-1.1.0.exe\n"

        self.assertEqual(updater.parse_sha256sums(sums_two_spaces, "ClipMic-Setup-1.1.0.exe"), expected)
        self.assertEqual(updater.parse_sha256sums(sums_star, "ClipMic-Setup-1.1.0.exe"), expected)
        self.assertIsNone(updater.parse_sha256sums(sums_two_spaces, "Other.exe"))
        self.assertIsNone(updater.parse_sha256sums(sums_wrong_path, "ClipMic-Setup-1.1.0.exe"))
        self.assertIsNone(updater.parse_sha256sums(sums_bad_hex, "ClipMic-Setup-1.1.0.exe"))
        self.assertIsNone(updater.parse_sha256sums(sums_two_spaces, "../ClipMic-Setup-1.1.0.exe"))

        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "ClipMic-Setup-1.1.0.exe"
            f.write_bytes(payload)
            self.assertTrue(updater.verify_file_sha256(f, expected))
            self.assertTrue(updater.verify_file_sha256(f, expected.upper()))
            self.assertFalse(updater.verify_file_sha256(f, "0" * 64))
            self.assertFalse(updater.verify_file_sha256(f, "short"))

    def test_check_for_updates_mocked_scenarios(self) -> None:
        import json
        import ssl
        import urllib.error
        from unittest import mock

        base_release = {
            "tag_name": "v1.2.0",
            "draft": False,
            "prerelease": False,
            "body": "Bug fixes and improvements.",
            "html_url": "https://github.com/3bdyna/clipmic/releases/tag/v1.2.0",
            "published_at": "2026-11-24T00:00:00Z",
            "assets": [
                {
                    "name": "ClipMic-Setup-1.2.0.exe.sig",
                    "size": 64,
                    "browser_download_url": "https://github.com/3bdyna/clipmic/releases/download/v1.2.0/ClipMic-Setup-1.2.0.exe.sig",
                },
                {
                    "name": "ClipMic-Setup-1.2.0.exe",
                    "size": 123456,
                    "browser_download_url": "https://github.com/3bdyna/clipmic/releases/download/v1.2.0/ClipMic-Setup-1.2.0.exe",
                },
                {
                    "name": "SHA256SUMS.txt",
                    "size": 95,
                    "browser_download_url": "https://github.com/3bdyna/clipmic/releases/download/v1.2.0/SHA256SUMS.txt",
                },
            ],
        }

        def _fake_open_json(payload: dict):
            raw = json.dumps(payload).encode("utf-8")
            return lambda url, **kw: _FakeHTTPResponse(raw, url)

        # 1. Newer release with valid installer + SHA256SUMS.txt
        with mock.patch.object(updater, "_open_trusted_url", side_effect=_fake_open_json(base_release)):
            res = updater.check_for_updates(current_version="1.0.0")
            self.assertEqual(res.status, "update_available")
            self.assertIsNotNone(res.release)
            assert res.release is not None
            self.assertEqual(res.release.version, "1.2.0")
            self.assertEqual(res.release.installer_name, "ClipMic-Setup-1.2.0.exe")

        # 2. Already up to date
        with mock.patch.object(updater, "_open_trusted_url", side_effect=_fake_open_json(base_release)):
            res = updater.check_for_updates(current_version="1.2.0")
            self.assertEqual(res.status, "up_to_date")
            self.assertIsNone(res.release)

        # 3. Draft / prerelease ignored
        for flag in ("draft", "prerelease"):
            flagged = dict(base_release, **{flag: True})
            with mock.patch.object(updater, "_open_trusted_url", side_effect=_fake_open_json(flagged)):
                res = updater.check_for_updates(current_version="1.0.0")
                self.assertEqual(res.status, "up_to_date")

        # 4. Missing SHA256SUMS.txt rejected
        no_sums = dict(base_release, assets=[base_release["assets"][1]])
        with mock.patch.object(updater, "_open_trusted_url", side_effect=_fake_open_json(no_sums)):
            res = updater.check_for_updates(current_version="1.0.0")
            self.assertEqual(res.status, "error")
            self.assertEqual(res.error_code, "missing_assets")

        # 5. 404, 403 rate limit, network error, TLS error handled cleanly without raising
        err_404 = urllib.error.HTTPError("https://api.github.com/x", 404, "Not Found", {}, None)
        with mock.patch.object(updater, "_open_trusted_url", side_effect=err_404):
            res = updater.check_for_updates(current_version="1.0.0")
            self.assertEqual(res.status, "error")
            self.assertEqual(res.error_code, "no_releases")

        err_403 = urllib.error.HTTPError(
            "https://api.github.com/x", 403, "rate limit exceeded", {"X-RateLimit-Remaining": "0"}, None
        )
        with mock.patch.object(updater, "_open_trusted_url", side_effect=err_403):
            res = updater.check_for_updates(current_version="1.0.0")
            self.assertEqual(res.status, "error")
            self.assertEqual(res.error_code, "rate_limited")

        with mock.patch.object(updater, "_open_trusted_url", side_effect=urllib.error.URLError("offline")):
            res = updater.check_for_updates(current_version="1.0.0")
            self.assertEqual(res.status, "error")
            self.assertEqual(res.error_code, "network")

        with mock.patch.object(
            updater, "_open_trusted_url", side_effect=urllib.error.URLError(ssl.SSLError("cert failed"))
        ):
            res = updater.check_for_updates(current_version="1.0.0")
            self.assertEqual(res.status, "error")
            self.assertEqual(res.error_code, "tls")

        import http.client

        with mock.patch.object(
            updater, "_open_trusted_url", side_effect=http.client.IncompleteRead(b"partial")
        ):
            res = updater.check_for_updates(current_version="1.0.0")
            self.assertEqual(res.status, "error")
            self.assertEqual(res.error_code, "network")

    def test_missing_issuer_ssl_triggers_root_refresh_and_retry(self) -> None:
        import ssl
        import urllib.error
        from unittest import mock

        url = "https://api.github.com/repos/3bdyna/clipmic/releases/latest"
        missing_err = urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get issuer certificate")
        )
        ok_resp = _FakeHTTPResponse(b'{"tag_name": "v1.0.1"}', url)

        first_opener = mock.Mock()
        first_opener.open.side_effect = missing_err
        second_opener = mock.Mock()
        second_opener.open.return_value = ok_resp
        redirect_h = updater._SafeGitHubRedirectHandler()

        with mock.patch.object(
            updater,
            "_build_safe_opener",
            side_effect=[(first_opener, redirect_h), (second_opener, redirect_h)],
        ), mock.patch.object(updater, "_refresh_windows_root_store", return_value=True) as mock_refresh:
            resp = updater._open_trusted_url(url, headers={"User-Agent": "ClipMic/1.0.1"}, timeout=5.0)
            self.assertIs(resp, ok_resp)
            mock_refresh.assert_called_once()
            self.assertEqual(mock_refresh.call_args[0][0], [url])

        # Non-issuer SSL error (e.g. hostname mismatch / revoked cert) must NOT trigger refresh
        other_err = urllib.error.URLError(ssl.SSLCertVerificationError(1, "hostname mismatch"))
        bad_opener = mock.Mock()
        bad_opener.open.side_effect = other_err
        with mock.patch.object(
            updater, "_build_safe_opener", return_value=(bad_opener, redirect_h)
        ), mock.patch.object(updater, "_refresh_windows_root_store") as mock_refresh2:
            with self.assertRaises(urllib.error.URLError):
                updater._open_trusted_url(url, headers={"User-Agent": "ClipMic/1.0.1"}, timeout=5.0)
            mock_refresh2.assert_not_called()

    def test_download_and_verify_update_success_tamper_and_cancel(self) -> None:
        import hashlib
        from unittest import mock

        installer_bytes = b"MZ" + (b"ClipMicTestData" * 12000)
        good_sha = hashlib.sha256(installer_bytes).hexdigest()
        sums_bytes = f"{good_sha}  ClipMic-Setup-1.2.0.exe\n".encode("utf-8")

        release = updater.ReleaseInfo(
            version="1.2.0",
            tag_name="v1.2.0",
            notes="Release notes",
            html_url="https://github.com/3bdyna/clipmic/releases/tag/v1.2.0",
            installer_name="ClipMic-Setup-1.2.0.exe",
            installer_url="https://github.com/3bdyna/clipmic/releases/download/v1.2.0/ClipMic-Setup-1.2.0.exe",
            installer_size=len(installer_bytes),
            checksums_url="https://github.com/3bdyna/clipmic/releases/download/v1.2.0/SHA256SUMS.txt",
            published_at="2026-11-24T00:00:00Z",
        )

        # 1. Valid download succeeds and leaves no .part file behind
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            seen_progress: list[tuple[int, int]] = []

            def _ok_open(url: str, **kw):
                if url.endswith("SHA256SUMS.txt"):
                    return _FakeHTTPResponse(sums_bytes, url)
                return _FakeHTTPResponse(installer_bytes, url)

            with mock.patch.object(updater, "_open_trusted_url", side_effect=_ok_open):
                out = updater.download_and_verify_update(
                    release, dest, on_progress=lambda d, tot: seen_progress.append((d, tot))
                )
            self.assertEqual(out, (dest / "ClipMic-Setup-1.2.0.exe").resolve())
            self.assertTrue(out.is_file())
            self.assertEqual(out.read_bytes(), installer_bytes)
            self.assertFalse((dest / "ClipMic-Setup-1.2.0.exe.part").exists())
            self.assertTrue(seen_progress and seen_progress[-1][0] == len(installer_bytes))

        # 2. Tampered bytes fail SHA-256 and delete partial/corrupt file
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            tampered = installer_bytes[:-4] + b"EVIL"

            def _tampered_open(url: str, **kw):
                if url.endswith("SHA256SUMS.txt"):
                    return _FakeHTTPResponse(sums_bytes, url)
                return _FakeHTTPResponse(tampered, url)

            with mock.patch.object(updater, "_open_trusted_url", side_effect=_tampered_open):
                with self.assertRaises(updater.UpdateError):
                    updater.download_and_verify_update(release, dest)
            self.assertFalse((dest / "ClipMic-Setup-1.2.0.exe").exists())
            self.assertFalse((dest / "ClipMic-Setup-1.2.0.exe.part").exists())

        # 3. Cancellation cleans up .part file
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td)
            cancel_ev = threading.Event()

            def _cancel_on_first_chunk(downloaded: int, _total: int) -> None:
                if downloaded > 0:
                    cancel_ev.set()

            with mock.patch.object(updater, "_open_trusted_url", side_effect=_ok_open):
                with self.assertRaises(updater.UpdateCancelledError):
                    updater.download_and_verify_update(
                        release, dest, on_progress=_cancel_on_first_chunk, cancel_event=cancel_ev
                    )
            self.assertFalse((dest / "ClipMic-Setup-1.2.0.exe").exists())
            self.assertFalse((dest / "ClipMic-Setup-1.2.0.exe.part").exists())

    def test_release_and_installer_static_guardrails(self) -> None:
        # 1. Single source of truth strict semver in paths.py
        self.assertRegex(paths.APP_VERSION, r"^\d+\.\d+\.\d+$")
        self.assertRegex(paths.GITHUB_REPO, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

        # 2. Inno Setup installer invariants
        iss_path = ROOT / "installer" / "ClipMic.iss"
        self.assertTrue(iss_path.is_file(), "installer/ClipMic.iss must exist")
        iss_text = iss_path.read_text(encoding="utf-8")
        self.assertRegex(
            iss_text,
            r"(?m)^AppId=\{\{[0-9A-Fa-f-]{36}\}$",
            "installer/ClipMic.iss must pin a fixed static AppId GUID",
        )
        self.assertIn("PrivilegesRequired=lowest", iss_text)
        self.assertIn(f"AppMutex={clipmic_main._MUTEX_NAME}", iss_text)
        self.assertIn(f"CheckForMutexes('{clipmic_main._MUTEX_NAME}')", iss_text)
        self.assertIn("AppVersion={#MyAppVersion}", iss_text)
        self.assertNotRegex(
            iss_text,
            r"(?m)^AppVersion=\d+\.\d+\.\d+",
            "installer/ClipMic.iss must derive AppVersion from {#MyAppVersion}, never hardcode it",
        )
        self.assertNotIn("[UninstallDelete]", iss_text)
        self.assertNotIn("{localappdata}\\ClipMic", iss_text.lower())
        self.assertIn("/RELAUNCH", iss_text)
        self.assertIn("/RELAUNCH", updater.build_installer_command(Path("ClipMic-Setup-1.0.0.exe"), silent=True))

        # 3. ClipMic.spec must not exclude TLS/SSL or urllib needed by updater.py
        spec_text = (ROOT / "ClipMic.spec").read_text(encoding="utf-8")
        for forbidden_exclude in ("'ssl'", '"ssl"', "'_ssl'", '"_ssl"', "'urllib'", '"urllib"'):
            self.assertNotIn(
                forbidden_exclude,
                spec_text,
                f"ClipMic.spec must not exclude {forbidden_exclude}",
            )

        # 4. Release script exists
        self.assertTrue((ROOT / "scripts" / "build_release.ps1").is_file())

    def test_update_dialog_reject_cancels_active_download(self) -> None:
        from PySide6.QtWidgets import QApplication
        import ui

        _app = QApplication.instance() or QApplication([])
        rel = updater.ReleaseInfo(
            version="1.2.0",
            tag_name="v1.2.0",
            notes="Notes",
            html_url="https://github.com/3bdyna/clipmic/releases/tag/v1.2.0",
            installer_name="ClipMic-Setup-1.2.0.exe",
            installer_url="https://github.com/3bdyna/clipmic/releases/download/v1.2.0/ClipMic-Setup-1.2.0.exe",
            installer_size=1024,
            checksums_url="https://github.com/3bdyna/clipmic/releases/download/v1.2.0/SHA256SUMS.txt",
            published_at="2026-11-24T00:00:00Z",
        )
        dlg = ui.UpdateDialog(rel, is_frozen_build=True)
        try:
            cancelled = []

            class _FakeWorker:
                def cancel(self) -> None:
                    cancelled.append(True)

            dlg._downloading = True
            dlg._dl_worker = _FakeWorker()  # type: ignore[assignment]
            dlg.reject()
            self.assertTrue(cancelled, "reject()/Escape must cancel an active download")
            self.assertFalse(dlg._downloading)
            self.assertIsNone(dlg.verified_installer_path)
        finally:
            dlg.deleteLater()


if __name__ == "__main__":
    unittest.main(verbosity=2)
