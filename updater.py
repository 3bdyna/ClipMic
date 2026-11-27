from __future__ import annotations

import ctypes
import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import paths

logger = logging.getLogger("clipmic.updater")

TRUSTED_DOWNLOAD_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "api.github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com",
        "codeload.github.com",
    }
)

MAX_API_RESPONSE_BYTES = 1 * 1024 * 1024  # 1 MiB
MAX_CHECKSUMS_BYTES = 64 * 1024  # 64 KiB
MAX_INSTALLER_BYTES = 512 * 1024 * 1024  # 512 MiB
_DOWNLOAD_CHUNK_SIZE = 64 * 1024

_SEMVER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)", re.ASCII)
_REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", re.ASCII)
_HEX64_RE = re.compile(r"[0-9a-fA-F]{64}", re.ASCII)
_SHA256_LINE_RE = re.compile(r"^([0-9a-fA-F]{64})[ \t]+\*?(.+)$", re.ASCII)
_INSTALLER_NAME_RE = re.compile(r"ClipMic-Setup-[A-Za-z0-9._-]+\.exe", re.ASCII)


class UpdateError(RuntimeError):
    """Raised when downloading, verifying, or launching an update fails."""


class UntrustedUrlError(UpdateError):
    """Raised when an update URL or redirect violates the strict HTTPS host allowlist."""


class UpdateCancelledError(UpdateError):
    """Raised when an in-progress update download is cancelled by the user."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    notes: str
    html_url: str
    installer_name: str
    installer_url: str
    installer_size: int
    checksums_url: str
    published_at: str


@dataclass(frozen=True)
class UpdateCheckResult:
    status: str  # "up_to_date" | "update_available" | "error"
    current_version: str
    release: Optional[ReleaseInfo] = None
    error: str = ""
    error_code: str = ""


def _is_safe_filename(name: object) -> bool:
    """Return True iff name is a plain single-segment filename without traversal or control chars."""
    if not isinstance(name, str) or not name or name in (".", ".."):
        return False
    if any(ch in name for ch in ("/", "\\", ":", "\x00")):
        return False
    if any(ord(ch) < 32 for ch in name):
        return False
    return Path(name).name == name


def parse_semver(version_str: str) -> tuple[int, int, int]:
    """Parse strict MAJOR.MINOR.PATCH semver (optional single leading v/V)."""
    if not isinstance(version_str, str) or any(ch in version_str for ch in ("\r", "\n")):
        raise ValueError(f"Invalid semantic version: {version_str!r}")
    cleaned = version_str.strip(" \t")
    if cleaned.startswith(("v", "V")):
        cleaned = cleaned[1:]
    match = _SEMVER_RE.fullmatch(cleaned)
    if match is None:
        raise ValueError(f"Invalid semantic version: {version_str!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_newer_version(candidate: str, current: str) -> bool:
    """Return True iff candidate is strictly newer than current using numeric semver comparison."""
    try:
        return parse_semver(candidate) > parse_semver(current)
    except (ValueError, TypeError):
        return False


def is_trusted_download_url(url: str) -> bool:
    """Enforce HTTPS, no credentials, standard port, and exact allowlisted GitHub host."""
    if not isinstance(url, str) or not url:
        return False
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) >= 127 or ch == "\\" for ch in url):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parsed.scheme.lower() != "https":
        return False
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port not in (None, 443):
        return False
    host = (parsed.hostname or "").lower()
    if not host or host.endswith("."):
        return False
    if parsed.netloc.lower() not in (host, f"{host}:443"):
        return False
    if host not in TRUSTED_DOWNLOAD_HOSTS:
        return False
    if not parsed.path or not parsed.path.startswith("/"):
        return False
    return True


def is_trusted_release_page_url(url: str, repo: str = paths.GITHUB_REPO) -> bool:
    """Restrict browser-opened release links strictly to https://github.com/<repo>/..."""
    if not is_trusted_download_url(url):
        return False
    if not isinstance(repo, str) or _REPO_RE.fullmatch(repo) is None:
        return False
    parsed = urllib.parse.urlsplit(url)
    if (parsed.hostname or "").lower() != "github.com":
        return False
    return parsed.path.lower().startswith(f"/{repo.lower()}/")


def parse_sha256sums(text: str, filename: str) -> Optional[str]:
    """Extract lowercase 64-hex SHA-256 digest matching exact basename filename."""
    if not isinstance(text, str) or not _is_safe_filename(filename):
        return None
    for idx, raw_line in enumerate(text.splitlines()):
        line = raw_line.lstrip("\ufeff") if idx == 0 else raw_line
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SHA256_LINE_RE.match(line)
        if not match:
            continue
        digest, entry_name = match.group(1), match.group(2).strip()
        if entry_name == filename:
            return digest.lower()
    return None


def verify_file_sha256(path: Path, expected_hex: str) -> bool:
    """Stream-hash file and compare against expected 64-hex SHA-256 using constant-time digest check."""
    if not isinstance(expected_hex, str) or _HEX64_RE.fullmatch(expected_hex) is None:
        return False
    file_path = Path(path)
    if not file_path.is_file():
        return False
    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as fh:
            while True:
                chunk = fh.read(_DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        return False
    return hmac.compare_digest(digest.hexdigest().lower(), expected_hex.lower())


def select_release_assets(release_json: dict) -> Optional[tuple[dict, dict]]:
    """Find both the Windows installer ClipMic-Setup-*.exe and SHA256SUMS.txt assets."""
    if not isinstance(release_json, dict):
        return None
    assets = release_json.get("assets")
    if not isinstance(assets, list):
        return None

    preferred_name: Optional[str] = None
    tag_raw = release_json.get("tag_name")
    if isinstance(tag_raw, str):
        try:
            ver = parse_semver(tag_raw)
            preferred_name = f"ClipMic-Setup-{ver[0]}.{ver[1]}.{ver[2]}.exe"
        except ValueError:
            preferred_name = None

    exact_installer: Optional[dict] = None
    fallback_installer: Optional[dict] = None
    checksums_asset: Optional[dict] = None

    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        dl_url = asset.get("browser_download_url")
        if not _is_safe_filename(name) or not isinstance(dl_url, str):
            continue
        if not is_trusted_download_url(dl_url):
            continue
        if name == "SHA256SUMS.txt":
            checksums_asset = asset
        elif _INSTALLER_NAME_RE.fullmatch(name):
            if preferred_name is not None and name == preferred_name:
                exact_installer = asset
            elif fallback_installer is None:
                fallback_installer = asset

    installer_asset = exact_installer or fallback_installer
    if installer_asset is None or checksums_asset is None:
        return None
    return installer_asset, checksums_asset


def _build_ssl_context() -> ssl.SSLContext:
    """Create a strict TLS 1.2+ SSLContext using the Windows system trust store."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if hasattr(ssl, "TLSVersion"):
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class _SafeGitHubRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Validate EVERY HTTP redirect hop against the strict GitHub HTTPS allowlist."""

    max_redirections = 5

    def __init__(self) -> None:
        super().__init__()
        self.last_url: Optional[str] = None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        resolved = urllib.parse.urljoin(req.full_url, newurl)
        if not is_trusted_download_url(resolved):
            if fp is not None:
                try:
                    fp.close()
                except Exception:
                    pass
            raise UntrustedUrlError(f"Blocked redirect to untrusted URL: {resolved}")
        self.last_url = resolved
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def _is_missing_issuer_ssl_error(exc: BaseException) -> bool:
    """Return True iff exc is an SSL verification error caused by an uncached Windows root CA."""
    err: Optional[BaseException] = exc
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        err = exc.reason
    if not isinstance(err, ssl.SSLError):
        return False
    msg = str(err).lower()
    return "unable to get issuer certificate" in msg or "unable to get local issuer certificate" in msg


def _refresh_windows_root_store(urls: list[str], timeout: float = 5.0) -> bool:
    """Trigger Windows Automatic Root Certificate Update via strict WinHTTP TLS for trusted hosts."""
    if sys.platform != "win32":
        return False
    hosts: list[str] = []
    seen: set[str] = set()
    for u in urls:
        if not u or not is_trusted_download_url(u):
            continue
        host = (urllib.parse.urlsplit(u).hostname or "").lower()
        if host and host not in seen:
            seen.add(host)
            hosts.append(host)
    if not hosts:
        return False

    try:
        import ctypes.wintypes as wt

        wh = ctypes.windll.winhttp
        wh.WinHttpOpen.argtypes = [wt.LPCWSTR, wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD]
        wh.WinHttpOpen.restype = wt.LPVOID
        wh.WinHttpSetTimeouts.argtypes = [wt.LPVOID, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        wh.WinHttpSetTimeouts.restype = wt.BOOL
        wh.WinHttpConnect.argtypes = [wt.LPVOID, wt.LPCWSTR, wt.WORD, wt.DWORD]
        wh.WinHttpConnect.restype = wt.LPVOID
        wh.WinHttpOpenRequest.argtypes = [
            wt.LPVOID, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPVOID, wt.DWORD,
        ]
        wh.WinHttpOpenRequest.restype = wt.LPVOID
        wh.WinHttpSendRequest.argtypes = [
            wt.LPVOID, wt.LPCWSTR, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, ctypes.c_size_t,
        ]
        wh.WinHttpSendRequest.restype = wt.BOOL
        wh.WinHttpCloseHandle.argtypes = [wt.LPVOID]
        wh.WinHttpCloseHandle.restype = wt.BOOL

        ms = max(1000, min(5000, int(timeout * 1000)))
        session = wh.WinHttpOpen("ClipMic-TLS-Sync/1.0", 4, None, None, 0)  # WINHTTP_ACCESS_TYPE_AUTOMATIC_PROXY
        if not session:
            session = wh.WinHttpOpen("ClipMic-TLS-Sync/1.0", 0, None, None, 0)  # WINHTTP_ACCESS_TYPE_DEFAULT_PROXY
        if not session:
            return False

        refreshed = False
        try:
            wh.WinHttpSetTimeouts(session, ms, ms, ms, ms)
            for host in hosts:
                conn = wh.WinHttpConnect(session, host, 443, 0)
                if not conn:
                    continue
                try:
                    # WINHTTP_FLAG_SECURE (0x00800000): strict Schannel TLS + triggers Windows AutoRootUpdate
                    req = wh.WinHttpOpenRequest(conn, "HEAD", "/", None, None, None, 0x00800000)
                    if not req:
                        continue
                    try:
                        if wh.WinHttpSendRequest(req, None, 0, None, 0, 0, 0):
                            refreshed = True
                    finally:
                        wh.WinHttpCloseHandle(req)
                finally:
                    wh.WinHttpCloseHandle(conn)
        finally:
            wh.WinHttpCloseHandle(session)
        return refreshed
    except Exception:
        return False


def _build_safe_opener() -> tuple[urllib.request.OpenerDirector, _SafeGitHubRedirectHandler]:
    """Build an HTTPS-only urllib opener (no HTTP, file://, or FTP handlers)."""
    opener = urllib.request.OpenerDirector()
    redirect_handler = _SafeGitHubRedirectHandler()
    opener.add_handler(urllib.request.ProxyHandler())
    opener.add_handler(urllib.request.UnknownHandler())
    opener.add_handler(urllib.request.HTTPSHandler(context=_build_ssl_context()))
    opener.add_handler(redirect_handler)
    opener.add_handler(urllib.request.HTTPErrorProcessor())
    opener.add_handler(urllib.request.HTTPDefaultErrorHandler())
    return opener, redirect_handler


def _open_trusted_url(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
):
    if not is_trusted_download_url(url):
        raise UntrustedUrlError(f"Refused untrusted update URL: {url}")
    req = urllib.request.Request(url, headers=headers, method="GET")
    opener, redirect_handler = _build_safe_opener()
    try:
        resp = opener.open(req, timeout=timeout)
    except (ssl.SSLError, urllib.error.URLError) as exc:
        if _is_missing_issuer_ssl_error(exc):
            targets = [url]
            if redirect_handler.last_url:
                targets.append(redirect_handler.last_url)
            if _refresh_windows_root_store(targets, timeout=min(timeout, 5.0)):
                retry_opener, _ = _build_safe_opener()
                resp = retry_opener.open(req, timeout=timeout)
            else:
                raise
        else:
            raise
    final_url = resp.geturl() if hasattr(resp, "geturl") else url
    if final_url and not is_trusted_download_url(final_url):
        resp.close()
        raise UntrustedUrlError(f"Refused untrusted final URL: {final_url}")
    return resp


def _read_bounded(resp, max_bytes: int) -> bytes:
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise UpdateError(f"Response exceeded maximum allowed size ({max_bytes} bytes).")
    return data


def check_for_updates(
    current_version: str = paths.APP_VERSION,
    repo: str = paths.GITHUB_REPO,
    timeout: float = 10.0,
) -> UpdateCheckResult:
    """Check GitHub Releases for a newer verified release without ever raising."""
    if not isinstance(repo, str) or _REPO_RE.fullmatch(repo) is None:
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error="Invalid GitHub repository configuration.",
            error_code="invalid_repo",
        )

    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {
        "User-Agent": f"ClipMic/{current_version}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    try:
        with _open_trusted_url(api_url, headers=headers, timeout=timeout) as resp:
            raw = _read_bounded(resp, MAX_API_RESPONSE_BYTES)
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            return UpdateCheckResult(
                status="error",
                current_version=current_version,
                error="Unexpected response from GitHub Releases.",
                error_code="invalid_response",
            )
    except urllib.error.HTTPError as exc:
        code = exc.code
        reason = str(exc.reason)
        remaining = exc.headers.get("X-RateLimit-Remaining", "") if exc.headers else ""
        try:
            exc.close()
        except Exception:
            pass
        if code == 404:
            return UpdateCheckResult(
                status="error",
                current_version=current_version,
                error="No published releases found yet.",
                error_code="no_releases",
            )
        if code == 403:
            if remaining == "0" or "rate limit" in reason.lower():
                return UpdateCheckResult(
                    status="error",
                    current_version=current_version,
                    error="GitHub API rate limit reached. Please try again later.",
                    error_code="rate_limited",
                )
            return UpdateCheckResult(
                status="error",
                current_version=current_version,
                error="GitHub rejected the update check (HTTP 403).",
                error_code="rate_limited",
            )
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error=f"GitHub returned HTTP {code}.",
            error_code="http_error",
        )
    except ssl.SSLError as exc:
        logger.warning("Update check TLS error: %s", exc)
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error=f"Secure connection (TLS) failed: {exc}",
            error_code="tls",
        )
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLError):
            logger.warning("Update check TLS error: %s", exc.reason)
            return UpdateCheckResult(
                status="error",
                current_version=current_version,
                error=f"Secure connection (TLS) failed: {exc.reason}",
                error_code="tls",
            )
        logger.info("Update check network error: %s", exc.reason)
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error="Could not reach GitHub. Check your internet connection.",
            error_code="network",
        )
    except (TimeoutError, OSError, http.client.HTTPException) as exc:
        logger.info("Update check timed out or failed: %s", exc)
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error="Could not reach GitHub. Check your internet connection.",
            error_code="network",
        )
    except (json.JSONDecodeError, UnicodeDecodeError, UpdateError, ValueError) as exc:
        logger.warning("Update check parse/validation error: %s", exc)
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error=f"Invalid update response: {exc}",
            error_code="invalid_response",
        )

    if data.get("draft") or data.get("prerelease"):
        return UpdateCheckResult(status="up_to_date", current_version=current_version)

    tag_name = str(data.get("tag_name") or "").strip()
    if not is_newer_version(tag_name, current_version):
        return UpdateCheckResult(status="up_to_date", current_version=current_version)

    selected = select_release_assets(data)
    if selected is None:
        return UpdateCheckResult(
            status="error",
            current_version=current_version,
            error="Release is missing a verified installer or SHA256SUMS.txt.",
            error_code="missing_assets",
        )

    installer_asset, checksums_asset = selected
    ver_tuple = parse_semver(tag_name)
    norm_version = f"{ver_tuple[0]}.{ver_tuple[1]}.{ver_tuple[2]}"

    raw_html_url = str(data.get("html_url") or "").strip()
    if is_trusted_release_page_url(raw_html_url, repo):
        html_url = raw_html_url
    else:
        html_url = f"https://github.com/{repo}/releases/latest"

    try:
        installer_size = max(0, int(installer_asset.get("size") or 0))
    except (TypeError, ValueError):
        installer_size = 0

    release = ReleaseInfo(
        version=norm_version,
        tag_name=tag_name,
        notes=str(data.get("body") or "").strip(),
        html_url=html_url,
        installer_name=str(installer_asset["name"]),
        installer_url=str(installer_asset["browser_download_url"]),
        installer_size=installer_size,
        checksums_url=str(checksums_asset["browser_download_url"]),
        published_at=str(data.get("published_at") or "").strip(),
    )
    return UpdateCheckResult(
        status="update_available",
        current_version=current_version,
        release=release,
    )


def download_and_verify_update(
    release: ReleaseInfo,
    dest_dir: Path,
    on_progress: Optional[Callable[[int, int], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    timeout: float = 30.0,
    on_verifying: Optional[Callable[[], None]] = None,
) -> Path:
    """Download SHA256SUMS.txt and installer, verify SHA-256, and return final .exe path."""
    if not _is_safe_filename(release.installer_name) or not _INSTALLER_NAME_RE.fullmatch(release.installer_name):
        raise UpdateError(f"Unsafe installer filename: {release.installer_name!r}")
    if not is_trusted_download_url(release.checksums_url):
        raise UntrustedUrlError(f"Untrusted checksums URL: {release.checksums_url}")
    if not is_trusted_download_url(release.installer_url):
        raise UntrustedUrlError(f"Untrusted installer URL: {release.installer_url}")

    dest_root = Path(dest_dir).resolve()
    dest_root.mkdir(parents=True, exist_ok=True)
    part_path = (dest_root / f"{release.installer_name}.part").resolve()
    final_path = (dest_root / release.installer_name).resolve()
    if part_path.parent != dest_root or final_path.parent != dest_root:
        raise UpdateError("Destination path escapes update staging directory.")

    if cancel_event is not None and cancel_event.is_set():
        raise UpdateCancelledError("Download cancelled.")

    headers = {
        "User-Agent": f"ClipMic/{paths.APP_VERSION}",
        "Accept": "application/octet-stream",
    }

    # 1. Fetch and parse SHA256SUMS.txt first (bounded to 64 KiB)
    try:
        with _open_trusted_url(release.checksums_url, headers=headers, timeout=timeout) as resp:
            sums_bytes = _read_bounded(resp, MAX_CHECKSUMS_BYTES)
        sums_text = sums_bytes.decode("utf-8", errors="strict")
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError(f"Failed to download SHA256SUMS.txt: {exc}") from exc

    expected_sha256 = parse_sha256sums(sums_text, release.installer_name)
    if expected_sha256 is None:
        raise UpdateError(f"SHA256SUMS.txt does not contain a valid entry for {release.installer_name}.")

    if cancel_event is not None and cancel_event.is_set():
        raise UpdateCancelledError("Download cancelled.")

    # 2. Stream installer to .part file with progress + cancel support
    if part_path.exists():
        try:
            part_path.unlink()
        except OSError:
            pass

    downloaded = 0
    try:
        with _open_trusted_url(release.installer_url, headers=headers, timeout=timeout) as resp:
            hdr_len = resp.headers.get("Content-Length") if resp.headers else None
            try:
                total = int(hdr_len) if hdr_len is not None else release.installer_size
            except (TypeError, ValueError):
                total = release.installer_size
            total = max(0, total)
            if total > MAX_INSTALLER_BYTES:
                raise UpdateError("Installer file exceeds maximum allowed size.")

            if on_progress is not None:
                on_progress(0, total)

            with part_path.open("wb") as out_fh:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise UpdateCancelledError("Download cancelled.")
                    chunk = resp.read(_DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_INSTALLER_BYTES:
                        raise UpdateError("Installer download exceeded maximum allowed size.")
                    out_fh.write(chunk)
                    if on_progress is not None:
                        on_progress(downloaded, total)

        if cancel_event is not None and cancel_event.is_set():
            raise UpdateCancelledError("Download cancelled.")

        # 3. Verify SHA-256 BEFORE promoting .part -> final .exe
        if on_verifying is not None:
            on_verifying()
        if not verify_file_sha256(part_path, expected_sha256):
            raise UpdateError("SHA-256 checksum mismatch; downloaded file was discarded.")

        os.replace(part_path, final_path)
        return final_path
    except Exception:
        try:
            if part_path.exists():
                part_path.unlink()
        except OSError:
            pass
        raise


def build_installer_command(installer_path: Path, silent: bool = True) -> list[str]:
    """Construct Inno Setup launch arguments (with /RELAUNCH for silent self-updates)."""
    exe = str(Path(installer_path))
    if silent:
        return [
            exe,
            "/SILENT",
            "/SUPPRESSMSGBOXES",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
            "/FORCECLOSEAPPLICATIONS",
            "/RELAUNCH",
        ]
    return [exe]


def launch_installer(installer_path: Path, silent: bool = True) -> None:
    """Launch a verified ClipMic Setup installer detached from the current process."""
    exe_path = Path(installer_path)
    if not exe_path.is_file():
        raise FileNotFoundError(f"Verified installer not found: {exe_path}")
    cmd = build_installer_command(exe_path, silent=silent)
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    subprocess.Popen(
        cmd,
        cwd=str(exe_path.parent),
        close_fds=True,
        creationflags=creationflags,
    )
