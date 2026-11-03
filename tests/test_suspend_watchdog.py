#!/usr/bin/env python3
"""
Suspend / stall tests for the WebDAV upload.

The incident: the laptop's idle timer suspended it mid-upload. The TCP
connection to ERDA died, and the PUT then blocked in send() for 40+ minutes
because socket timeouts run on the monotonic clock, which stops during sleep.
keep_awake() now inhibits sleep on Linux/macOS too, and SuspendWatchdog
shuts the socket down so the retry loop gets a chance.
"""

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import RV

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def blocked_send(stall_after=1.0, fake_time=None):
    """Fill a socketpair nobody reads; return (error, reason, seconds)."""
    a, b = socket.socketpair()
    a.settimeout(600)                       # like the real PUT connection

    def abort():                            # same as the app's _abort_conn
        try: a.shutdown(socket.SHUT_RDWR)
        except Exception: pass
        try: a.close()
        except Exception: pass

    real_time = RV.time
    if fake_time:
        RV.time = fake_time
    err, t0 = None, time.monotonic()
    try:
        with RV.SuspendWatchdog(abort, stall_after=stall_after, poll=0.2) as wd:
            try:
                while True:
                    a.send(b"x" * 65536)
                    wd.progress()
            except Exception as e:
                err = e
    finally:
        RV.time = real_time
        a.close(); b.close()
    return err, wd.reason, time.monotonic() - t0


print("\n== stall: send() blocked on a peer that never reads ==")
err, reason, dt = blocked_send(stall_after=1.0)
check("blocked send is aborted", err is not None, repr(err))
check("aborted within a few seconds", dt < 5, f"{dt:.1f}s")
check("reason says stalled", reason and "stalled" in reason, reason)


print("\n== suspend: wall clock jumps ahead of monotonic ==")
class _Jumping:
    """time module whose wall clock leaps 1 h forward after 0.5 s."""
    def __init__(self):
        self._t0 = time.monotonic()
    def time(self):
        jump = 3600 if time.monotonic() - self._t0 > 0.5 else 0
        return time.time() + jump
    def __getattr__(self, n):
        return getattr(time, n)

err, reason, dt = blocked_send(stall_after=999, fake_time=_Jumping())
check("suspend aborts the send", err is not None, repr(err))
check("reason says sleep", reason and "sleep" in reason, reason)


print("\n== reasons never look like a permanent HTTP error ==")
# Mirrors the substring test in _worker_copy_webdav's except block.
for r in ("system went to sleep - connection dropped",
          "no data sent for too long - connection stalled"):
    hit = [t for t in ("401", "403", "409", "507", "authentication refused") if t in r]
    check(f"retryable: {r!r}", not hit, str(hit))


print("\n== keep_awake holds and releases a sleep lock ==")
def inhibitors():
    return subprocess.run(["systemd-inhibit", "--list", "--no-legend"],
                          capture_output=True, text=True).stdout

def can_inhibit():
    """CI runners have systemd-inhibit but no session allowed to take a lock."""
    try:
        return subprocess.run(["systemd-inhibit", "--what=sleep", "--who=probe",
                               "--why=probe", "true"], capture_output=True,
                              timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False

if platform.system() == "Linux" and shutil.which("systemd-inhibit") and can_inhibit():
    mine = lambda: [l for l in inhibitors().splitlines()
                    if "FieldClip" in l and "test-lock" in l]
    restore = RV.keep_awake("test-lock")
    time.sleep(0.5)
    check("lock present while transferring", len(mine()) == 1, inhibitors())
    restore()
    time.sleep(0.5)
    check("lock released afterwards", not mine())

    # The lock must not outlive a crashed app: run keep_awake in a child
    # process, SIGKILL it, and the inhibitor has to disappear on its own.
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    child = subprocess.Popen([sys.executable, "-c",
        "import sys,time; sys.path.insert(0, sys.argv[1]); "
        "from _common import RV; RV.keep_awake('test-lock'); time.sleep(60)",
        tests_dir])
    for _ in range(50):
        if mine(): break
        time.sleep(0.2)
    check("child holds the lock", len(mine()) == 1)
    child.kill(); child.wait()
    for _ in range(25):
        if not mine(): break
        time.sleep(0.2)
    check("lock dies with a killed app", not mine())
else:
    print("  (skipped: not Linux / systemd-inhibit missing or not allowed here)")


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
