from __future__ import annotations

from typing import Optional

SUPPORTED_LANGS: tuple[str, ...] = ("en", "ar")

_current_lang: str = "en"


def _normalize_lang(lang: object) -> str:
    if isinstance(lang, str):
        code = lang.strip().lower()
        if code in SUPPORTED_LANGS:
            return code
    return "en"


def get_lang() -> str:
    """Return the active UI language code ('en' or 'ar')."""
    return _current_lang


def set_lang(lang: str) -> str:
    """Set the active UI language ('en' or 'ar', defaulting to 'en')."""
    global _current_lang
    _current_lang = _normalize_lang(lang)
    return _current_lang


def is_rtl(lang: Optional[str] = None) -> bool:
    """Return True if the specified (or active) language uses right-to-left layout."""
    code = _current_lang if lang is None else _normalize_lang(lang)
    return code == "ar"


def ltr(value: object) -> str:
    """Isolate LTR tokens (hotkeys, numbers, units) when rendering in RTL mode."""
    text = str(value)
    return f"\u2066{text}\u2069" if is_rtl() else text


TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # App & Language
        "app.title": "ClipMic",
        "app.already_running": "ClipMic is already running in the system tray.",
        "app.unexpected_error_title": "ClipMic — Unexpected Error",
        "app.unexpected_error_body": "ClipMic encountered an unexpected error:\n\n{summary}\n\nDetails were saved to:\n{log_file}",
        "app.error_title": "ClipMic Error",
        "app.error_short": "ClipMic encountered an unexpected error:\n\n{summary}",
        "lang.label": "Language:",
        "lang.en": "English",
        "lang.ar": "العربية",
        "lang.toggle_btn": "عربي",
        "lang.tooltip": "Switch interface language between English and Arabic",

        # Common buttons & hotkey capture
        "common.cancel": "Cancel",
        "common.done": "Done",
        "common.reset": "Reset",
        "common.edit": "Edit",
        "common.copy": "Copy",
        "common.delete": "Delete",
        "common.stop": "■ Stop",
        "common.stopped": "Stopped",
        "common.preview": "▶ Preview",
        "hotkey.set": "Set Hotkey",
        "hotkey.press": "Press key... (Esc cancel)",

        # Tray menu
        "tray.tooltip": "ClipMic — Always-On {n}s Game Audio Clipper",
        "tray.open": "Open ClipMic",
        "tray.clip_last": "Clip Last {n}s",
        "tray.stop_all": "Stop All Audio",
        "tray.restore_all_mics": "Reset All Mics",
        "tray.quit": "Quit",

        # MainWindow header & top bar
        "main.effects_btn": "🎛 Effects",
        "main.effects_tooltip": "Create, preview, edit, or delete custom voice effects",
        "main.clip_last_btn": "✂ Clip Last {n}s ({hk})",
        "main.clip_last_no_hk": "✂ Clip Last {n}s",
        "main.clip_last_tooltip": "Capture the last {n} seconds of audio into the trimmer",
        "main.clip_hk_label": "Clip:",
        "main.clip_hk_tooltip": "Global hotkey to capture the last {n} seconds of audio",
        "main.stop_btn": "■ Stop ({hk})",
        "main.stop_btn_no_hk": "■ Stop",
        "main.stop_tooltip": "Immediately stop all playing audio (mic and local monitor)",
        "main.stop_hk_label": "Stop:",
        "main.stop_hk_tooltip": "Global hotkey to stop all playing audio mid-game (Backspace to clear)",

        # MainWindow mic, APO & mixer
        "main.mic_label": "Mic:",
        "main.mic_combo_tooltip": "Select the target microphone for Direct Mic injection and mic recording",
        "main.mic_default_scanning": "Windows Default (Scanning…)",
        "main.mic_default_named": "Windows Default ({name})",
        "main.mic_default": "Windows Default",
        "main.refresh_devices_tooltip": "Rescan microphones and playback capture devices",
        "main.enable_direct_mic": "Enable Mic",
        "main.enable_direct_mic_tooltip": "Allow ClipMic to play clips through the selected microphone",
        "main.enable_direct_mic_backup_tooltip": "Another or unplugged mic is still linked — use tray 'Reset All Mics' to reset all.",
        "main.restore_mic": "Disable Mic",
        "main.restore_mic_tooltip": "Disconnect ClipMic from this microphone and return it to normal",
        "main.enabling": "Enabling...",
        "main.restoring": "Disabling...",
        "main.apo_status_enabling": "Enabling Direct Mic…",
        "main.apo_status_restoring": "Disabling Direct Mic…",
        "main.apo_uac_cancelled": "Direct Mic permission cancelled",
        "main.mic_vol_label": "Mic Vol:",
        "main.mic_vol_tooltip": "Mic send volume (how loud clips sound to teammates on Discord/games)",
        "main.monitor_chk": "Monitor",
        "main.monitor_chk_tooltip": "Hear clips locally in your headphones when playing to mic (uncheck to mute local monitor)",
        "main.monitor_vol_tooltip": "Local monitor volume (how loud you hear clips; does not affect Discord/mic volume)",

        # MainWindow capture source & buffer
        "main.capture_label": "Capture:",
        "main.loopback_default": "Desktop Audio (Default)",
        "main.loopback_tooltip": "Select the desktop playback device to capture",
        "main.capture_mic_chk": "Capture Mic ({n}s)",
        "main.capture_mic_tooltip": "Keep a {n}s rolling buffer of your own microphone so you can pick or mix it on {hk}",
        "main.discord_chk": "Listen to Discord",
        "main.discord_tooltip": "Include Discord voice audio in the {n}s clip buffer",
        "main.buffer_label": "Buffer:",
        "main.buffer_preset": "{n}s",
        "main.buffer_preset_tooltip": "Keep the last {n} seconds of audio in memory",
        "main.buffer_spin_suffix": "s",
        "main.buffer_spin_tooltip": "Custom rolling capture duration ({min}–{max} seconds)",
        "main.setup_toggle_expanded": "Audio & Capture Settings ▾",
        "main.setup_toggle_collapsed": "Audio & Capture Settings ▸",
        "main.setup_toggle_tooltip": "Show or hide microphone, volume, and capture settings",

        # MainWindow listening & status indicators
        "main.listening_checking": "● Listening: checking active apps…",
        "main.listening_reconnecting": "◌ Reconnecting capture device…{detail}",
        "main.listening_stopped": "○ Capture stopped",
        "main.listening_discord_paused": "◌ Paused (Discord only — Discord listening OFF) • {device}",
        "main.listening_waiting": "Waiting for active apps…",
        "main.listening_line": "{dot} Listening: {apps}  •  {mode}  •  {mic}",
        "main.mic_buf_on": "Mic {n}s ON",
        "main.mic_buf_off": "Mic {n}s OFF",
        "main.mic_buf_unavailable": "Mic unavailable",
        "main.mode_per_app_outputs": "All Apps",
        "main.buf_reconnecting": "Reconnecting…",
        "main.buf_per_app": "App Audio",
        "main.buf_loopback": "System Audio",
        "main.buf_off": "Audio Off",
        "main.apo_active": "Mic Ready",
        "main.apo_preview_mode": "Preview Only",
        "main.apo_active_short": "Mic Ready",
        "main.apo_preview_short": "Preview Only",
        "main.enhancements_off": "Audio Enhancements OFF",
        "main.enhancements_off_tooltip": "Windows Audio Enhancements are turned off for this microphone. Turn them on in Windows Sound Settings for Direct Mic to work.",
        "main.status_pill": "{buf_state} • {mode_state}",
        "main.status_bar": "Capture: {buf_state} ({device})  •  Mic: {mic}  •  {mode}",
        "main.status_audio_stopped": "Audio stopped",
        "main.status_nothing_playing": "Nothing is playing",
        "main.status_playing_mic": "Playing '{name}' to Mic…",
        "main.status_looping_mic": "Looping '{name}' to Mic… (Press {hk} to stop)",
        "main.status_looping_mic_no_hk": "Looping '{name}' to Mic…",
        "main.status_previewing_clip": "Previewing '{name}' (Speakers)…",
        "main.status_no_audio": "No audio detected in the last {n} seconds",
        "main.status_no_audio_channels": "No audio detected in selected channels",
        "main.status_buffer_error": "Buffer resize error: {err}",

        # MainWindow drop zone & media import
        "main.drop_default": "Drop MP3, MP4, WAV… or click to import",
        "main.drop_hover": "Release to import audio or video into Trim & Effects",
        "main.drop_tooltip": "Drop an audio or video file (MP3, MP4, WAV, MKV, WEBM, MOV, etc.) to trim and add effects",
        "main.import_dialog_title": "Import Audio or Video File",
        "main.import_filter_all_media": "Audio & Video Files ({exts})",
        "main.import_filter_audio": "Audio Files ({exts})",
        "main.import_filter_video": "Video Files ({exts})",
        "main.import_filter_any": "All Files (*.*)",
        "main.import_unsupported": "Unsupported file — drop an audio or video file (MP3, MP4, WAV, MKV, etc.)",
        "main.import_busy": "Already importing a media file…",
        "main.import_extracting": "Extracting audio from {name}…",
        "main.import_decoding": "Decoding {name}…",
        "main.import_failed": "Import failed: {err}",
        "main.import_decode_error": "Could not decode audio",

        # MainWindow saved clips & export
        "main.saved_clips_header": "Saved Soundboard Clips",
        "main.clip_count_one": "1 clip",
        "main.clip_count_many": "{n} clips",
        "main.empty_clips": "No saved clips yet. Press {hk}, click 'Clip Last {n}s', or drop an audio or video file above.",
        "main.clip_meta": "{dur:.2f}s  •  {preset}",
        "main.clip_hk_tooltip": "Click to set or change global hotkey (Backspace to clear, Esc to cancel)",
        "main.clip_preview_btn": "🔊 Preview",
        "main.clip_preview_tooltip": "Preview this clip on your speakers only (not sent to mic)",
        "main.clip_play_btn": "▶ Mic",
        "main.clip_play_tooltip": "Play this clip to your microphone",
        "main.clip_loop_btn": "🔁 Loop",
        "main.clip_loop_tooltip": "Loop this clip non-stop to your microphone (press Stop / F10 to stop)",
        "main.clip_edit_btn": "Edit",
        "main.clip_edit_tooltip": "Trim clip or apply voice effects",
        "main.clip_export_btn": "↓ MP3",
        "main.clip_export_tooltip": "Export as MP3…",
        "main.clip_delete_tooltip": "Delete this clip",
        "main.clip_err_missing_wav": "Cannot play '{name}': audio file not found",
        "main.clip_err_apo_disabled": "Direct Mic is not enabled — click 'Enable Mic' first (or use Preview for speakers)",
        "main.clip_err_ring_unavailable": "Direct Mic buffer unavailable — try re-enabling Mic",
        "main.clip_err_playback_failed": "Playback failed: {err}",
        "main.ctx_preview": "Preview (Speakers)",
        "main.ctx_play": "Play to Mic",
        "main.ctx_edit": "Edit",
        "main.ctx_export": "Export as MP3…",
        "main.ctx_delete": "Delete",
        "main.export_dialog_title": "Export Clip as MP3",
        "main.export_filter": "MP3 Audio (*.mp3)",
        "main.export_missing": "Export failed: Audio file not found for '{name}'",
        "main.export_in_progress": "Exporting {name}…",
        "main.export_failed": "Export failed: {err}",
        "main.export_done": "Exported {name}",

        # MainWindow footer checkboxes
        "main.startup_chk": "Start with Windows",
        "main.startup_tooltip": "Launch ClipMic quietly in the system tray when you sign in to Windows",
        "main.tray_close_chk": "Minimize to tray on close",
        "main.tray_close_tooltip": "Keep ClipMic running in the system tray when clicking the window close button",

        # MainWindow warnings & error dialogs
        "main.err_startup_title": "Startup Setting Error",
        "main.err_startup_body": "Could not update Windows startup setting:\n{err}",
        "main.err_capture_title": "Capture Device Error",
        "main.err_capture_body": "Could not switch capture device:\n{err}",
        "main.err_hotkey_title": "Hotkey Conflict",
        "main.err_hotkey_body": "Could not register global hotkey(s): {hotkeys}",
        "main.err_missing_file_title": "Missing File",
        "main.err_missing_file_body": "Audio file not found: {path}",
        "main.err_apo_missing_title": "APO DLL Not Built",
        "main.err_apo_missing_body": "clipmic_apo.dll was not found.\nReinstall ClipMic or run native\\directmic\\build.ps1 first.",
        "main.err_no_mic_title": "No Microphone Selected",
        "main.err_no_mic_body": "Select an active microphone first.",
        "main.err_enhancements_title": "Windows Audio Enhancements Disabled",
        "main.err_enhancements_body": (
            "Windows 'Audio Enhancements' is currently turned OFF for this microphone.\n\n"
            "Direct Mic APO requires Audio Enhancements to be enabled:\n"
            "  Windows Settings → System → Sound → select your microphone →\n"
            "  Audio enhancements → Device Default Effects.\n\n"
            "Install Direct Mic APO anyway?"
        ),
        "main.err_apo_setup_title": "Direct Mic Setup Error",

        # ChannelPickerDialog
        "picker.window_title": "ClipMic — Choose Audio Channels",
        "picker.title": "Choose Audio Channels",
        "picker.subtitle_one": "Last {dur:.1f}s  •  1 active channel",
        "picker.subtitle_many": "Last {dur:.1f}s  •  {n} active channels",
        "picker.badge_game": "Game",
        "picker.badge_app": "App",
        "picker.badge_mic": "Mic",
        "picker.badge_system": "System",
        "picker.chk_tooltip": "Include {name} in clip",
        "picker.wave_tooltip": "Click or drag anywhere on the waveform to scrub preview audio",
        "picker.preview_tooltip": "Preview this channel on your speakers (never sent to mic)",
        "picker.hint": "Enter to trim  •  Esc to cancel",
        "picker.confirm_btn": "Trim Selected ({n}) →",
        "picker.confirm_tooltip": "Mix selected channels and open Trim & Effects",

        # TrimDialog
        "trim.window_title": "ClipMic — Trim & Effects",
        "trim.change_channels_count": "← Change Channels ({n})",
        "trim.change_channels": "← Change Channels",
        "trim.change_channels_tooltip": "Return to Channel Picker with the same {dur} snapshot intact",
        "trim.title_edit": "Edit Saved Clip",
        "trim.title_trim": "Trim: {summary}",
        "trim.title_import": "Import: {name}",
        "trim.title_capture": "Instant Capture (Last {n}s)",
        "trim.range_label": "{start:.2f}s – {end:.2f}s  ({dur:.2f}s)",
        "trim.waveform_tooltip": "Drag handles to trim or click the waveform to scrub preview",
        "trim.fx_section": "Voice Effect (Non-Destructive)",
        "trim.fx_combo_tooltip": "Choose a voice effect to preview and apply",
        "trim.new_fx_btn": "+ New Effect",
        "trim.new_fx_tooltip": "Build a custom voice effect with live preview on this clip",
        "trim.manage_fx_btn": "Manage",
        "trim.manage_fx_tooltip": "Edit, duplicate, delete, or restore voice effects",
        "trim.play_preview_btn": "▶ Play Preview",
        "trim.play_preview_tooltip": "Preview trimmed audio with the selected effect on your speakers",
        "trim.stop_tooltip": "Stop preview or mic playback",
        "trim.send_mic_btn": "🎙 Send to Mic",
        "trim.send_mic_tooltip": "Play the trimmed clip with effects directly to your microphone",
        "trim.status_processing": "Processing FFmpeg effect...",
        "trim.status_playing": "Playing preview...",
        "trim.status_sending": "Sending to Mic...",
        "trim.status_speaker_mode": "Previewing (Speaker Mode)...",
        "trim.status_scrubbing": "Scrubbing {sec:.2f}s",
        "trim.name_label": "Name:",
        "trim.name_placeholder": "Clip name (e.g. Funny Laugh)",
        "trim.untitled_clip": "Untitled Clip",
        "trim.hotkey_label": "Hotkey:",
        "trim.hotkey_tooltip": "Optional global hotkey to play this clip anytime",
        "trim.save_btn": "Save Clip",
        "trim.save_tooltip": "Save this trimmed clip to your soundboard",
        "trim.err_ffmpeg_title": "FFmpeg Error",

        # EffectBuilderDialog
        "builder.title_new": "New Voice Effect",
        "builder.title_edit": "Edit Voice Effect",
        "builder.title_duplicate": "Duplicate Voice Effect",
        "builder.window_title": "ClipMic — {title}",
        "builder.copy_suffix": "{name} (Copy)",
        "builder.name_label": "Name:",
        "builder.name_placeholder": "e.g. Evil Gremlin, Squeaky Mock",
        "builder.pitch_label": "Pitch",
        "builder.pitch_tooltip": "Voice pitch multiplier (0.60x deep to 1.80x squeaky)",
        "builder.speed_label": "Speed",
        "builder.speed_tooltip": "Playback speed (0.55x slow to 1.80x fast)",
        "builder.wobble_label": "Wobble",
        "builder.wobble_tooltip": "Pitch vibrato / wobble intensity",
        "builder.crunch_label": "Crunch",
        "builder.crunch_tooltip": "Bass boost + bitcrush distortion with safe limiter",
        "builder.tone_label": "Tone:",
        "builder.tone_tooltip": "Apply an EQ / tonal character preset",
        "builder.tone_normal": "Normal",
        "builder.tone_feminine": "Feminine",
        "builder.tone_nasal": "Nasal",
        "builder.tone_bad_mic": "Bad Mic",
        "builder.tone_underwater": "Underwater",
        "builder.echo_label": "Echo:",
        "builder.echo_tooltip": "Add room or cathedral echo",
        "builder.echo_none": "None",
        "builder.echo_room": "Room",
        "builder.echo_cathedral": "Cathedral",
        "builder.robot_chk": "Robot Voice",
        "builder.robot_tooltip": "Add robotic modulation to the voice",
        "builder.reverse_chk": "Reverse Audio",
        "builder.reverse_tooltip": "Play the audio clip backwards",
        "builder.adv_chk": "Advanced FFmpeg filter override",
        "builder.adv_tooltip": "Override sliders with a custom FFmpeg -af filter chain",
        "builder.adv_placeholder": "Optional raw FFmpeg -af chain (supports {sr})",
        "builder.preview_clip_tooltip": "Hear these live settings on your current clip",
        "builder.preview_sample_tooltip": "Hear these live settings on a short voice test sample",
        "builder.stop_tooltip": "Stop effect preview",
        "builder.reset_tooltip": "Reset all effect sliders and options to defaults",
        "builder.save_btn": "Save Effect",
        "builder.save_tooltip": "Save this voice effect to your presets",
        "builder.status_rendering": "Rendering preview...",
        "builder.status_playing": "Playing preview...",
        "builder.err_preview_title": "Preview Error",
        "builder.err_name_required_title": "Name Required",
        "builder.err_name_required_body": "Enter a name for this voice effect.",
        "builder.err_reserved_title": "Reserved Name",
        "builder.err_reserved_body": '"Normal" is reserved. Choose a different name.',
        "builder.err_save_title": "Save Error",

        # ManageEffectsDialog & effect descriptions
        "manage_fx.window_title": "ClipMic — Manage Voice Effects",
        "manage_fx.title": "Voice Effects",
        "manage_fx.new_btn": "+ New Effect",
        "manage_fx.new_tooltip": "Create a new custom voice effect",
        "manage_fx.active_hdr": "Active Effects ({n})",
        "manage_fx.more_hdr": "More Built-in Effects ({n} available to add)",
        "manage_fx.tag_locked": "Locked",
        "manage_fx.tag_builtin": "Built-in",
        "manage_fx.tag_custom": "Custom",
        "manage_fx.edit_tooltip": "Edit this voice effect",
        "manage_fx.copy_tooltip": "Duplicate this effect as a new custom preset",
        "manage_fx.delete_tooltip": "Delete {name}",
        "manage_fx.add_btn": "+ Add",
        "manage_fx.add_tooltip": 'Add "{name}" to your active effects',
        "manage_fx.restore_btn": "Restore Default Effects",
        "manage_fx.restore_tooltip": "Bring back all built-in presets without removing your custom effects",
        "manage_fx.delete_confirm_title": "Delete Effect",
        "manage_fx.delete_confirm_body": 'Delete "{name}"?',
        "manage_fx.err_add_title": "Add Effect Error",
        "manage_fx.err_delete_title": "Delete Error",
        "fx.desc_unmodified": "Unmodified audio",
        "fx.desc_neutral": "Neutral",
        "fx.desc_custom_ffmpeg": "Custom FFmpeg: {chain}",
        "fx.desc_pitch": "Pitch {val:.2f}x",
        "fx.desc_speed": "Speed {val:.2f}x",
        "fx.desc_wobble": "Wobble {val}%",
        "fx.desc_crunch": "Crunch {val}%",
        "fx.desc_echo": "{echo} Echo",
        "fx.desc_robot": "Robot",
        "fx.desc_reverse": "Reverse",
    },
    "ar": {
        # App & Language
        "app.title": "ClipMic",
        "app.already_running": "تطبيق ClipMic يعمل بالفعل في شريط النظام.",
        "app.unexpected_error_title": "ClipMic — خطأ غير متوقع",
        "app.unexpected_error_body": "حدث خطأ غير متوقع في ClipMic:\n\n{summary}\n\nتم حفظ التفاصيل في:\n{log_file}",
        "app.error_title": "خطأ في ClipMic",
        "app.error_short": "حدث خطأ غير متوقع في ClipMic:\n\n{summary}",
        "lang.label": "اللغة:",
        "lang.en": "English",
        "lang.ar": "العربية",
        "lang.toggle_btn": "EN",
        "lang.tooltip": "تبديل لغة الواجهة بين العربية والإنجليزية",

        # Common buttons & hotkey capture
        "common.cancel": "إلغاء",
        "common.done": "تم",
        "common.reset": "إعادة ضبط",
        "common.edit": "تعديل",
        "common.copy": "نسخ",
        "common.delete": "حذف",
        "common.stop": "■ إيقاف",
        "common.stopped": "متوقف",
        "common.preview": "▶ معاينة",
        "hotkey.set": "تعيين اختصار",
        "hotkey.press": "اضغط مفتاحاً... (Esc للإلغاء)",

        # Tray menu
        "tray.tooltip": "ClipMic — تسجيل مستمر لآخر {n} ثانية من صوت الألعاب",
        "tray.open": "فتح ClipMic",
        "tray.clip_last": "قص آخر {n} ثانية",
        "tray.stop_all": "إيقاف كل الصوت",
        "tray.restore_all_mics": "إعادة ضبط جميع المايكات",
        "tray.quit": "خروج",

        # MainWindow header & top bar
        "main.effects_btn": "🎛 التأثيرات",
        "main.effects_tooltip": "إنشاء ومعاينة وتعديل أو حذف التأثيرات الصوتية",
        "main.clip_last_btn": "✂ قص آخر {n} ثانية ({hk})",
        "main.clip_last_no_hk": "✂ قص آخر {n} ثانية",
        "main.clip_last_tooltip": "التقاط آخر {n} ثانية من الصوت لقصها وإضافة التأثيرات",
        "main.clip_hk_label": "القص:",
        "main.clip_hk_tooltip": "اختصار عام لقص آخر {n} ثانية من الصوت",
        "main.stop_btn": "■ إيقاف ({hk})",
        "main.stop_btn_no_hk": "■ إيقاف",
        "main.stop_tooltip": "إيقاف جميع الأصوات فوراً في المايك والسماعة",
        "main.stop_hk_label": "الإيقاف:",
        "main.stop_hk_tooltip": "اختصار عام لإيقاف جميع الأصوات أثناء اللعب (Backspace للمسح)",

        # MainWindow mic, APO & mixer
        "main.mic_label": "المايك:",
        "main.mic_combo_tooltip": "اختيار المايك المستخدم للبث المباشر وتسجيل صوتك",
        "main.mic_default_scanning": "المايك الافتراضي (جارٍ الفحص…)",
        "main.mic_default_named": "المايك الافتراضي ({name})",
        "main.mic_default": "المايك الافتراضي",
        "main.refresh_devices_tooltip": "إعادة فحص أجهزة المايك ومصادر الصوت",
        "main.enable_direct_mic": "تفعيل المايك",
        "main.enable_direct_mic_tooltip": "السماح لتطبيق ClipMic بتشغيل المقاطع عبر المايك المحدد",
        "main.enable_direct_mic_backup_tooltip": "يوجد مايك آخر مرتبط حالياً — استخدم خيار «إعادة ضبط جميع المايكات» من شريط النظام.",
        "main.restore_mic": "إيقاف المايك",
        "main.restore_mic_tooltip": "فصل ClipMic عن هذا المايك وإرجاعه لوضعه الطبيعي",
        "main.enabling": "جارٍ التفعيل...",
        "main.restoring": "جارٍ الإيقاف...",
        "main.apo_status_enabling": "جارٍ تفعيل المايك المباشر…",
        "main.apo_status_restoring": "جارٍ إيقاف المايك المباشر…",
        "main.apo_uac_cancelled": "تم إلغاء إذن المسؤول للمايك المباشر",
        "main.mic_vol_label": "صوت المايك:",
        "main.mic_vol_tooltip": "مستوى صوت المقاطع التي يسمعها الآخرون عبر المايك في ديسكورد والألعاب",
        "main.monitor_chk": "سماع الصوت",
        "main.monitor_chk_tooltip": "سماع المقاطع في سماعتك أثناء تشغيلها عبر المايك",
        "main.monitor_vol_tooltip": "مستوى سماع المقاطع في سماعتك فقط (لا يؤثر على صوت المايك)",

        # MainWindow capture source & buffer
        "main.capture_label": "مصدر الصوت:",
        "main.loopback_default": "صوت النظام (الافتراضي)",
        "main.loopback_tooltip": "اختيار جهاز إخراج الصوت المراد تسجيله",
        "main.capture_mic_chk": "تسجيل المايك ({n} ث)",
        "main.capture_mic_tooltip": "الاحتفاظ بآخر {n} ثانية من صوت المايك لاختياره أو دمجه عند الضغط على {hk}",
        "main.discord_chk": "تسجيل ديسكورد",
        "main.discord_tooltip": "تضمين صوت ديسكورد ضمن تسجيل آخر {n} ثانية",
        "main.buffer_label": "مدة التسجيل:",
        "main.buffer_preset": "{n} ث",
        "main.buffer_preset_tooltip": "الاحتفاظ بآخر {n} ثانية من الصوت في الذاكرة",
        "main.buffer_spin_suffix": " ث",
        "main.buffer_spin_tooltip": "مدة تسجيل مخصصة ({min}–{max} ثانية)",
        "main.setup_toggle_expanded": "إعدادات الصوت والتسجيل ▾",
        "main.setup_toggle_collapsed": "إعدادات الصوت والتسجيل ◂",
        "main.setup_toggle_tooltip": "إظهار أو إخفاء إعدادات المايك ومستوى الصوت والتسجيل",

        # MainWindow listening & status indicators
        "main.listening_checking": "● الاستماع: جارٍ فحص التطبيقات النشطة…",
        "main.listening_reconnecting": "◌ جارٍ إعادة الاتصال بجهاز التسجيل…{detail}",
        "main.listening_stopped": "○ توقف التسجيل",
        "main.listening_discord_paused": "◌ متوقف مؤقتاً (ديسكورد فقط — تسجيل ديسكورد متوقف) • {device}",
        "main.listening_waiting": "بانتظار صوت التطبيقات…",
        "main.listening_line": "{dot} الاستماع: {apps}  •  {mode}  •  {mic}",
        "main.mic_buf_on": "تسجيل المايك {n} ث مفعّل",
        "main.mic_buf_off": "تسجيل المايك {n} ث متوقف",
        "main.mic_buf_unavailable": "المايك غير متاح",
        "main.mode_per_app_outputs": "جميع التطبيقات",
        "main.buf_reconnecting": "جارٍ إعادة الاتصال…",
        "main.buf_per_app": "صوت التطبيقات",
        "main.buf_loopback": "صوت النظام",
        "main.buf_off": "التسجيل متوقف",
        "main.apo_active": "المايك جاهز",
        "main.apo_preview_mode": "معاينة فقط",
        "main.apo_active_short": "المايك جاهز",
        "main.apo_preview_short": "معاينة فقط",
        "main.enhancements_off": "تحسينات الصوت متوقفة",
        "main.enhancements_off_tooltip": "خيار تحسينات الصوت (Audio Enhancements) متوقف لهذا المايك في ويندوز. قم بتفعيله من إعدادات الصوت ليعمل المايك المباشر.",
        "main.status_pill": "{buf_state} • {mode_state}",
        "main.status_bar": "التسجيل: {buf_state} ({device})  •  المايك: {mic}  •  {mode}",
        "main.status_audio_stopped": "تم إيقاف الصوت",
        "main.status_nothing_playing": "لا يوجد صوت حالياً",
        "main.status_playing_mic": "جارٍ تشغيل «{name}» عبر المايك…",
        "main.status_looping_mic": "تكرار «{name}» عبر المايك… (اضغط {hk} للإيقاف)",
        "main.status_looping_mic_no_hk": "تكرار «{name}» عبر المايك…",
        "main.status_previewing_clip": "جارٍ معاينة «{name}» في السماعة…",
        "main.status_no_audio": "لا يوجد صوت في آخر {n} ثانية",
        "main.status_no_audio_channels": "لا يوجد صوت في القنوات المحددة",
        "main.status_buffer_error": "خطأ في تغيير مدة التسجيل: {err}",

        # MainWindow drop zone & media import
        "main.drop_default": "اسحب ملف MP3 أو MP4 أو WAV هنا… أو انقر للاستيراد",
        "main.drop_hover": "أفلت الملف لاستيراد الصوت أو الفيديو إلى نافذة القص والتأثيرات",
        "main.drop_tooltip": "اسحب ملف صوت أو فيديو (MP3، MP4، WAV، MKV، WEBM، MOV وغيرها) لقصه وإضافة التأثيرات",
        "main.import_dialog_title": "استيراد ملف صوت أو فيديو",
        "main.import_filter_all_media": "ملفات الصوت والفيديو ({exts})",
        "main.import_filter_audio": "ملفات الصوت ({exts})",
        "main.import_filter_video": "ملفات الفيديو ({exts})",
        "main.import_filter_any": "جميع الملفات (*.*)",
        "main.import_unsupported": "صيغة غير مدعومة — استخدم ملف صوت أو فيديو (MP3، MP4، WAV، MKV وغيرها)",
        "main.import_busy": "جارٍ استيراد ملف آخر حالياً…",
        "main.import_extracting": "جارٍ استخراج الصوت من {name}…",
        "main.import_decoding": "جارٍ تجهيز {name}…",
        "main.import_failed": "فشل الاستيراد: {err}",
        "main.import_decode_error": "تعذر قراءة الملف الصوتي",

        # MainWindow saved clips & export
        "main.saved_clips_header": "المقاطع المحفوظة",
        "main.clip_count_one": "مقطع واحد",
        "main.clip_count_many": "{n} مقاطع",
        "main.empty_clips": "لا توجد مقاطع محفوظة بعد. اضغط {hk}، أو انقر على 'قص آخر {n} ثانية'، أو اسحب ملفاً صوتياً للأعلى.",
        "main.clip_meta": "{dur:.2f} ث  •  {preset}",
        "main.clip_hk_tooltip": "انقر لتعيين أو تغيير الاختصار العام (Backspace للمسح، Esc للإلغاء)",
        "main.clip_preview_btn": "🔊 معاينة",
        "main.clip_preview_tooltip": "معاينة هذا المقطع في السماعة فقط (لا يُرسل إلى المايك)",
        "main.clip_play_btn": "▶ المايك",
        "main.clip_play_tooltip": "تشغيل هذا المقطع عبر المايك",
        "main.clip_loop_btn": "🔁 تكرار",
        "main.clip_loop_tooltip": "تكرار هذا المقطع باستمرار عبر المايك (اضغط إيقاف / F10 للإيقاف)",
        "main.clip_edit_btn": "تعديل",
        "main.clip_edit_tooltip": "قص المقطع أو تعديل التأثيرات الصوتية",
        "main.clip_export_btn": "↓ MP3",
        "main.clip_export_tooltip": "تصدير بصيغة MP3…",
        "main.clip_delete_tooltip": "حذف هذا المقطع",
        "main.clip_err_missing_wav": "تعذر تشغيل «{name}»: الملف الصوتي غير موجود",
        "main.clip_err_apo_disabled": "المايك المباشر غير مفعّل — اضغط «تفعيل المايك» أولاً (أو استخدم المعاينة للسماعة)",
        "main.clip_err_ring_unavailable": "مخزن المايك المباشر غير متاح — جرّب إعادة تفعيل المايك",
        "main.clip_err_playback_failed": "فشل التشغيل: {err}",
        "main.ctx_preview": "معاينة (السماعة)",
        "main.ctx_play": "تشغيل عبر المايك",
        "main.ctx_edit": "تعديل",
        "main.ctx_export": "تصدير بصيغة MP3…",
        "main.ctx_delete": "حذف",
        "main.export_dialog_title": "تصدير المقطع بصيغة MP3",
        "main.export_filter": "ملف صوتي MP3 (*.mp3)",
        "main.export_missing": "فشل التصدير: الملف الصوتي غير موجود للمقطع '{name}'",
        "main.export_in_progress": "جارٍ تصدير {name}…",
        "main.export_failed": "فشل التصدير: {err}",
        "main.export_done": "تم تصدير {name}",

        # MainWindow footer checkboxes
        "main.startup_chk": "التشغيل مع ويندوز",
        "main.startup_tooltip": "تشغيل ClipMic تلقائياً في شريط النظام عند بدء تشغيل ويندوز",
        "main.tray_close_chk": "التصغير إلى شريط النظام عند الإغلاق",
        "main.tray_close_tooltip": "إبقاء ClipMic يعمل في شريط النظام عند النقر على زر إغلاق النافذة",

        # MainWindow warnings & error dialogs
        "main.err_startup_title": "خطأ في إعداد بدء التشغيل",
        "main.err_startup_body": "تعذر تحديث إعداد التشغيل مع ويندوز:\n{err}",
        "main.err_capture_title": "خطأ في جهاز التسجيل",
        "main.err_capture_body": "تعذر التبديل إلى جهاز التسجيل:\n{err}",
        "main.err_hotkey_title": "تعارض في الاختصارات",
        "main.err_hotkey_body": "تعذر تسجيل الاختصار العام: {hotkeys}",
        "main.err_missing_file_title": "ملف مفقود",
        "main.err_missing_file_body": "الملف الصوتي غير موجود: {path}",
        "main.err_apo_missing_title": "ملف APO غير موجود",
        "main.err_apo_missing_body": "لم يتم العثور على ملف clipmic_apo.dll.\nأعد تثبيت ClipMic أو قم ببناء الملف أولاً.",
        "main.err_no_mic_title": "لم يتم اختيار مايك",
        "main.err_no_mic_body": "يرجى اختيار مايك متصل أولاً.",
        "main.err_enhancements_title": "تحسينات الصوت متوقفة في ويندوز",
        "main.err_enhancements_body": (
            "خيار «تحسينات الصوت» (Audio Enhancements) متوقف حالياً لهذا المايك.\n\n"
            "يتطلب المايك المباشر تفعيل تحسينات الصوت:\n"
            "  إعدادات ويندوز ← النظام ← الصوت ← اختر المايك ←\n"
            "  تحسينات الصوت ← التأثيرات الافتراضية للجهاز.\n\n"
            "هل تريد تثبيت المايك المباشر على أي حال؟"
        ),
        "main.err_apo_setup_title": "خطأ في إعداد المايك المباشر",

        # ChannelPickerDialog
        "picker.window_title": "ClipMic — اختيار قنوات الصوت",
        "picker.title": "اختيار قنوات الصوت",
        "picker.subtitle_one": "آخر {dur:.1f} ثانية  •  قناة نشطة واحدة",
        "picker.subtitle_many": "آخر {dur:.1f} ثانية  •  {n} قنوات نشطة",
        "picker.badge_game": "لعبة",
        "picker.badge_app": "تطبيق",
        "picker.badge_mic": "مايك",
        "picker.badge_system": "النظام",
        "picker.chk_tooltip": "تضمين {name} في المقطع",
        "picker.wave_tooltip": "انقر أو اسحب على الموجة الصوتية للمعاينة من أي نقطة",
        "picker.preview_tooltip": "معاينة هذه القناة في السماعة (لا تُرسل إلى المايك)",
        "picker.hint": "Enter للقص  •  Esc للإلغاء",
        "picker.confirm_btn": "قص المحدد ({n}) ←",
        "picker.confirm_tooltip": "دمج القنوات المحددة والانتقال إلى القص والتأثيرات",

        # TrimDialog
        "trim.window_title": "ClipMic — القص والتأثيرات",
        "trim.change_channels_count": "→ تغيير القنوات ({n})",
        "trim.change_channels": "→ تغيير القنوات",
        "trim.change_channels_tooltip": "العودة لاختيار القنوات مع الاحتفاظ بنفس تسجيل {dur}",
        "trim.title_edit": "تعديل مقطع محفوظ",
        "trim.title_trim": "قص: {summary}",
        "trim.title_import": "استيراد: {name}",
        "trim.title_capture": "التقاط فوري (آخر {n} ثانية)",
        "trim.range_label": "{start:.2f} ث – {end:.2f} ث  ({dur:.2f} ث)",
        "trim.waveform_tooltip": "اسحب المقابض لقص المقطع أو انقر على الموجة للمعاينة",
        "trim.fx_section": "التأثير الصوتي (قابل للتغيير)",
        "trim.fx_combo_tooltip": "اختيار تأثير صوتي للمعاينة والتطبيق",
        "trim.new_fx_btn": "+ تأثير جديد",
        "trim.new_fx_tooltip": "إنشاء تأثير صوتي مخصص مع معاينة مباشرة على هذا المقطع",
        "trim.manage_fx_btn": "إدارة",
        "trim.manage_fx_tooltip": "تعديل أو نسخ أو حذف أو استعادة التأثيرات الصوتية",
        "trim.play_preview_btn": "▶ تشغيل المعاينة",
        "trim.play_preview_tooltip": "سماع المقطع المقصوص مع التأثير المحدد في السماعة",
        "trim.stop_tooltip": "إيقاف المعاينة أو التشغيل عبر المايك",
        "trim.send_mic_btn": "🎙 إرسال للمايك",
        "trim.send_mic_tooltip": "تشغيل المقطع المقصوص مع التأثير مباشرة عبر المايك",
        "trim.status_processing": "جارٍ تطبيق التأثير الصوتي...",
        "trim.status_playing": "جارٍ تشغيل المعاينة...",
        "trim.status_sending": "جارٍ الإرسال إلى المايك...",
        "trim.status_speaker_mode": "جارٍ المعاينة (وضع السماعة)...",
        "trim.status_scrubbing": "التنقل عند {sec:.2f} ث",
        "trim.name_label": "الاسم:",
        "trim.name_placeholder": "اسم المقطع (مثال: ضحكة قوية)",
        "trim.untitled_clip": "مقطع بدون اسم",
        "trim.hotkey_label": "الاختصار:",
        "trim.hotkey_tooltip": "اختصار عام اختياري لتشغيل هذا المقطع في أي وقت",
        "trim.save_btn": "حفظ المقطع",
        "trim.save_tooltip": "حفظ هذا المقطع في قائمة المقاطع المحفوظة",
        "trim.err_ffmpeg_title": "خطأ في المعالجة الصوتية",

        # EffectBuilderDialog
        "builder.title_new": "تأثير صوتي جديد",
        "builder.title_edit": "تعديل التأثير الصوتي",
        "builder.title_duplicate": "نسخ التأثير الصوتي",
        "builder.window_title": "ClipMic — {title}",
        "builder.copy_suffix": "{name} (نسخة)",
        "builder.name_label": "الاسم:",
        "builder.name_placeholder": "مثال: صوت عميق، صوت سريع",
        "builder.pitch_label": "حدة الصوت",
        "builder.pitch_tooltip": "تغيير طبقة الصوت (من 0.60x عميق إلى 1.80x حاد)",
        "builder.speed_label": "السرعة",
        "builder.speed_tooltip": "سرعة التشغيل (من 0.55x بطيء إلى 1.80x سريع)",
        "builder.wobble_label": "الاهتزاز",
        "builder.wobble_tooltip": "درجة اهتزاز وتذبذب الصوت",
        "builder.crunch_label": "التشويش",
        "builder.crunch_tooltip": "تضخيم البيس مع تشويش صوتي محمي من الإزعاج",
        "builder.tone_label": "النبرة:",
        "builder.tone_tooltip": "اختيار طابع صوتي جاهز للنبرة",
        "builder.tone_normal": "عادي",
        "builder.tone_feminine": "ناعم",
        "builder.tone_nasal": "أنفي",
        "builder.tone_bad_mic": "مايك رديء",
        "builder.tone_underwater": "تحت الماء",
        "builder.echo_label": "الصدى:",
        "builder.echo_tooltip": "إضافة صدى غرفة أو قاعة واسعة",
        "builder.echo_none": "بدون",
        "builder.echo_room": "غرفة",
        "builder.echo_cathedral": "قاعة واسعة",
        "builder.robot_chk": "صوت روبوت",
        "builder.robot_tooltip": "إضافة تأثير صوت آلي",
        "builder.reverse_chk": "عكس الصوت",
        "builder.reverse_tooltip": "تشغيل المقطع الصوتي بالعكس",
        "builder.adv_chk": "تخصيص فلتر FFmpeg متقدم",
        "builder.adv_tooltip": "استبدال الإعدادات بسلسلة فلاتر FFmpeg مخصصة",
        "builder.adv_placeholder": "سلسلة فلاتر FFmpeg -af اختيارية (تدعم {sr})",
        "builder.preview_clip_tooltip": "سماع هذه الإعدادات مباشرة على المقطع الحالي",
        "builder.preview_sample_tooltip": "سماع هذه الإعدادات على عينة صوتية تجريبية",
        "builder.stop_tooltip": "إيقاف معاينة التأثير",
        "builder.reset_tooltip": "إعادة جميع الإعدادات إلى الوضع الافتراضي",
        "builder.save_btn": "حفظ التأثير",
        "builder.save_tooltip": "حفظ هذا التأثير في قائمة التأثيرات",
        "builder.status_rendering": "جارٍ تجهيز المعاينة...",
        "builder.status_playing": "جارٍ تشغيل المعاينة...",
        "builder.err_preview_title": "خطأ في المعاينة",
        "builder.err_name_required_title": "الاسم مطلوب",
        "builder.err_name_required_body": "يرجى إدخال اسم لهذا التأثير الصوتي.",
        "builder.err_reserved_title": "اسم محجوز",
        "builder.err_reserved_body": 'الاسم "Normal" محجوز. يرجى اختيار اسم آخر.',
        "builder.err_save_title": "خطأ في الحفظ",

        # ManageEffectsDialog & effect descriptions
        "manage_fx.window_title": "ClipMic — إدارة التأثيرات الصوتية",
        "manage_fx.title": "التأثيرات الصوتية",
        "manage_fx.new_btn": "+ تأثير جديد",
        "manage_fx.new_tooltip": "إنشاء تأثير صوتي مخصص جديد",
        "manage_fx.active_hdr": "التأثيرات المفعّلة ({n})",
        "manage_fx.more_hdr": "تأثيرات إضافية جاهزة ({n} متاحة للإضافة)",
        "manage_fx.tag_locked": "أساسي",
        "manage_fx.tag_builtin": "مدمج",
        "manage_fx.tag_custom": "مخصص",
        "manage_fx.edit_tooltip": "تعديل هذا التأثير الصوتي",
        "manage_fx.copy_tooltip": "نسخ هذا التأثير كإعداد مخصص جديد",
        "manage_fx.delete_tooltip": "حذف {name}",
        "manage_fx.add_btn": "+ إضافة",
        "manage_fx.add_tooltip": 'إضافة "{name}" إلى التأثيرات المفعّلة',
        "manage_fx.restore_btn": "استعادة التأثيرات الافتراضية",
        "manage_fx.restore_tooltip": "إرجاع جميع التأثيرات المدمجة دون حذف تأثيراتك المخصصة",
        "manage_fx.delete_confirm_title": "حذف التأثير",
        "manage_fx.delete_confirm_body": 'هل تريد حذف "{name}"؟',
        "manage_fx.err_add_title": "خطأ في إضافة التأثير",
        "manage_fx.err_delete_title": "خطأ في الحذف",
        "fx.desc_unmodified": "صوت أصلي بدون تعديل",
        "fx.desc_neutral": "متوازن",
        "fx.desc_custom_ffmpeg": "فلتر مخصص: {chain}",
        "fx.desc_pitch": "الحدة {val:.2f}x",
        "fx.desc_speed": "السرعة {val:.2f}x",
        "fx.desc_wobble": "اهتزاز {val}%",
        "fx.desc_crunch": "تشويش {val}%",
        "fx.desc_echo": "صدى {echo}",
        "fx.desc_robot": "روبوت",
        "fx.desc_reverse": "معكوس",
    },
}


def t(key: str, **kwargs: object) -> str:
    """Translate key for the active language, falling back to English then key."""
    table = TRANSLATIONS.get(_current_lang, TRANSLATIONS["en"])
    text = table.get(key)
    if text is None:
        text = TRANSLATIONS["en"].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError, IndexError):
            return text
    return text


_BUILTIN_EFFECT_NAMES_EN: dict[str, str] = {
    "normal": "Normal",
    "kid_high_pitch": "Kid / High Pitch",
    "feminine_girl": "Feminine / Girl",
    "nerd_nasal": "Nerd / Nasal",
    "mocking_spongebob": "Mocking / SpongeBob",
    "drunk_slurred": "Drunk / Slurred",
    "lobby_mic_2009": "2009 Lobby Mic",
    "underwater": "Underwater",
    "cathedral_dramatic": "Cathedral / Dramatic",
    "deep_fried_meme": "Deep-Fried / Meme",
    "fast_yap": "Fast Yap",
    "reverse": "Reverse",
    "chipmunk": "Chipmunk",
    "robot": "Robot",
    "echo": "Echo",
    "deep_demon": "Deep / Demon",
    "bass_boost_loud": "Bass Boost / Loud",
}

_BUILTIN_EFFECT_NAMES_AR: dict[str, str] = {
    "normal": "عادي",
    "kid_high_pitch": "طفل / صوت حاد",
    "feminine_girl": "صوت ناعم / فتاة",
    "nerd_nasal": "صوت أنفي",
    "mocking_spongebob": "ساخر / سبونج بوب",
    "drunk_slurred": "مترنّح / بطيء",
    "lobby_mic_2009": "مايك لوبي 2009",
    "underwater": "تحت الماء",
    "cathedral_dramatic": "كاتدرائية / درامي",
    "deep_fried_meme": "مشوّش / ميم",
    "fast_yap": "كلام سريع",
    "reverse": "معكوس",
    "chipmunk": "سنجاب",
    "robot": "روبوت",
    "echo": "صدى",
    "deep_demon": "عميق / وحش",
    "bass_boost_loud": "بيس عالي / صاخب",
}

_BUILTIN_EFFECT_ID_BY_EN: dict[str, str] = {
    en_name.lower(): eid for eid, en_name in _BUILTIN_EFFECT_NAMES_EN.items()
}


def localize_effect_name(effect_id: Optional[str], name: str) -> str:
    """Return Arabic display name for unmodified built-in effects; preserve custom/renamed names."""
    if _current_lang != "ar":
        return name
    clean_name = str(name or "").strip()
    if not clean_name:
        return name
    eid = str(effect_id or "").strip()
    if eid:
        default_en = _BUILTIN_EFFECT_NAMES_EN.get(eid)
        if default_en is not None and clean_name.lower() == default_en.lower():
            return _BUILTIN_EFFECT_NAMES_AR.get(eid, name)
        return name
    matched_id = _BUILTIN_EFFECT_ID_BY_EN.get(clean_name.lower())
    if matched_id is not None:
        return _BUILTIN_EFFECT_NAMES_AR.get(matched_id, name)
    return name
