---
name: publish-release
description: >-
  Use this skill when building, packaging, locally installing (`-InstallLocal`),
  publishing a GitHub release (`"publish update"`), or editing ClipMic's
  installer (`installer/ClipMic.iss`), PyInstaller spec (`ClipMic.spec`),
  release script (`scripts/build_release.ps1`), or self-updater (`updater.py`).
---

# ClipMic Release, Installer & Updater Runbook

## Commands
- **Full unit tests:** `$env:PYTHONPATH=".;tests"; python -m unittest test_clipmic -v`
- **Build local release installer (no publish):** `.\scripts\build_release.ps1`
- **Fast local install + relaunch (after tests pass & git push):** `.\scripts\build_release.ps1 -SkipTests -InstallLocal`
- **Publish release to GitHub:** `.\scripts\build_release.ps1 -Publish` (or `-SkipBuild -SkipTests -Publish` after verifying a clean local build)

## 2-Word Publish Workflow (`"publish update"`)
1. Check if tag `v<APP_VERSION>` already exists on GitHub (`git tag -l` / `gh release view v<APP_VERSION>`).
2. If it exists, auto-bump the patch version in `paths.py` (`APP_VERSION` ONLY).
3. Run the full unit test suite (`$env:PYTHONPATH=".;tests"; python -m unittest test_clipmic -v`).
4. Commit and push changes to `origin/main`.
5. Run `.\scripts\build_release.ps1 -Publish`.

## Smart Local App Updates
- Skip rebuilds for docs-only edits (`README.md`, `AGENTS.md`).
- While iterating on quick UI/small tweaks, batch local rebuilds unless the user asks to test the installed app immediately.
- When finishing a feature or changing critical logic, run `.\scripts\build_release.ps1 -SkipTests -InstallLocal` (after tests pass and changes are pushed) so the installed app updates in-place and relaunches automatically.

## Packaging, Installer & Updater Guardrails (DO NOT BREAK)
1. **Single Version Source:** `paths.APP_VERSION` (`MAJOR.MINOR.PATCH`) is the ONLY hardcoded version string. Never hardcode versions in `ui.py`, `updater.py`, `installer/ClipMic.iss`, or `scripts/build_release.ps1`.
2. **Never Change Inno Setup `AppId`:** The fixed GUID `{{8D4F6C2B-9A3E-4E71-B5C8-3F1D2E6A940B}` in `installer/ClipMic.iss` must NEVER change, or Windows will install duplicate copies instead of upgrading in-place.
3. **Mutex Name Parity:** `main._MUTEX_NAME` (`Global\ClipMic_SingleInstance_Mutex`) MUST stay identical to `AppMutex` and `CheckForMutexes(...)` in `installer/ClipMic.iss` (enforced by automated tests). Always release the mutex in `main.py` before launching the silent installer.
4. **PyInstaller Spec (`ClipMic.spec`):** Must bundle `native/directmic/clipmic_apo.dll`, `native/procloopback/clipmic_procloop.dll`, `ffmpeg.exe`, `clipmic.ico`, `LICENSE`, `THIRD_PARTY_NOTICES.txt`; excludes `tkinter` and unused Qt modules; must NEVER exclude `ssl`/`urllib`.
5. **User Data Preservation:** Never store user clips/configs inside the install directory, and NEVER delete `%LOCALAPPDATA%\ClipMic` during install, upgrade, or uninstall.
6. **Updater Security Invariants:** Keep `updater.py` 100% Python stdlib (zero new pip packages). Never weaken `TRUSTED_DOWNLOAD_HOSTS`, never disable TLS verification or per-hop redirect validation, and never skip `SHA256SUMS.txt` verification before launching an installer.
7. **Non-Intrusive Startup Checks:** Background startup update checks run ONLY in frozen builds (`paths.is_frozen()`), fail silently offline, and NEVER show modal dialogs on startup.
8. **DirectMic APO DLL Upgrades:** `clipmic_apo.dll` is installed to `%ProgramData%\ClipMic\MicPlugin\` only via explicit elevated `apo_setup.py` flow when a user enables Direct Mic. Never silently modify Windows registry or `Audiosrv` from the per-user installer; if a future release updates `clipmic_apo.dll`, users refresh it via Disable Mic -> Enable Mic.
9. **Network Policy:** No telemetry and no third-party media/audio downloaders. The ONLY permitted outbound network traffic is `updater.py` querying and downloading hash-verified releases from `paths.GITHUB_REPO` on GitHub Releases.
