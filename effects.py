from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import uuid
import wave
from pathlib import Path
from typing import Any

import numpy as np

import paths

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

FFMPEG_MISSING_MSG = (
    "FFmpeg (ffmpeg.exe) was not found on this PC.\n\n"
    "ClipMic needs FFmpeg to trim clips and apply voice effects.\n"
    "To install it quickly, open PowerShell or Terminal and run:\n"
    "  winget install Gyan.FFmpeg\n\n"
    "Or download ffmpeg.exe from https://ffmpeg.org/download.html and place it "
    "next to ClipMic.exe, then restart ClipMic."
)

ECHO_MODES = ("none", "room", "cathedral")
TONE_MODES = ("normal", "feminine", "nasal", "bad_mic", "underwater")
EFFECTS_SCHEMA_VERSION = 2
SUPPORTED_AUDIO_EXTS: frozenset[str] = frozenset({
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus", ".aac", ".wma",
})
SUPPORTED_VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".wmv", ".m4v", ".flv", ".ts",
})
SUPPORTED_MEDIA_EXTS: frozenset[str] = SUPPORTED_AUDIO_EXTS | SUPPORTED_VIDEO_EXTS
MAX_IMPORT_DURATION_S: float = 600.0
_WIN_RESERVED_NAMES: frozenset[str] = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})

DEFAULT_PARAMS: dict[str, Any] = {
    "pitch": 1.0,
    "speed": 1.0,
    "wobble": 0,
    "echo": "none",
    "tone": "normal",
    "crunch": 0,
    "robot": False,
    "reverse": False,
}


def normalize_params(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Clamp and sanitize structured effect parameters into safe ranges."""
    src = raw if isinstance(raw, dict) else {}

    def _f(key: str, lo: float, hi: float, default: float) -> float:
        try:
            val = float(src.get(key, default))
            if not np.isfinite(val):
                return default
        except (TypeError, ValueError):
            return default
        return round(max(lo, min(hi, val)), 2)

    def _i(key: str, lo: int, hi: int, default: int) -> int:
        try:
            val = int(round(float(src.get(key, default))))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, val))

    echo = str(src.get("echo", "none")).strip().lower()
    if echo not in ECHO_MODES:
        echo = "none"

    tone = str(src.get("tone", "normal")).strip().lower()
    if tone not in TONE_MODES:
        tone = "normal"

    return {
        "pitch": _f("pitch", 0.60, 1.80, 1.0),
        "speed": _f("speed", 0.55, 1.80, 1.0),
        "wobble": _i("wobble", 0, 100, 0),
        "echo": echo,
        "tone": tone,
        "crunch": _i("crunch", 0, 100, 0),
        "robot": bool(src.get("robot", False)),
        "reverse": bool(src.get("reverse", False)),
    }


def _make_builtin(effect_id: str, name: str, **param_overrides: Any) -> dict[str, Any]:
    params = dict(DEFAULT_PARAMS)
    params.update(param_overrides)
    return {
        "id": effect_id,
        "name": name,
        "builtin": True,
        "params": normalize_params(params),
        "custom_filter": "",
    }


CORE_EFFECT_IDS: tuple[str, ...] = (
    "normal",
    "kid_high_pitch",
    "feminine_girl",
    "nerd_nasal",
    "mocking_spongebob",
)

EFFECT_LIBRARY: list[dict[str, Any]] = [
    _make_builtin("normal", "Normal"),
    _make_builtin("kid_high_pitch", "Kid / High Pitch", pitch=1.35),
    _make_builtin("feminine_girl", "Feminine / Girl", pitch=1.28, tone="feminine"),
    _make_builtin("nerd_nasal", "Nerd / Nasal", pitch=1.16, tone="nasal"),
    _make_builtin("mocking_spongebob", "Mocking / SpongeBob", pitch=1.24, wobble=75, tone="nasal"),
    _make_builtin("drunk_slurred", "Drunk / Slurred", pitch=0.92, speed=0.82, wobble=22),
    _make_builtin("lobby_mic_2009", "2009 Lobby Mic", tone="bad_mic", crunch=55),
    _make_builtin("underwater", "Underwater", tone="underwater", wobble=30),
    _make_builtin("cathedral_dramatic", "Cathedral / Dramatic", echo="cathedral"),
    _make_builtin("deep_fried_meme", "Deep-Fried / Meme", crunch=95),
    _make_builtin("fast_yap", "Fast Yap", pitch=1.10, speed=1.45),
    _make_builtin("reverse", "Reverse", reverse=True),
    _make_builtin("chipmunk", "Chipmunk", pitch=1.60),
    _make_builtin("robot", "Robot", robot=True),
    _make_builtin("echo", "Echo", echo="room"),
    _make_builtin("deep_demon", "Deep / Demon", pitch=0.72),
    _make_builtin("bass_boost_loud", "Bass Boost / Loud", crunch=35),
]

DEFAULT_EFFECTS: list[dict[str, Any]] = [
    e for e in EFFECT_LIBRARY if e["id"] in CORE_EFFECT_IDS
]

QUICK_PICK_IDS: tuple[str, ...] = CORE_EFFECT_IDS


def _atempo_chain(factor: float) -> list[str]:
    """Split an arbitrary tempo multiplier into FFmpeg-legal [0.5, 2.0] atempo stages."""
    f = max(0.1, min(8.0, float(factor)))
    if abs(f - 1.0) < 0.005:
        return []
    stages: list[str] = []
    while f < 0.5:
        stages.append("atempo=0.5")
        f /= 0.5
    while f > 2.0:
        stages.append("atempo=2.0")
        f /= 2.0
    if abs(f - 1.0) >= 0.005:
        stages.append(f"atempo={f:.4f}")
    return stages


def build_filter_from_params(params: dict[str, Any] | None, sample_rate: int) -> str | None:
    """Convert structured slider/toggle params into a valid FFmpeg filter chain."""
    p = normalize_params(params)
    sr = int(sample_rate) if 8000 <= int(sample_rate) <= 48000 else 48000
    stages: list[str] = []

    pitch = p["pitch"]
    speed = p["speed"]
    if abs(pitch - 1.0) >= 0.005:
        pitched_sr = max(4000, int(round(sr * pitch)))
        stages.extend([f"aresample={sr}", f"asetrate={pitched_sr}", f"aresample={sr}"])
        stages.extend(_atempo_chain(speed / pitch))
    elif abs(speed - 1.0) >= 0.005:
        stages.extend(_atempo_chain(speed))

    tone = p["tone"]
    if tone == "feminine":
        stages.append(
            "highpass=f=180,"
            "equalizer=f=260:t=q:w=1.0:g=-5,"
            "equalizer=f=2800:t=q:w=1.2:g=5,"
            "equalizer=f=5500:t=q:w=1.4:g=4"
        )
    elif tone == "nasal":
        stages.append(
            "highpass=f=480,lowpass=f=2800,"
            "equalizer=f=1150:t=q:w=1.5:g=18,"
            "equalizer=f=2200:t=q:w=1.6:g=8"
        )
    elif tone == "bad_mic":
        stages.append("highpass=f=380,lowpass=f=2500")
    elif tone == "underwater":
        stages.append("lowpass=f=450")

    wobble = p["wobble"]
    if wobble > 0:
        freq = round(1.0 + wobble * 0.085, 2)
        depth = round(min(0.95, 0.30 + (wobble / 100.0) * 0.55), 2)
        stages.append(f"vibrato=f={freq}:d={depth}")

    if p["robot"]:
        stages.append("afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75")

    crunch = p["crunch"]
    if crunch > 0:
        bass_g = round(4.0 + (crunch / 100.0) * 16.0, 1)
        bits = round(12.0 - (crunch / 100.0) * 8.5, 2)
        samples = max(1, int(round(1 + (crunch / 100.0) * 5)))
        lvl_in = round(1.0 + (crunch / 100.0) * 1.4, 2)
        stages.append(
            f"bass=g={bass_g}:f=110,"
            f"acrusher=level_in={lvl_in}:level_out=1:bits={bits}:samples={samples}:mode=log:aa=1,"
            f"acompressor=threshold=0.2:ratio=4:makeup=1.5"
        )

    echo = p["echo"]
    if echo == "room":
        stages.append("aecho=0.8:0.88:60|120:0.4|0.3")
    elif echo == "cathedral":
        stages.append("aecho=0.8:0.85:70|150|260:0.45|0.35|0.25")

    if p["reverse"]:
        stages.append("areverse")

    if not stages:
        return None

    stages.append("alimiter=limit=0.95:attack=5:release=50:level=0")
    return ",".join(stages)


def default_effects() -> list[dict[str, Any]]:
    return copy.deepcopy(DEFAULT_EFFECTS)


def _normalize_effect_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "")).strip()
    if not name:
        return None
    eid = str(raw.get("id", "")).strip()
    if not eid:
        slug = "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_") or "fx"
        eid = f"{slug[:24]}_{uuid.uuid4().hex[:6]}"
    if eid == "normal" or name.lower() == "normal":
        return copy.deepcopy(DEFAULT_EFFECTS[0])
    return {
        "id": eid,
        "name": name[:48],
        "builtin": bool(raw.get("builtin", False)),
        "params": normalize_params(raw.get("params")),
        "custom_filter": str(raw.get("custom_filter", "") or "").strip(),
    }


def _ensure_normal_first(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = [copy.deepcopy(DEFAULT_EFFECTS[0])]
    seen.add("normal")
    for raw in entries:
        item = _normalize_effect_entry(raw)
        if not item or item["id"] in seen:
            continue
        seen.add(item["id"])
        cleaned.append(item)
    return cleaned


def save_effects(effects: list[dict[str, Any]], path: str | Path | None = None) -> list[dict[str, Any]]:
    """Atomically persist effects list to effects.json."""
    target = Path(path) if path is not None else paths.effects_json_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    cleaned = _ensure_normal_first(effects)
    payload = {"version": EFFECTS_SCHEMA_VERSION, "effects": cleaned}
    tmp = target.parent / f".{target.name}.{uuid.uuid4().hex[:8]}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise
    return cleaned


def load_effects(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Load effects from effects.json, auto-seeding or recovering cleanly if missing/corrupt."""
    target = Path(path) if path is not None else paths.effects_json_path()
    if not target.is_file():
        return save_effects(default_effects(), target)

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        version = raw.get("version", 1) if isinstance(raw, dict) else 1
        items = raw.get("effects") if isinstance(raw, dict) else raw
        if not isinstance(items, list) or not items:
            raise ValueError("Empty or invalid effects payload")
        normalized = [e for e in (_normalize_effect_entry(x) for x in items) if e is not None]
        if not normalized:
            raise ValueError("No valid effect entries")
        if int(version) < EFFECTS_SCHEMA_VERSION:
            # Migrate v1 (16 built-ins) to v2 (Normal + 4 main effects + any user custom effects).
            lib_ids = {e["id"] for e in EFFECT_LIBRARY}
            customs = [e for e in normalized if not e.get("builtin") and e["id"] not in lib_ids]
            return save_effects(default_effects() + customs, target)
        return _ensure_normal_first(normalized)
    except Exception:
        try:
            backup = target.with_suffix(".json.corrupt")
            shutil.copy2(target, backup)
        except OSError:
            pass
        return save_effects(default_effects(), target)


def available_library_effects(active_effects: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return built-in library presets not currently in the user's active effect list."""
    active = active_effects if active_effects is not None else load_effects()
    active_ids = {e["id"] for e in active}
    return [copy.deepcopy(e) for e in EFFECT_LIBRARY if e["id"] not in active_ids]


def add_library_effect(effect_id: str, path: str | Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Add a built-in effect from EFFECT_LIBRARY into the active effects.json list."""
    match = next((e for e in EFFECT_LIBRARY if e["id"] == effect_id), None)
    if match is None:
        raise ValueError(f"Unknown library effect: {effect_id}")
    return upsert_effect(copy.deepcopy(match), path)


def upsert_effect(effect: dict[str, Any], path: str | Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Create or update an effect entry (Normal is locked) and persist atomically."""
    current = load_effects(path)
    name = str(effect.get("name", "")).strip()
    if not name:
        raise ValueError("Effect name cannot be empty.")
    eid = str(effect.get("id", "")).strip()
    if eid == "normal" or (not eid and name.lower() == "normal"):
        raise ValueError("The built-in Normal effect is locked and cannot be edited.")

    if not eid:
        slug = "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_") or "custom"
        eid = f"{slug[:24]}_{uuid.uuid4().hex[:6]}"

    entry = {
        "id": eid,
        "name": name[:48],
        "builtin": bool(effect.get("builtin", False)),
        "params": normalize_params(effect.get("params")),
        "custom_filter": str(effect.get("custom_filter", "") or "").strip(),
    }

    replaced = False
    for idx, existing in enumerate(current):
        if existing["id"] == eid:
            current[idx] = entry
            replaced = True
            break
    if not replaced:
        current.append(entry)

    saved = save_effects(current, path)
    return entry, saved


def delete_effect(effect_id: str, path: str | Path | None = None) -> list[dict[str, Any]]:
    """Delete an effect by id (refuses to delete Normal) and persist atomically."""
    if str(effect_id).strip() == "normal":
        raise ValueError("The built-in Normal effect cannot be deleted.")
    current = load_effects(path)
    updated = [e for e in current if e["id"] != effect_id]
    return save_effects(updated, path)


def restore_default_effects(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Restore the 4 main built-in effects (+ Normal) without wiping custom effects."""
    current = load_effects(path)
    lib_ids = {e["id"] for e in EFFECT_LIBRARY}
    customs = [e for e in current if not e.get("builtin") and e["id"] not in lib_ids]
    restored = default_effects() + customs
    return save_effects(restored, path)


def find_effect(preset: str, effects_list: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Resolve an effect by id or name across active effects, falling back to EFFECT_LIBRARY."""
    key = str(preset).strip()
    pool = effects_list if effects_list is not None else load_effects()
    for e in pool:
        if e["id"] == key or e["name"] == key:
            return e
    for e in EFFECT_LIBRARY:
        if e["id"] == key or e["name"] == key:
            return e
    return None


def resolve_effect_filter(effect: dict[str, Any], sample_rate: int) -> str | None:
    """Resolve an effect entry into its FFmpeg filter string for sample_rate."""
    sr = int(sample_rate) if 8000 <= int(sample_rate) <= 48000 else 48000
    custom = str(effect.get("custom_filter", "") or "").strip()
    if custom:
        return custom.replace("{sr}", str(sr))
    return build_filter_from_params(effect.get("params"), sr)


# Backward-compatible built-in preset map (name -> 48kHz filter preview or None).
PRESETS: dict[str, str | None] = {
    e["name"]: resolve_effect_filter(e, 48000) for e in EFFECT_LIBRARY
}


def find_ffmpeg() -> str:
    """Locate ffmpeg.exe: bundled app folder first, then PATH, then WinGet/common paths."""
    bundled_candidates = [
        paths.bundled_asset("ffmpeg.exe"),
        paths.exe_dir() / "ffmpeg.exe",
        paths.bundle_dir() / "ffmpeg.exe",
        paths.bundle_dir() / "bin" / "ffmpeg.exe",
        paths.exe_dir() / "bin" / "ffmpeg.exe",
    ]
    for path in bundled_candidates:
        if path.is_file():
            return str(path)

    found = shutil.which("ffmpeg")
    if found:
        return found

    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(local_app) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
        Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
    ]
    winget_pkgs = Path(local_app) / "Microsoft" / "WinGet" / "Packages"
    if winget_pkgs.is_dir():
        candidates.extend(winget_pkgs.glob("*FFmpeg*/**/ffmpeg.exe"))
    for path in candidates:
        if path.is_file():
            return str(path)
    raise FileNotFoundError(FFMPEG_MISSING_MSG)


def probe_wav_format(path: str | Path) -> tuple[int, int]:
    """Return (sample_rate, channels) of a WAV file, defaulting to (48000, 2) if unreadable."""
    try:
        with wave.open(str(path), "rb") as wf:
            sr = int(wf.getframerate()) or 48000
            ch = int(wf.getnchannels()) or 2
            return sr, ch
    except Exception:
        return 48000, 2


def build_filter_chain(
    preset: str,
    sample_rate: int,
    *,
    effects_list: list[dict[str, Any]] | None = None,
) -> str | None:
    """Resolve a preset name/id into a dynamic FFmpeg filter string for sample_rate."""
    effect = find_effect(preset, effects_list=effects_list)
    if effect is None:
        raise ValueError(f"Unknown preset: {preset}")
    return resolve_effect_filter(effect, sample_rate)


def write_wav(path: str | Path, audio: np.ndarray, sample_rate: int) -> Path:
    """Write float32 or int16 mono/stereo audio to a 16-bit PCM WAV file."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    arr = np.asarray(audio)
    if arr.ndim == 1:
        arr = arr[:, None]
    if np.issubdtype(arr.dtype, np.floating):
        pcm = np.clip(arr, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
    else:
        pcm = arr.astype(np.int16, copy=False)

    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(pcm.shape[1])
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(pcm.tobytes())
    return out


def make_preview_sample_wav(path: str | Path, sample_rate: int = 48000, duration_s: float = 1.5) -> Path:
    """Synthesize a short speech-like harmonic test WAV for previewing effects when no clip is loaded."""
    n = max(1, int(sample_rate * duration_s))
    t = np.linspace(0.0, duration_s, n, endpoint=False, dtype=np.float32)
    f0 = 175.0 + 35.0 * np.sin(2.0 * np.pi * 2.2 * t)
    phase = 2.0 * np.pi * np.cumsum(f0) / float(sample_rate)
    env = np.clip(np.sin(np.pi * (t / duration_s)) * 1.4, 0.0, 1.0)
    syllables = 0.55 + 0.45 * np.sin(2.0 * np.pi * 3.5 * t)
    sig = (
        0.50 * np.sin(phase)
        + 0.28 * np.sin(2.0 * phase)
        + 0.14 * np.sin(3.0 * phase)
        + 0.08 * np.sin(5.0 * phase)
    ) * env * syllables * 0.45
    stereo = np.column_stack([sig, sig]).astype(np.float32)
    return write_wav(path, stereo, sample_rate)


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Read a 16-bit PCM WAV file as float32 array (frames, channels) and sample rate."""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sample_rate = wf.getframerate()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        raise ValueError(f"Unsupported WAV sample width: {sampwidth * 8}-bit")
    data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels)
    else:
        data = data.reshape(-1, 1)
    return data, sample_rate


def process_clip(
    input_wav: str | Path,
    output_wav: str | Path,
    preset: str = "Normal",
    start_s: float = 0.0,
    end_s: float | None = None,
    ffmpeg_bin: str | None = None,
    *,
    params: dict[str, Any] | None = None,
    custom_filter: str | None = None,
    effects_list: list[dict[str, Any]] | None = None,
) -> Path:
    """Trim input_wav and apply a non-destructive FFmpeg preset/params into output_wav."""
    src = Path(input_wav)
    dst = Path(output_wav)
    dst.parent.mkdir(parents=True, exist_ok=True)

    src_sr, src_ch = probe_wav_format(src)
    target_sr = src_sr if 8000 <= src_sr <= 48000 else 48000
    target_ch = min(max(src_ch, 1), 2)

    custom = str(custom_filter or "").strip()
    if custom:
        af: str | None = custom.replace("{sr}", str(target_sr))
    elif params is not None:
        af = build_filter_from_params(params, target_sr)
    else:
        af = build_filter_chain(preset, target_sr, effects_list=effects_list)

    exe = ffmpeg_bin or find_ffmpeg()
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error"]
    if start_s > 0.0:
        cmd += ["-ss", f"{start_s:.3f}"]
    if end_s is not None and end_s > start_s:
        cmd += ["-t", f"{end_s - start_s:.3f}"]
    cmd += ["-i", str(src)]

    if af:
        cmd += ["-af", af]

    cmd += ["-acodec", "pcm_s16le", "-ar", str(target_sr), "-ac", str(target_ch), str(dst)]

    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=30)
    if res.returncode != 0:
        label = preset if params is None and not custom else "custom effect"
        raise RuntimeError(f"FFmpeg failed ({label}): {res.stderr.strip() or res.returncode}")
    return dst


def is_supported_audio_file(path: str | Path) -> bool:
    """Return True if path points to an existing file with a supported audio or video extension."""
    p = Path(path)
    return p.is_file() and p.suffix.lower() in SUPPORTED_MEDIA_EXTS


def is_video_file(path: str | Path) -> bool:
    """Return True if path has a supported video file extension."""
    return Path(path).suffix.lower() in SUPPORTED_VIDEO_EXTS


def probe_media_audio(
    src_path: str | Path,
    ffmpeg_bin: str | None = None,
    *,
    max_duration_s: float = MAX_IMPORT_DURATION_S,
) -> float | None:
    """Probe media file via FFmpeg to ensure an audio stream exists and duration is within limit."""
    src = Path(src_path)
    exe = ffmpeg_bin or find_ffmpeg()
    try:
        res = subprocess.run(
            [exe, "-hide_banner", "-i", str(src)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_NO_WINDOW,
            timeout=8,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Timed out inspecting {src.name}") from exc

    stderr = res.stderr or ""
    if not re.search(r"Stream\s+#\d+.*:\s*Audio:", stderr):
        raise ValueError("This file has no audio track.")

    dur_match = re.search(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", stderr)
    if dur_match:
        hours, mins, secs = dur_match.groups()
        duration_s = int(hours) * 3600 + int(mins) * 60 + float(secs)
        if duration_s > max_duration_s:
            raise ValueError("File is too long (max 10 minutes for ClipMic import).")
        return duration_s
    return None


def decode_audio_to_wav(
    src_path: str | Path,
    output_wav: str | Path,
    ffmpeg_bin: str | None = None,
    *,
    sample_rate: int = 48000,
    channels: int = 2,
) -> Path:
    """Decode any supported audio or video file into 16-bit PCM WAV via FFmpeg."""
    src = Path(src_path)
    if not src.is_file():
        raise FileNotFoundError(f"Audio file not found: {src}")
    if src.suffix.lower() not in SUPPORTED_MEDIA_EXTS:
        raise ValueError(f"Unsupported media format: {src.suffix or '(none)'}")

    exe = ffmpeg_bin or find_ffmpeg()
    probe_media_audio(src, ffmpeg_bin=exe)

    dst = Path(output_wav)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-sn",
        "-dn",
        "-map",
        "0:a:0",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(int(sample_rate)),
        "-ac",
        str(max(1, min(2, int(channels)))),
        str(dst),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"Could not decode {src.name}: {res.stderr.strip() or res.returncode}")
        if not dst.is_file() or dst.stat().st_size <= 44:
            raise RuntimeError(f"Decoded audio is empty: {src.name}")
    except Exception:
        try:
            dst.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return dst


def sanitize_filename(name: str, fallback: str = "clip") -> str:
    """Sanitize a string into a safe Windows filename stem."""
    cleaned = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]+', "", str(name or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        return fallback
    base = cleaned.split(".", 1)[0].strip().upper()
    if base in _WIN_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def export_wav_to_mp3(
    src_wav: str | Path,
    dst_mp3: str | Path,
    title: str | None = None,
    bitrate: str = "192k",
    ffmpeg_bin: str | None = None,
) -> str:
    """Encode a WAV file to MP3 via FFmpeg using temp staging for safe cross-drive moves."""
    src = Path(src_wav)
    if not src.is_file():
        raise FileNotFoundError(f"Source audio file not found: {src}")

    dst = Path(dst_mp3)
    exe = ffmpeg_bin or find_ffmpeg()
    tmp_mp3 = paths.temp_dir() / f"export_{uuid.uuid4().hex[:10]}.mp3"
    cmd = [
        exe,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        str(bitrate),
        "-id3v2_version",
        "3",
    ]
    clean_title = str(title).strip() if title is not None else ""
    if clean_title:
        cmd += ["-metadata", f"title={clean_title}"]
    cmd.append(str(tmp_mp3))

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW, timeout=60)
        if res.returncode != 0:
            raise RuntimeError(f"Could not export MP3: {res.stderr.strip() or res.returncode}")
        if not tmp_mp3.is_file() or tmp_mp3.stat().st_size == 0:
            raise RuntimeError("Exported MP3 file is empty.")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_mp3), str(dst))
    except Exception:
        try:
            tmp_mp3.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return str(dst)


def compute_waveform_peaks(audio: np.ndarray, bins: int = 240) -> np.ndarray:
    """Downsample audio into normalized mono peak amplitudes [0..1] for UI rendering."""
    if audio.size == 0 or bins <= 0:
        return np.zeros(max(bins, 1), dtype=np.float32)
    mono = np.mean(np.abs(audio), axis=1) if audio.ndim > 1 else np.abs(audio)
    n = len(mono)
    if n <= bins:
        out = np.zeros(bins, dtype=np.float32)
        out[:n] = mono
        return np.clip(out, 0.0, 1.0)
    step = n // bins
    usable = step * bins
    peaks = mono[:usable].reshape(bins, step).max(axis=1).astype(np.float32, copy=False)
    if usable < n:
        peaks[-1] = max(float(peaks[-1]), float(mono[usable:].max()))
    return np.clip(peaks, 0.0, 1.0)
