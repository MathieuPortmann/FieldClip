#!/usr/bin/env python3
# =============================================================================
#  FieldClip — rename, organise, verify and export field videos
#  fieldclip/app.py
#
#  Requires Python 3.8+  •  tkinter bundled with standard Python installer
#  No pip packages needed.  Works on Windows, macOS, Linux.
#
#  Everything project-specific (sites, groups, devices, file-name template,
#  encoding, server) comes from a profile: see fieldclip/settings.py and
#  docs/configuration.md.
#
#  ffmpeg: bin/ffmpeg/<os>/ in the FieldClip root, tools.ffmpeg in the
#  profile, FIELDCLIP_FFMPEG, or the PATH.
#
#  Launch:
#    double-click a launcher in launchers/, or:  python -m fieldclip
# =============================================================================

import os, re, csv, json, shutil, platform, subprocess, struct, threading, time
import urllib.request, urllib.error, urllib.parse, http.client, socket
import queue, traceback, hashlib, base64
from pathlib import Path
from datetime import datetime, timedelta

from . import settings as S
from . import naming as NM

# --- Flight-log add-on (SRT reconstruction, telemetry enrichment, true model) ---
try:
    from . import flightlog as HFL
except Exception:
    HFL = None

# --- File logging: the console window dies with the app, the log file does not ---
try:
    from . import log as LOG
except Exception:
    class _NoLog:                      # keep the app usable even without it
        def __getattr__(self, _): return lambda *a, **k: ""
    LOG = _NoLog()

# --- Mount manager: lets FieldClip detect and repair a dead SSHFS mount ---
try:
    from . import remote_mount as EM
except Exception:
    EM = None

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# =============================================================================
#  COLOUR PALETTE  (tk.Label / tk.Frame for reliable Mac colours)
# =============================================================================
C = {
    "bg":          "#1c2018",  "panel":       "#252b20",  "panel2":      "#2e3628",
    "accent":      "#6b8f3e",  "accent_lite": "#9bbf5c",  "accent_dark": "#4a6628",
    "text":        "#e8e0cc",  "muted":       "#8a9678",  "success":     "#7ab648",
    "warning":     "#c8a84b",  "danger":      "#c45c3a",  "entry_bg":    "#131510",
    "separator":   "#3a4530",  "btn_fg":      "#f5f0e6",  "header_bg":   "#161a12",
    "tree_sel":    "#4a6628",  "auto_badge":  "#2a4060",
}

# =============================================================================
#  PERSISTENT USER STATE  — per-user config directory (see settings.py)
# =============================================================================
CONFIG_FILE = S.user_config_file()
load_config = S.load_user_config
save_config = S.save_user_config

# =============================================================================
#  PROFILE  — everything below used to be hard-coded; it now comes from the
#  active profile. Edit profiles/*.json, not this file.
# =============================================================================
SETTINGS = S.active()
NAMING = NM.Naming(SETTINGS)

SITE_LABEL  = SETTINGS["fields"]["site"]["label"]
GROUP_LABEL = SETTINGS["fields"]["group"]["label"]
TRAINING_LABEL = SETTINGS["training"]["label"]
REMOTE_NAME = SETTINGS["remote"]["display_name"]

SITES = S.value_names(SETTINGS["fields"]["site"]["values"])
GROUP_STRUCTURES = S.value_names(SETTINGS["fields"]["group"]["values"])
BUILTIN_DEVICES  = list(SETTINGS["devices"]["builtin"])
DJI_FALLBACK     = SETTINGS["devices"]["dji_fallback"]
PHONE_DEVICE     = SETTINGS["devices"]["phone"]
# Export target when the destination cannot be derived from anything saved.
DEFAULT_WEBDAV_SUBDIR = SETTINGS["remote"]["webdav"]["subdir"]
VIDEO_EXTENSIONS = {e.lower() for e in SETTINGS["files"]["video_ext"]}
COMPANION_EXTENSIONS = set(SETTINGS["files"]["companion_ext"])

# Sidecars whose extension is compound. "DJI_0083.flightlog.csv" has to split as
# stem "DJI_0083" + suffix ".flightlog.csv", not as Path()'s "DJI_0083.flightlog"
# + ".csv" — otherwise the file matches no video and is left behind at rename.
# Longest first: the first match wins.
COMPANION_COMPOUND_SUFFIXES = tuple(sorted(SETTINGS["files"]["compound_suffixes"],
                                           key=len, reverse=True))

# Drone models as they may be written in a folder name. Most specific first:
# "…_mini3pro" must not be caught by the plain "mini3" rule.
DEVICE_PATH_PATTERNS = [(tok, re.compile(pat))
                        for tok, pat in SETTINGS["devices"]["folder_patterns"]]
# Placeholders that are not a real drone model. A raw DJI filename only tells
# the family apart (see dji_family); a real model found elsewhere wins.
GENERIC_DEVICES = set(SETTINGS["devices"]["generic"])

# Device tokens of an earlier nomenclature, as they may still appear in names
# already written. Read-only: a file carrying one is parsed, and renamed with
# the current token. None = family only, the model has to be resolved.
LEGACY_DEVICE_TOKENS = dict(SETTINGS["devices"]["legacy_aliases"])

SECRET_LABEL = SETTINGS["remote"].get("secret_label", "Password")
REENCODE_WHEN = SETTINGS["encoding"]["reencode"]["when"]
WEBDAV_RESPONSE_TIMEOUT = SETTINGS["remote"]["webdav"].get("response_timeout_s", 600)


def _template_wants(field: str, is_training: bool) -> bool:
    """True when the (training) name template contains {field} as required."""
    toks = NAMING.tr_tokens if is_training else NAMING.tokens
    return any(k == "field" and n == field and not opt for k, n, opt in toks)


def _reencode_hint() -> str:
    codec = SETTINGS["encoding"]["reencode"]["codec"]
    if REENCODE_WHEN == "always":
        return f"Every video is re-encoded with {codec}."
    if REENCODE_WHEN == "never":
        return "Re-encoding is disabled in this profile."
    return f"H.264 10-bit crash-recovery files are re-encoded with {codec}."


def _reencode_checkbox_text() -> str:
    codec = SETTINGS["encoding"]["reencode"]["codec"]
    if REENCODE_WHEN == "always":
        return f"Re-encode every video → {codec}  ⚠  slow"
    return f"Re-encode H.264 10-bit crash-recovery → {codec}  ⚠  ~5 min/video"

WEBDAV_URL   = (SETTINGS["remote"]["webdav"].get("url") or "").rstrip("/") + "/" \
               if SETTINGS["remote"]["webdav"].get("url") else ""


def _layout_hint() -> str:
    """'Training/<site>/  and  Main/<site>/' for the destination card."""
    def one(tr):
        return "/".join(c.replace("{category}", NAMING.category(tr))
                         .replace("{", "<").replace("}", ">") for c in NAMING.layout) + "/"
    return f"{one(True)}  and  {one(False)}"

PHONE_PREFIX_RE = re.compile(
    r'^(' + "|".join(re.escape(p.upper()) for p in SETTINGS["devices"]["phone_prefixes"]) + r')'
    if SETTINGS["devices"]["phone_prefixes"] else r'(?!)')

# Minimum delay between two progress updates pushed to the UI, in seconds.
# The transfer callbacks fire once per 4 MB chunk; at 3.5 GB per file that is
# 875 calls each, so without throttling a 60-file export floods the UI thread
# with ~105 000 messages that no human can read anyway.
UI_UPDATE_INTERVAL = 0.25

# =============================================================================
#  WINDOWS SLEEP / KEEPALIVE HELPERS
# =============================================================================

def keep_awake(reason: str = "FieldClip transfer in progress"):
    """
    Stop the machine from suspending while a transfer runs, on every OS.

    It used to be Windows-only. On Linux the desktop's idle timer suspended
    the laptop mid-upload: the TCP connection to the server died with the network,
    and the PUT then sat on a dead socket (see SuspendWatchdog for why it did
    not even time out).

      Windows : SetThreadExecutionState (ES_CONTINUOUS | ES_SYSTEM_REQUIRED |
                ES_DISPLAY_REQUIRED).
      Linux   : a systemd-inhibit "sleep:idle" block lock held by a child
                process. The child is `tail --pid=<us>` so the lock dies with
                the app even if it is killed - a plain `sleep infinity` would
                outlive a crash and block suspend until reboot.
      macOS   : caffeinate -i -s -w <us>, same lifetime guarantee via -w.

    Closing a laptop lid still suspends (logind ignores inhibitors for the
    lid by default); SuspendWatchdog covers that case.

    Call once at the start of a transfer worker; the returned function
    releases the lock.
    """
    system = platform.system()
    if system == "Windows":
        ES_CONTINUOUS       = 0x80000000
        ES_SYSTEM_REQUIRED  = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            def restore():
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            return restore
        except Exception:
            return lambda: None

    pid = str(os.getpid())
    if system == "Linux" and shutil.which("systemd-inhibit") and shutil.which("tail"):
        cmd = ["systemd-inhibit", "--what=sleep:idle", "--who=FieldClip",
               f"--why={reason}", "--mode=block",
               "tail", f"--pid={pid}", "-f", "/dev/null"]
    elif system == "Darwin" and shutil.which("caffeinate"):
        cmd = ["caffeinate", "-i", "-s", "-w", pid]
    else:
        return lambda: None
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except Exception as e:
        LOG.warn(f"keep_awake: could not start {cmd[0]}: {e}")
        return lambda: None
    LOG.info(f"keep_awake: sleep inhibited ({cmd[0]})")

    def restore():
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try: proc.kill()
            except Exception: pass
    return restore


class SuspendWatchdog:
    """
    Abort a blocking network call once it can no longer succeed.

    Socket timeouts are measured on the monotonic clock, which does NOT advance
    while the machine is suspended. After a resume the upload's 600 s timeout
    therefore restarts from wherever it was, while the kernel keeps
    retransmitting into a connection the server forgot long ago (observed:
    1.6 of 3.8 GB sent, then frozen for 40+ min after two suspends).

    The watchdog thread fires `on_abort` - which should shutdown() the socket,
    making the blocked send()/recv() raise at once and fall into the caller's
    retry loop - when either:
      * wall-clock time jumped ahead of monotonic time by more than
        `suspend_gap` s, i.e. the machine slept; or
      * `stall_after` s passed without a `progress()` call while `armed`.
        The caller disarms it during waits that are legitimately silent
        (a server checksumming a multi-GB file before answering the PUT).
    """

    def __init__(self, on_abort, stall_after: float = 120.0,
                 suspend_gap: float = 20.0, poll: float = 2.0):
        self._on_abort    = on_abort
        self._stall_after = stall_after
        self._suspend_gap = suspend_gap
        self._poll        = poll
        self._stop        = threading.Event()
        self._last        = time.monotonic()
        self.armed        = True
        self.reason       = None     # set when the watchdog fired
        self._thread      = threading.Thread(target=self._run, daemon=True,
                                             name="suspend-watchdog")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        return False

    @property
    def send_timeout(self) -> float:
        """Socket timeout for the sending phase: a little past `stall_after`,
        so the watchdog has fired (and set `reason`) by the time it expires."""
        return self._stall_after * 1.5 + self._poll

    def progress(self):
        self._last = time.monotonic()

    def _run(self):
        offset = time.time() - time.monotonic()
        while not self._stop.wait(self._poll):
            # No numbers in `reason`: the upload's retry loop classifies errors
            # by substring, and "~4013 s" would read as a permanent HTTP 401.
            now_off = time.time() - time.monotonic()
            if now_off - offset > self._suspend_gap:
                LOG.info(f"watchdog: clock gap of {int(now_off - offset)} s")
                self.reason = "system went to sleep - connection dropped"
            elif self.armed and time.monotonic() - self._last > self._stall_after:
                self.reason = "no data sent for too long - connection stalled"
            # A manual clock change also moves the offset; resync so that a
            # backwards change cannot hide a later suspend.
            offset = now_off
            if self.reason:
                LOG.warn(f"watchdog: {self.reason}")
                try: self._on_abort()
                except Exception: pass
                return


def probe_path(path: str, timeout: float = 6.0) -> str:
    """
    Answer whether `path` is usable, WITHOUT the risk of blocking forever.

    Returns 'MOUNTED', 'ABSENT' or 'ZOMBIE'.

    On a dead SSHFS mount the drive letter still exists but every I/O - even
    os.path.exists() - enters an UNINTERRUPTIBLE kernel wait. A plain
    os.listdir() there never returns and never raises. So the test runs in a
    daemon thread and we simply stop waiting for it; we cannot kill it, and the
    thread being daemon means it will not hold the app open.

    Delegates to remote_mount when available so there is one implementation.
    """
    if EM is not None:
        try:
            return EM.probe(path, timeout=timeout)
        except Exception:
            pass

    result = {}

    def _worker():
        try:
            os.listdir(path)
            result['s'] = 'MOUNTED'
        except (FileNotFoundError, NotADirectoryError):
            result['s'] = 'ABSENT'
        except OSError:
            result['s'] = 'ZOMBIE'

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return 'ZOMBIE'
    return result.get('s', 'ABSENT')


def _network_keepalive_thread(dest_root: str, stop_event: threading.Event):
    """
    Daemon thread that pings the destination every 30 s so Windows / macOS do
    not tear down an idle network drive mid-transfer.
    dest_root can be a local path or an http(s) URL.

    The directory branch goes through probe_path rather than calling
    os.listdir() directly: on a zombie mount the bare call blocks forever, and
    this thread would sit wedged in the kernel for the rest of the session.
    """
    while not stop_event.wait(timeout=30):
        try:
            if dest_root.startswith("http://") or dest_root.startswith("https://"):
                _webdav_request(dest_root, "HEAD", timeout=8)
            else:
                probe_path(dest_root, timeout=8)
        except Exception:
            pass  # Keepalive errors are non-fatal


# =============================================================================
#  WEBDAV HELPERS
# =============================================================================

# ── Credentials ───────────────────────────────────────────────────────────────
# remote.auth in the profile decides how the one secret typed in the UI is used:
#   sharelink : as BOTH user name and password (e.g. ERDA share links)
#   basic     : as the password, with remote.username / FIELDCLIP_REMOTE_USER
#   none      : no Authorization header at all
# The secret itself lives only in the per-user secrets file; never here.
_WEBDAV_SECRET = ""


def set_webdav_credentials(secret: str):
    """Install the secret used for every subsequent WebDAV call."""
    global _WEBDAV_SECRET
    _WEBDAV_SECRET = (secret or "").strip()


def _webdav_auth_header() -> dict:
    """Basic auth header, or {} when no secret is set or auth is 'none'."""
    r = SETTINGS["remote"]
    if not _WEBDAV_SECRET or r["auth"] == "none":
        return {}
    if r["auth"] == "sharelink":
        user = _WEBDAV_SECRET
    else:
        user = os.environ.get(S.ENV_REMOTE_USER, "") or r.get("username", "")
    token = base64.b64encode(
        f"{user}:{_WEBDAV_SECRET}".encode()).decode()
    return {"Authorization": "Basic " + token}


# ── TLS ───────────────────────────────────────────────────────────────────────
# Some servers send only their leaf certificate, not the intermediate (ERDA
# omits "GEANT TLS RSA 1"). Windows quietly downloads the missing link (AIA
# fetching); OpenSSL on Linux and macOS does not, and every HTTPS call fails
# with CERTIFICATE_VERIFY_FAILED. remote.webdav.ca_bundle names a PEM file that
# is added to the system trust store - verification stays fully on, it just
# completes.
_CA_EXTRA = S.resolve_path(SETTINGS["remote"]["webdav"].get("ca_bundle", ""))
_SSL_CTX = None


def _ssl_context():
    global _SSL_CTX
    if _SSL_CTX is None:
        import ssl
        ctx = ssl.create_default_context()
        if _CA_EXTRA and os.path.isfile(_CA_EXTRA):
            try:
                ctx.load_verify_locations(cafile=_CA_EXTRA)
            except Exception as e:
                LOG.warn(f"could not load {_CA_EXTRA}: {e}")
        _SSL_CTX = ctx
    return _SSL_CTX


def _webdav_request(url: str, method: str, data=None, headers: dict = None,
                    timeout: int = 15):
    """
    One authenticated WebDAV request. Returns the response object.

    Every _webdav_* helper goes through here. They previously called
    urllib.request.urlopen() with no credentials at all, so against an
    authenticated server every single one came back 401.
    """
    h = _webdav_auth_header()
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())


def _webdav_test_connection(url: str, timeout: int = 10) -> tuple:
    """
    Test connectivity AND credentials against a WebDAV URL.
    Returns (ok: bool, message: str).
    """
    try:
        resp = _webdav_request(url, "HEAD", timeout=timeout)
        return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        # 401/403 used to be reported as success ("server reachable"), which is
        # how a share ID typo could sail through the test and only surface three
        # hours into an export.
        if e.code in (401, 403):
            return False, (f"HTTP {e.code} — authentication refused. "
                           f"Check the {SETTINGS['remote']['secret_label']}.")
        if e.code in (200, 201, 204, 207, 301, 302, 405):
            return True, f"HTTP {e.code}"
        return False, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, str(e)


def _webdav_folder_exists(url: str, timeout: int = 10) -> bool:
    """Return True if a WebDAV collection (folder) already exists."""
    try:
        resp = _webdav_request(url, "HEAD", timeout=timeout)
        return resp.status in (200, 204, 207)
    except urllib.error.HTTPError as e:
        return e.code in (200, 204, 207)
    except Exception:
        return False


def _webdav_mkcol(url: str, timeout: int = 15) -> tuple:
    """
    Create a WebDAV collection (folder) with MKCOL.
    Returns (ok: bool, message: str).
    Only creates the immediate collection; intermediate folders must already
    exist (call iteratively from root to leaf).
    """
    try:
        resp = _webdav_request(url, "MKCOL", timeout=timeout)
        return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        if e.code in (201, 204, 301, 405):
            # 405 = already exists on many servers — that's fine
            return True, f"HTTP {e.code}"
        return False, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, str(e)


def _webdav_ensure_collection(base_url: str, path_parts: list, timeout: int = 15):
    """
    Ensure base_url / part1 / part2 / ... exists, creating every missing level
    with MKCOL. Returns the final collection URL (with a trailing slash).

    Walks from the SERVER ROOT, not from base_url. MKCOL only creates one level
    at a time and returns 409 Conflict if the parent is missing, so assuming
    base_url already existed meant that pointing at a destination folder which
    had yet to be created failed the whole export with a bare
    "HTTP 409 from server" and no hint as to why.
    """
    parsed = urllib.parse.urlparse(base_url)
    root   = f"{parsed.scheme}://{parsed.netloc}"

    segments = [s for s in parsed.path.split("/") if s]
    segments += [urllib.parse.quote(p, safe="") for p in path_parts]

    url = root
    for seg in segments:
        url = url + "/" + seg
        if not _webdav_folder_exists(url + "/", timeout=timeout):
            ok, msg = _webdav_mkcol(url + "/", timeout=timeout)
            if not ok:
                LOG.warn(f"MKCOL {url}/ failed: {msg}")
    return url + "/"


def _webdav_file_remote_size(url: str, timeout: int = 10) -> int:
    """
    Return the remote file size in bytes via HEAD Content-Length.
    Returns -1 if the file does not exist or size cannot be determined.
    """
    try:
        resp = _webdav_request(url, "HEAD", timeout=timeout)
        cl = resp.headers.get("Content-Length")
        return int(cl) if cl else 0
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return -1
        cl = e.headers.get("Content-Length") if hasattr(e, "headers") else None
        return int(cl) if cl else 0
    except Exception:
        return -1


def _webdav_delete(url: str, timeout: int = 30) -> tuple:
    """Delete a remote file via WebDAV DELETE. Returns (ok, message).

    Used to clear an incomplete (partial) upload so it can be re-sent cleanly —
    ERDA does not honour Content-Range resume, so a partial file must be
    removed and uploaded again from scratch rather than appended to.
    """
    try:
        resp = _webdav_request(url, "DELETE", timeout=timeout)
        return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        # 204/200/404 all mean "it is gone now"
        if e.code in (200, 202, 204, 404):
            return True, f"HTTP {e.code}"
        return False, f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return False, str(e)


# urllib.parse is not imported yet at module level — add it
import urllib.parse

# =============================================================================
#  FFMPEG
# =============================================================================

def _prepare_unix_binary(path: str):
    """Make a bundled binary runnable on macOS / Linux.

    Two things commonly stop a shipped binary from running on macOS:
      • it loses its executable bit when the folder is copied / unzipped;
      • Gatekeeper tags downloaded files with com.apple.quarantine and refuses
        to launch them ("cannot be opened because the developer cannot be
        verified"), which is exactly what made faststart / re-encode fail.
    Both are fixed here, best-effort.
    """
    try: os.chmod(path, 0o755)
    except Exception: pass
    if platform.system() == "Darwin":
        try:
            subprocess.run(["xattr", "-d", "com.apple.quarantine", path],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=5)
        except Exception: pass

def find_bundled_ffmpeg() -> str:
    """ffmpeg to use: FIELDCLIP_FFMPEG / tools.ffmpeg, then a copy dropped in
    bin/ffmpeg/<os>/ (or ffmpeg/<os>/ beside this file, the layout of older
    installations), then the PATH. "" when there is none."""
    system = platform.system()
    explicit = os.environ.get(S.ENV_FFMPEG) or SETTINGS["tools"].get("ffmpeg", "")
    candidates = [S.resolve_path(explicit)] if explicit else []
    for base in (os.path.join(S.ROOT_DIR, "bin"), os.path.dirname(os.path.abspath(__file__))):
        if system == "Windows":
            candidates.append(os.path.join(base, "ffmpeg", "windows", "bin", "ffmpeg.exe"))
        elif system == "Darwin":
            # Prefer the native architecture, but fall back to the other build:
            # Apple Silicon runs x86_64 binaries transparently via Rosetta 2.
            order = ["mac_arm", "mac_x86"] if platform.machine() == "arm64" \
                    else ["mac_x86", "mac_arm"]
            for sub in order:
                candidates.append(os.path.join(base, "ffmpeg", sub, "ffmpeg"))
        else:
            candidates.append(os.path.join(base, "ffmpeg", "linux", "ffmpeg"))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            if system != "Windows":
                _prepare_unix_binary(candidate)
            return candidate
    for cmd in ("ffmpeg", "ffmpeg.exe"):
        try:
            if subprocess.run([cmd, "-version"], capture_output=True, timeout=5).returncode == 0:
                return cmd
        except: pass
    return ""

def _find_ffprobe(ffmpeg: str) -> str:
    """Return a usable ffprobe path, or "" when none is bundled / on PATH.

    The macOS bundle ships only ffmpeg (no ffprobe), so callers must be able to
    fall back to parsing ffmpeg's own output when this returns "".
    """
    d = os.path.dirname(ffmpeg)
    for name in ("ffprobe.exe", "ffprobe"):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            if platform.system() != "Windows":
                _prepare_unix_binary(p)
            return p
    # Last resort: a system-wide ffprobe on PATH (if any).
    for name in ("ffprobe", "ffprobe.exe"):
        try:
            if subprocess.run([name, "-version"],
                              capture_output=True, timeout=5).returncode == 0:
                return name
        except Exception: pass
    return ""

def _ffmpeg_probe_text(ffmpeg: str, path: str, timeout: int = 15) -> str:
    """Run `ffmpeg -i <path>` and return its stderr (where stream info goes)."""
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", path],
                           capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
        return r.stderr.decode(errors='replace')
    except Exception:
        return ""

def is_unplayable_mp4(ffmpeg: str, path: str) -> bool:
    """True if an MP4/MOV has no readable moov atom.

    These are incomplete / interrupted recordings (e.g. the drone lost power
    mid-capture): the media data is present but the index was never written.
    ffmpeg cannot open them, faststart cannot repair them, and they will not
    play or stream anywhere — they need dedicated recovery (e.g. untrunc).
    """
    if not ffmpeg:
        return False
    low = _ffmpeg_probe_text(ffmpeg, path, timeout=20).lower()
    return ("moov atom not found" in low
            or "invalid data found when processing input" in low)

def _probe_duration(ffmpeg: str, path: str) -> float:
    probe = _find_ffprobe(ffmpeg)
    if probe:
        try:
            r = subprocess.run(
                [probe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=duration", "-of", "csv=p=0", path],
                capture_output=True, stdin=subprocess.DEVNULL, timeout=15)
            val = r.stdout.decode(errors='replace').strip()
            if val and val.upper() != "N/A":
                return float(val)
        except: pass
    # Fallback (e.g. macOS, no bundled ffprobe): parse ffmpeg's "Duration:" line
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
                  _ffmpeg_probe_text(ffmpeg, path))
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return 0.0

def _detect_codec_via_ffmpeg(ffmpeg: str, path: str) -> dict:
    """Codec detection without ffprobe — parse the ffmpeg "Video:" stream line.

    Example line:
        Stream #0:0(eng): Video: h264 (Main 10) (avc1 / 0x31637661), yuv420p10le, ...
    """
    txt = _ffmpeg_probe_text(ffmpeg, path)
    m = re.search(r"Video:\s*([A-Za-z0-9_]+).*", txt)
    if not m:
        return {}
    line = m.group(0)
    codec_name = m.group(1).strip()
    tag = re.search(r"\(([A-Za-z0-9]{4})\s*/\s*0x", line)
    pix = re.search(r"(yuv[0-9a-z]+|gbr[0-9a-z]+|nv\d{2}|rgb[0-9a-z]*|gray[0-9a-z]*|p010[0-9a-z]*)", line)
    return {"codec_name": codec_name,
            "codec_tag":  tag.group(1).strip() if tag else "",
            "pix_fmt":    pix.group(1).strip() if pix else ""}

def detect_codec(ffmpeg: str, path: str) -> dict:
    probe = _find_ffprobe(ffmpeg)
    if probe:
        try:
            r = subprocess.run(
                [probe, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name,codec_tag_string,pix_fmt",
                 "-of", "csv=p=0", path],
                capture_output=True, stdin=subprocess.DEVNULL, timeout=15)
            if r.returncode == 0:
                parts = r.stdout.decode(errors='replace').strip().split(',')
                if len(parts) >= 3 and parts[0].strip():
                    return {"codec_name": parts[0].strip(),
                            "codec_tag":  parts[1].strip(),
                            "pix_fmt":    parts[2].strip()}
        except: pass
    # Fallback (e.g. macOS, no bundled ffprobe): parse ffmpeg's own output
    return _detect_codec_via_ffmpeg(ffmpeg, path)

def detect_codec_threaded(ffmpeg: str, path: str, timeout: int = 15) -> dict:
    result = {}
    def _run(): result.update(detect_codec(ffmpeg, path))
    t = threading.Thread(target=_run, daemon=True)
    t.start(); t.join(timeout=timeout)
    return result

def is_broken_h264_10bit(codec_info: dict) -> bool:
    if not codec_info:
        return False
    return (codec_info.get("codec_name") == "h264"
            and "10" in codec_info.get("pix_fmt", "")
            and codec_info.get("codec_tag") == "avc1")


def needs_reencode(codec_info: dict) -> bool:
    """encoding.reencode.when: broken_h264_10bit (DJI crash-recovery files that
    browsers cannot play), always, or never."""
    when = SETTINGS["encoding"]["reencode"]["when"]
    if when == "always":
        return True
    if when == "never":
        return False
    return is_broken_h264_10bit(codec_info)


def _reencode_video_args() -> tuple:
    """ffmpeg video options for a re-encode, from encoding.reencode."""
    r = SETTINGS["encoding"]["reencode"]
    args = ["-c:v", r["codec"]]
    if r.get("preset"):
        args += ["-preset", str(r["preset"])]
    if r.get("crf") is not None:
        args += ["-crf", str(r["crf"])]
    elif r.get("bitrate"):
        args += ["-b:v", str(r["bitrate"])]
    if r.get("pix_fmt"):
        args += ["-pix_fmt", r["pix_fmt"]]
    if r.get("tag"):
        args += ["-tag:v", r["tag"]]
    return tuple(args + [str(a) for a in r.get("extra_args") or []])

def _keep_meta_args(src: str, video_codec=("-c:v", "copy")) -> list:
    """ffmpeg output options that keep what a bare remux loses: the SRT-like
    mov_text track DJI writes, the global tags, and creation_time - the only
    clock a clip without SRT has, and the flight-log anchor (step 3). Without
    them ffmpeg keeps one video track and writes a zero mvhd time. The DJI
    'priv' data track cannot be copied into MP4 and is dropped."""
    args = ["-map", "0:v", "-map", "0:a?", "-map", "0:s?",
            *video_codec, "-c:a", "copy", "-c:s", "copy", "-map_metadata", "0"]
    created = HFL.mp4_meta(src)[3] if HFL else None
    if created and created.year >= 2000:
        args += ["-metadata", f"creation_time={created:%Y-%m-%dT%H:%M:%S}Z"]
    return args

def run_faststart_local(ffmpeg: str, src: str, local_tmp_dir: str) -> tuple:
    tmp = os.path.join(local_tmp_dir, "tmp_" + os.path.basename(src))
    # Faststart remuxes with -c copy: it reads and rewrites the whole file, so
    # the time needed scales with size (and is much slower on network drives).
    # Allow ~1 s per 4 MB, minimum 10 min, so multi-GB files aren't cut off.
    try:
        size_mb = os.path.getsize(src) / (1024 * 1024)
    except OSError:
        size_mb = 0
    timeout_s = max(int(SETTINGS["encoding"]["faststart_min_timeout_s"]),
                    int(size_mb / 4) + 180)
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", src, *_keep_meta_args(src), "-movflags", "+faststart", tmp],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout_s)
        if r.returncode == 0: return tmp, "ok"
        err = r.stderr.decode(errors='replace').strip().splitlines()
        try: os.remove(tmp)
        except: pass
        return None, (" | ".join(err[-3:]) if err else "unknown ffmpeg error")
    except subprocess.TimeoutExpired:
        try: os.remove(tmp)
        except: pass
        return None, f"timeout (>{timeout_s // 60} min)"
    except Exception as e:
        try: os.remove(tmp)
        except: pass
        return None, str(e)

def run_reencode_hevc(ffmpeg: str, src: str, dst: str, progress_cb=None) -> tuple:
    duration_s = _probe_duration(ffmpeg, src)
    try:
        proc = subprocess.Popen(
            [ffmpeg, "-y", "-i", src,
             *_keep_meta_args(src, _reencode_video_args()),
             "-movflags", "+faststart",
             "-progress", "pipe:2", dst],
            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        fps_val = time_done = 0.0
        for raw in proc.stderr:
            line = raw.decode(errors='replace').strip()
            if line.startswith("out_time_ms="):
                try: time_done = int(line.split("=")[1]) / 1_000_000
                except: pass
            elif line.startswith("fps="):
                try: fps_val = float(line.split("=")[1])
                except: pass
            elif line.startswith("progress=") and progress_cb:
                pct = min((time_done / duration_s) if duration_s > 0 else 0.0, 1.0)
                remaining = ((duration_s - time_done) / fps_val) if fps_val > 0 else 0
                progress_cb(pct, fps_val, str(timedelta(seconds=int(remaining))))
        limit = int(SETTINGS["encoding"]["reencode"]["timeout_s"])
        proc.wait(timeout=limit)
        return (True, "ok") if proc.returncode == 0 else (False, f"exit {proc.returncode}")
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except: pass
        return False, f"timeout (>{limit // 60} min)"
    except Exception as e:
        return False, str(e)

def write_exif_date(ffmpeg: str, src: str, new_dt: datetime) -> tuple:
    """Write creation_time metadata to MP4 using ffmpeg (lossless copy)."""
    tmp = src + ".exif_tmp" + Path(src).suffix
    dt_str = new_dt.strftime('%Y-%m-%dT%H:%M:%S')
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-i", src,
             *_keep_meta_args(src),
             "-metadata", f"creation_time={dt_str}",    # later -metadata wins
             "-movflags", "+faststart",
             tmp],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=300)
        if r.returncode == 0:
            os.replace(tmp, src)
            return True, "ok"
        try: os.remove(tmp)
        except: pass
        err = r.stderr.decode(errors='replace').strip().splitlines()
        return False, (err[-1] if err else "ffmpeg error")
    except Exception as e:
        try: os.remove(tmp)
        except: pass
        return False, str(e)

# =============================================================================
#  MP4 STRUCTURE DETECTION
# =============================================================================

def _read_box_size(f, size32: int):
    """Resolve an MP4 box size, handling the 64-bit and to-EOF encodings.

    A 32-bit size of 1 means the real (64-bit) size follows in the next 8 bytes
    (used by DJI and any file with boxes/files >4 GB); a size of 0 means the box
    runs to the end of the file. Returns the resolved size, or None if it can't
    be determined (caller should stop scanning).
    """
    if size32 == 1:
        ext = f.read(8)
        if len(ext) < 8:
            return None
        return struct.unpack('>Q', ext)[0]
    if size32 == 0:
        return None        # extends to EOF — no further boxes follow
    return size32

def check_moov_position(path: str) -> str:
    try:
        moov_pos = mdat_pos = None
        with open(path, 'rb') as f:
            while True:
                hdr = f.read(8)
                if len(hdr) < 8: break
                size32 = struct.unpack('>I', hdr[:4])[0]
                box    = hdr[4:8].decode('latin-1', errors='replace')
                off    = f.tell() - 8
                if box == 'moov' and moov_pos is None: moov_pos = off
                if box == 'mdat' and mdat_pos is None: mdat_pos = off
                if moov_pos is not None and mdat_pos is not None: break
                size = _read_box_size(f, size32)
                if size is None or size < 8: break
                f.seek(off + size)
        if moov_pos is None or mdat_pos is None: return 'unknown'
        return 'front' if moov_pos < mdat_pos else 'end'
    except: return 'unknown'

def is_fragmented_mp4(path: str) -> bool:
    try:
        with open(path, 'rb') as f:
            while True:
                hdr = f.read(8)
                if len(hdr) < 8: break
                size32 = struct.unpack('>I', hdr[:4])[0]
                box    = hdr[4:8].decode('latin-1', errors='replace')
                off    = f.tell() - 8
                if box == 'moof': return True
                size = _read_box_size(f, size32)
                if size is None or size < 8: break
                f.seek(off + size)
    except: pass
    return False

def _detect_mp4_type(path: str, timeout: int = 10) -> tuple:
    result = {'pos': 'unknown', 'frag': False}
    def _run():
        result['pos']  = check_moov_position(path)
        result['frag'] = is_fragmented_mp4(path)
    t = threading.Thread(target=_run, daemon=True)
    t.start(); t.join(timeout=timeout)
    if t.is_alive(): return 'unknown', False
    return result['pos'], result['frag']

# =============================================================================
#  FILE UTILITIES
# =============================================================================

def is_hidden(path: str) -> bool:
    if os.path.basename(path).startswith('.'): return True
    if platform.system() == "Windows":
        try:
            import ctypes
            a = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            if a != -1 and (a & 2): return True
        except: pass
    return False

sanitize = NM.sanitize

def guess_from_filename(filename: str, all_devices: list) -> dict:
    """
    Parse every answer back from a name written by build_new_basename(), using
    the profile's naming.template / training_template (see naming.py).
    Raw camera names (no date/time anchor) fall back to device detection.
    """
    result = NAMING.parse(filename, all_devices)
    if not result.pop('matched'):
        result['device'] = detect_device(filename, all_devices)
        if result['device'] != 'Unknown':
            result['guessed'].add('device')
    return result

def detect_device(filename: str, all_devices: list = None) -> str:
    stem    = Path(filename).stem
    name_up = stem.upper()
    known   = list(BUILTIN_DEVICES) + [d for d in (all_devices or []) if d not in BUILTIN_DEVICES]
    for dev in known:
        if stem.startswith(dev + "_") or stem == dev: return dev
    if name_up.startswith("DJI_"):                      return DJI_FALLBACK
    if PHONE_PREFIX_RE.match(name_up):                  return PHONE_DEVICE
    for dev in known:
        if dev.lower() in stem.lower(): return dev
    return "Unknown"

def dji_family(filename: str) -> str:
    """Drone family told by a raw DJI filename - a hint, never a model:
    DJI_NNNN is the Mini 3 series (Mini3 or Mini3Pro), DJI_<14 digits>_NNNN_X
    the Mini 4 series."""
    name_up = Path(filename).stem.upper()
    if re.match(r'^DJI_\d{14}_\d+_[A-Z]$', name_up): return "Mini 4 series"
    if re.match(r'^DJI_\d{3,5}$', name_up):            return "Mini 3 series"
    if name_up.startswith("DJI_MINI_4_"):              return "Mini 4 series"
    if name_up.startswith("DJI_MINI_3_"):              return "Mini 3 series"
    return ""

def detect_device_from_path(filepath: str) -> str:
    """
    Drone model read from the folder names, e.g. '…/Rey_moessoe_mini3pro/x.MP4'
    → 'Mini3Pro'. The deepest folder wins: a session folder is more specific
    than the drive root it sits on. Returns '' when nothing matches.
    """
    parts = str(filepath).replace('\\', '/').split('/')[:-1]   # drop the filename
    for part in reversed(parts):
        low = part.lower()
        for token, pat in DEVICE_PATH_PATTERNS:
            if pat.search(low):
                return token
    return ""

# Written next to the clips the first time a flight log names the drone, so the
# model survives into later runs made after the logs have been archived away.
DEVICE_MEMO_FILE = SETTINGS["files"]["device_memo"]
LEGACY_DEVICE_MEMOS = list(SETTINGS["files"].get("legacy_device_memos") or [])

def read_device_memo(folder: str) -> str:
    for name in [DEVICE_MEMO_FILE] + LEGACY_DEVICE_MEMOS:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                dev = (json.load(f) or {}).get("device", "")
        except Exception:
            continue
        if dev and dev not in GENERIC_DEVICES:
            return dev
    return ""

def write_device_memo(folder: str, device: str, source: str = "flightlog") -> bool:
    if not device or device in GENERIC_DEVICES:
        return False
    try:
        with open(os.path.join(folder, DEVICE_MEMO_FILE), "w", encoding="utf-8") as f:
            json.dump({"device": device, "source": source,
                       "written": datetime.now().isoformat(timespec="seconds")},
                      f, indent=2)
        return True
    except Exception:
        return False

def detect_device_from_siblings(folder: str, all_devices: list) -> str:
    """Model of the files in this folder that already carry a FieldClip name."""
    counts = {}
    try:
        names = os.listdir(folder)
    except Exception:
        return ""
    for f in names:
        if Path(f).suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        dev = guess_from_filename(f, all_devices).get('device', 'Unknown')
        if dev not in GENERIC_DEVICES:
            counts[dev] = counts.get(dev, 0) + 1
    return max(counts, key=counts.get) if counts else ""

TRAINING_KEYWORDS = [k.lower() for k in SETTINGS["training"]["path_keywords"]]
TRAINING_MAX_DURATION_S = float(SETTINGS["training"]["auto_max_duration_s"] or 0)

def _path_says_training(path: str) -> bool:
    low = str(path).lower()
    return any(k in low for k in TRAINING_KEYWORDS)

def guess_from_path(filepath: str, all_devices: list) -> dict:
    """Guess metadata from path (folder names) + filename."""
    filename = os.path.basename(filepath)
    r = guess_from_filename(filename, all_devices)
    ps = str(filepath).replace('\\', '/')

    # Drone model. The raw DJI filename only tells the family apart (DJI_NNNN =
    # Mini 3 series, DJI_<14 digits>_NNNN_X = Mini 4 series); Mini3 vs Mini3Pro
    # needs something more. In order of authority: what a flight log established
    # here on an earlier run, the folder name, then the files already renamed
    # beside this one.
    if r['device'] in GENERIC_DEVICES:
        folder = os.path.dirname(filepath)
        for dev, tag in ((read_device_memo(folder),                      'device_from_memo'),
                         (detect_device_from_path(filepath),             'device_from_path'),
                         (detect_device_from_siblings(folder, all_devices),
                                                                         'device_from_siblings')):
            if dev:
                r['device'] = dev
                r['guessed'].add('device')
                r['guessed'].add(tag)
                break

    # Fill missing from path components
    # Normalize hyphens/underscores so "North-Meadow" matches a folder named "North_Meadow"
    if not r['site']:
        for site in sorted(SITES, key=len, reverse=True):
            site_norm = site.lower().replace('-', '_')
            san_norm  = sanitize(site).lower().replace('-', '_')
            for part in ps.lower().split('/'):
                part_norm = part.replace('-', '_')
                if site_norm in part_norm or san_norm in part_norm:
                    r['site'] = site; r['guessed'].add('site'); break
            if r['site']: break

    if not r['group']:
        for grp in sorted(GROUP_STRUCTURES, key=len, reverse=True):
            if grp.lower() in ps.lower():
                r['group'] = grp; r['guessed'].add('group'); break

    if not r['is_training'] and _path_says_training(ps):
        r['is_training'] = True; r['guessed'].add('is_training')

    return r

def extract_timestamp(filepath: str) -> str:
    stem = Path(filepath).stem
    m = re.search(r'(\d{14})', stem)
    if m:
        try: return datetime.strptime(m.group(1),'%Y%m%d%H%M%S').strftime('%Y-%m-%d_%H-%M-%S')
        except: pass
    m2 = re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})', stem)
    if m2: return m2.group(1)
    try:    return datetime.fromtimestamp(os.path.getmtime(filepath)).strftime('%Y-%m-%d_%H-%M-%S')
    except: return datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

def extract_timestamp_from_name(filename: str):
    stem = Path(filename).stem
    m = re.search(r'(\d{14})', stem)
    if m:
        try: return datetime.strptime(m.group(1), '%Y%m%d%H%M%S')
        except: pass
    m2 = re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})', stem)
    if m2:
        try: return datetime.strptime(m2.group(1), '%Y-%m-%d_%H-%M-%S')
        except: pass
    return None

def get_file_dates(filepath: str) -> dict:
    try:
        st = os.stat(filepath)
        modified = datetime.fromtimestamp(st.st_mtime)
        if platform.system() == "Windows":
            created = datetime.fromtimestamp(st.st_ctime)
        elif hasattr(st, 'st_birthtime'):
            created = datetime.fromtimestamp(st.st_birthtime)
        else:
            # Linux: st_ctime is the last inode change (chmod, rename, copy),
            # not the creation - showing it as "Created" only misleads.
            created = None
        return {'created': created, 'modified': modified}
    except: return {'created': None, 'modified': None}

def mp4_local_info(path: str) -> dict:
    """Duration and recording start read from the MP4 header (mvhd) - what a
    clip without SRT still knows about itself. The header time is UTC by the
    spec (DJI and phones follow it); it is turned into this machine's local
    time, the clock the SRT, the filenames and the flight logs use. A file
    rewritten by an ffmpeg that dropped the metadata reads back 0 → None."""
    out = {'duration_s': None, 'start_dt': None}
    if not HFL or Path(path).suffix.lower() not in ('.mp4', '.mov'):
        return out
    dur, _fps, _n, created = HFL.mp4_meta(path)
    out['duration_s'] = dur
    if created and created.year >= 2000:
        import calendar
        out['start_dt'] = datetime.fromtimestamp(calendar.timegm(created.timetuple()))
    return out

def get_file_size_mb(path: str) -> float:
    try:    return os.path.getsize(path) / (1024 * 1024)
    except: return 0.0

def parse_srt_companion(video_path: str) -> dict:
    """Parse .SRT companion file. Returns start/end datetimes, duration, color_md."""
    result = {'start_dt': None, 'end_dt': None, 'duration_s': None,
               'srt_path': None, 'color_md': None, 'raw_content': None}
    p = Path(video_path)
    video_stem = p.stem

    # 1. Exact match
    for ext in ('.SRT', '.srt'):
        candidate = p.with_suffix(ext)
        if candidate.exists():
            result['srt_path'] = str(candidate)
            break

    # 2/3/4. Fuzzy match: prefix or shared DJI base stem
    if not result['srt_path']:
        video_base = _dji_base_stem(video_stem)
        try:
            for f in p.parent.iterdir():
                if f.suffix.lower() not in ('.srt',): continue
                cs = f.stem
                cb = _dji_base_stem(cs)
                matched = False
                if (len(cs) >= 8
                        and video_stem.startswith(cs)
                        and len(video_stem) > len(cs)
                        and video_stem[len(cs)] == '_'):
                    matched = True
                elif (len(video_stem) >= 8
                      and cs.startswith(video_stem)
                      and len(cs) > len(video_stem)
                      and cs[len(video_stem)] == '_'):
                    matched = True
                elif video_base != video_stem and cb == video_base:
                    matched = True
                if matched:
                    result['srt_path'] = str(f)
                    break
        except Exception:
            pass

    if not result['srt_path']:
        return result

    dt_pattern = re.compile(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})')
    tc_pattern  = re.compile(r'(\d{2}:\d{2}:\d{2}),\d+ --> (\d{2}:\d{2}:\d{2}),\d+')
    cmd_pattern = re.compile(r'\[color_md:\s*([^\]]+)\]')

    try:
        with open(result['srt_path'], 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
        result['raw_content'] = content

        datetimes = []
        for m in dt_pattern.finditer(content):
            try: datetimes.append(datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S'))
            except: pass

        last_end_tc = None
        for m in tc_pattern.finditer(content): last_end_tc = m.group(2)

        cm = cmd_pattern.search(content)
        if cm: result['color_md'] = cm.group(1).strip().lower()

        if datetimes:
            result['start_dt']   = datetimes[0]
            result['end_dt']     = datetimes[-1]
            result['duration_s'] = (datetimes[-1] - datetimes[0]).total_seconds()
        if result['duration_s'] is None and last_end_tc:
            h, m2, s = last_end_tc.split(':')
            result['duration_s'] = int(h)*3600 + int(m2)*60 + int(s)
    except: pass

    return result

def rewrite_srt_with_new_start(srt_content: str, new_start: datetime) -> str:
    """
    Rewrite all absolute timestamps in a DJI SRT file.

    Strategy: use the cumulative DiffTime (ms) from each frame header to compute
    the exact datetime of every frame relative to new_start.
    This is more accurate than a global offset because it accounts for
    variable frame intervals.

    If DiffTime is not found for a frame, falls back to the SRT timecode offset.
    """
    # Pattern to find each subtitle block:
    # Group 1: block number
    # Group 2: timecode line (used as fallback offset)
    # Group 3: FrameCnt + DiffTime line
    # Group 4: absolute datetime line (to replace)
    block_pattern = re.compile(
        r'(\d+)\r?\n'                                           # subtitle number
        r'(\d{2}:\d{2}:\d{2},\d+) --> (\d{2}:\d{2}:\d{2},\d+)\r?\n'  # timecode
        r'<font[^>]*>FrameCnt:\s*(\d+),\s*DiffTime:\s*(\d+)ms\r?\n'   # FrameCnt/DiffTime
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)\r?\n',          # datetime to replace
        re.MULTILINE)

    cumulative_ms = 0
    last_frame    = 0

    def _replace_block(m):
        nonlocal cumulative_ms, last_frame
        frame_cnt  = int(m.group(4))
        diff_ms    = int(m.group(5))

        # Accumulate ms since the first frame
        if frame_cnt <= last_frame:
            cumulative_ms = 0  # reset on new sequence
        cumulative_ms += diff_ms
        last_frame = frame_cnt

        # First frame: use exactly new_start (its cumulative_ms = diff_ms of frame 1)
        # For frame 1: offset = 0 (it IS the start)
        offset_ms = cumulative_ms - diff_ms if frame_cnt == 1 else cumulative_ms - diff_ms
        frame_dt  = new_start + timedelta(milliseconds=cumulative_ms - diff_ms)

        # Preserve original microsecond-level sub-second part format
        orig_subsec = m.group(6).split('.')[1] if '.' in m.group(6) else '000'
        new_dt_str  = frame_dt.strftime('%Y-%m-%d %H:%M:%S') + '.' + orig_subsec

        return (f"{m.group(1)}\n"
                f"{m.group(2)} --> {m.group(3)}\n"
                f"<font size=\"28\">FrameCnt: {m.group(4)}, DiffTime: {m.group(5)}ms\n"
                f"{new_dt_str}\n")

    result = block_pattern.sub(_replace_block, srt_content)

    # Fallback: if no DiffTime blocks found, use simple timecode offset
    if result == srt_content:
        dt_pattern = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(\.\d+)?')
        tc_pattern = re.compile(r'(\d{2}):(\d{2}):(\d{2}),(\d+) -->')

        # Get the first timecode offset to anchor new_start
        first_tc = tc_pattern.search(srt_content)
        if first_tc:
            anchor_ms = (int(first_tc.group(1)) * 3600
                         + int(first_tc.group(2)) * 60
                         + int(first_tc.group(3))) * 1000 + int(first_tc.group(4))
        else:
            anchor_ms = 0

        def _replace_simple(m):
            # Find the timecode before this datetime
            pos    = m.start()
            before = srt_content[:pos]
            tcs    = tc_pattern.findall(before)
            if tcs:
                h, mi, s, ms = tcs[-1]
                tc_ms = (int(h)*3600 + int(mi)*60 + int(s))*1000 + int(ms)
            else:
                tc_ms = 0
            frame_dt = new_start + timedelta(milliseconds=max(0, tc_ms - anchor_ms))
            suffix   = m.group(2) if m.group(2) else ''
            return frame_dt.strftime('%Y-%m-%d %H:%M:%S') + suffix

        result = dt_pattern.sub(_replace_simple, srt_content)

    return result

def apply_date_correction(video_path: str, new_dt: datetime,
                           old_dt: datetime, ffmpeg: str,
                           srt_copy_dir: str = None) -> dict:
    """
    Apply date correction to a video file:
    - os.utime() on video + original SRT
    - write EXIF creation_time via ffmpeg
    - rewrite SRT dates and save copy to srt_copy_dir (or same folder if None)
    Returns {'utime': bool, 'exif': bool, 'srt_copy': str|None, 'errors': list}
    """
    result = {'utime': False, 'exif': False, 'srt_copy': None, 'errors': []}
    ts = new_dt.timestamp()

    # os.utime on video
    try:
        os.utime(video_path, (ts, ts))
        result['utime'] = True
    except Exception as e:
        result['errors'].append(f"utime video: {e}")

    # EXIF via ffmpeg
    if ffmpeg:
        ok, msg = write_exif_date(ffmpeg, video_path, new_dt)
        result['exif'] = ok
        if not ok: result['errors'].append(f"exif: {msg}")

    # SRT companion
    p = Path(video_path)
    for ext in ('.SRT', '.srt'):
        srt_orig = p.with_suffix(ext)
        if srt_orig.exists():
            # utime on original SRT
            try: os.utime(str(srt_orig), (ts, ts))
            except Exception as e: result['errors'].append(f"utime srt: {e}")

            # Read + rewrite dates
            try:
                with open(str(srt_orig), 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                new_content = rewrite_srt_with_new_start(content, new_dt)

                # Save copy
                copy_dir = srt_copy_dir or str(srt_orig.parent)
                srt_copy_path = os.path.join(copy_dir, srt_orig.name)
                with open(srt_copy_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                result['srt_copy'] = srt_copy_path
            except Exception as e:
                result['errors'].append(f"srt rewrite: {e}")
            break

    return result

def split_companion_name(path) -> tuple:
    """
    Split a companion filename into (stem, suffix), honouring compound suffixes.
      'DJI_0083.flightlog.csv' → ('DJI_0083', '.flightlog.csv')
      'DJI_0083.SRT'           → ('DJI_0083', '.SRT')
    """
    name = os.path.basename(str(path))
    low  = name.lower()
    for suf in COMPANION_COMPOUND_SUFFIXES:
        if low.endswith(suf) and len(name) > len(suf):
            return name[:-len(suf)], name[-len(suf):]
    p = Path(name)
    return p.stem, p.suffix


# A sidecar still under the name the camera gave the clip, i.e. one that never
# followed its video through a rename.
_RAW_DJI_STEM = re.compile(r'^DJI_(\d{3,5}|\d{14}_\d{4}_[A-Z])$', re.I)
_sidecar_utc_cache = {}

def sidecar_start_utc(path: str):
    """
    UTC wall clock of video_t_s == 0, read from the sidecar's own rows.

    This is what lets a `.flightlog.csv` be re-attached to its clip after the
    clip has been renamed: the sidecar carries the recording's absolute start
    time inside it, so the link survives losing the shared filename.
    """
    key = (path, os.path.getmtime(path)) if os.path.exists(path) else (path, 0)
    if key in _sidecar_utc_cache:
        return _sidecar_utc_cache[key]
    best = None
    try:
        with open(path, encoding='utf-8', newline='') as fh:
            for row in csv.DictReader(fh):
                try:    t = float(row.get('video_t_s'))
                except (TypeError, ValueError): continue
                if best is None or abs(t) < abs(best[0]):
                    best = (t, row.get('utc') or '')
                if t > 1.0:          # rows are ordered, we are past t = 0
                    break
    except Exception:
        best = None
    out = None
    if best and best[1]:
        try:
            out = (datetime.strptime(best[1][:19], '%Y-%m-%dT%H:%M:%S')
                   - timedelta(seconds=best[0]))
        except ValueError:
            out = None
    _sidecar_utc_cache[key] = out
    return out


def map_stale_sidecars(folder: str) -> dict:
    """
    Pair `.flightlog.csv` sidecars still carrying their raw DJI name with the
    videos of the folder, which an earlier run may already have renamed.

    The sidecar knows its start in UTC, the renamed video carries local time, so
    the folder's UTC offset is voted on across every candidate pair and each
    sidecar is then matched within 5 s. Returns {sidecar_path: video_stem}.
    """
    try:
        names = os.listdir(folder)
    except Exception:
        return {}

    stale = {}
    for f in names:
        cs, csuf = split_companion_name(f)
        if csuf.lower() != '.flightlog.csv' or not _RAW_DJI_STEM.match(cs):
            continue
        u = sidecar_start_utc(os.path.join(folder, f))
        if u is not None:
            stale[f] = u
    if not stale:
        return {}

    # Videos that already carry a FieldClip timestamp, keyed by local start.
    by_time = {}
    for f in names:
        if Path(f).suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        dt = extract_timestamp_from_name(f)
        if dt is not None:
            by_time[dt] = Path(f).stem
    if not by_time:
        return {}

    votes = {}
    for u in stale.values():
        for loc in by_time:
            h = round((loc - u).total_seconds() / 3600.0)
            votes[h] = votes.get(h, 0) + 1
    offset_h = max(votes, key=votes.get)

    out = {}
    for f, u in stale.items():
        target = u + timedelta(hours=offset_h)
        hit = min(by_time, key=lambda d: abs((d - target).total_seconds()))
        if abs((hit - target).total_seconds()) <= 5:
            out[os.path.join(folder, f)] = by_time[hit]
    return out


def _dji_base_stem(stem: str) -> str:
    """
    Extract the original DJI base stem from a (possibly renamed) filename.
    e.g. 'DJI_20260331134254_0158_D_custom_name' → 'DJI_20260331134254_0158_D'
         'Mini3_2026-04-11_09-32-46_Herd-A_Lake' → kept as-is (no DJI raw pattern)
    DJI raw pattern: DJI_YYYYMMDDHHMMSS_NNNN_X  (14-digit timestamp + 4-digit seq + letter)
    """
    m = re.match(r'^(DJI_\d{14}_\d{4}_[A-Z])', stem)
    return m.group(1) if m else stem


def find_companion_files(video_path: str) -> list:
    """
    Return non-video companion files for a video.
    Matching strategies (in order):
      1. Exact stem match
      2. Companion stem is a prefix of video stem (separated by '_')
      3. Both share the same DJI raw base stem (DJI_YYYYMMDDHHMMSS_NNNN_X)
      4. Telemetry sidecar left under its raw DJI name, matched on the start
         time it records for itself
    """
    p = Path(video_path)
    video_stem = p.stem
    video_base = _dji_base_stem(video_stem)
    result  = []
    seen    = set()
    pending = []   # raw-named sidecars, resolved after the walk (rule 5)
    try:
        for f in p.parent.iterdir():
            if f.suffix.lower() in VIDEO_EXTENSIONS: continue
            if is_hidden(str(f)): continue
            cs, csuf = split_companion_name(f)
            cb   = _dji_base_stem(cs)
            path = str(f)
            if path in seen: continue
            matched = False
            # 1. Exact
            if cs == video_stem:
                matched = True
            # 2. Companion is prefix of video (companion was not renamed)
            elif (len(cs) >= 8
                  and video_stem.startswith(cs)
                  and len(video_stem) > len(cs)
                  and video_stem[len(cs)] == '_'):
                matched = True
            # 3. Video is prefix of companion (video was renamed, companion not)
            elif (len(video_stem) >= 8
                  and cs.startswith(video_stem)
                  and len(cs) > len(video_stem)
                  and cs[len(video_stem)] == '_'):
                matched = True
            # 4. Shared DJI base stem
            elif (video_base != video_stem   # only if there IS a DJI base
                  and cb == video_base):
                matched = True
            # 5. Telemetry sidecar that never followed its clip through an
            #    earlier rename. Deferred: resolving it means reading the
            #    sidecars, and in a healthy folder there are none to read.
            elif csuf.lower() == '.flightlog.csv' and _RAW_DJI_STEM.match(cs):
                pending.append(path)
            if matched:
                result.append(path)
                seen.add(path)
        if pending:
            stale = map_stale_sidecars(str(p.parent))
            for path in pending:
                if stale.get(path) == video_stem and path not in seen:
                    result.append(path)
                    seen.add(path)
    except Exception:
        pass
    return result

def find_videos_and_companions(folder: str) -> tuple:
    """
    Returns (video_paths, orphan_companion_paths).
    Orphan companions = companion files with no matching video (exact or prefix).
    """
    try:
        all_files = [f for f in sorted(os.listdir(folder))
                     if not is_hidden(os.path.join(folder, f))
                     and os.path.isfile(os.path.join(folder, f))]
    except: return [], []

    video_stems = set()
    videos = []
    for f in all_files:
        if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
            videos.append(os.path.join(folder, f))
            video_stems.add(Path(f).stem)

    orphans = []
    comp_exts = {s.lower() for s in COMPANION_EXTENSIONS}
    stale     = map_stale_sidecars(folder)
    for f in all_files:
        cs, csuf = split_companion_name(f)
        if csuf.lower() not in comp_exts:
            continue
        # Check: is this companion matched by any video (exact or prefix)?
        matched = False
        if cs in video_stems or os.path.join(folder, f) in stale:
            matched = True
        else:
            for vs in video_stems:
                if (len(cs) >= 8
                        and vs.startswith(cs)
                        and len(vs) > len(cs)
                        and vs[len(cs)] == '_'):
                    matched = True
                    break
        if not matched:
            orphans.append(os.path.join(folder, f))

    return videos, orphans

def count_files_in_folder(folder: str) -> tuple:
    if not os.path.isdir(folder): return 0, 0
    vids = tot = 0
    try:
        for f in os.listdir(folder):
            fp = os.path.join(folder, f)
            if not os.path.isfile(fp) or is_hidden(fp): continue
            tot += 1
            if Path(f).suffix.lower() in VIDEO_EXTENSIONS: vids += 1
    except: pass
    return vids, tot

def edge_hash(path: str, edge: int = 1024 * 1024) -> str:
    """
    sha256 of the first and last `edge` bytes plus the total size.

    Hashing 3.5 GB end to end over the network would cost more than the copy
    itself. The edges are what actually matter: the head carries the MP4 header
    (and the moov atom after faststart), the tail is what a truncated or
    appended-to transfer gets wrong. Combined with an exact size match this
    catches every corruption mode seen here - notably the append bug, which
    produced a file of exactly the right size but the wrong first half.
    """
    h = hashlib.sha256()
    size = os.path.getsize(path)
    h.update(str(size).encode())
    with open(path, 'rb') as f:
        h.update(f.read(min(edge, size)))
        if size > edge:
            f.seek(max(0, size - edge))
            h.update(f.read(edge))
    return h.hexdigest()


def webdav_edge_hash(url: str, timeout: int = 60) -> tuple:
    """Same idea over HTTP, using Range requests. Returns (digest, size) or
    (None, -1) if the remote file cannot be read."""
    size = _webdav_file_remote_size(url, timeout=timeout)
    if size <= 0:
        return None, size
    edge = 1024 * 1024
    h = hashlib.sha256()
    h.update(str(size).encode())
    try:
        first = _webdav_request(
            url, "GET", headers={"Range": f"bytes=0-{min(edge, size)-1}"},
            timeout=timeout).read()
        h.update(first)
        if size > edge:
            last = _webdav_request(
                url, "GET", headers={"Range": f"bytes={size-edge}-{size-1}"},
                timeout=timeout).read()
            h.update(last)
        return h.hexdigest(), size
    except Exception:
        return None, size


def verify_transfer(src: str, dst: str) -> tuple:
    """
    Confirm dst really is a faithful copy of src.
    Returns (ok: bool, detail: str). Works for both local paths and WebDAV URLs.
    """
    try:
        src_size = os.path.getsize(src)
    except Exception as e:
        return False, f"source unreadable: {e}"

    try:
        src_hash = edge_hash(src)
    except Exception as e:
        return False, f"source unreadable: {e}"

    if dst.startswith("http://") or dst.startswith("https://"):
        remote_hash, remote_size = webdav_edge_hash(dst)
        if remote_size < 0:
            return False, "not found on server"
        if remote_size != src_size:
            return False, f"size {remote_size} != {src_size}"
        if remote_hash is None:
            return False, "could not read the remote file back"
        if remote_hash != src_hash:
            return False, "content differs (edge hash mismatch)"
        return True, "verified"

    if not os.path.exists(dst):
        return False, "missing at destination"
    try:
        dst_size = os.path.getsize(dst)
        if dst_size != src_size:
            return False, f"size {dst_size} != {src_size}"
        if edge_hash(dst) != src_hash:
            return False, "content differs (edge hash mismatch)"
        return True, "verified"
    except Exception as e:
        return False, str(e)


def build_new_basename(device, timestamp, is_training, group=None, site=None) -> str:
    """New name without extension, from the profile's naming template."""
    return NAMING.build(device, timestamp, is_training, group=group, site=site)

def is_already_renamed(filename: str, all_devices: list) -> bool:
    """True if filename already matches the profile's naming convention:
    a timestamp, plus every site/group field its template contains."""
    g = guess_from_filename(filename, all_devices)
    if not g.get('timestamp'):
        return False
    tokens = NAMING.tr_tokens if g.get('is_training') else NAMING.tokens
    return all(g.get(name) for kind, name, _ in tokens
               if kind == 'field' and name in ('site', 'group'))

def copy_with_progress(src: str, dst: str, progress_cb=None,
                       chunk_size: int = 4 * 1024 * 1024,
                       overwrite: bool = False, resume: bool = False) -> str:
    """
    Copy src to dst with an optional progress callback.

    Returns 'copied', 'resumed', 'skipped' (already identical size) or
    'overwritten'.

    Three deliberate design points, each fixing a real defect:

    1. `overwrite` and `resume` are SEPARATE. They used to be conflated: the
       caller asked for overwrite=True, logged "overwriting", and the function
       then opened the destination in 'ab' and APPENDED to it. When the existing
       file was not a prefix of the source, that produced a corrupt video of
       entirely plausible size - which the size-only verification then passed,
       and the user was offered the chance to delete the originals.
       Appending now happens only when the caller explicitly asks to resume.

    2. The copy goes to `dst + '.part'` and is promoted with os.replace() only
       once complete. A file at the final path is therefore always whole, and a
       partial one is unambiguously identifiable as ours.

    3. copystat() failures are swallowed rather than triggering a full recopy.
       Setting mtime/permissions fails routinely on WinFsp/SSHFS. The old code
       caught that and fell back to shutil.copy2 - a complete re-copy with no
       progress callback, which itself calls copystat, raises again, and lets
       the retry loop re-copy the whole file up to five times. On a 3.5 GB file
       that is hours of apparent freeze caused by nothing worse than a failed
       timestamp.
    """
    total = os.path.getsize(src)

    # Already there, complete and identical in size - nothing to do.
    if not overwrite and os.path.exists(dst) and os.path.getsize(dst) == total:
        return 'skipped'

    dst_existed = os.path.exists(dst)
    part        = dst + '.part'

    # Resume only from OUR OWN .part file, never from whatever sits at dst.
    resume_offset = 0
    if resume and os.path.exists(part):
        part_size = os.path.getsize(part)
        if 0 < part_size < total:
            resume_offset = part_size
        elif part_size >= total:
            try: os.remove(part)          # stale or oversized - start again
            except Exception: pass

    # SSHFS/WinFsp refuses to reopen an existing file for writing at all -
    # both 'wb' and 'ab' come back as PermissionError. Creating a fresh file is
    # allowed, so remove the stale .part and start over rather than failing.
    if os.path.exists(part) and not resume_offset:
        try: os.remove(part)
        except Exception: pass

    try:
        fo_ctx = open(part, 'ab' if resume_offset else 'wb')
    except PermissionError:
        # Append refused by the mount: drop the partial file and restart at 0.
        try: os.remove(part)
        except Exception: pass
        resume_offset = 0
        fo_ctx = open(part, 'wb')

    with open(src, 'rb') as fi:
        if resume_offset:
            fi.seek(resume_offset)
        with fo_ctx as fo:
            done = resume_offset
            while True:
                chunk = fi.read(chunk_size)
                if not chunk:
                    break
                fo.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, total)
            # Push the tail out to the filesystem before promoting, so that
            # os.replace() cannot publish a file whose last chunk is still
            # sitting in a buffer. Best-effort: fsync is not always meaningful
            # over SSHFS/WinFsp.
            try:
                fo.flush()
                os.fsync(fo.fileno())
            except Exception:
                pass

    written = os.path.getsize(part)
    if written != total:
        raise IOError(f"short copy: wrote {written} of {total} bytes")

    os.replace(part, dst)

    try:
        shutil.copystat(src, dst)
    except Exception:
        pass    # mtime/permissions routinely fail on WinFsp/SSHFS - not a copy error

    if resume_offset:
        return 'resumed'
    return 'overwritten' if dst_existed else 'copied'

# =============================================================================
#  DEVICE DIALOG
# =============================================================================

class DeviceDialog(tk.Toplevel):
    def __init__(self, parent, all_devices):
        super().__init__(parent)
        self.title("Unknown device — select or enter")
        self.configure(bg=C["bg"]); self.resizable(False,False)
        self.grab_set(); self.result = None; self.transient(parent)
        self.geometry("440x340"); self.update_idletasks()
        px = parent.winfo_rootx() + parent.winfo_width()//2 - 220
        py = parent.winfo_rooty() + parent.winfo_height()//2 - 170
        self.geometry(f"+{px}+{py}")

        hdr = tk.Frame(self, bg=C["panel"], pady=10); hdr.pack(fill='x')
        tk.Label(hdr, text="Device not recognised", bg=C["panel"],
                 fg=C["accent_lite"], font=('Helvetica',13,'bold')).pack(padx=16)
        tk.Label(hdr, text="Pick from the list or type a custom name below.",
                 bg=C["panel"], fg=C["muted"], font=('Helvetica',10)).pack(padx=16)

        frm = tk.Frame(self, bg=C["bg"], padx=16, pady=10); frm.pack(fill='both',expand=True)
        self._lv = tk.StringVar(value=all_devices)
        self._lb = tk.Listbox(frm, listvariable=self._lv, bg=C["entry_bg"], fg=C["text"],
                              selectbackground=C["accent_dark"], selectforeground=C["btn_fg"],
                              font=('Helvetica',11), relief='flat', height=7, activestyle='none')
        self._lb.pack(fill='both',expand=True)
        if all_devices: self._lb.selection_set(0)
        tk.Label(frm, text="Or type a custom / new device name:",
                 bg=C["bg"], fg=C["muted"], font=('Helvetica',9)).pack(anchor='w',pady=(8,2))
        self._cv = tk.StringVar()
        tk.Entry(frm, textvariable=self._cv, bg=C["entry_bg"], fg=C["text"],
                 insertbackground=C["text"], relief='flat',
                 font=('Helvetica',11)).pack(fill='x')
        tk.Label(frm, text="Custom names are saved for future sessions.",
                 bg=C["bg"], fg=C["muted"], font=('Helvetica',9)).pack(anchor='w',pady=(3,0))

        br = tk.Frame(self, bg=C["panel"], pady=8); br.pack(fill='x',side='bottom')
        tk.Button(br, text="Confirm", bg=C["accent"], fg=C["btn_fg"],
                  activebackground=C["accent_lite"], font=('Helvetica',11,'bold'),
                  relief='flat', cursor='hand2', command=self._confirm).pack(side='right',padx=12)
        tk.Button(br, text="Cancel", bg=C["panel2"], fg=C["muted"],
                  activebackground=C["separator"], font=('Helvetica',10),
                  relief='flat', cursor='hand2', command=self.destroy).pack(side='right')
        self.bind('<Return>', lambda _: self._confirm())
        self.wait_window()

    def _confirm(self):
        custom = self._cv.get().strip()
        if custom: self.result = sanitize(custom)
        else:
            sel = self._lb.curselection()
            if sel: self.result = self._lb.get(sel[0])
        self.destroy()

# =============================================================================
#  SCROLLABLE HELPER
# =============================================================================

def make_scrollable(parent) -> tk.Frame:
    outer = tk.Frame(parent, bg=C["bg"]); outer.pack(fill='both', expand=True)
    canvas = tk.Canvas(outer, bg=C["bg"], highlightthickness=0)
    vsb    = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    vsb.pack(side='right', fill='y')
    canvas.pack(side='left', fill='both', expand=True)
    inner = tk.Frame(canvas, bg=C["bg"])
    win   = canvas.create_window((0,0), window=inner, anchor='nw')

    def _rc(e): canvas.itemconfig(win, width=e.width)
    def _ri(e): canvas.configure(scrollregion=canvas.bbox('all'))
    canvas.bind('<Configure>', _rc)
    inner.bind('<Configure>', _ri)

    def _scroll(e):   canvas.yview_scroll(int(-1*(e.delta/120)), 'units')
    def _scroll_u(e): canvas.yview_scroll(-1, 'units')
    def _scroll_d(e): canvas.yview_scroll( 1, 'units')
    canvas.bind_all('<MouseWheel>', _scroll)
    canvas.bind_all('<Button-4>',   _scroll_u)
    canvas.bind_all('<Button-5>',   _scroll_d)

    def _unbind(e=None):
        try:
            canvas.unbind_all('<MouseWheel>')
            canvas.unbind_all('<Button-4>')
            canvas.unbind_all('<Button-5>')
        except: pass
    canvas.bind('<Destroy>', _unbind)
    return inner

# =============================================================================
#  MAIN APP
# =============================================================================

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("FieldClip")
        self.configure(bg=C["bg"]); self.resizable(True,True); self.minsize(860,600)

        system = platform.system()
        if system == "Windows":   self.state('zoomed')
        elif system == "Darwin":
            self.update_idletasks()
            self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")
        else: self.attributes('-zoomed', True)

        self.cfg          = load_config()
        self.source_dir   = tk.StringVar()
        _saved_dest       = self.cfg.get("last_dest_dir", "")
        self.dest_dir     = tk.StringVar(value=_saved_dest)
        self.op_mode      = tk.StringVar(value=self.cfg.get("op_mode","copy"))
        # Transport: 'webdav' (HTTPS upload) or 'mount' (copy to a mounted drive).
        # A profile without a WebDAV server defaults to a plain folder copy.
        self.transport    = tk.StringVar(value=self.cfg.get(
            "transport", "webdav" if WEBDAV_URL else "mount"))
        # The credential lives in the per-user secrets file (or the
        # FIELDCLIP_REMOTE_SECRET variable), shared with remote_mount.py.
        # Never in a profile, never in this file.
        _seed_secret = S.get_secret("remote_secret")
        self.share_id     = tk.StringVar(value=_seed_secret)
        set_webdav_credentials(_seed_secret)
        self.do_faststart = tk.BooleanVar(value=self.cfg.get("do_faststart", True))
        self.do_reencode_hevc = tk.BooleanVar(value=self.cfg.get("do_reencode_hevc", True))
        self.videos       = []    # list of video dicts
        self.orphans      = []    # list of companion-only file dicts
        self.renamed      = []    # results after processing
        self.current_idx  = 0
        self._proc_thread = None
        self._rename_thread = None
        # A real Queue, not a list. The old code snapshotted a list and then
        # cleared it inside the loop, destroying anything the worker appended in
        # between - including the 'done' message, which left the UI frozen for
        # good. Queue.get_nowait() cannot lose a message that way.
        self._queue       = queue.Queue()
        # The mount badge has its own queue: sharing the worker queue let the
        # badge pump swallow a transfer's 'done' message (step 4 never ended).
        self._badge_queue = queue.Queue()
        self._terminal_received = False   # set once done/cancelled/fatal arrives
        self._worker_dead_at    = None    # for the stuck-worker watchdog
        self._cancel_flag = threading.Event()
        self._retry_event = threading.Event()   # created once, not per attempt
        # Values copied off the Tk variables just before a worker starts.
        # Workers must never call .get() on a tkinter variable themselves.
        self._snap        = {}
        self._ffmpeg      = find_bundled_ffmpeg()
        self._dji_log_bin = HFL.find_dji_log_binary() if HFL else ""
        self.dji_api_key  = tk.StringVar(value=(HFL.read_api_key() if HFL else ""))
        self.do_flightlog = tk.BooleanVar(value=self.cfg.get("do_flightlog", True))
        self._folderlogs  = {}   # session folder -> HFL.FolderLogs (decode cache)
        # Old-nomenclature names a past run may have saved are dropped here.
        self._all_devices = BUILTIN_DEVICES + [
            d for d in self.cfg.get("custom_devices",[])
            if d not in BUILTIN_DEVICES and d not in LEGACY_DEVICE_TOKENS]
        self._last_device = ""
        self._skipped     = set()
        self._date_corrections = {}
        self._source_dirs = []   # list of source folder paths   # idx → (old_dt, new_dt)

        self._build_styles()
        self._build_layout()
        self._show_step_source()

    # =========================================================================
    #  STYLES
    # =========================================================================

    def _build_styles(self):
        s = ttk.Style(self); s.theme_use('clam')
        bg = C["bg"]
        s.configure('.',               background=bg, foreground=C["text"])
        s.configure('TFrame',          background=bg)
        s.configure('Card.TFrame',     background=C["panel2"])
        s.configure('TLabel',          background=bg, foreground=C["text"],       font=('Helvetica',11))
        s.configure('Title.TLabel',    background=bg, foreground=C["accent_lite"], font=('Helvetica',17,'bold'))
        s.configure('Sub.TLabel',      background=bg, foreground=C["muted"],       font=('Helvetica',10))
        s.configure('Card.TLabel',     background=C["panel2"], foreground=C["text"],  font=('Helvetica',11))
        s.configure('CardKey.TLabel',  background=C["panel2"], foreground=C["muted"], font=('Helvetica',10))
        s.configure('Auto.TLabel',     background=C["auto_badge"], foreground="#7ec8e3",
                    font=('Helvetica',8,'bold'), padding=(4,1))
        s.configure('TButton',         background=C["accent"], foreground=C["btn_fg"],
                    font=('Helvetica',11,'bold'), borderwidth=0, relief='flat', padding=(14,7))
        s.map('TButton', background=[('active',C["accent_lite"]),('pressed',C["accent_dark"])])
        s.configure('Ghost.TButton',   background=C["panel2"], foreground=C["accent_lite"],
                    font=('Helvetica',10), borderwidth=0, relief='flat', padding=(8,4))
        s.map('Ghost.TButton',         background=[('active',C["separator"])])
        s.configure('TCheckbutton',    background=bg, foreground=C["text"],  font=('Helvetica',11))
        s.configure('TRadiobutton',    background=bg, foreground=C["text"],  font=('Helvetica',11))
        s.configure('TCombobox',       fieldbackground=C["entry_bg"], background=C["panel2"],
                    foreground=C["text"], selectbackground=C["accent"], arrowcolor=C["accent_lite"])
        s.map('TCombobox', fieldbackground=[('readonly',C["entry_bg"])],
              foreground=[('readonly',C["text"])])
        s.configure('TEntry',          fieldbackground=C["entry_bg"], foreground=C["text"],
                    insertcolor=C["text"], borderwidth=0, relief='flat')
        s.configure('Treeview',        background=C["panel2"], foreground=C["text"],
                    fieldbackground=C["panel2"], rowheight=26, font=('Helvetica',10))
        s.configure('Treeview.Heading',background=C["accent_dark"], foreground=C["btn_fg"],
                    font=('Helvetica',10,'bold'))
        s.map('Treeview', background=[('selected',C["tree_sel"])],
              foreground=[('selected',C["btn_fg"])])
        s.configure('TScrollbar',      background=C["panel2"], troughcolor=bg, arrowcolor=C["muted"])
        s.configure('TProgressbar',    background=C["accent"],  troughcolor=C["panel2"], borderwidth=0)
        s.configure('TSeparator',      background=C["separator"])

    # =========================================================================
    #  LAYOUT SKELETON
    # =========================================================================

    def _build_layout(self):
        hdr = tk.Frame(self, bg=C["header_bg"]); hdr.pack(fill='x')
        inner = tk.Frame(hdr, bg=C["header_bg"]); inner.pack(fill='x', padx=20, pady=12)
        tk.Label(inner, text="🌿  FieldClip", bg=C["header_bg"], fg=C["accent_lite"],
                 font=('Helvetica',18,'bold')).pack(side='left')
        self.step_lbl = tk.Label(inner, text="Step 1 / 6", bg=C["header_bg"],
                                  fg=C["muted"], font=('Helvetica',10))
        self.step_lbl.pack(side='right')
        tk.Frame(self, bg=C["accent"], height=2).pack(fill='x')
        self.content = tk.Frame(self, bg=C["bg"], padx=22, pady=16)
        self.content.pack(fill='both', expand=True)

        ftr = tk.Frame(self, bg=C["panel"], pady=10); ftr.pack(fill='x', side='bottom')
        self.btn_back = tk.Button(ftr, text="← Back", bg=C["panel2"], fg=C["muted"],
                                   activebackground=C["separator"], font=('Helvetica',10),
                                   relief='flat', cursor='hand2', command=self._back)
        self.btn_back.pack(side='left', padx=16)
        self.btn_next = tk.Button(ftr, text="Next →", bg=C["accent"], fg=C["btn_fg"],
                                   activebackground=C["accent_lite"],
                                   font=('Helvetica',11,'bold'), relief='flat',
                                   cursor='hand2', padx=18, pady=6, command=self._next)
        self.btn_next.pack(side='right', padx=16)

    def _clear_content(self):
        for w in self.content.winfo_children(): w.destroy()

    def _set_step(self, n, total=6):
        self.step_lbl.config(text=f"Step {n} / {total}")

    def _label(self, parent, text, fg=None, font=None, **kw):
        """Cross-platform tk.Label with explicit colours."""
        return tk.Label(parent, text=text,
                        bg=kw.pop('bg', C["bg"]),
                        fg=fg or C["text"],
                        font=font or ('Helvetica',11),
                        **kw)

    # =========================================================================
    #  STEP 1 — Source, destination & options
    # =========================================================================

    def _show_step_source(self):
        self._clear_content(); self._set_step(1)
        self._remove_skip_btn()
        self.btn_back.config(state='disabled')
        self.btn_next.config(text="Scan & continue →", command=self._step_source_next)

        inner = make_scrollable(self.content)

        self._label(inner, "Step 1 — Source, destination & options",
                    fg=C["accent_lite"], font=('Helvetica',17,'bold')).pack(anchor='w', pady=(0,4))

        # ── Source folders (multi-select) ─────────────────────────────────
        src_card = tk.Frame(inner, bg=C["panel2"], padx=12, pady=10)
        src_card.pack(fill='x', pady=(0,8))
        self._label(src_card, "Source folders", fg=C["text"],
                    bg=C["panel2"], font=('Helvetica',11,'bold')).pack(anchor='w', pady=(0,2))
        self._label(src_card, "Add one or more folders containing videos to rename.",
                    fg=C["muted"], bg=C["panel2"], font=('Helvetica',10)).pack(anchor='w', pady=(0,6))

        # Listbox showing added folders
        lb_frame = tk.Frame(src_card, bg=C["panel2"]); lb_frame.pack(fill='x')
        self._src_listbox = tk.Listbox(lb_frame, bg=C["entry_bg"], fg=C["text"],
                                        selectbackground=C["accent_dark"],
                                        font=('Helvetica',10), height=4,
                                        relief='flat', activestyle='none')
        self._src_listbox.pack(side='left', fill='x', expand=True)
        lb_sb = ttk.Scrollbar(lb_frame, orient='vertical',
                               command=self._src_listbox.yview)
        lb_sb.pack(side='left', fill='y')
        self._src_listbox.config(yscrollcommand=lb_sb.set)

        # Pre-populate from last used
        if hasattr(self, '_source_dirs') and self._source_dirs:
            for d in self._source_dirs:
                self._src_listbox.insert('end', d)
        elif self.source_dir.get():
            self._src_listbox.insert('end', self.source_dir.get())
            if not hasattr(self, '_source_dirs'):
                self._source_dirs = [self.source_dir.get()]

        btn_row = tk.Frame(src_card, bg=C["panel2"]); btn_row.pack(fill='x', pady=(6,0))
        tk.Button(btn_row, text="+ Add folder", bg=C["accent_dark"], fg=C["btn_fg"],
                  activebackground=C["accent"], font=('Helvetica',10),
                  relief='flat', cursor='hand2',
                  command=self._add_source_dir).pack(side='left', padx=(0,8))
        tk.Button(btn_row, text="− Remove selected", bg=C["panel"], fg=C["muted"],
                  activebackground=C["separator"], font=('Helvetica',10),
                  relief='flat', cursor='hand2',
                  command=self._remove_source_dir).pack(side='left')

        # ── Operation mode ────────────────────────────────────────────────
        # Flight-log processing (Step 3: SRT recovery + telemetry + model)
        if HFL is not None:
            fl_card = tk.Frame(inner, bg=C["panel2"], padx=12, pady=10)
            fl_card.pack(fill='x', pady=(0,8))
            self._label(fl_card, "Flight-log processing (Step 3)", fg=C["text"],
                        bg=C["panel2"], font=('Helvetica',11,'bold')).pack(anchor='w', pady=(0,2))
            self._label(fl_card,
                        "Keep each flight session's files together (MP4 + SRT + DJI log .txt). "
                        "Missing SRTs are rebuilt from the log, a .flightlog.csv is written, and the "
                        "true model (Mini4Pro / Mini3Pro / Mini3) is set from the log.",
                        fg=C["muted"], bg=C["panel2"], font=('Helvetica',10),
                        wraplength=780, justify='left').pack(anchor='w', pady=(0,6))
            ttk.Checkbutton(fl_card, text="Enable flight-log processing",
                            variable=self.do_flightlog).pack(anchor='w', pady=(0,6))
            krow = tk.Frame(fl_card, bg=C["panel2"]); krow.pack(fill='x')
            self._label(krow, "DJI API key:", fg=C["text"], bg=C["panel2"],
                        font=('Helvetica',10)).pack(side='left', padx=(0,6))
            ttk.Entry(krow, textvariable=self.dji_api_key, width=44, show="•").pack(
                side='left', fill='x', expand=True)
            self._label(fl_card,
                        "Required to decrypt DJI v13 logs. Kept in your user settings (or in the shared "
                        "key file named by the profile), never in a profile or the repository. "
                        "Get one free at developer.dji.com (create an 'Open API' app).",
                        fg=C["muted"], bg=C["panel2"], font=('Helvetica',9),
                        wraplength=780, justify='left').pack(anchor='w', pady=(4,0))
            if not self._dji_log_bin:
                self._label(fl_card,
                            "dji-log binary not found for this OS - flight-log step will be skipped.",
                            fg=C["warning"], bg=C["panel2"], font=('Helvetica',9)).pack(anchor='w')

        op_card = tk.Frame(inner, bg=C["panel2"], padx=12, pady=10)
        op_card.pack(fill='x', pady=(0,8))
        self._label(op_card, "Operation mode", fg=C["text"],
                    bg=C["panel2"], font=('Helvetica',11,'bold')).pack(anchor='w', pady=(0,6))

        for val, label, desc in [
            ("copy",   "Copy files to destination",
             "Copies renamed files into Videos/Category/Site/ — originals untouched"),
            ("rename", "Rename in place only",
             "Renames files in their current folder — no files are moved or copied"),
        ]:
            rb_row = tk.Frame(op_card, bg=C["panel2"]); rb_row.pack(fill='x', pady=2)
            ttk.Radiobutton(rb_row, text=label, variable=self.op_mode, value=val,
                            command=self._toggle_dest_card).pack(side='left')
            self._label(rb_row, f"  — {desc}", fg=C["muted"],
                        bg=C["panel2"], font=('Helvetica',10)).pack(side='left')

        # ── Destination (shown only if copy) ──────────────────────────────
        self._dest_card = tk.Frame(inner, bg=C["panel2"], padx=12, pady=10)
        self._dest_card.pack(fill='x', pady=(0,8))
        self._label(self._dest_card, "Destination — root folder",
                    fg=C["text"], bg=C["panel2"],
                    font=('Helvetica',11,'bold')).pack(anchor='w', pady=(0,4))
        self._label(self._dest_card,
                    "Files will be sorted into  " + _layout_hint(),
                    fg=C["muted"], bg=C["panel2"],
                    font=('Helvetica',10)).pack(anchor='w', pady=(0,6))
        self._label(self._dest_card,
                    "Pick a local folder, or paste a WebDAV URL to upload directly to a server.",
                    fg=C["muted"], bg=C["panel2"],
                    font=('Helvetica',10)).pack(anchor='w', pady=(0,6))

        # ── Transport choice ──────────────────────────────────────────────
        # Measured on one ERDA share: WebDAV 22.7 MB/s vs mounted drive 19.4 MB/s.
        # WebDAV keeps the kernel filesystem driver out of the export path, so
        # a network drop is a Python exception rather than a frozen machine.
        # WebDAV servers commonly reject ranged PUTs, so an interrupted file
        # is deleted remotely and restarts from zero.
        tr_row = tk.Frame(self._dest_card, bg=C["panel2"]); tr_row.pack(fill='x', pady=(4,2))
        self._label(tr_row, "Transport", fg=C["text"], bg=C["panel2"],
                    font=('Helvetica',10,'bold')).pack(anchor='w')
        for val, label, desc in [
            ("webdav", "Upload over HTTPS (WebDAV)",
             "no drive needed; an interrupted file restarts"),
            ("mount",  "Copy to a folder or mounted drive",
             "local disk, USB, or network drive — resumes an interrupted file"),
        ]:
            row = tk.Frame(self._dest_card, bg=C["panel2"]); row.pack(fill='x')
            ttk.Radiobutton(row, text=label, variable=self.transport, value=val,
                            command=self._on_transport_change).pack(side='left')
            self._label(row, f"  — {desc}", fg=C["muted"], bg=C["panel2"],
                        font=('Helvetica',9)).pack(side='left')

        # ── Credential + mount status ─────────────────────────────────────
        sid_row = tk.Frame(self._dest_card, bg=C["panel2"]); sid_row.pack(fill='x', pady=(8,0))
        if SETTINGS["remote"]["auth"] != "none":
            self._label(sid_row, f"{REMOTE_NAME} {SECRET_LABEL}:", fg=C["text"],
                        bg=C["panel2"], font=('Helvetica',10)).pack(side='left', padx=(0,6))
            ttk.Entry(sid_row, textvariable=self.share_id, width=18,
                      show="•").pack(side='left')
        self._mount_badge = tk.Label(sid_row, text="  checking…  ",
                                     bg=C["panel"], fg=C["muted"],
                                     font=('Helvetica',9,'bold'))
        self._mount_badge.pack(side='left', padx=(10,0))
        tk.Button(sid_row, text="Remount", bg=C["panel"], fg=C["accent_lite"],
                  activebackground=C["separator"], font=('Helvetica',9),
                  relief='flat', cursor='hand2',
                  command=self._remount_now).pack(side='left', padx=(6,0))
        self.share_id.trace_add(
            'write', lambda *_: set_webdav_credentials(self.share_id.get()))

        dst_row = tk.Frame(self._dest_card, bg=C["panel2"]); dst_row.pack(fill='x', pady=(6,0))
        self._dest_entry = ttk.Entry(dst_row, textvariable=self.dest_dir, width=56)
        self._dest_entry.pack(side='left', expand=True, fill='x', padx=(0,8))

        # Dynamic button: Browse… for local paths, Test connection for URLs
        self._dest_action_btn = tk.Button(
            dst_row, text="Browse…", bg=C["panel"], fg=C["accent_lite"],
            activebackground=C["separator"], font=('Helvetica',10),
            relief='flat', cursor='hand2',
            command=lambda: self._browse(self.dest_dir))
        self._dest_action_btn.pack(side='left')

        # Update action button (Browse… vs Test connection) whenever dest changes
        def _on_dest_change(*_):
            val = self.dest_dir.get().strip()
            is_url = val.startswith("https://") or val.startswith("http://")
            if is_url:
                self._dest_action_btn.config(
                    text="Test connection",
                    command=lambda: self._test_webdav_connection(self.dest_dir.get().strip()))
            else:
                self._dest_action_btn.config(
                    text="Browse…",
                    command=lambda: self._browse(self.dest_dir))

        self.dest_dir.trace_add('write', _on_dest_change)
        _on_dest_change()              # apply immediately on load
        self._toggle_dest_card()
        # Reconcile the destination with the selected transport, ALWAYS - not
        # only when it is empty. last_dest_dir is remembered across sessions, so
        # a saved drive path could sit under a WebDAV transport; _show_step_copy
        # picks its worker from the destination string, so the app would quietly
        # use the mount while the UI said HTTPS.
        self._on_transport_change()
        self._refresh_mount_badge()

        # ── Web compatibility ─────────────────────────────────────────────
        web_card = tk.Frame(inner, bg=C["panel2"], padx=12, pady=10)
        web_card.pack(fill='x', pady=(0,8))
        web_top = tk.Frame(web_card, bg=C["panel2"]); web_top.pack(fill='x')
        self._label(web_top, "🌐  Web compatibility (browsers / web servers)",
                    fg=C["text"], bg=C["panel2"],
                    font=('Helvetica',11,'bold')).pack(side='left')
        badge_bg = C["accent_dark"] if self._ffmpeg else C["danger"]
        badge_txt = "  ffmpeg ready ✓  " if self._ffmpeg else "  ffmpeg not found  "
        tk.Label(web_top, text=badge_txt, bg=badge_bg, fg=C["btn_fg"],
                 font=('Helvetica',9,'bold')).pack(side='left', padx=(10,0))

        self._label(web_card,
                    "Videos may need metadata moved to front for web streaming.\n"
                    + _reencode_hint(),
                    fg=C["muted"], bg=C["panel2"],
                    font=('Helvetica',10)).pack(anchor='w', pady=(4,8))

        fs_state = 'normal' if self._ffmpeg else 'disabled'
        ttk.Checkbutton(web_card,
                        text="Move metadata to front  (fast, lossless — recommended)",
                        variable=self.do_faststart, state=fs_state).pack(anchor='w', pady=(2,0))
        ttk.Checkbutton(web_card,
                        text=_reencode_checkbox_text(),
                        variable=self.do_reencode_hevc,
                        state=fs_state if REENCODE_WHEN != "never" else 'disabled'
                        ).pack(anchor='w', pady=(4,0))

    def _add_source_dir(self):
        path = filedialog.askdirectory(title="Add source folder")
        if path and path not in self._src_listbox.get(0, 'end'):
            self._src_listbox.insert('end', path)

    def _remove_source_dir(self):
        sel = self._src_listbox.curselection()
        for i in reversed(sel):
            self._src_listbox.delete(i)

    def _browse_source(self):
        path = filedialog.askdirectory(title="Select source folder")
        if path:
            self.source_dir.set(path)

    def _test_webdav_connection(self, url: str):
        """Test connectivity AND credentials, then show the verdict."""
        set_webdav_credentials(self.share_id.get())
        ok, msg = _webdav_test_connection(url, timeout=10)
        if ok:
            messagebox.showinfo("Connection test",
                f"✓  Reachable and authenticated\n{url}\n\nServer response: {msg}")
        else:
            messagebox.showerror("Connection test",
                f"✗  Failed\n{url}\n\n{msg}")

    # ── Mount status badge ────────────────────────────────────────────────────

    def _webdav_base(self) -> str:
        """Root of the share over WebDAV(S), from remote.webdav.url.

        Mind the port: some servers (ERDA) answer WebDAV on 4443, not 443."""
        return WEBDAV_URL

    # The share root is the SAME place under both transports:
    #     W:\              ==  https://server/
    #     W:\Project\Videos ==  https://server/Project/Videos/
    # so switching transport must translate the path, not fall back to a
    # default. Getting this wrong sends an export to the root of the shared
    # space instead of the intended Videos folder - visible to every member of
    # the project, in the wrong place.

    def _mount_root(self) -> str:
        if EM is not None:
            try: return EM.mountpoint(EM.load_config())
            except Exception: pass
        return ""

    def _path_to_url(self, path: str) -> str:
        """W:\\Project\\Videos  ->  https://server/Project/Videos/

        Returns "" when the path is not on the share. It used to return the
        share ROOT instead, which - being a non-empty string - beat every
        better fallback in _on_transport_change: a local folder, or a Windows
        "W:/..." path remembered on Linux, silently became an upload to the
        top level of the project space.
        """
        base = self._webdav_base()
        if not path or not base:
            return ""
        drive = re.match(r'^([A-Za-z]):[\\/]*(.*)$', path)
        if drive and platform.system() != "Windows":
            # A drive path saved on Windows: W:\ is the share root there.
            try:
                letter = EM.load_config().get("windows_drive", "W") if EM else "W"
            except Exception:
                letter = "W"
            if drive.group(1).upper() != letter.rstrip(":").upper():
                return ""
            rel = drive.group(2)
        else:
            root = self._mount_root()
            if not root:
                return ""
            try:
                rel = os.path.relpath(path, root)
            except Exception:
                return ""
            if rel.startswith(".."):
                return ""
        parts = [p for p in re.split(r'[\\/]+', rel) if p not in ("", ".")]
        return base + "".join(urllib.parse.quote(p, safe="") + "/" for p in parts)

    def _url_to_path(self, url: str) -> str:
        """https://server/Project/Videos/  ->  W:\\Project\\Videos"""
        root = self._mount_root()
        if not root:
            return ""
        rel = urllib.parse.urlparse(url).path.strip("/")
        if not rel:
            return root
        parts = [urllib.parse.unquote(p) for p in rel.split("/") if p]
        return os.path.join(root, *parts)

    def _on_transport_change(self):
        """Swap the destination between a drive path and the URL for the SAME
        folder, so the files land in one place whichever transport is used."""
        cur = self.dest_dir.get().strip()
        is_url = cur.startswith("http://") or cur.startswith("https://")

        if self.transport.get() == "webdav":
            if not is_url:
                self.cfg["last_mount_dir"] = cur
                translated = self._path_to_url(cur) if cur else ""
                saved = self.cfg.get("last_dest_dir", "")
                if not saved.startswith(("http://", "https://")):
                    saved = ""
                # Never fall back to the bare share root: the project's Videos
                # folder is what an export means when nothing else is known.
                self.dest_dir.set(translated or self.cfg.get("last_webdav_url")
                                  or saved
                                  or (self._webdav_base() + DEFAULT_WEBDAV_SUBDIR
                                      if self._webdav_base() else ""))
        else:
            if is_url:
                self.cfg["last_webdav_url"] = cur
                translated = self._url_to_path(cur)
                self.dest_dir.set(translated or self.cfg.get("last_mount_dir", ""))
        self._refresh_mount_badge()

    def _refresh_mount_badge(self):
        """Probe the mount off the UI thread and colour the badge accordingly."""
        if not hasattr(self, "_mount_badge"):
            return
        if self.transport.get() == "webdav":
            self._mount_badge.config(text="  HTTPS — no drive needed  ",
                                     bg=C["panel"], fg=C["muted"])
            return

        self._mount_badge.config(text="  checking…  ", bg=C["panel"], fg=C["muted"])

        def _probe():
            path = ""
            try:
                path = (EM.mountpoint(EM.load_config()) if EM is not None
                        else self.dest_dir.get().strip())
            except Exception:
                path = self.dest_dir.get().strip()
            state = probe_path(path, timeout=8) if path else 'ABSENT'
            self._badge_queue.put(('mount_state', (state, path)))

        threading.Thread(target=_probe, daemon=True).start()
        self._pump_badge_queue()

    def _pump_badge_queue(self):
        """Small dedicated pump: the badge runs outside a transfer, so the main
        _poll_queue loop is not running at this point."""
        try:
            if not self._mount_badge.winfo_exists():
                return
        except tk.TclError:
            return                                  # window already closed
        try:
            while True:
                kind, payload = self._badge_queue.get_nowait()
                if kind == 'mount_state':
                    state, path = payload
                    colours = {'MOUNTED': (C["accent_dark"], f"  {REMOTE_NAME} mounted  "),
                               'ABSENT':  (C["danger"],      "  not mounted  "),
                               'ZOMBIE':  (C["warning"],     "  BLOCKED — remount  ")}
                    bg, txt = colours.get(state, (C["panel"], f"  {state}  "))
                    self._mount_badge.config(text=txt, bg=bg, fg=C["btn_fg"])
                    if state == 'MOUNTED' and path and not self.dest_dir.get().strip():
                        self.dest_dir.set(path)
                    return
        except queue.Empty:
            pass
        self.after(200, self._pump_badge_queue)

    def _remount_now(self):
        """Manual repair button next to the badge."""
        if EM is None or not SETTINGS["remote"]["mount"].get("host"):
            messagebox.showwarning(
                "Mount manager unavailable",
                "No remote.mount.host in the active profile (or remote_mount.py "
                "failed to load), so the drive cannot be repaired from here.")
            return
        self._mount_badge.config(text="  remounting…  ", bg=C["panel"], fg=C["muted"])

        def _do():
            try:
                cfg = EM.load_config()
                if self.share_id.get().strip():
                    EM.save_credential(cfg, self.share_id.get().strip())
                EM.do_remount(cfg)
            except Exception as e:
                LOG.error(f"manual remount failed: {e}")
            self.after(0, self._refresh_mount_badge)

        threading.Thread(target=_do, daemon=True).start()

    def _toggle_dest_card(self):
        mode = self.op_mode.get()
        state = 'normal' if mode == 'copy' else 'disabled'
        for w in self._dest_card.winfo_children():
            try: w.config(state=state)
            except: pass
            for sub in w.winfo_children():
                try: sub.config(state=state)
                except: pass

    def _step_source_next(self):
        # Read folders from listbox
        dirs = list(self._src_listbox.get(0, 'end'))
        if not dirs:
            messagebox.showerror("Error", "Please add at least one source folder.")
            return
        invalid = [d for d in dirs if not os.path.isdir(d)]
        if invalid:
            messagebox.showerror("Error",
                f"Invalid folder(s):\n" + "\n".join(invalid))
            return
        if self.op_mode.get() == "copy" and not self.dest_dir.get().strip():
            messagebox.showerror("Error", "Please select a destination folder.")
            return

        # Refuse a destination that contradicts the chosen transport, rather
        # than silently running the other one.
        if self.op_mode.get() == "copy":
            dest_v = self.dest_dir.get().strip()
            is_url = dest_v.startswith("http://") or dest_v.startswith("https://")
            if self.transport.get() == "webdav" and not is_url:
                messagebox.showerror(
                    "Destination does not match the transport",
                    "Transport is set to HTTPS upload, but the destination is a "
                    f"folder:\n{dest_v}\n\nEither switch to \"Copy to a mounted "
                    "drive\" or give an https:// URL.")
                return
            if self.transport.get() == "mount" and is_url:
                messagebox.showerror(
                    "Destination does not match the transport",
                    "Transport is set to a mounted drive, but the destination is "
                    f"a URL:\n{dest_v}\n\nEither switch to \"Upload over HTTPS\" "
                    "or pick a folder.")
                return
            if (self.transport.get() == "webdav" and SETTINGS["remote"]["auth"] != "none"
                    and not self.share_id.get().strip()):
                messagebox.showerror(
                    f"{SECRET_LABEL} required",
                    f"Uploading over HTTPS needs the {REMOTE_NAME} {SECRET_LABEL}.")
                return

            # The bare share root is almost never what anyone means. Exporting
            # there scatters the category folders next to everything else in
            # the shared space, where the whole project sees them.
            if self.transport.get() == "webdav":
                if not urllib.parse.urlparse(dest_v).path.strip("/"):
                    if not messagebox.askyesno(
                            "Upload to the root of the share?",
                            f"{dest_v}\n\nThis is the TOP level of the shared "
                            f"{REMOTE_NAME} space.\n\nThe export would create "
                            f"{NAMING.main_dir}/ and {NAMING.training_dir}/ there, "
                            "visible to everyone with access to the share.\n\n"
                            "Did you mean a subfolder"
                            + (f", such as .../{DEFAULT_WEBDAV_SUBDIR} ?" if DEFAULT_WEBDAV_SUBDIR else "?")
                            + "\n\nContinue anyway?", icon='warning'):
                        return

        self._source_dirs = dirs
        self.source_dir.set(dirs[0])   # keep for backward compat

        # Collect videos + orphans from ALL source dirs
        all_videos, all_orphans = [], []
        for d in dirs:
            v, o = find_videos_and_companions(d)
            all_videos.extend(v)
            all_orphans.extend(o)

        if not all_videos and not all_orphans:
            messagebox.showwarning("No files", "No video or companion files found.")
            return

        # Save options
        self.cfg["op_mode"]          = self.op_mode.get()
        self.cfg["do_faststart"]     = self.do_faststart.get()
        self.cfg["do_reencode_hevc"] = self.do_reencode_hevc.get()
        self.cfg["transport"]        = self.transport.get()
        S.set_secret("remote_secret", self.share_id.get())
        set_webdav_credentials(self.share_id.get())
        # Always save dest_dir, including WebDAV URLs
        dest_val = self.dest_dir.get().strip()
        if dest_val:
            self.cfg["last_dest_dir"] = dest_val
        save_config(self.cfg)

        # Check if already renamed → skip to Step 4 copy directly
        if all_videos:
            already = sum(1 for p in all_videos
                          if is_already_renamed(os.path.basename(p), self._all_devices))
            if already == len(all_videos):
                ans = messagebox.askyesno(
                    "Already renamed?",
                    f"All {len(all_videos)} video(s) appear to already have FieldClip names.\n\n"
                    "YES → Skip to Step 4 — Copy files directly\n"
                    "NO  → Go through Step 2 to review / edit names")
                if ans:
                    self._build_video_list(all_videos, all_orphans)
                    self.renamed = [{**v,
                        'path': v['path'], 'filename': v['filename'],
                        'new_path': v['path'],
                        'dest_folder': os.path.dirname(v['path']),
                        'status': 'ok',
                        'codec_info': {}, 'moov_pos': 'unknown', 'fragmented': False,
                    } for v in self.videos]
                    self._show_step_copy()
                    return

        self._build_video_list(all_videos, all_orphans)
        self.current_idx = 0
        self._show_step_questions()

    def _build_video_list(self, video_paths, orphan_paths):
        self.videos  = []
        self.orphans = []
        self._skipped = set()
        self._date_corrections = {}

        for p in video_paths:
            g   = guess_from_path(p, self._all_devices)
            srt = parse_srt_companion(p)

            # Drone family: the raw filename, else the SRT colour profile (D-Log M
            # is Mini 4 only). A family is a hint for the user, never a model.
            color_md = srt.get('color_md') or ''
            family = dji_family(p) or ('Mini 4 series' if 'dlog_m' in color_md else
                                       'Mini 3 series' if 'd_cinelike' in color_md else '')
            if g['device'] == 'Unknown' and color_md:
                g['device'] = DJI_FALLBACK; g['guessed'].add('device'); g['guessed'].add('device_from_srt')

            # Timestamp: SRT start > filename > MP4 header > mtime. The mtime is
            # the last resort: on a DJI card it is the END of the recording.
            mp4       = mp4_local_info(p)
            srt_start = srt.get('start_dt')
            name_dt   = extract_timestamp_from_name(p)
            start     = srt_start or name_dt or mp4['start_dt']
            ts = (start.strftime('%Y-%m-%d_%H-%M-%S')
                  if start else extract_timestamp(p))

            # Duration-based training pre-selection — ONLY from title or duration, never carried
            dur      = srt.get('duration_s') or mp4['duration_s']
            is_short = (dur is not None and TRAINING_MAX_DURATION_S > 0
                        and dur < TRAINING_MAX_DURATION_S)

            # Priority: training marker in filename > short duration > path keyword
            if 'is_training' in g['guessed']:
                is_tr = g['is_training']      # from filename parse
            elif is_short:
                is_tr = True
                g['guessed'].add('is_training_short')
            elif _path_says_training(p):
                is_tr = True
                g['guessed'].add('is_training')
            else:
                is_tr = False                 # never carry from config/previous

            # Carry last device if unknown
            dev = g['device']
            if dev == 'Unknown' and self._last_device:
                dev = self._last_device

            self.videos.append({
                'path': p, 'filename': os.path.basename(p),
                'device': dev,
                'family': family,
                'timestamp': ts,
                'mp4': mp4,
                'size_mb': get_file_size_mb(p),
                'is_training': is_tr,
                'site':  g.get('site', ''),   # do NOT pre-fill from config here;
                'group': g.get('group', ''),  # presel() in Step 2 handles carry-forward
                'new_basename': '',
                'guessed': g['guessed'],
                'srt': srt,
            })

        for p in orphan_paths:
            self.orphans.append({'path': p, 'filename': os.path.basename(p),
                                  'new_basename': ''})

    # =========================================================================
    #  STEP 2 — Questions per video
    # =========================================================================

    def _show_step_questions(self):
        self._clear_content(); self._set_step(2)
        idx = self.current_idx; total = len(self.videos); v = self.videos[idx]

        # Skip button
        ftr = self.btn_back.master
        for w in ftr.winfo_children():
            if getattr(w, '_is_skip_btn', False): w.destroy()
        btn_skip = tk.Button(ftr, text="⏭  Skip",
                              bg=C["panel2"], fg=C["muted"],
                              activebackground=C["separator"],
                              font=('Helvetica',10), relief='flat',
                              cursor='hand2', command=self._skip_video)
        btn_skip._is_skip_btn = True
        btn_skip.pack(side='left', padx=(8,0))

        self.btn_back.config(state='normal',
            command=(self._show_step_source if idx == 0 else self._go_back_one))
        self.btn_next.config(text=f"Save & next  ({idx+1}/{total}) →",
                             command=self._step_questions_next)

        # Carry last device if unknown
        if v.get('device','Unknown') == 'Unknown' and self._last_device:
            v['device'] = self._last_device

        # Date resolution — collect all available sources
        srt        = v.get('srt', {})
        srt_start  = srt.get('start_dt')
        title_dt   = extract_timestamp_from_name(v['filename'])
        file_dates = get_file_dates(v['path'])

        ts_sources = {}
        if title_dt:
            ts_sources['Filename'] = title_dt.strftime('%Y-%m-%d_%H-%M-%S')
        if srt_start:
            ts_sources['SRT start'] = srt_start.strftime('%Y-%m-%d_%H-%M-%S')
        if srt.get('end_dt'):
            ts_sources['SRT end']   = srt['end_dt'].strftime('%Y-%m-%d_%H-%M-%S')
        mp4 = v.get('mp4') or {}
        if mp4.get('start_dt'):
            ts_sources['MP4 metadata'] = mp4['start_dt'].strftime('%Y-%m-%d_%H-%M-%S')
        if file_dates['created']:
            ts_sources['Created']   = file_dates['created'].strftime('%Y-%m-%d_%H-%M-%S')
        if file_dates['modified']:
            ts_sources['Modified']  = file_dates['modified'].strftime('%Y-%m-%d_%H-%M-%S')
        if not ts_sources:
            ts_sources['Filename']  = v['timestamp']

        # Priority: SRT start (most reliable) > Filename > MP4 header > first available
        for default_src in ('SRT start', 'Filename', 'MP4 metadata'):
            if default_src in ts_sources: break
        else:
            default_src = list(ts_sources.keys())[0]
        self._ts_src_var = tk.StringVar(value=default_src)
        self._ts_var     = tk.StringVar(value=ts_sources[default_src])
        v['timestamp']   = ts_sources[default_src]

        def _dates_match(dt1, ts2):
            if dt1 is None or not ts2: return True
            try:
                dt2p = datetime.strptime(ts2, '%Y-%m-%d_%H-%M-%S')
                return abs((dt1 - dt2p).total_seconds()) <= 2
            except: return True

        created_match  = _dates_match(file_dates['created'],  v['timestamp'])
        modified_match = _dates_match(file_dates['modified'], v['timestamp'])
        # The MP4 header is the recording start: a name off from it by more
        # than a few seconds is a name that was given from the wrong clock.
        mp4_match      = _dates_match(mp4.get('start_dt'),    v['timestamp'])
        dates_differ   = not created_match or not modified_match or not mp4_match

        def _on_source_change(*_):
            src = self._ts_src_var.get()
            if src in ts_sources:
                self._ts_var.set(ts_sources[src])
        self._ts_src_var.trace_add('write', _on_source_change)

        # ── Layout ───────────────────────────────────────────────────────
        self._label(self.content, f"Step 2 — Video {idx+1} of {total}",
                    fg=C["accent_lite"], font=('Helvetica',17,'bold')).pack(anchor='w', pady=(0,6))

        card = tk.Frame(self.content, bg=C["panel2"], padx=12, pady=10)
        card.pack(fill='x', pady=(0,8))

        # Step 2 uses slightly larger fonts for readability
        F_KEY  = ('Helvetica', 11)   # label keys
        F_VAL  = ('Helvetica', 12)   # values
        F_MONO = ('Courier',   11)   # timestamp entry

        def kv(lbl, val, fg=None):
            r = tk.Frame(card, bg=C["panel2"]); r.pack(fill='x', pady=2)
            tk.Label(r, text=f"{lbl:<22}", bg=C["panel2"],
                     fg=C["muted"], font=F_KEY).pack(side='left')
            tk.Label(r, text=str(val), bg=C["panel2"],
                     fg=fg or C["text"], font=F_VAL).pack(side='left')

        kv("File", v['filename'])
        kv("File size", f"{v['size_mb']:.1f} MB")

        # SRT info, else what the MP4 header says
        dur      = srt.get('duration_s')
        dur_src  = "SRT"
        if dur is None and mp4.get('duration_s') is not None:
            dur, dur_src = mp4['duration_s'], "MP4"
        start_dt = srt.get('start_dt')
        end_dt   = srt.get('end_dt')
        if srt.get('srt_path'):
            if start_dt: kv("Recording start", start_dt.strftime('%Y-%m-%d  %H:%M:%S'), C["accent_lite"])
            if end_dt:   kv("Recording end",   end_dt.strftime('%Y-%m-%d  %H:%M:%S'),   C["accent_lite"])
        else:
            kv("SRT file", "not found", C["muted"])
            if mp4.get('start_dt'):
                kv("Recording start", mp4['start_dt'].strftime('%Y-%m-%d  %H:%M:%S')
                   + "  (MP4 header)", C["accent_lite"])
        if dur is not None:
            mins, secs = divmod(int(dur), 60)
            short  = TRAINING_MAX_DURATION_S > 0 and dur < TRAINING_MAX_DURATION_S
            col    = C["warning"] if short else C["accent_lite"]
            suffix = (f"  ⚠ < {TRAINING_MAX_DURATION_S / 60:g} min — {TRAINING_LABEL} pre-selected"
                      if short else "")
            src_t  = f"  (from {dur_src})" if dur_src != "SRT" else ""
            kv("Duration", f"{mins}m {secs:02d}s{src_t}{suffix}", col)

        # Date source selector
        ds_row = tk.Frame(card, bg=C["panel2"]); ds_row.pack(fill='x', pady=(6,2))
        tk.Label(ds_row, text=f"{'Date source':<22}", bg=C["panel2"],
                 fg=C["muted"], font=F_KEY).pack(side='left')
        for src_name in ts_sources:
            tk.Radiobutton(ds_row, text=src_name,
                           variable=self._ts_src_var, value=src_name,
                           bg=C["panel2"], fg=C["text"],
                           selectcolor=C["entry_bg"],
                           activebackground=C["panel2"],
                           font=F_KEY).pack(side='left', padx=(0,10))
        if dates_differ:
            tk.Label(ds_row, text=" ⚠ dates differ across sources",
                     bg=C["warning"], fg=C["entry_bg"],
                     font=('Helvetica',9,'bold'), padx=4).pack(side='left', padx=(6,0))

        # Editable timestamp
        ts_row = tk.Frame(card, bg=C["panel2"]); ts_row.pack(fill='x', pady=(2,4))
        tk.Label(ts_row, text=f"{'Timestamp':<22}", bg=C["panel2"],
                 fg=C["muted"], font=F_KEY).pack(side='left')
        tk.Entry(ts_row, textvariable=self._ts_var, width=22,
                 bg=C["entry_bg"], fg=C["text"],
                 insertbackground=C["text"], relief='flat',
                 font=F_MONO).pack(side='left', padx=(0,6))
        tk.Label(ts_row, text="YYYY-MM-DD_HH-MM-SS",
                 bg=C["panel2"], fg=C["muted"],
                 font=('Helvetica',10)).pack(side='left')
        tk.Button(ts_row, text="Apply correction",
                  bg=C["accent_dark"], fg=C["btn_fg"],
                  activebackground=C["accent"],
                  font=('Helvetica',10), relief='flat', cursor='hand2',
                  command=lambda: self._apply_date_correction(idx)).pack(side='left', padx=(10,0))

        # Play button
        play_row = tk.Frame(card, bg=C["panel2"]); play_row.pack(fill='x', pady=(4,1))
        tk.Label(play_row, text=f"{'Preview':<22}", bg=C["panel2"],
                 fg=C["muted"], font=('Helvetica',10)).pack(side='left')
        tk.Button(play_row, text="▶  Play video", bg=C["accent_dark"], fg=C["btn_fg"],
                  activebackground=C["accent"], font=('Helvetica',9,'bold'),
                  relief='flat', cursor='hand2',
                  command=lambda p=v['path']: self._play_video(p)).pack(side='left')

        # Device row
        dr = tk.Frame(card, bg=C["panel2"]); dr.pack(fill='x', pady=1)
        tk.Label(dr, text=f"{'Device':<22}", bg=C["panel2"],
                 fg=C["muted"], font=('Helvetica',10)).pack(side='left')
        dev = v.get('device','Unknown')
        dev_col = C["warning"] if dev in GENERIC_DEVICES else C["accent_lite"]
        self._dev_lbl = tk.Label(dr, text=dev, bg=C["panel2"],
                                  fg=dev_col, font=('Helvetica',11))
        self._dev_lbl.pack(side='left')
        if 'device' in v.get('guessed',set()):
            _gs = v.get('guessed', set())
            if   'device_from_memo'     in _gs: src_txt = " from flight log (remembered)"
            elif 'device_from_srt'      in _gs: src_txt = " from SRT"
            elif 'device_from_path'     in _gs: src_txt = " from folder name"
            elif 'device_from_siblings' in _gs: src_txt = " from the other files here"
            else:                               src_txt = " auto-detected"
            tk.Label(dr, text=src_txt, bg=C["auto_badge"],
                     fg="#7ec8e3", font=('Helvetica',8,'bold'),
                     padx=4, pady=1).pack(side='left', padx=6)
        if dev in GENERIC_DEVICES and v.get('family'):
            hint = {"Mini 3 series": "Mini3 or Mini3Pro?",
                    "Mini 4 series": "Mini4Pro?"}.get(v['family'], "")
            tk.Label(dr, text=f" {v['family']} — {hint} ", bg=C["auto_badge"],
                     fg=C["warning"], font=('Helvetica',8,'bold'),
                     padx=4, pady=1).pack(side='left', padx=6)
        btn_lbl = "  ⚠ Select device  " if dev=="Unknown" else "  change  "
        btn_bg  = C["warning"] if dev=="Unknown" else C["panel"]
        tk.Button(dr, text=btn_lbl, bg=btn_bg, fg=C["btn_fg"],
                  activebackground=C["accent_lite"], font=('Helvetica',9,'bold'),
                  relief='flat', cursor='hand2',
                  command=lambda i=idx: self._pick_device(i)).pack(side='left', padx=(12,0))

        # Questions
        q = tk.Frame(self.content, bg=C["bg"]); q.pack(fill='x', pady=(4,0))

        # Training: determined solely by this video's title/duration, never carried
        pre_tr = v['is_training']
        self._is_training_var = tk.BooleanVar(value=pre_tr)
        tr_row = tk.Frame(q, bg=C["bg"]); tr_row.pack(anchor='w', pady=(0,6))
        tk.Label(tr_row, text=f"{TRAINING_LABEL} clip", bg=C["bg"], fg=C["text"],
                 font=('Helvetica',11,'bold')).pack(side='left', padx=(0,8))
        ttk.Checkbutton(tr_row, text=f"Mark as {TRAINING_LABEL} clip",
                        variable=self._is_training_var,
                        command=self._toggle_training_fields).pack(side='left')
        if 'is_training_short' in v.get('guessed',set()):
            tk.Label(tr_row, text=" auto — duration < 5 min",
                     bg=C["warning"], fg=C["entry_bg"],
                     font=('Helvetica',8,'bold'), padx=4).pack(side='left', padx=6)
        elif 'is_training' in v.get('guessed',set()):
            tk.Label(tr_row, text=" auto-detected",
                     bg=C["auto_badge"], fg="#7ec8e3",
                     font=('Helvetica',8,'bold'), padx=4).pack(side='left', padx=6)

        self._fields_frame = tk.Frame(q, bg=C["bg"]); self._fields_frame.pack(fill='x')

        # Carry values from previous video (site, group, device always carried)
        # Training is determined by duration/title only — not carried
        prev = self.videos[idx - 1] if idx > 0 else None

        def presel(key, fallback, choices=None):
            # Priority: current video's own value → previous video → config
            val = v.get(key) or ""
            if not val and prev:
                val = prev.get(key, "")
            if not val:
                val = self.cfg.get(f"last_{key}", fallback)
            # If the value isn't in the known list, keep it as a custom entry
            # rather than silently discarding it (show a warning badge instead).
            if choices and val and val not in choices:
                # Mark as custom so the UI can warn the user
                v.setdefault('guessed', set()).add(f'{key}_custom')
                # Still return it — the combobox will show empty but custom entry will be filled
                return ""
            return val

        # Site
        sr = tk.Frame(self._fields_frame, bg=C["bg"]); sr.pack(fill='x', pady=3)
        tk.Label(sr, text=SITE_LABEL, width=16, anchor='w', bg=C["bg"],
                 fg=C["text"], font=('Helvetica',11)).pack(side='left')
        # Determine whether the detected site value is in the known SITES list
        _site_detected = v.get('site', '')
        _site_in_list  = _site_detected in SITES
        self._site_var = tk.StringVar(value=presel('site', "", SITES))
        ttk.Combobox(sr, textvariable=self._site_var, values=SITES,
                     width=32, state='readonly').pack(side='left', padx=(0,8))
        # Pre-fill custom field if detected value is not in the standard list
        self._site_custom_var = tk.StringVar(
            value=_site_detected if (_site_detected and not _site_in_list) else "")
        tk.Entry(sr, textvariable=self._site_custom_var, width=20,
                 bg=C["entry_bg"], fg=C["text"],
                 insertbackground=C["text"], relief='flat').pack(side='left')
        tk.Label(sr, text="← custom / new site", bg=C["bg"],
                 fg=C["muted"], font=('Helvetica',9)).pack(side='left', padx=4)
        if 'site' in v.get('guessed', set()):
            badge_txt = " auto-detected (custom)" if not _site_in_list else " auto-detected"
            badge_bg  = C["warning"] if not _site_in_list else C["auto_badge"]
            tk.Label(sr, text=badge_txt, bg=badge_bg,
                     fg="#7ec8e3" if _site_in_list else C["entry_bg"],
                     font=('Helvetica',8,'bold'),
                     padx=4).pack(side='left', padx=6)

        # Group
        self._group_row = tk.Frame(self._fields_frame, bg=C["bg"])
        self._group_row.pack(fill='x', pady=3)
        gr = self._group_row
        tk.Label(gr, text=GROUP_LABEL, width=16, anchor='w', bg=C["bg"],
                 fg=C["text"], font=('Helvetica',11)).pack(side='left')
        self._group_var = tk.StringVar(value=presel('group',"",GROUP_STRUCTURES))
        ttk.Combobox(gr, textvariable=self._group_var, values=GROUP_STRUCTURES,
                     width=24, state='readonly').pack(side='left')
        if 'group' in v.get('guessed',set()):
            tk.Label(gr, text=" auto-detected", bg=C["auto_badge"],
                     fg="#7ec8e3", font=('Helvetica',8,'bold'),
                     padx=4).pack(side='left', padx=6)

        # Preview
        self._preview_lbl = tk.Label(self._fields_frame, text="",
                                      bg=C["bg"], fg=C["accent_lite"],
                                      font=('Courier',10), wraplength=900, anchor='w')
        self._preview_lbl.pack(anchor='w', pady=(8,0))

        for var in (self._site_var, self._site_custom_var, self._group_var, self._ts_var):
            var.trace_add('write', lambda *_: self._update_preview())
        self._is_training_var.trace_add('write', lambda *_: self._update_preview())
        self._toggle_training_fields()

    def _apply_date_correction(self, idx):
        """Apply date correction immediately to file + SRT from Step 2."""
        v = self.videos[idx]
        new_ts = self._ts_var.get().strip()
        try:
            new_dt = datetime.strptime(new_ts, '%Y-%m-%d_%H-%M-%S')
        except ValueError:
            messagebox.showerror("Invalid date",
                "Date format must be YYYY-MM-DD_HH-MM-SS"); return

        srt      = v.get('srt', {})
        old_dt   = srt.get('start_dt') or extract_timestamp_from_name(v['filename'])
        if old_dt is None:
            old_dt = new_dt   # no delta, just set

        if messagebox.askyesno("Apply date correction",
            f"Apply correction to file and SRT?\n\n"
            f"Old: {old_dt.strftime('%Y-%m-%d %H:%M:%S') if old_dt else 'unknown'}\n"
            f"New: {new_dt.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"This will immediately modify file timestamps and rewrite SRT dates."):

            result = apply_date_correction(
                v['path'], new_dt, old_dt or new_dt, self._ffmpeg)
            msgs = []
            msgs.append("✓ File timestamps updated" if result['utime'] else "✗ Could not update timestamps")
            msgs.append("✓ EXIF metadata written"   if result['exif']  else "✗ EXIF write failed")
            if result['srt_copy']: msgs.append(f"✓ SRT copy saved: {os.path.basename(result['srt_copy'])}")
            if result['errors']:   msgs.append("Errors:\n" + "\n".join(result['errors']))
            messagebox.showinfo("Date correction", "\n".join(msgs))

            # Store correction for Step 3
            self._date_corrections[idx] = (old_dt, new_dt)
            v['timestamp'] = new_ts

    def _pick_device(self, idx):
        dlg = DeviceDialog(self, self._all_devices)
        if dlg.result:
            dev = dlg.result
            self.videos[idx]['device'] = dev
            self._last_device = dev
            self._dev_lbl.config(text=dev, fg=C["accent_lite"])
            if dev not in self._all_devices:
                self._all_devices.append(dev)
                cl = self.cfg.get("custom_devices",[])
                if dev not in cl:
                    cl.append(dev); self.cfg["custom_devices"] = cl
                    save_config(self.cfg)
            self._update_preview()

    def _play_video(self, path):
        try:
            if platform.system() == "Windows": os.startfile(path)
            elif platform.system() == "Darwin": subprocess.Popen(["open", path])
            else: subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Cannot open video", str(e))

    def _go_back_one(self):
        self.current_idx -= 1; self._show_step_questions()

    def _go_back_to_last_question(self):
        self.current_idx = len(self.videos)-1; self._show_step_questions()

    def _toggle_training_fields(self):
        is_tr = self._is_training_var.get()
        for child in self._fields_frame.winfo_children():
            if child == self._preview_lbl:
                continue
            # Site row (index 0) stays enabled always; group row disabled when training
            is_group_row = (child == self._group_row if hasattr(self, '_group_row') else False)
            state = 'disabled' if (is_tr and is_group_row) else 'normal'
            for w in child.winfo_children():
                try:
                    w.config(state=state)
                except Exception:
                    pass
        self._update_preview()

    def _update_preview(self):
        v     = self.videos[self.current_idx]
        is_tr = self._is_training_var.get()
        site  = self._site_custom_var.get().strip() or self._site_var.get()
        grp   = self._group_var.get()
        ts    = self._ts_var.get().strip() if hasattr(self,'_ts_var') else v['timestamp']
        base  = build_new_basename(v['device'], ts, is_tr, group=grp, site=site)
        ext   = Path(v['filename']).suffix.upper()
        parts = NAMING.dest_parts(is_tr, {'site': site or '?', 'group': grp,
                                          'device': v['device'], 'timestamp': ts})
        try:
            self._preview_lbl.config(
                text=f"File:  {base}{ext}\n"
                     f"Path:  {'/'.join(parts)}/")
        except: pass

    def _step_questions_next(self):
        v     = self.videos[self.current_idx]
        is_tr = self._is_training_var.get()
        site  = self._site_custom_var.get().strip() or self._site_var.get()
        grp   = self._group_var.get()
        ts    = self._ts_var.get().strip() if hasattr(self,'_ts_var') else v['timestamp']

        if not site and _template_wants('site', is_tr):
            messagebox.showerror("Missing field",
                                 f"Please select or enter a {SITE_LABEL.lower()}."); return
        if v['device'] == "Unknown":
            messagebox.showwarning("Unknown device","Please select a device."); return
        # A generic device (e.g. DJI_Other) means only the family is known
        # (raw filename / SRT profile). Warn once — the real model is wanted.
        if v['device'] in GENERIC_DEVICES and not getattr(self, '_generic_dev_ack', False):
            if not messagebox.askyesno(
                    "Drone model not identified",
                    f"The model is only a guess from the filename: {v['device']}.\n\n"
                    "The real model comes from a DJI flight "
                    "record next to the clips, from a folder named after the drone, or "
                    "from “change” above.\n\n"
                    "Keep this name anyway for the rest of this run?"):
                return
            self._generic_dev_ack = True
        if not grp and _template_wants('group', is_tr):
            messagebox.showerror("Missing field",
                                 f"Please select a {GROUP_LABEL.lower()}."); return

        base     = build_new_basename(v['device'], ts, is_tr, group=grp, site=site)
        ext      = Path(v['path']).suffix.upper()
        new_name = base + ext
        old_path = v['path']                              # original path (before rename)
        src_dir  = os.path.dirname(old_path)
        new_path = os.path.join(src_dir, new_name)

        # ── Rename file immediately ───────────────────────────────────────
        if old_path != new_path:
            # Handle collision with an existing file that is NOT the source itself
            if os.path.exists(new_path) and os.path.normcase(old_path) != os.path.normcase(new_path):
                counter = 2
                while True:
                    cand = os.path.join(src_dir, f"{base}_{counter}{ext}")
                    if not os.path.exists(cand):
                        new_path = cand
                        new_name = os.path.basename(new_path)
                        base     = Path(new_path).stem
                        break
                    counter += 1
            try:
                # Rename companions first (while we still know the old stem),
                # and track the new .srt path so v['srt'] stays up to date.
                old_srt_path = (v.get('srt') or {}).get('srt_path')
                new_srt_path = None
                for comp in find_companion_files(old_path):
                    comp_new = os.path.join(src_dir, base + split_companion_name(comp)[1])
                    if comp != comp_new:
                        try:
                            os.rename(comp, comp_new)
                            # If this companion was the SRT, record its new path
                            if old_srt_path and os.path.normcase(comp) == os.path.normcase(old_srt_path):
                                new_srt_path = comp_new
                        except Exception:
                            pass
                os.rename(old_path, new_path)
            except Exception as e:
                messagebox.showerror("Rename failed",
                    f"Could not rename:\n{os.path.basename(old_path)}\n\n{e}")
                return
        else:
            new_path = old_path   # name unchanged, keep path as-is
            new_srt_path = None

        src = new_path   # src now points to the (possibly renamed) file

        v.update(is_training=is_tr, site=site, group=grp,
                 new_basename=base, timestamp=ts,
                 path=src, filename=new_name)
        # Keep v['srt']['srt_path'] in sync with the renamed companion file
        if new_srt_path and v.get('srt') is not None:
            v['srt']['srt_path'] = new_srt_path
        if v['device'] != 'Unknown':
            self._last_device = v['device']
        self.cfg.update({'last_site': site})
        if not is_tr: self.cfg['last_group'] = grp
        save_config(self.cfg)
        self.current_idx += 1
        self._advance_or_finish()

    def _skip_video(self):
        self._skipped.add(self.current_idx)
        # Carry site/group/device from the last *confirmed* video so that
        # presel() in the next step doesn't fall back to stale config values.
        last_confirmed = None
        for i in range(self.current_idx - 1, -1, -1):
            if i not in self._skipped:
                last_confirmed = self.videos[i]
                break
        v = self.videos[self.current_idx]
        if last_confirmed:
            if not v.get('site'):
                v['site'] = last_confirmed.get('site', '')
            if not v.get('group'):
                v['group'] = last_confirmed.get('group', '')
            if v.get('device', 'Unknown') == 'Unknown':
                v['device'] = last_confirmed.get('device', 'Unknown')
        self.current_idx += 1
        self._advance_or_finish()

    def _advance_or_finish(self):
        if self.current_idx < len(self.videos):
            self._show_step_questions()
        else:
            active = [v for i,v in enumerate(self.videos) if i not in self._skipped]
            if not active:
                messagebox.showwarning("No videos","All videos were skipped.")
                self._show_step_source(); return
            self.videos = active
            self._remove_skip_btn()
            self._show_step_rename()

    def _remove_skip_btn(self):
        ftr = self.btn_back.master
        for w in ftr.winfo_children():
            if getattr(w, '_is_skip_btn', False): w.destroy()

    def _browse(self, var):
        path = filedialog.askdirectory(title="Select folder")
        if path: var.set(path)

    # =========================================================================
    #  STEP 3 — Rename + Diagnostic
    # =========================================================================

    def _show_step_rename(self):
        self._clear_content(); self._set_step(3)
        self._remove_skip_btn()
        self.btn_back.config(state='normal', command=self._go_back_to_last_question)
        self.btn_next.config(state='disabled', text="Working…")

        self._label(self.content, "Step 3 — Renaming & Diagnostics",
                    fg=C["accent_lite"], font=('Helvetica',17,'bold')).pack(anchor='w', pady=(0,6))

        self._log = scrolledtext.ScrolledText(self.content, bg=C["entry_bg"],
            fg=C["text"], font=('Courier',10), relief='flat', state='disabled')
        self._log.pack(fill='both', expand=True)

        pf = tk.Frame(self.content, bg=C["bg"]); pf.pack(fill='x', pady=(8,0))
        self._prog = ttk.Progressbar(pf, length=400, mode='determinate',
                                      maximum=100, value=0)
        self._prog.pack(side='left', fill='x', expand=True)
        self._prog_lbl = tk.Label(pf, text="0 / 0", bg=C["bg"], fg=C["muted"],
                                   font=('Helvetica',9))
        self._prog_lbl.pack(side='left', padx=(8,0))
        self._btn_cancel = tk.Button(pf, text="✕  Cancel",
                                      bg=C["danger"], fg=C["btn_fg"],
                                      activebackground="#a03020",
                                      font=('Helvetica',9,'bold'),
                                      relief='flat', cursor='hand2',
                                      command=self._request_cancel)
        self._btn_cancel.pack(side='left', padx=(12,0))
        self._eta_lbl = tk.Label(self.content, text="", bg=C["bg"],
                                  fg=C["muted"], font=('Helvetica',9))
        self._eta_lbl.pack(anchor='w', pady=(2,0))

        self._cancel_flag.clear()
        self._queue = queue.Queue()
        # Snapshot every Tk variable the worker needs, HERE on the main thread.
        # tkinter variables are not thread-safe: reading one from a worker
        # raises "main thread is not in main loop", which killed the thread
        # before it copied anything.
        self._snap = {
            'do_flightlog':     self.do_flightlog.get(),
            'dji_api_key':      self.dji_api_key.get().strip(),
            'do_faststart':     self.do_faststart.get(),
            'do_reencode_hevc': self.do_reencode_hevc.get(),
        }
        self._rename_thread = self._start_worker(self._worker_rename, "rename")
        self._poll_queue()

    def _worker_rename(self):
        """
        Step 3: Full diagnostic + faststart/reencode IN PLACE.
        Files are ALREADY renamed in Step 2 (Save & next).
        """
        # Flight-log add-on: rebuild SRTs, enrich, set true model (before diagnostics)
        if HFL is not None and self._snap['do_flightlog']:
            try:
                key = self._snap['dji_api_key']
                if key: HFL.write_api_key(key)
                self.cfg['do_flightlog'] = True; save_config(self.cfg)
                self._flightlog_phase(key)
            except Exception as e:
                self._q_log(f"\n[flight-log] phase failed: {e}", C["warning"])
        total            = len(self.videos)
        do_fs            = self._snap['do_faststart']     and bool(self._ffmpeg)
        do_reencode_hevc = self._snap['do_reencode_hevc'] and bool(self._ffmpeg)
        self.renamed     = []

        for i, v in enumerate(self.videos):
            if self._cancel_flag.is_set():
                self._q_log(f"\n⚠  Cancelled after {i}/{total}.", C["warning"])
                self._queue.put(('cancelled', None))
                return

            src  = v['path']   # already renamed in Step 2
            name = v['filename']

            self._q_log(f"\n[{i+1}/{total}]  {name}", C["text"])
            self._q_update_prog(i / total, i + 1, total, "Analysing…")

            if not os.path.exists(src):
                self._q_log(f"         ✗  File not found: {src}", C["danger"])
                self.renamed.append({**v, 'status': 'error',
                                      'error': 'file not found after rename'})
                self._q_update_prog((i+1)/total, i+1, total, "")
                continue

            # ── 2. Full diagnostic ────────────────────────────────────────
            self._q_log(f"         📋  Diagnostic:", C["muted"])

            is_mp4 = Path(src).suffix.lower() in ('.mp4', '.mov')
            codec_info = {}
            if self._ffmpeg and is_mp4:
                # Large files (moov at end) can be slow to probe, especially on
                # network drives — give the probe more time before giving up.
                try:
                    _probe_to = 60 if get_file_size_mb(src) > 1500 else 20
                except Exception:
                    _probe_to = 20
                codec_info = detect_codec_threaded(self._ffmpeg, src, timeout=_probe_to)

            if codec_info:
                self._q_log(
                    f"             codec:     {codec_info.get('codec_name','?')} "
                    f"({codec_info.get('codec_tag','?')})", C["muted"])
                self._q_log(
                    f"             pix_fmt:   {codec_info.get('pix_fmt','?')}", C["muted"])
                if is_broken_h264_10bit(codec_info):
                    self._q_log(
                        "             ⚠  H.264 10-bit crash-recovery detected", C["warning"])
            else:
                self._q_log(f"             codec:     (ffprobe unavailable or N/A)", C["muted"])

            pos, frag = _detect_mp4_type(src, timeout=10) if is_mp4 else ('N/A', False)
            self._q_log(
                f"             moov:      {pos}{'  (fragmented)' if frag else ''}", C["muted"])

            # No moov atom found + ffmpeg cannot read codec → confirm whether the
            # file is an incomplete/corrupt recording that can never be played.
            corrupt = False
            if is_mp4 and pos == 'unknown' and not codec_info:
                if is_unplayable_mp4(self._ffmpeg, src):
                    corrupt = True
                    self._q_log(
                        "         ✗  NO MOOV ATOM — file is INCOMPLETE / CORRUPT "
                        "(interrupted recording).", C["danger"])
                    self._q_log(
                        "            It cannot be played, streamed or fixed by "
                        "faststart. Recover it (e.g. untrunc) before uploading.",
                        C["danger"])

            srt_data = v.get('srt', {})
            dur, dur_src = srt_data.get('duration_s'), "SRT"
            if dur is None:
                dur, dur_src = mp4_local_info(src)['duration_s'], "MP4 header"
            if dur is not None:
                mm, ss = divmod(int(dur), 60)
                self._q_log(f"             duration:  {mm}m {ss:02d}s  (from {dur_src})", C["muted"])

            fd = get_file_dates(src)
            if fd['created']:
                self._q_log(
                    f"             created:   {fd['created'].strftime('%Y-%m-%d %H:%M:%S')}",
                    C["muted"])
            if fd['modified']:
                self._q_log(
                    f"             modified:  {fd['modified'].strftime('%Y-%m-%d %H:%M:%S')}",
                    C["muted"])
            self._q_log(f"             size:      {get_file_size_mb(src):.1f} MB", C["muted"])

            # ── 3. Faststart / re-encode IN PLACE ────────────────────────
            broken = needs_reencode(codec_info)

            if do_fs and is_mp4:
                local_tmp_dir = os.path.join(os.path.dirname(src), "tmp_codec_move")
                os.makedirs(local_tmp_dir, exist_ok=True)

                if broken and do_reencode_hevc:
                    tmp_out = os.path.join(local_tmp_dir, "tmp_" + os.path.basename(src))
                    self._q_log(
                        f"         ⚙  Re-encoding ({SETTINGS['encoding']['reencode']['codec']}) in place…", C["warning"])

                    def _re_prog(pct, fps, eta, _i=i, _tot=total):
                        self._q_update_prog(
                            _i / _tot + pct * 0.8 / _tot, _i + 1, _tot,
                            f"Re-encoding… {fps:.0f} fps  ETA {eta}")

                    ok, msg = run_reencode_hevc(
                        self._ffmpeg, src, tmp_out, progress_cb=_re_prog)
                    if ok:
                        os.replace(tmp_out, src)
                        self._q_log("         ✓  Re-encoded in place", C["muted"])
                    else:
                        self._q_log(f"         ⚠  Re-encode failed: {msg}", C["warning"])
                        try:
                            os.remove(tmp_out)
                        except Exception:
                            pass

                elif not frag and pos == 'end':
                    ok_path, msg = run_faststart_local(self._ffmpeg, src, local_tmp_dir)
                    if ok_path:
                        os.replace(ok_path, src)
                        self._q_log("         ✓  Faststart done (moov moved to front)", C["muted"])
                    else:
                        self._q_log(f"         ⚠  Faststart failed: {msg}", C["warning"])

                elif pos == 'front':
                    self._q_log(
                        "         ✓  moov already at front — no faststart needed", C["muted"])

                elif frag:
                    self._q_log(
                        "         ⚠  Fragmented MP4 — faststart not applicable", C["warning"])

            self.renamed.append({
                **v,
                'path':        src,
                'filename':    name,
                'new_path':    src,
                'dest_folder': os.path.dirname(src),
                'status':      'ok',
                'codec_info':  codec_info,
                'moov_pos':    pos,
                'fragmented':  frag,
                'corrupt':     corrupt,
            })
            self._q_update_prog((i + 1) / total, i + 1, total, "")

        # Orphan companions
        if self.orphans:
            self._q_log("\n── Companion-only files (no matching video) ──", C["muted"])
            for orph in self.orphans:
                self._q_log(f"  {orph['filename']}", C["muted"])

        self._q_log("\n✓  Step 3 complete — renaming and in-place processing done.", C["success"])
        self._queue.put(('rename_done', None))

    # ---- Flight-log add-on (Step 3) ----------------------------------------
    def _clip_gps(self, v):
        srt = v.get('srt') or {}
        raw = srt.get('raw_content')
        if not raw and srt.get('srt_path'):
            try:
                with open(srt['srt_path'], 'r', encoding='utf-8', errors='replace') as f:
                    raw = f.read(1500)
            except Exception:
                raw = None
        if not raw:
            return None
        m = re.search(r'latitude:\s*([\-\d.]+)\]\s*\[longitude:\s*([\-\d.]+)', raw)
        if m:
            try: return (float(m.group(1)), float(m.group(2)))
            except Exception: return None
        return None

    def _rename_to_device(self, v, new_device):
        """Re-rename a video (+companions) to a new device token, keeping the rest."""
        try:
            base = build_new_basename(new_device, v['timestamp'], v.get('is_training', False),
                                      group=v.get('group'), site=v.get('site'))
            old_path = v['path']; src_dir = os.path.dirname(old_path)
            ext = Path(old_path).suffix
            new_path = os.path.join(src_dir, base + ext)
            if os.path.normcase(new_path) == os.path.normcase(old_path):
                return False
            if os.path.exists(new_path):
                return False
            old_srt = (v.get('srt') or {}).get('srt_path'); new_srt = None
            for comp in find_companion_files(old_path):
                comp_new = os.path.join(src_dir, base + split_companion_name(comp)[1])
                if comp != comp_new and not os.path.exists(comp_new):
                    try:
                        os.rename(comp, comp_new)
                        if old_srt and os.path.normcase(comp) == os.path.normcase(old_srt):
                            new_srt = comp_new
                    except Exception:
                        pass
            os.rename(old_path, new_path)
            v.update(device=new_device, new_basename=base, path=new_path, filename=base + ext)
            if new_srt and v.get('srt') is not None:
                v['srt']['srt_path'] = new_srt
            return True
        except Exception:
            return False

    def _flightlog_phase(self, key):
        """Decode DJI logs per session folder; set true model, rebuild missing
        SRTs, write .flightlog.csv sidecars."""
        if not self._dji_log_bin:
            self._q_log("\n[flight-log] dji-log binary not found for this OS - skipped.", C["warning"]); return
        if not key:
            self._q_log("\n[flight-log] no DJI API key entered - skipped.", C["warning"]); return
        self._q_log("\n-- Flight-log processing --", C["accent_lite"])
        folders = {}
        for v in self.videos:
            folders.setdefault(os.path.dirname(v['path']), []).append(v)
        for folder, vids in folders.items():
            self._q_log(f"\n  Folder: {folder}", C["text"])
            _ok, _err, _warn = HFL.validate_session(folder)
            for w in _warn: self._q_log(f"     - {w}", C["muted"])
            if not HFL.find_logs(folder):
                self._q_log("     -  No DJI flight record (DJIFlightRecord_*.txt) here or one "
                            "level down - no SRT rebuild, no telemetry sidecar.", C["warning"])
                devs = sorted({v.get('device', '?') for v in vids})
                self._q_log(f"        Drone model stays {', '.join(devs)} - taken from the "
                            "folder/file name, not from a log.", C["muted"])
                continue
            fl = self._folderlogs.get(folder)
            if fl is None:
                fl = HFL.FolderLogs(folder, self._dji_log_bin, key,
                                    log=lambda m: self._q_log(m, C["muted"]))
                self._folderlogs[folder] = fl
            if fl.errors:
                for e in fl.errors: self._q_log(f"     x  {e}", C["danger"])
                continue
            for w in fl.warnings: self._q_log(f"     !  {w}", C["warning"])
            clips = []
            for v in vids:
                srt = v.get('srt') or {}
                mvhd = None; dur = srt.get('duration_s')
                if not srt.get('start_dt'):
                    # No SRT: the MP4 header gives both. Without the duration
                    # calibrate_offset has nothing to pair on, and no clip
                    # without SRT would ever find its segment.
                    _d, _f, _n, mvhd = HFL.mp4_meta(v['path'])
                    dur = dur or _d
                clips.append({'v': v, 'srt_start': srt.get('start_dt'),
                              'mvhd': mvhd, 'duration': dur,
                              'name_ts': extract_timestamp_from_name(v['path']),
                              'gps': self._clip_gps(v)})
            fl.calibrate_offset(clips)
            memo_written = read_device_memo(folder)
            for c in clips:
                v = c['v']; name = os.path.basename(v['path'])
                dur = c['duration']; fps = nframes = None
                if not dur:
                    dur, fps, nframes, _ = HFL.mp4_meta(v['path'])
                anchor = fl.anchor(c)
                m = fl.match(anchor, dur, c['gps']) if anchor else None
                if not m:
                    self._q_log(f"     !  {name}: no matching flight-log segment.", C["warning"]); continue
                seg = m['seg']; pos = m['pos']
                model = HFL.model_token(seg['meta'])
                # Remember it once per folder: the logs get archived away from
                # the clips, and without them a later run can only guess the
                # family (Mini 3 series) and not the model (Mini3 vs Mini3Pro).
                if model and model != DJI_FALLBACK and model != memo_written:
                    if write_device_memo(folder, model):
                        memo_written = model
                        self._q_log(f"     .  drone model {model} recorded in "
                                    f"{DEVICE_MEMO_FILE} for later runs", C["muted"])
                if model and model != DJI_FALLBACK and model != v.get('device'):
                    old = v.get('device')
                    if self._rename_to_device(v, model):
                        self._q_log(f"     >  {name}: model {old} -> {model} (from log)", C["muted"])
                        name = os.path.basename(v['path'])
                srt = v.get('srt') or {}
                if not srt.get('srt_path'):
                    if fps is None:
                        _d, fps, nframes, _ = HFL.mp4_meta(v['path'])
                    clip_wall = seg['wall'] + timedelta(seconds=pos - seg['seg_a'])
                    srt_path = os.path.splitext(v['path'])[0] + ".SRT"
                    try:
                        with open(srt_path, "w", encoding="utf-8") as fh:
                            fh.write(HFL.reconstruct_srt_text(seg, pos, clip_wall, dur, fps, nframes))
                        v['srt'] = {'srt_path': srt_path, 'start_dt': clip_wall, 'duration_s': dur}
                        self._q_log(f"     +  {name}: SRT reconstructed from log", C["success"])
                    except Exception as e:
                        self._q_log(f"     !  {name}: SRT reconstruction failed: {e}", C["warning"])
                try:
                    sc = os.path.splitext(v['path'])[0] + ".flightlog.csv"
                    nrows = HFL.write_sidecar(sc, seg, pos, dur)
                    gd = f"  (GPS {m['gps_dist']:.0f} m)" if m.get('gps_dist') is not None else ""
                    self._q_log(f"     v  {name}: sidecar {nrows} rows{gd}", C["muted"])
                except Exception as e:
                    self._q_log(f"     !  {name}: sidecar failed: {e}", C["warning"])
        self._q_log("\n  Flight-log processing done.", C["success"])


    # =========================================================================
    #  STEP 4 — Copy (or auto-skip)
    # =========================================================================

    def _show_step_copy(self):
        self._clear_content(); self._set_step(4)
        self.btn_back.config(state='disabled')
        # Reset stale thread references from Step 3
        self._rename_thread = None
        self._proc_thread   = None

        mode = self.op_mode.get()
        if mode == "rename":
            self._label(self.content, "Step 4 — Copy",
                        fg=C["accent_lite"], font=('Helvetica',17,'bold')).pack(anchor='w')
            self._label(self.content,
                        "Rename-only mode selected — no copy needed.\nProceeding to summary…",
                        fg=C["muted"]).pack(anchor='w', pady=20)
            self.btn_next.config(state='disabled', text="Skipping…")
            self.after(1500, self._show_step_summary)
            return

        dest = self.dest_dir.get().strip()
        if not dest:
            messagebox.showerror("Error", "No destination folder selected.")
            return

        ok_files = [r for r in self.renamed if r.get('status') == 'ok']
        if not ok_files:
            messagebox.showwarning("Nothing to copy",
                "No successfully renamed files to copy.")
            self._show_step_summary()
            return

        # Check destination is accessible
        is_webdav = dest.startswith("http://") or dest.startswith("https://")
        if is_webdav:
            ok, err_msg = _webdav_test_connection(dest, timeout=10)
            if not ok:
                messagebox.showerror("Destination not accessible",
                    f"Cannot reach WebDAV destination:\n{dest}\n\n{err_msg}")
                return
        else:
            try:
                os.makedirs(dest, exist_ok=True)
                test = os.path.join(dest, '.fieldclip_test')
                with open(test, 'w') as f: f.write('ok')
                os.remove(test)
            except Exception as e:
                messagebox.showerror("Destination not accessible",
                    f"Cannot write to destination:\n{dest}\n\n{e}\n\n"
                    "Check the network connection or the mounted drive.")
                return

        self.btn_next.config(state='disabled', text="Copying…")
        self._label(self.content, "Step 4 — Copying files",
                    fg=C["accent_lite"], font=('Helvetica',17,'bold')).pack(anchor='w', pady=(0,6))

        self._log = scrolledtext.ScrolledText(self.content, bg=C["entry_bg"],
            fg=C["text"], font=('Courier',10), relief='flat', state='disabled')
        self._log.pack(fill='both', expand=True)

        pf = tk.Frame(self.content, bg=C["bg"]); pf.pack(fill='x', pady=(8,0))
        self._prog = ttk.Progressbar(pf, length=400, mode='determinate',
                                      maximum=100, value=0)
        self._prog.pack(side='left', fill='x', expand=True)
        self._prog_lbl = tk.Label(pf, text="0 / 0", bg=C["bg"], fg=C["muted"],
                                   font=('Helvetica',9))
        self._prog_lbl.pack(side='left', padx=(8,0))
        self._btn_cancel = tk.Button(pf, text="✕  Cancel",
                                      bg=C["danger"], fg=C["btn_fg"],
                                      activebackground="#a03020",
                                      font=('Helvetica',9,'bold'),
                                      relief='flat', cursor='hand2',
                                      command=self._request_cancel)
        self._btn_cancel.pack(side='left', padx=(12,0))

        self._eta_lbl = tk.Label(self.content, text="", bg=C["bg"],
                                  fg=C["muted"], font=('Helvetica',9))
        self._eta_lbl.pack(anchor='w', pady=(2,0))
        self._file_lbl = tk.Label(self.content, text="", bg=C["bg"],
                                   fg=C["muted"], font=('Helvetica',9))
        self._file_lbl.pack(anchor='w')

        self._cancel_flag.clear()
        self._queue = queue.Queue()
        # See the note in _show_step_rename: Tk variables must be read on the
        # main thread, not from inside the worker.
        self._snap = {'dest_root': dest}
        if is_webdav:
            self._proc_thread = self._start_worker(self._worker_copy_webdav, "upload")
        else:
            self._proc_thread = self._start_worker(self._worker_copy, "copy")
        self._poll_queue()

    def _ensure_mount_ready(self, dest_root: str, tries: int = 3) -> bool:
        """
        Before touching a file, make sure the destination is actually alive, and
        repair it if not.

        This matters because a dropped mount otherwise burns one of the five
        retry attempts per file - and each attempt on a 3.5 GB file is minutes
        of re-copying. Remounting first means the same file resumes cleanly
        instead of the export failing its way down the list.

        Returns True when the destination is usable.
        """
        if dest_root.startswith("http://") or dest_root.startswith("https://"):
            return True                      # nothing to mount over HTTP

        for attempt in range(tries):
            state = probe_path(dest_root, timeout=8)
            if state == 'MOUNTED':
                return True

            self._q_log(f"         ⚠  Destination {state.lower()} — "
                        f"repairing (attempt {attempt+1}/{tries})…", C["warning"])
            LOG.warn(f"destination {state} at {dest_root}, remounting")

            if EM is None:
                # No mount manager available: just wait and re-probe.
                time.sleep(5)
                continue

            try:
                cfg = EM.load_config()
                EM.do_remount(cfg)
            except Exception as e:
                LOG.error(f"remount failed: {e}")
            time.sleep(3)

        final = probe_path(dest_root, timeout=8)
        if final == 'MOUNTED':
            return True
        self._q_log(f"         ✗  Destination still unusable ({final}).", C["danger"])
        return False

    def _worker_copy(self):
        """
        Step 4: Plain copy of already-renamed and processed files to destination.
        Faststart/reencode already done in Step 3 in place.
        """
        dest_root   = self._snap['dest_root']
        total       = len(self.renamed)
        t_start     = time.time()
        mb_done_total = 0.0     # running total; see the O(n^2) note further down

        # Prevent the machine from sleeping during transfer (all OSes)
        restore_fn  = keep_awake("FieldClip copy in progress")
        # Start keepalive thread to maintain network connection
        _ka_stop    = threading.Event()
        _ka_thread  = threading.Thread(
            target=_network_keepalive_thread,
            args=(dest_root, _ka_stop), daemon=True)
        _ka_thread.start()

        try:
            for i, r in enumerate(self.renamed):
                if r.get('status') != 'ok':
                    continue
                if r.get('corrupt'):
                    self._q_log(f"\n[{i+1}/{total}]  {r.get('filename','?')}", C["text"])
                    self._q_log(
                        "         ✗  Skipped — incomplete/corrupt file (no moov atom). "
                        "Recover it before uploading.", C["danger"])
                    continue
                if self._cancel_flag.is_set():
                    self._q_log(f"\n⚠  Cancelled after {i}/{total}.", C["warning"])
                    self._queue.put(('cancelled', None))
                    return

                src  = r['path']
                name = r['filename']
                # Use filename as source of truth for training detection
                # (is_training field may be stale if renamed in Step 2)
                is_tr = NAMING.is_training_name(name) or bool(r.get('is_training'))
                final_dest = os.path.join(dest_root, *NAMING.dest_parts(is_tr, r))

                # Check the mount is alive BEFORE spending a retry attempt on it.
                if not self._ensure_mount_ready(dest_root):
                    self._q_log(f"\n[{i+1}/{total}]  {name}", C["text"])
                    self._q_log("         ✗  Skipped — destination unreachable.",
                                C["danger"])
                    r['status'] = 'error'; r['error'] = 'destination unreachable'
                    continue

                # makedirs used to sit outside any try. On a dropped SSHFS mount
                # it raises OSError, which killed the worker thread outright -
                # no 'done', no error, just a frozen UI.
                try:
                    os.makedirs(final_dest, exist_ok=True)
                except Exception as e:
                    self._q_log(f"\n[{i+1}/{total}]  {name}", C["text"])
                    self._q_log(f"         ✗  Cannot create {final_dest}: {e}", C["danger"])
                    LOG.error(f"makedirs failed for {final_dest}: {e}")
                    r['status'] = 'error'; r['error'] = f"destination unreachable: {e}"
                    continue
                dst = os.path.join(final_dest, name)

                self._q_log(f"\n[{i+1}/{total}]  {name}", C["text"])
                self._q_log(f"         → {final_dest}", C["muted"])
                self._queue.put(('file_info', f"[{i+1}/{total}]  {name}"))
                base_pct = i / total

                # Check destination — compare against actual dst, not source
                if os.path.exists(dst) and dst != src:
                    try:
                        src_sz = os.path.getsize(src)
                        dst_sz = os.path.getsize(dst)
                    except Exception:
                        src_sz = dst_sz = -1
                    if src_sz == dst_sz and src_sz > 0:
                        self._q_log(
                            "         ⏭  Already at destination (same size) — skipped", C["muted"])
                        r['new_path']    = dst
                        r['dest_folder'] = final_dest
                        self._q_update_prog((i + 1) / total, i + 1, total, "")
                        continue
                    else:
                        self._q_log(
                            f"         ⚠  Exists but different size ({dst_sz/(1024*1024):.1f} MB"
                            f" vs {src_sz/(1024*1024):.1f} MB source) — overwriting", C["warning"])

                # Plain copy with Windows-style dynamic progress
                self._q_update_prog(base_pct + 0.02 / total, i + 1, total, "Copying…")
                file_size = os.path.getsize(src) if os.path.exists(src) else 0
                file_t_start = time.time()

                # Throttle state for this file. Without it the callback posts two
                # messages per 4 MB chunk: 875 chunks for a 3.5 GB file, ~105 000
                # messages across a 60-file export, all of which the UI thread
                # has to render.
                _last_ui = [0.0]

                def _copy_cb(done_bytes, total_bytes,
                             _i=i, _tot=total, _bp=base_pct,
                             _fs=file_size, _name=name, _fts=file_t_start,
                             _last=_last_ui):
                    now = time.monotonic()
                    # Always let the final update through, so the bar lands on 100%.
                    if now - _last[0] < UI_UPDATE_INTERVAL and done_bytes < total_bytes:
                        return
                    _last[0] = now
                    elapsed_f = max(time.time() - _fts, 0.001)
                    speed_mb  = (done_bytes / elapsed_f) / (1024 * 1024)
                    pct_file  = done_bytes / max(total_bytes, 1)
                    overall   = _bp + pct_file / _tot
                    remaining_b = total_bytes - done_bytes
                    rate_b    = done_bytes / elapsed_f
                    eta_s     = remaining_b / max(rate_b, 1)
                    eta_str   = str(timedelta(seconds=int(eta_s)))
                    done_mb   = done_bytes / (1024 * 1024)
                    total_mb  = total_bytes / (1024 * 1024)
                    label     = (f"{done_mb:.0f} / {total_mb:.0f} MB  •  "
                                 f"{speed_mb:.1f} MB/s  •  ETA {eta_str}")
                    self._q_update_prog(overall, _i + 1, _tot, label)
                    self._queue.put(('file_info',
                        f"[{_i+1}/{_tot}]  {_name}  ({pct_file*100:.0f}%)"))

                # Copy with retry on network failure
                max_retries = 5
                retry_delay = 10
                copy_ok     = False

                for attempt in range(max_retries):
                    if self._cancel_flag.is_set():
                        self._queue.put(('cancelled', None))
                        return

                    try:
                        # overwrite on the first attempt, resume from our own
                        # .part on later ones. Never append to whatever happens
                        # to be sitting at dst.
                        status = copy_with_progress(src, dst,
                                                     progress_cb=_copy_cb,
                                                     overwrite=True,
                                                     resume=(attempt > 0))
                        copy_ok = True
                        break
                    except Exception as e:
                        err_msg = str(e)
                        if attempt < max_retries - 1:
                            self._q_log(
                                f"         ⚠  Copy error (attempt {attempt+1}/{max_retries}): "
                                f"{err_msg}", C["warning"])
                            self._q_log(
                                f"         ⏳  Retrying in {retry_delay}s… "
                                f"(or click 'Force retry' button)", C["muted"])
                            self._queue.put(('copy_error', (name, err_msg, attempt+1)))
                            # Reuse the single Event created in __init__.
                            # Rebinding it here raced with the UI thread reading
                            # self._retry_event in _show_retry_popup: the popup
                            # could set an Event nobody was waiting on any more.
                            self._retry_event.clear()
                            deadline = time.time() + retry_delay
                            while time.time() < deadline:
                                if self._cancel_flag.is_set():
                                    self._queue.put(('cancelled', None))
                                    return
                                if self._retry_event.is_set():
                                    break
                                time.sleep(0.5)
                        else:
                            self._q_log(
                                f"         ✗  Copy failed after {max_retries} attempts: "
                                f"{err_msg}", C["danger"])
                            r['status'] = 'error'
                            r['error']  = err_msg

                if copy_ok:
                    self._q_log(
                        f"         ✓  {status.capitalize()}  ({get_file_size_mb(dst):.1f} MB)",
                        C["success"])

                # Copy companions. Every stat here used to sit outside the try,
                # so a mount that dropped between two files killed the thread.
                for cr in find_companion_files(src):
                    try:
                        comp_dst = os.path.join(final_dest, os.path.basename(cr))
                        if (os.path.exists(comp_dst) and comp_dst != cr
                                and os.path.getsize(comp_dst) == os.path.getsize(cr)):
                            self._q_log(
                                f"         ⏭  Companion already exists — skipped: "
                                f"{os.path.basename(cr)}", C["muted"])
                        else:
                            # NOT shutil.copy2. WinFsp reports st_ino = 0 for
                            # every file on the mount, so os.path.samefile()
                            # answers True for any pair and shutil refuses with
                            # SameFileError. Companions were therefore never
                            # actually reaching ERDA - the failure was swallowed
                            # by this except and logged as a mere warning.
                            copy_with_progress(cr, comp_dst, overwrite=True)
                            self._q_log(
                                f"         ✓  Companion: {os.path.basename(cr)}", C["muted"])
                    except Exception as e:
                        self._q_log(f"         ⚠  Companion failed: {e}", C["warning"])

                r['new_path']    = dst
                r['dest_folder'] = final_dest

                # Running total instead of re-stat'ing every file copied so far.
                # The old expression was O(n^2) over the network: ~1 830 stat
                # calls across a 60-file export, each a round trip to ERDA.
                if copy_ok:
                    mb_done_total += file_size / (1024 * 1024)
                elapsed  = time.time() - t_start
                speed    = mb_done_total / max(elapsed, 0.001)
                avg_left = elapsed / (i + 1) * (total - i - 1)
                self._q_eta(speed, str(timedelta(seconds=int(avg_left))), i + 1, total)
                self._q_update_prog((i + 1) / total, i + 1, total, "")

            # Report honestly. The old line claimed success even when every
            # single file had failed.
            n_err = sum(1 for rr in self.renamed if rr.get('status') == 'error')
            n_ok  = sum(1 for rr in self.renamed if rr.get('new_path'))
            if n_err:
                self._q_log(f"\n⚠  Finished with {n_err} failure(s); "
                            f"{n_ok} file(s) copied.", C["warning"])
            else:
                self._q_log(f"\n✓  All {n_ok} file(s) copied.", C["success"])
            self._queue.put(('done', None))
        finally:
            _ka_stop.set()
            restore_fn()

    def _worker_copy_webdav(self):
        """
        Step 4 (WebDAV mode): Upload already-renamed files to a WebDAV server
        using HTTP PUT, with folder creation (MKCOL), skip-if-same-size, resume,
        and automatic retry.
        """
        dest_root  = self._snap['dest_root'].rstrip("/") + "/"
        total      = len(self.renamed)
        t_start    = time.time()
        chunk_size = 4 * 1024 * 1024  # 4 MB chunks
        mb_done_total = 0.0     # running total; see the note further down

        # Prevent the machine from sleeping during transfer (all OSes)
        restore_fn = keep_awake("FieldClip upload in progress")
        # Start keepalive thread
        _ka_stop   = threading.Event()
        _ka_thread = threading.Thread(
            target=_network_keepalive_thread,
            args=(dest_root, _ka_stop), daemon=True)
        _ka_thread.start()

        try:
            for i, r in enumerate(self.renamed):
                if r.get('status') != 'ok':
                    continue
                if r.get('corrupt'):
                    self._q_log(f"\n[{i+1}/{total}]  {r.get('filename','?')}", C["text"])
                    self._q_log(
                        "         ✗  Skipped — incomplete/corrupt file (no moov atom). "
                        "Recover it before uploading.", C["danger"])
                    continue
                if self._cancel_flag.is_set():
                    self._q_log(f"\n⚠  Cancelled after {i}/{total}.", C["warning"])
                    self._queue.put(('cancelled', None))
                    return

                src  = r['path']
                name = r['filename']
                is_tr  = NAMING.is_training_name(name) or bool(r.get('is_training'))
                parts  = NAMING.dest_parts(is_tr, r)

                # Build remote URL: dest_root / <layout folders> / filename
                # Ensure intermediate collections exist
                try:
                    col_url = _webdav_ensure_collection(
                        dest_root.rstrip("/"), parts, timeout=15)
                except Exception as e:
                    self._q_log(f"         ⚠  MKCOL failed: {e}", C["warning"])
                    col_url = dest_root + "".join(
                        urllib.parse.quote(p, safe="") + "/" for p in parts)

                file_url = col_url + urllib.parse.quote(name, safe="")

                self._q_log(f"\n[{i+1}/{total}]  {name}", C["text"])
                self._q_log(f"         → {col_url}", C["muted"])
                self._queue.put(('file_info', f"[{i+1}/{total}]  {name}"))

                try:
                    src_size = os.path.getsize(src)
                except Exception:
                    self._q_log(f"         ✗  Cannot read source file: {src}", C["danger"])
                    r['status'] = 'error'; r['error'] = 'source unreadable'
                    continue

                base_pct    = i / total
                max_retries = 5
                retry_delay = 15
                upload_ok   = False

                for attempt in range(max_retries):
                    if self._cancel_flag.is_set():
                        self._queue.put(('cancelled', None))
                        return

                    # Check if remote file already exists and its size
                    remote_size = _webdav_file_remote_size(file_url, timeout=10)

                    if remote_size == src_size:
                        # Size-only match. Same name and same length is almost
                        # always the same file, but not necessarily - step 6
                        # re-checks with a head/tail hash, which is what catches
                        # a re-recorded clip that happens to match in length.
                        self._q_log(
                            "         ⏭  Already on server (same size) — skipped "
                            "(content confirmed at step 6)", C["muted"])
                        upload_ok = True
                        r['new_path']    = file_url
                        r['dest_folder'] = col_url
                        break

                    # Many WebDAV servers (ERDA among them) do not honour
                    # Content-Range resume: a partial
                    # remote file can never be appended to (every resume attempt
                    # is dropped). Remove the incomplete file and re-upload the
                    # whole thing from byte 0 instead.
                    if 0 < remote_size < src_size:
                        self._q_log(
                            f"         🗑  Removing incomplete remote file "
                            f"({remote_size/(1024*1024):.1f} MB) and re-uploading…",
                            C["muted"])
                        ok_del, del_msg = _webdav_delete(file_url, timeout=30)
                        if not ok_del:
                            self._q_log(
                                f"         ⚠  Could not delete partial file ({del_msg}); "
                                "PUT will attempt to overwrite it", C["warning"])
                    resume_from = 0

                    file_t_start = time.time()

                    try:
                        with open(src, 'rb') as fi:
                            bytes_to_send = src_size

                            # Build a streaming iterator for the request body
                            # urllib.request does not support streaming PUT natively,
                            # so we use http.client directly.
                            parsed = urllib.parse.urlparse(file_url)
                            host   = parsed.netloc
                            path   = parsed.path
                            is_ssl = parsed.scheme == "https"

                            # Response timeout of 600 s, not 60. ERDA checksums a
                            # multi-GB upload after the last byte arrives, which
                            # took far longer than a minute: getresponse() timed
                            # out on files that had in fact been stored fine.
                            if is_ssl:
                                conn = http.client.HTTPSConnection(
                                    host, context=_ssl_context(), timeout=WEBDAV_RESPONSE_TIMEOUT)
                            else:
                                conn = http.client.HTTPConnection(host, timeout=WEBDAV_RESPONSE_TIMEOUT)

                            headers = {
                                "Content-Type":   "application/octet-stream",
                                "Content-Length": str(bytes_to_send),
                            }
                            # The streaming PUT bypasses urllib entirely, so the
                            # opener's credentials never apply here - the header
                            # has to be set explicitly or ERDA answers 401.
                            headers.update(_webdav_auth_header())

                            # The watchdog shuts the socket down if the machine sleeps or the
                            # stream stalls, so the blocked send()/getresponse() raises and the
                            # retry loop below starts a clean upload instead of hanging forever.
                            # shutdown() wakes the blocked call on Linux; on Windows it
                            # sometimes does not, close() does. On macOS neither wakes a
                            # blocked send(), so the body is also sent with a socket
                            # timeout just past `stall_after`: a stall ends there on every OS.
                            def _abort_conn(c=conn):
                                for stop in (lambda s: s.shutdown(socket.SHUT_RDWR),
                                             lambda s: s.close()):
                                    try: stop(c.sock)
                                    except Exception: pass

                            try:
                                with SuspendWatchdog(_abort_conn) as wd:
                                    try:
                                        conn.connect()
                                        conn.sock.settimeout(wd.send_timeout)
                                        conn.putrequest("PUT", path)
                                        for hk, hv in headers.items():
                                            conn.putheader(hk, hv)
                                        conn.endheaders()

                                        done_bytes = resume_from
                                        _last_ui   = 0.0
                                        while True:
                                            chunk = fi.read(chunk_size)
                                            if not chunk:
                                                break
                                            conn.send(chunk)
                                            wd.progress()
                                            done_bytes += len(chunk)

                                            # Throttled progress update (see UI_UPDATE_INTERVAL).
                                            _now = time.monotonic()
                                            if (_now - _last_ui < UI_UPDATE_INTERVAL
                                                    and done_bytes < src_size):
                                                continue
                                            _last_ui = _now
                                            elapsed_f = max(time.time() - file_t_start, 0.001)
                                            speed_mb  = ((done_bytes - resume_from) / elapsed_f) / (1024*1024)
                                            pct_file  = done_bytes / max(src_size, 1)
                                            overall   = base_pct + pct_file / total
                                            remaining = src_size - done_bytes
                                            eta_s     = remaining / max(
                                                (done_bytes - resume_from) / elapsed_f, 1)
                                            label = (
                                                f"{done_bytes/(1024*1024):.0f} / "
                                                f"{src_size/(1024*1024):.0f} MB  •  "
                                                f"{speed_mb:.1f} MB/s  •  "
                                                f"ETA {timedelta(seconds=int(eta_s))}")
                                            self._q_update_prog(overall, i + 1, total, label)
                                            self._queue.put(('file_info',
                                                f"[{i+1}/{total}]  {name}  ({pct_file*100:.0f}%)"))

                                        # ERDA checksums the file before answering: silence is
                                        # normal here, so only a suspend may abort the wait.
                                        wd.armed = False
                                        conn.sock.settimeout(WEBDAV_RESPONSE_TIMEOUT)
                                        response = conn.getresponse()
                                        resp_status = response.status
                                    except Exception as e:
                                        if wd.reason:
                                            raise IOError(wd.reason) from e
                                        raise
                            finally:
                                conn.close()

                            if resp_status in (200, 201, 204):
                                self._q_log(
                                    f"         ✓  Uploaded  ({src_size/(1024*1024):.1f} MB)"
                                    f"  HTTP {resp_status}", C["success"])
                                upload_ok = True
                                r['new_path']    = file_url
                                r['dest_folder'] = col_url
                                break
                            elif resp_status == 409:
                                raise IOError(
                                    "HTTP 409 — the destination folder does not "
                                    "exist on the server and could not be created. "
                                    f"Check the destination URL and the {SECRET_LABEL}.")
                            elif resp_status in (401, 403):
                                raise IOError(
                                    f"HTTP {resp_status} — authentication refused. "
                                    f"Check the {SETTINGS['remote']['secret_label']}.")
                            elif resp_status == 507:
                                raise IOError(
                                    "HTTP 507 — no space left on the server.")
                            else:
                                raise IOError(f"HTTP {resp_status} from server")

                    except Exception as e:
                        err_msg = str(e)
                        # Authentication and configuration errors will not fix
                        # themselves: retrying five times at 15 s apart burns
                        # 75 s per file for nothing.
                        if any(t in err_msg for t in
                               ("401", "403", "409", "507", "authentication refused")):
                            self._q_log(f"         ✗  {err_msg}", C["danger"])
                            self._q_log("         (not retrying — this will not "
                                        "resolve on its own)", C["muted"])
                            LOG.error(f"permanent upload error for {name}: {err_msg}")
                            r['status'] = 'error'; r['error'] = err_msg
                            break
                        if attempt < max_retries - 1:
                            self._q_log(
                                f"         ⚠  Upload error (attempt {attempt+1}/{max_retries}): "
                                f"{err_msg}", C["warning"])
                            self._q_log(
                                f"         ⏳  Retrying in {retry_delay}s…", C["muted"])
                            self._queue.put(('copy_error', (name, err_msg, attempt+1)))
                            # Reuse the single Event created in __init__.
                            # Rebinding it here raced with the UI thread reading
                            # self._retry_event in _show_retry_popup: the popup
                            # could set an Event nobody was waiting on any more.
                            self._retry_event.clear()
                            deadline = time.time() + retry_delay
                            while time.time() < deadline:
                                if self._cancel_flag.is_set():
                                    self._queue.put(('cancelled', None))
                                    return
                                if self._retry_event.is_set():
                                    break
                                time.sleep(0.5)
                        else:
                            self._q_log(
                                f"         ✗  Upload failed after {max_retries} attempts: "
                                f"{err_msg}", C["danger"])
                            r['status'] = 'error'
                            r['error']  = err_msg

                # Upload companion files (.SRT, .LRF) via WebDAV PUT too
                if upload_ok:
                    for comp in find_companion_files(src):
                        comp_name = os.path.basename(comp)
                        comp_url  = col_url + urllib.parse.quote(comp_name, safe="")
                        remote_comp_sz = _webdav_file_remote_size(comp_url, timeout=8)
                        try:
                            local_comp_sz = os.path.getsize(comp)
                        except Exception:
                            continue
                        if remote_comp_sz == local_comp_sz:
                            self._q_log(
                                f"         ⏭  Companion already on server — skipped: {comp_name}",
                                C["muted"])
                            continue
                        try:
                            with open(comp, 'rb') as fc:
                                comp_data = fc.read()
                            _webdav_request(
                                comp_url, "PUT", data=comp_data,
                                headers={"Content-Type": "application/octet-stream",
                                         "Content-Length": str(len(comp_data))},
                                timeout=120)
                            self._q_log(
                                f"         ✓  Companion: {comp_name}", C["muted"])
                        except Exception as e:
                            self._q_log(
                                f"         ⚠  Companion upload failed: {e}", C["warning"])

                # Running total. The old expression summed the CURRENT file's
                # size once per already-uploaded file - i.e. src_size * n, which
                # was not the transferred volume by any reading, and cost a HEAD
                # round trip per entry on top.
                if upload_ok:
                    mb_done_total += src_size / (1024 * 1024)
                elapsed  = time.time() - t_start
                speed    = mb_done_total / max(elapsed, 0.001)
                avg_left = (elapsed / (i + 1)) * (total - i - 1)
                self._q_eta(speed, str(timedelta(seconds=int(avg_left))), i + 1, total)
                self._q_update_prog((i + 1) / total, i + 1, total, "")

            n_err = sum(1 for rr in self.renamed if rr.get('status') == 'error')
            n_ok  = sum(1 for rr in self.renamed if rr.get('new_path'))
            if n_err:
                self._q_log(f"\n⚠  Finished with {n_err} failure(s); "
                            f"{n_ok} file(s) uploaded.", C["warning"])
            else:
                self._q_log(f"\n✓  All {n_ok} file(s) uploaded.", C["success"])
            self._queue.put(('done', None))
        finally:
            _ka_stop.set()
            restore_fn()

    # ── Thread-safe helpers ───────────────────────────────────────────────────

    def _log_write(self, text, color=None):
        self._log.config(state='normal')
        tag = color or 'default'
        self._log.tag_configure(tag, foreground=color or C["text"])
        self._log.insert('end', text+'\n', tag)
        self._log.see('end')
        self._log.config(state='disabled')

    def _request_cancel(self):
        self._cancel_flag.set()
        try: self._btn_cancel.config(state='disabled', text="Cancelling…")
        except: pass

    def _q_log(self, text, color=None):
        self._queue.put(('log', (text, color)))

    def _q_update_prog(self, frac, done_n, total_n, label):
        self._queue.put(('progress', (frac, done_n, total_n, label)))

    def _q_eta(self, speed_mb, eta_str, done, total):
        self._queue.put(('eta', (speed_mb, eta_str, done, total)))

    # ── Worker lifecycle ──────────────────────────────────────────────────────

    def _start_worker(self, fn, kind):
        """
        Launch a worker thread that ALWAYS reports how it ended.

        Previously the workers had a try/finally with no except: any unforeseen
        exception (a dropped SSHFS mount inside os.makedirs, say) killed the
        thread silently, no terminal message was ever posted, and the UI sat on
        "Copying…" with Next disabled until the user killed the app.
        """
        def _guarded():
            try:
                fn()
            except Exception as e:
                tb = LOG.exception(f"worker {kind} crashed", e)
                self._queue.put(('fatal', (kind, str(e), tb)))
            finally:
                # Safety net: if fn() returned without posting a terminal
                # message, say so rather than leaving the UI waiting forever.
                self._queue.put(('worker_exit', kind))

        self._terminal_received = False
        self._worker_dead_at    = None
        LOG.info(f"worker {kind} starting")
        t = threading.Thread(target=_guarded, daemon=True, name=f"worker-{kind}")
        t.start()
        return t

    def _finish_transfer_ui(self, next_text, next_cmd, msg=None, color=None):
        """Put the UI back in a usable state, whatever happened."""
        self._terminal_received = True
        try:    self._prog['value'] = 100 if msg is None else self._prog['value']
        except Exception: pass
        try:    self._btn_cancel.config(state='disabled')
        except Exception: pass
        try:    self._file_lbl.config(text="")
        except Exception: pass
        if msg:
            try: self._eta_lbl.config(text=msg, fg=color or C["warning"])
            except Exception: pass
        self.btn_next.config(state='normal', text=next_text, command=next_cmd)

    # Cap on messages drained per tick. The progress callback can post thousands
    # of updates while the UI thread is busy; without a cap, one tick could block
    # the event loop long enough to look like a hang of its own.
    MAX_MESSAGES_PER_TICK = 200

    def _poll_queue(self):
        drained = 0
        while drained < self.MAX_MESSAGES_PER_TICK:
            try:
                msg_type, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            drained += 1
            if msg_type == 'log':
                self._log_write(*payload)
            elif msg_type == 'progress':
                frac, done_n, total_n, label = payload
                pct = max(0.0, min(1.0, frac)) * 100
                self._prog['value'] = pct
                lbl = f"{done_n} / {total_n}  ({pct:.0f}%)"
                if label: lbl += f"  —  {label}"
                self._prog_lbl.config(text=lbl)
            elif msg_type == 'eta':
                speed, eta, done, total = payload
                self._eta_lbl.config(
                    text=f"Copying  •  {speed:.1f} MB/s  •  ETA {eta}  •  {done}/{total} files")
            elif msg_type == 'file_info':
                try: self._file_lbl.config(text=payload)
                except: pass
            elif msg_type == 'copy_error':
                name, err_msg, attempt = payload
                try:
                    self._eta_lbl.config(
                        text=f"⚠  Network error — retrying automatically…  ({attempt}/5)")
                except: pass
                # Show popup with Force Retry button (non-blocking)
                self._show_retry_popup(name, err_msg)
            elif msg_type == 'rename_done':
                self._terminal_received = True
                self._prog['value'] = 100
                try: self._btn_cancel.config(state='disabled')
                except: pass
                self.btn_next.config(state='normal', text="Continue to copy →",
                                     command=self._show_step_copy)
                LOG.info("step 3 complete")
                # Auto-advance to Step 4 after 2 minutes if the user has not clicked
                self._auto_advance_timer = self.after(120_000, self._auto_advance_copy)
                return
            elif msg_type == 'done':
                LOG.info("transfer complete")
                self._finish_transfer_ui("View summary →", self._show_step_summary)
                return
            elif msg_type == 'cancelled':
                LOG.info("transfer cancelled by user")
                self._finish_transfer_ui("View partial summary →",
                                         self._show_step_summary,
                                         msg="Cancelled.")
                self.btn_back.config(state='normal',
                                     command=self._go_back_to_last_question)
                return
            elif msg_type == 'fatal':
                kind, err, tb = payload
                self._finish_transfer_ui(
                    "View partial summary →", self._show_step_summary,
                    msg=f"⚠  Interrupted by an error — see {LOG.path()}",
                    color=C["danger"])
                self._log_write(f"\n✗  {kind} stopped on an unexpected error:\n{err}",
                                C["danger"])
                self._show_fatal_popup(kind, err, tb)
                return
            elif msg_type == 'worker_exit':
                # The worker returned. If it already posted a terminal message
                # this is a no-op; otherwise it ended without saying so, and the
                # watchdog below will unblock the UI rather than hang forever.
                if not self._terminal_received and self._worker_dead_at is None:
                    self._worker_dead_at = time.time()
                    LOG.warn(f"worker {payload} exited without a terminal message")

        # Keep polling until a terminal message actually arrives. A dead thread
        # is NOT proof that the work finished - it may have died before posting
        # anything at all. The old code stopped polling on is_alive() == False,
        # which is exactly how the UI ended up stuck on "Copying…" forever.
        if self._terminal_received:
            return

        if self._worker_dead_at is not None and (time.time() - self._worker_dead_at) > 5:
            LOG.error("worker died without reporting - releasing the UI")
            self._finish_transfer_ui(
                "View partial summary →", self._show_step_summary,
                msg=f"⚠  Transfer interrupted — see {LOG.path()}",
                color=C["danger"])
            return

        self.after(50, self._poll_queue)

    def _show_retry_popup(self, filename: str, error: str):
        """Non-blocking popup shown on network error during copy."""
        try:
            popup = tk.Toplevel(self)
            popup.title("Connection error")
            popup.configure(bg=C["bg"])
            popup.geometry("480x200")
            popup.transient(self)
            popup.update_idletasks()
            px = self.winfo_rootx() + self.winfo_width()//2 - 240
            py = self.winfo_rooty() + self.winfo_height()//2 - 100
            popup.geometry(f"+{px}+{py}")

            tk.Label(popup, text="⚠  Network error during copy",
                     bg=C["bg"], fg=C["warning"],
                     font=('Helvetica',13,'bold')).pack(pady=(16,4))
            tk.Label(popup, text=f"File: {filename}",
                     bg=C["bg"], fg=C["text"],
                     font=('Helvetica',10)).pack()
            tk.Label(popup, text=error, bg=C["bg"], fg=C["muted"],
                     font=('Helvetica',9), wraplength=440).pack(pady=(4,12))

            btn_row = tk.Frame(popup, bg=C["bg"]); btn_row.pack()

            def _force_retry():
                self._retry_event.set()
                popup.destroy()

            tk.Button(btn_row, text="Force retry now",
                      bg=C["accent"], fg=C["btn_fg"],
                      font=('Helvetica',10,'bold'), relief='flat',
                      cursor='hand2', command=_force_retry).pack(side='left', padx=8)
            tk.Button(btn_row, text="Cancel copy",
                      bg=C["danger"], fg=C["btn_fg"],
                      font=('Helvetica',10), relief='flat',
                      cursor='hand2',
                      command=lambda: [self._request_cancel(), popup.destroy()]).pack(side='left')

            # Auto-close after 10s
            popup.after(10_000, lambda: popup.destroy() if popup.winfo_exists() else None)
        except Exception:
            pass

    def _show_fatal_popup(self, kind: str, err: str, tb: str):
        """
        Shown when a worker died on an unforeseen error.

        The point is that the UI stays usable and the user can resume: files
        already transferred at the right size are skipped on the next run, so
        restarting costs only the files that had not made it.
        """
        try:
            popup = tk.Toplevel(self)
            popup.title("Transfer interrupted")
            popup.configure(bg=C["bg"])
            popup.geometry("680x420")
            popup.transient(self)

            tk.Label(popup, text=f"✗  The {kind} step stopped on an unexpected error",
                     bg=C["bg"], fg=C["danger"],
                     font=('Helvetica', 13, 'bold')).pack(pady=(16, 4))
            tk.Label(popup, text=err, bg=C["bg"], fg=C["text"],
                     font=('Helvetica', 10), wraplength=630).pack(pady=(0, 8))

            box = scrolledtext.ScrolledText(popup, bg=C["entry_bg"], fg=C["muted"],
                                            font=('Courier', 9), relief='flat',
                                            height=12)
            box.pack(fill='both', expand=True, padx=14)
            box.insert('end', tb or "(no traceback)")
            box.config(state='disabled')

            tk.Label(popup, text=f"Full log: {LOG.path()}",
                     bg=C["bg"], fg=C["muted"],
                     font=('Helvetica', 9)).pack(pady=(6, 4))

            row = tk.Frame(popup, bg=C["bg"]); row.pack(pady=(0, 12))
            tk.Button(row, text="Resume transfer", bg=C["accent"], fg=C["btn_fg"],
                      font=('Helvetica', 10, 'bold'), relief='flat', cursor='hand2',
                      command=lambda: [popup.destroy(), self._show_step_copy()]
                      ).pack(side='left', padx=8)
            tk.Button(row, text="View partial summary", bg=C["panel2"], fg=C["text"],
                      font=('Helvetica', 10), relief='flat', cursor='hand2',
                      command=lambda: [popup.destroy(), self._show_step_summary()]
                      ).pack(side='left', padx=8)
            tk.Button(row, text="Close", bg=C["panel2"], fg=C["muted"],
                      font=('Helvetica', 10), relief='flat', cursor='hand2',
                      command=popup.destroy).pack(side='left', padx=8)
        except Exception:
            # A popup failing must never mask the original error.
            LOG.exception("could not show the fatal-error popup")

    def _auto_advance_copy(self):
        """Auto-advance to Step 4 (copy) 2 min after Step 3 (rename) completes."""
        # Only advance if Step 3 is still displayed (button still says "Continue to copy")
        try:
            if self.btn_next.cget('text') == "Continue to copy →":
                self._show_step_copy()
        except Exception:
            pass

    # =========================================================================
    #  STEP 5 — Summary
    # =========================================================================

    def _show_step_summary(self):
        self._clear_content(); self._set_step(5)
        self.btn_back.config(state='disabled')
        self.btn_next.config(text="Verify & validate →", command=self._show_step_validate)

        ok_list  = [r for r in self.renamed if r.get('status')=='ok']
        err_list = [r for r in self.renamed if r.get('status')=='error']
        src_vids, _ = count_files_in_folder(self.source_dir.get())
        mode   = self.op_mode.get()
        action = "Renamed" if mode=="rename" else "Copied"

        inner = make_scrollable(self.content)
        self._label(inner, "Step 5 — Summary",
                    fg=C["accent_lite"], font=('Helvetica',17,'bold')).pack(anchor='w', pady=(0,12))

        kpi = tk.Frame(inner, bg=C["panel2"], pady=10); kpi.pack(fill='x', pady=(0,12))
        def kpi_col(lbl, val, col=None):
            f = tk.Frame(kpi, bg=C["panel2"]); f.pack(side='left', padx=20)
            tk.Label(f, text=lbl, bg=C["panel2"], fg=C["muted"], font=('Helvetica',9)).pack()
            tk.Label(f, text=str(val), bg=C["panel2"],
                     fg=col or C["text"], font=('Helvetica',20,'bold')).pack()

        kpi_col("Source videos", src_vids, C["muted"])
        kpi_col(f"{action} ✓",   len(ok_list),  C["success"])
        kpi_col("Errors",         len(err_list),
                C["danger"] if err_list else C["muted"])

        if err_list:
            tk.Label(inner, text=f"⚠  {len(err_list)} file(s) failed — do NOT delete originals!",
                     bg=C["bg"], fg=C["warning"],
                     font=('Helvetica',12,'bold')).pack(anchor='w', pady=(0,8))
            for r in err_list:
                tk.Label(inner, text=f"   ✗  {r['filename']}  — {r.get('error','')}",
                         bg=C["bg"], fg=C["danger"]).pack(anchor='w')

        tk.Label(inner, text=f"By {SITE_LABEL.lower()}", bg=C["bg"], fg=C["text"],
                 font=('Helvetica',11,'bold')).pack(anchor='w', pady=(12,2))
        site_stats = {}
        for r in ok_list:
            k = r.get('site','?') if not r.get('is_training') else f"_{TRAINING_LABEL}"
            site_stats[k] = site_stats.get(k,0) + 1
        t1 = ttk.Treeview(inner, columns=('s','v'), show='headings', height=5)
        t1.heading('s',text=SITE_LABEL); t1.heading('v',text='Videos')
        t1.column('s',width=300);   t1.column('v',width=80,anchor='center')
        for k,n in sorted(site_stats.items()): t1.insert('','end',values=(k,n))
        t1.pack(fill='x', pady=(0,8))

        tk.Label(inner, text=f"By {GROUP_LABEL.lower()}", bg=C["bg"], fg=C["text"],
                 font=('Helvetica',11,'bold')).pack(anchor='w', pady=(4,2))
        grp_stats = {}
        for r in ok_list:
            if not r.get('is_training'): grp_stats[r.get('group','?')] = grp_stats.get(r.get('group','?'),0)+1
        t2 = ttk.Treeview(inner, columns=('g','v'), show='headings', height=5)
        t2.heading('g',text=GROUP_LABEL); t2.heading('v',text='Videos')
        t2.column('g',width=260); t2.column('v',width=80,anchor='center')
        for g,n in sorted(grp_stats.items()): t2.insert('','end',values=(g,n))
        t2.pack(fill='x')

    # =========================================================================
    #  STEP 6 — Verify + optional delete
    # =========================================================================

    def _show_step_validate(self):
        self._clear_content(); self._set_step(6)
        self.btn_back.config(state='normal', command=self._show_step_summary)
        self.btn_next.config(text="Finish", command=self._finish)

        errors = [r for r in self.renamed if r.get('status')=='error']
        ok     = [r for r in self.renamed if r.get('status')=='ok']
        mode   = self.op_mode.get()

        inner = make_scrollable(self.content)
        self._label(inner, "Step 6 — Verification & clean-up",
                    fg=C["accent_lite"], font=('Helvetica',17,'bold')).pack(anchor='w', pady=(0,10))

        if errors:
            tk.Label(inner, text=f"⚠  {len(errors)} file(s) failed.",
                     bg=C["bg"], fg=C["warning"],
                     font=('Helvetica',12,'bold')).pack(anchor='w', pady=(0,8))
            for r in errors:
                tk.Label(inner, text=f"   ✗  {r['filename']}  — {r.get('error','')}",
                         bg=C["bg"], fg=C["danger"]).pack(anchor='w')
        else:
            action = "renamed" if mode=="rename" else "copied and verified"
            tk.Label(inner, text=f"✓  All {len(ok)} video(s) {action} successfully.",
                     bg=C["bg"], fg=C["success"],
                     font=('Helvetica',12,'bold')).pack(anchor='w', pady=(0,10))

        if mode == "copy" and ok:
            tk.Label(inner, text="Integrity check (size + head/tail hash)",
                     bg=C["bg"], fg=C["text"],
                     font=('Helvetica',11,'bold')).pack(anchor='w', pady=(4,2))
            tk.Label(inner,
                     text="Comparing sizes alone is not enough: a partially "
                          "overwritten file can be exactly the right size and "
                          "still be corrupt.",
                     bg=C["bg"], fg=C["muted"],
                     font=('Helvetica',9), wraplength=760,
                     justify='left').pack(anchor='w', pady=(0,4))

            st = ttk.Treeview(inner, columns=('f','src','dst','note'),
                              show='headings', height=min(len(ok)+1,8))
            for col,txt,w in [('f','File',280),('src','Source MB',100),
                               ('dst','Dest MB',100),('note','Verification',220)]:
                st.heading(col,text=txt); st.column(col,width=w,
                           anchor=('w' if col in ('f','note') else 'center'))
            st.pack(fill='x', pady=(0,8))

            self._verify_tree   = st
            self._verify_passed = False
            self._verify_rows   = ok

            self._verify_status = tk.Label(
                inner, text="Verifying…", bg=C["bg"], fg=C["muted"],
                font=('Helvetica',10,'bold'))
            self._verify_status.pack(anchor='w', pady=(0,6))

            # Verification reads both ends of every file, which is slow over the
            # network - so it runs off the UI thread and streams its results in.
            threading.Thread(target=self._verify_worker, daemon=True).start()
            self._pump_verify_queue()

        if mode == "copy":
            ttk.Separator(inner, orient='horizontal').pack(fill='x', pady=10)
            tk.Label(inner, text="Verify before deleting originals:",
                     bg=C["bg"], fg=C["text"], font=('Helvetica',11)).pack(anchor='w', pady=(0,4))
            self._chk_count = tk.BooleanVar()
            self._chk_play  = tk.BooleanVar()
            self._chk_names = tk.BooleanVar()
            for txt, var in [
                ("All source videos appear in the destination folder", self._chk_count),
                ("I played at least one destination video — it streams fine", self._chk_play),
                ("The new filenames look correct",                             self._chk_names),
            ]:
                ttk.Checkbutton(inner, text=txt, variable=var).pack(anchor='w')

            ttk.Separator(inner, orient='horizontal').pack(fill='x', pady=10)
            tk.Label(inner, text="Optional — delete originals from source folder",
                     bg=C["bg"], fg=C["text"],
                     font=('Helvetica',11,'bold')).pack(anchor='w')
            tk.Label(inner, text="Tick to permanently DELETE originals when you click Finish.  ⚠  Irreversible.",
                     bg=C["bg"], fg=C["warning"], font=('Helvetica',10)).pack(anchor='w', pady=(2,0))
            self._delete_hint = tk.Label(
                inner, text="Locked until every file passes verification.",
                bg=C["bg"], fg=C["warning"], font=('Helvetica',9))
            self._delete_hint.pack(anchor='w', pady=(2,0))
            self._chk_delete = tk.BooleanVar()
            self._chk_delete_widget = ttk.Checkbutton(
                inner,
                text="Yes — delete originals from source after confirming above",
                variable=self._chk_delete, state='disabled')
            self._chk_delete_widget.pack(anchor='w', pady=(6,0))
            self._update_delete_gate()
        else:
            self._chk_delete = tk.BooleanVar(value=False)

    # ── Step 6 verification worker ────────────────────────────────────────────

    def _verify_worker(self):
        """Verify every transferred file, one queue message per result."""
        n_ok = 0
        try:
            for r in self._verify_rows:
                src = r['path']
                dst = r.get('new_path', '')
                s_mb = get_file_size_mb(src)
                if not dst:
                    self._queue.put(('verify_row',
                        (r['filename'], s_mb, 0.0, False, "no destination recorded")))
                    continue
                ok_v, detail = verify_transfer(src, dst)
                if dst.startswith("http"):
                    d_mb = max(_webdav_file_remote_size(dst), 0) / (1024*1024)
                else:
                    d_mb = get_file_size_mb(dst)
                if ok_v:
                    n_ok += 1
                else:
                    LOG.warn(f"verification failed for {r['filename']}: {detail}")
                self._queue.put(('verify_row',
                    (r['filename'], s_mb, d_mb, ok_v, detail)))
        except Exception as e:
            LOG.exception("verification worker crashed", e)
        finally:
            self._queue.put(('verify_done', (n_ok, len(self._verify_rows))))

    def _pump_verify_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == 'verify_row':
                    name, s_mb, d_mb, ok_v, detail = payload
                    self._verify_tree.insert(
                        '', 'end',
                        values=(name, f"{s_mb:.1f}", f"{d_mb:.1f}",
                                ("✓ verified" if ok_v else f"✗ {detail}")))
                elif kind == 'verify_done':
                    n_ok, n_tot = payload
                    self._verify_passed = (n_ok == n_tot and n_tot > 0)
                    if self._verify_passed:
                        self._verify_status.config(
                            text=f"✓  All {n_tot} file(s) verified byte-for-byte "
                                 f"at the edges.", fg=C["success"])
                    else:
                        self._verify_status.config(
                            text=f"✗  {n_tot - n_ok} of {n_tot} file(s) FAILED "
                                 f"verification — do NOT delete the originals.",
                            fg=C["danger"])
                    self._update_delete_gate()
                    return
        except queue.Empty:
            pass
        self.after(150, self._pump_verify_queue)

    def _update_delete_gate(self):
        """The delete checkbox stays disabled until verification has passed."""
        if not hasattr(self, "_chk_delete_widget"):
            return
        if getattr(self, "_verify_passed", False):
            self._chk_delete_widget.config(state='normal')
            self._delete_hint.config(
                text="Verification passed — deleting the originals is now allowed.",
                fg=C["muted"])
        else:
            self._chk_delete.set(False)
            self._chk_delete_widget.config(state='disabled')
            self._delete_hint.config(
                text="Locked until every file passes verification.",
                fg=C["warning"])

    def _finish(self):
        mode = self.op_mode.get()
        if mode == "copy" and hasattr(self,'_chk_delete') and self._chk_delete.get():
            if not getattr(self, "_verify_passed", False):
                messagebox.showerror(
                    "Verification did not pass",
                    "The integrity check did not pass for every file.\n\n"
                    "Deleting the originals is blocked.")
                return
            if not all([self._chk_count.get(), self._chk_play.get(), self._chk_names.get()]):
                messagebox.showwarning("Verification required",
                    "Please tick all three boxes before deleting source files."); return
            if not messagebox.askyesno("⚠  Delete originals?",
                "This will permanently delete the original video files.\n\nAre you absolutely sure?",
                icon='warning'): return

            deleted, errs = [], []
            for r in self.renamed:
                if r.get('status') != 'ok': continue
                for path in [r['path']] + find_companion_files(r['path']):
                    try:
                        if os.path.exists(path): os.remove(path); deleted.append(path)
                    except Exception as e:
                        errs.append(f"{os.path.basename(path)}: {e}")

            msg = f"Deleted {len(deleted)} file(s)."
            if errs: msg += "\n\nErrors:\n" + "\n".join(errs)
            messagebox.showinfo("Clean-up done", msg)

        messagebox.showinfo("Complete","All done!\nYou may now close the application.")
        self.quit()

    def _back(self): pass
    def _next(self): pass


# =============================================================================
#  ENTRY POINT
# =============================================================================
def main() -> int:
    LOG.install_excepthook()
    LOG.prune()
    LOG.session_start({
        "profile": SETTINGS.get("_path") or "(defaults)",
        "ffmpeg":  find_bundled_ffmpeg() or "(not found)",
        "flightlog": "yes" if HFL else "no",
        "mount":   "yes" if EM else "no",
    })
    try:
        app = App()
        app.mainloop()
    except Exception as exc:
        LOG.exception("fatal error at top level", exc)
        try:
            messagebox.showerror(
                "FieldClip — fatal error",
                f"{exc}\n\nFull details in:\n{LOG.path()}")
        except Exception:
            pass
        raise
    finally:
        LOG.info("session end")
    return 0


if __name__ == '__main__':
    main()
