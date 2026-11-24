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
import mic_inject
import paths


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
