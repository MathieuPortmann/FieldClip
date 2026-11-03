#!/usr/bin/env python3
"""
Integration tests for steps 3-6: mount awareness, authenticated WebDAV,
transport selection and hash verification.

Talks to a real WebDAV share (and, if given, the same share mounted as a
drive), then cleans up everything it created. Skipped unless the server is
set in the environment - see tests/_common.py.
"""

import os
import shutil
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common
from _common import RV

_common.require_network()
SHARE_ID = _common.SECRET
BASE_URL = _common.WEBDAV_URL
TESTDIR  = "_fieldclip_selftest"
MOUNT    = _common.MOUNT

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


print("\n  Steps 3-6 integration")
print("  " + "=" * 66)

# ── Step 3: mount probing ────────────────────────────────────────────────────
print("\n  Step 3 - mount awareness")
check("remote_mount imported by the app", RV.EM is not None)
if MOUNT:
    check("probe_path reports the live mount as MOUNTED",
          RV.probe_path(MOUNT, timeout=8) == 'MOUNTED',
          RV.probe_path(MOUNT, timeout=8))
check("probe_path reports a missing path as ABSENT",
      RV.probe_path("Q:\\nonexistent_xyz", timeout=5) == 'ABSENT',
      RV.probe_path("Q:\\nonexistent_xyz", timeout=5))

t0 = time.time()
RV.probe_path("Q:\\nonexistent_xyz", timeout=5)
check("probe_path returns quickly, never blocks", time.time() - t0 < 8,
      f"{time.time()-t0:.1f}s")

# ── Step 4: WebDAV authentication ────────────────────────────────────────────
print("\n  Step 4 - authenticated WebDAV")

RV.set_webdav_credentials("")
ok, msg = RV._webdav_test_connection(BASE_URL, timeout=15)
check("no credentials -> connection test FAILS (was reported as success)",
      not ok, msg)

RV.set_webdav_credentials("definitely-wrong-id")
ok, msg = RV._webdav_test_connection(BASE_URL, timeout=15)
check("wrong credential -> rejected with a clear message", not ok, msg)

RV.set_webdav_credentials(SHARE_ID)
ok, msg = RV._webdav_test_connection(BASE_URL, timeout=15)
check("correct credential -> authenticated", ok, msg)

col = RV._webdav_ensure_collection(BASE_URL.rstrip('/'), [TESTDIR], timeout=20)
check("MKCOL creates a collection when authenticated",
      RV._webdav_folder_exists(col, timeout=15), col)

# ── Step 6: edge hashing catches the corruption size cannot ──────────────────
print("\n  Step 6 - integrity verification")

tmp = tempfile.mkdtemp(prefix="fieldclip_verify_")
src = os.path.join(tmp, "src.MP4")
with open(src, 'wb') as f:
    f.write(os.urandom(3 * 1024 * 1024))
n = os.path.getsize(src)

good = os.path.join(tmp, "good.MP4")
shutil.copyfile(src, good)
ok_v, det = RV.verify_transfer(src, good)
check("identical copy verifies", ok_v, det)

# The exact corruption the old code produced: right size, wrong first half.
bad = os.path.join(tmp, "bad.MP4")
with open(src, 'rb') as fi:
    tail = fi.read()[n // 2:]
with open(bad, 'wb') as f:
    f.write(b'\xAA' * (n // 2)); f.write(tail)
same_size = os.path.getsize(bad) == n
ok_v, det = RV.verify_transfer(src, bad)
check("append-corrupted file has the EXACT right size", same_size,
      f"{os.path.getsize(bad)} == {n}")
check("edge hash still catches it (size check alone would not)",
      not ok_v, det)

# Truncated by one byte
short = os.path.join(tmp, "short.MP4")
with open(src, 'rb') as fi, open(short, 'wb') as fo:
    fo.write(fi.read(n - 1))
ok_v, det = RV.verify_transfer(src, short)
check("truncated file rejected", not ok_v, det)

# Missing entirely
ok_v, det = RV.verify_transfer(src, os.path.join(tmp, "nope.MP4"))
check("missing destination rejected", not ok_v, det)

# ── Round trip over WebDAV, verified by hash ─────────────────────────────────
print("\n  WebDAV round trip")

import urllib.parse
url = col + urllib.parse.quote("verify_probe.MP4", safe="")
with open(src, 'rb') as f:
    data = f.read()
try:
    RV._webdav_request(url, "PUT", data=data,
                       headers={"Content-Type": "application/octet-stream",
                                "Content-Length": str(len(data))},
                       timeout=300)
    put_ok = True
except Exception as e:
    put_ok = False
    print(f"        PUT failed: {e}")
check("authenticated PUT uploads a file", put_ok)

check("remote size matches", RV._webdav_file_remote_size(url, timeout=20) == n,
      str(RV._webdav_file_remote_size(url, timeout=20)))

ok_v, det = RV.verify_transfer(src, url)
check("verify_transfer validates the uploaded file over HTTP", ok_v, det)

ok_v, det = RV.verify_transfer(bad, url)
check("verify_transfer rejects a mismatched source over HTTP", not ok_v, det)

# ── Step 5: transport wiring in the UI ───────────────────────────────────────
print("\n  Step 5 - transport selection")

app = RV.App()
app.withdraw()
check("transport variable exists and defaults to webdav",
      app.transport.get() == 'webdav', app.transport.get())
check("credential seeded from the secrets store",
      app.share_id.get() == SHARE_ID)

app.transport.set('mount')
app._on_transport_change()
dest_mount = app.dest_dir.get()
check("selecting the mount transport clears the URL",
      not dest_mount.startswith('http'), dest_mount)

app.transport.set('webdav')
app._on_transport_change()
dest_web = app.dest_dir.get()
check("selecting WebDAV sets a URL on the configured server",
      dest_web.startswith(BASE_URL), dest_web)

app.share_id.set("typed-by-hand")
check("typing a credential updates the WebDAV credentials",
      RV._WEBDAV_SECRET == "typed-by-hand")
app.share_id.set(SHARE_ID)
app.destroy()

# ── Cleanup ──────────────────────────────────────────────────────────────────
RV.set_webdav_credentials(SHARE_ID)
RV._webdav_delete(url, timeout=30)
RV._webdav_delete(col, timeout=30)
gone = not RV._webdav_folder_exists(col, timeout=15)
check("test collection removed from the server", gone)
shutil.rmtree(tmp, ignore_errors=True)

print("  " + "=" * 66)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  FAILED: " + "; ".join(FAIL))
sys.exit(1 if FAIL else 0)
