#!/usr/bin/env python3
# =============================================================================
#  FieldClip                                                         log.py
#
#  File logging for FieldClip.
#
#  Why this exists: the app is launched from a double-clicked launcher, so
#  when Python raises, the traceback scrolls past and dies with the console
#  window. Nothing survives to diagnose an export that failed three hours in.
#  Everything worth knowing now also lands in logs/fieldclip_YYYY-MM-DD.log,
#  in the per-user config directory unless the profile sets logging.dir.
#  Known secrets are replaced by *** before anything is written.
#
#  Deliberately dependency-free and failure-proof: logging must never be the
#  thing that breaks a 200 GB transfer. Every call is wrapped; if the log cannot
#  be written, the app carries on in silence.
# =============================================================================

import os
import platform
import sys
import threading
import time
import traceback
from datetime import datetime

try:
    from . import settings as S
except ImportError:                       # run as a plain script
    import settings as S

PREFIX = "fieldclip_"

_LOCK     = threading.Lock()
_LOG_PATH = None
_STARTED  = False


def _log_dir() -> str:
    """logging.dir, else <user config dir>/logs. Falls back to the temp dir
    if not writable."""
    try:
        here = (S.resolve_path(S.active()["logging"].get("dir", ""))
                or os.path.join(S.user_config_dir(), "logs"))
    except Exception:
        here = os.path.join(S.user_config_dir(), "logs")
    try:
        os.makedirs(here, exist_ok=True)
        probe = os.path.join(here, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return here
    except Exception:
        import tempfile
        fallback = os.path.join(tempfile.gettempdir(), "fieldclip_logs")
        try:
            os.makedirs(fallback, exist_ok=True)
        except Exception:
            pass
        return fallback


def path() -> str:
    global _LOG_PATH
    if _LOG_PATH is None:
        _LOG_PATH = os.path.join(
            _log_dir(), f"{PREFIX}{datetime.now().strftime('%Y-%m-%d')}.log")
    return _LOG_PATH


def write(level: str, msg: str) -> None:
    """Append one line. Never raises."""
    try:
        for secret in {S.get_secret("remote_secret"), S.get_secret("dji_api_key")}:
            if secret:
                msg = msg.replace(secret, "***")
        line = (f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
                f"[{level:<7}] {msg}\n")
        with _LOCK:
            with open(path(), "a", encoding="utf-8", errors="replace") as f:
                f.write(line)
    except Exception:
        pass


def info(msg):  write("INFO", msg)
def warn(msg):  write("WARNING", msg)
def error(msg): write("ERROR", msg)


def exception(context: str, exc: BaseException = None) -> str:
    """Log a full traceback. Returns the formatted text so the caller can also
    show it in the UI."""
    tb = traceback.format_exc() if exc is None else "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__))
    write("ERROR", f"{context}\n{tb}")
    return tb


def session_start(extra: dict = None) -> None:
    """One banner per run, with the context needed to interpret what follows."""
    global _STARTED
    if _STARTED:
        return
    _STARTED = True
    write("INFO", "=" * 70)
    write("INFO", "FieldClip session start")
    write("INFO", f"  python   : {sys.version.split()[0]}")
    write("INFO", f"  platform : {platform.system()} {platform.release()}")
    write("INFO", f"  script   : {os.path.abspath(__file__)}")
    for k, v in (extra or {}).items():
        write("INFO", f"  {k:<9}: {v}")


def install_excepthook() -> None:
    """Catch anything that escapes to the top level, including inside threads.
    Without this, a crash in a worker thread vanishes with no trace at all."""
    prev = sys.excepthook

    def _hook(exc_type, exc, tb):
        write("ERROR", "UNCAUGHT EXCEPTION\n" + "".join(
            traceback.format_exception(exc_type, exc, tb)))
        if prev:
            prev(exc_type, exc, tb)

    sys.excepthook = _hook

    # threading.excepthook exists from 3.8, which is the documented minimum.
    if hasattr(threading, "excepthook"):
        prev_t = threading.excepthook

        def _thook(args):
            write("ERROR", f"UNCAUGHT EXCEPTION in thread {args.thread.name}\n"
                  + "".join(traceback.format_exception(
                      args.exc_type, args.exc_value, args.exc_traceback)))
            if prev_t:
                prev_t(args)

        threading.excepthook = _thook


def prune(keep_days: int = None) -> None:
    """Drop logs older than keep_days so the folder does not grow forever."""
    try:
        if keep_days is None:
            keep_days = int(S.active()["logging"].get("retention_days", 30))
        cutoff = time.time() - keep_days * 86400
        d = _log_dir()
        for name in os.listdir(d):
            if not name.startswith(PREFIX) or not name.endswith(".log"):
                continue
            p = os.path.join(d, name)
            if os.path.getmtime(p) < cutoff:
                os.remove(p)
    except Exception:
        pass
