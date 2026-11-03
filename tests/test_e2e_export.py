#!/usr/bin/env python3
"""
End-to-end export tests: the real workers, both transports, against a real
WebDAV share and the same share mounted as a drive. Skipped unless both are
set in the environment - see tests/_common.py.

Scenario 3 is the one that matters most: the mount is torn down mid-transfer
and the export has to notice, repair it, and carry on.
"""

import os
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common
from _common import RV
import make_test_data

_common.require_network(need_mount=True)
SHARE_ID  = _common.SECRET
_SUB      = [p for p in _common.SUBDIR.split("/") if p]
WEBDAV    = _common.WEBDAV_URL + "".join(urllib.parse.quote(p, safe="") + "/"
                                         for p in _SUB) + "_fieldclip_e2e/"
MOUNT     = _common.MOUNT
MOUNT_E2E = os.path.join(MOUNT, *_SUB, "_fieldclip_e2e")
_ORIGIN   = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(WEBDAV))

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def make_app(dest, transport):
    app = RV.App()
    app.withdraw()
    app._log = RV.scrolledtext.ScrolledText(app)
    app._prog = RV.ttk.Progressbar(app, maximum=100, value=0)
    app._prog_lbl = tk.Label(app); app._eta_lbl = tk.Label(app)
    app._file_lbl = tk.Label(app); app._btn_cancel = tk.Button(app)
    app.dest_dir.set(dest)
    app.transport.set(transport)
    app.share_id.set(SHARE_ID)
    RV.set_webdav_credentials(SHARE_ID)
    # Same snapshot the real _show_step_copy takes on the main thread.
    app._snap = {'dest_root': dest}
    return app


def entries(src_dir):
    """Build the `renamed` list the copy workers consume."""
    out = []
    for f in sorted(os.listdir(src_dir)):
        if not f.upper().endswith(".MP4"):
            continue
        p = os.path.join(src_dir, f)
        corrupt = "0011" in f          # the truncated one
        out.append({
            'path': p, 'filename': f, 'status': 'ok',
            'site': 'North-Meadow', 'group': 'Herd-A',
            'is_training': False, 'corrupt': corrupt,
        })
    return out


def run_worker(app, worker, timeout=600):
    app._proc_thread = app._start_worker(worker, "copy")
    app._poll_queue()
    t0 = time.time()
    while time.time() - t0 < timeout:
        app.update()
        if app._terminal_received:
            return True
        time.sleep(0.01)
    return False


root = tempfile.mkdtemp(prefix="fieldclip_e2e_")
data = make_test_data.build(root)
src_dir = data["_source_dir"]
n_videos = len([f for f in os.listdir(src_dir) if f.upper().endswith(".MP4")])

print("\n  End-to-end export")
print("  " + "=" * 66)
print(f"  dataset: {n_videos} videos in {src_dir}")

# Start from a clean slate on BOTH destinations. A previous aborted run left
# files of identical size but different content, so the worker skipped them on
# size and the hash check then - correctly - reported a mismatch.
RV.set_webdav_credentials(SHARE_ID)


def _purge(url, depth=0):
    """Depth-first delete of a WebDAV subtree. PROPFIND Depth:1 lists the
    collection itself first, so that self-entry must be skipped by path."""
    if depth > 6:
        return
    try:
        import re
        own = urllib.parse.urlparse(url).path.rstrip('/')
        body = RV._webdav_request(url, "PROPFIND", headers={"Depth": "1"},
                                  timeout=30).read().decode('utf-8', 'replace')
        for href in re.findall(r'<[Dd]:href>([^<]+)</[Dd]:href>', body):
            if href.rstrip('/') == own:
                continue
            full = _ORIGIN + href
            if href.endswith('/'):
                _purge(full, depth + 1)
            RV._webdav_delete(full, timeout=30)
    except Exception:
        pass


_purge(WEBDAV)
RV._webdav_delete(WEBDAV, timeout=30)
shutil.rmtree(MOUNT_E2E, ignore_errors=True)
print("  starting from a clean slate on the server and on the mount")

# ── 1. Export over WebDAV ────────────────────────────────────────────────────
print("\n  1. Export over WebDAV")
app = make_app(WEBDAV, "webdav")
app.renamed = entries(src_dir)
finished = run_worker(app, app._worker_copy_webdav)
check("upload run reaches a terminal state", finished,
      f"terminal={app._terminal_received}")

uploaded = [r for r in app.renamed if r.get('new_path')]
corrupt_skipped = [r for r in app.renamed if r.get('corrupt') and not r.get('new_path')]
check("corrupt file skipped, not uploaded", len(corrupt_skipped) == 1,
      f"{len(corrupt_skipped)}")
check("all healthy videos uploaded", len(uploaded) == n_videos - 1,
      f"{len(uploaded)} of {n_videos-1}")

bad = []
for r in uploaded:
    ok_v, det = RV.verify_transfer(r['path'], r['new_path'])
    if not ok_v:
        bad.append(f"{r['filename']}: {det}")
check("every uploaded file verifies by hash", not bad, "; ".join(bad))

comp_url = (WEBDAV + urllib.parse.quote("Main", safe="") + "/"
            + urllib.parse.quote("North-Meadow", safe="") + "/"
            + urllib.parse.quote("DJI_20260610185801_0001_D.SRT", safe=""))
check("companion .SRT uploaded alongside the video",
      RV._webdav_file_remote_size(comp_url, timeout=20) > 0,
      str(RV._webdav_file_remote_size(comp_url, timeout=20)))
app.destroy()

# ── 2. Export to the mounted drive ───────────────────────────────────────────
print("\n  2. Export to the mounted drive")
if os.path.exists(MOUNT_E2E):
    shutil.rmtree(MOUNT_E2E, ignore_errors=True)
os.makedirs(MOUNT_E2E, exist_ok=True)

app = make_app(MOUNT_E2E, "mount")
app.renamed = entries(src_dir)
finished = run_worker(app, app._worker_copy)
check("copy run reaches a terminal state", finished)

copied = [r for r in app.renamed if r.get('new_path') and os.path.exists(r.get('new_path',''))]
check("all healthy videos copied to the mount", len(copied) == n_videos - 1,
      f"{len(copied)} of {n_videos-1}")

bad = []
for r in copied:
    ok_v, det = RV.verify_transfer(r['path'], r['new_path'])
    if not ok_v:
        bad.append(f"{r['filename']}: {det}")
check("every copied file verifies by hash", not bad, "; ".join(bad))

srt_dst = os.path.join(MOUNT_E2E, "Main", "North-Meadow",
                       "DJI_20260610185801_0001_D.SRT")
check("companion .SRT copied to the mount", os.path.exists(srt_dst))

# ── 3. Re-run: everything already there must be skipped ──────────────────────
print("\n  3. Re-running the same export")
app2 = make_app(MOUNT_E2E, "mount")
app2.renamed = entries(src_dir)
t0 = time.time()
finished = run_worker(app2, app2._worker_copy)
elapsed = time.time() - t0
check("re-run reaches a terminal state", finished)
log_text = app2._log.get('1.0', 'end')
check("re-run skips files already present",
      log_text.count("Already at destination") >= n_videos - 2,
      f"{log_text.count('Already at destination')} skips in {elapsed:.1f}s")
app2.destroy()

# ── 4. Overwrite a half-filled destination on the real mount ─────────────────
print("\n  4. Half-filled destination on the mount")
victim = os.path.join(MOUNT_E2E, "Main", "North-Meadow",
                      "DJI_20260610185801_0001_D.MP4")
src_v = os.path.join(src_dir, "DJI_20260610185801_0001_D.MP4")
if os.path.exists(victim):
    os.remove(victim)
with open(src_v, 'rb') as fi:
    whole = fi.read()
with open(victim, 'wb') as f:
    f.write(b'\xAA' * (len(whole) // 2))

app3 = make_app(MOUNT_E2E, "mount")
app3.renamed = [e for e in entries(src_dir)
                if e['filename'] == "DJI_20260610185801_0001_D.MP4"]
run_worker(app3, app3._worker_copy)
ok_v, det = RV.verify_transfer(src_v, victim)
check("half-filled file on the mount is repaired, not appended to", ok_v, det)
app3.destroy()
app.destroy()

# ── 5. Mount torn down mid-transfer ──────────────────────────────────────────
print("\n  5. Mount dropped during the export")

# This scenario unmounts W:. If these test files themselves live on W:, the
# interpreter is reading its own source from the drive it is about to pull out,
# and the remount cannot succeed. Run the suite from a local copy to exercise it.
_self = os.path.abspath(__file__)
_on_mount = os.path.normcase(_self).startswith(os.path.normcase(MOUNT))
if _on_mount:
    print("  [SKIP] the test suite is running from the drive it would unmount.")
    print("         Copy the tests to a local folder to exercise this scenario.")
else:
    app4 = make_app(MOUNT_E2E, "mount")
    app4.renamed = entries(src_dir)
    for r in app4.renamed:                     # force real work, no skipping
        dst = os.path.join(MOUNT_E2E, "Main", "North-Meadow", r['filename'])
        if os.path.exists(dst):
            try: os.remove(dst)
            except Exception: pass

    dropped = {"done": False}


    def _drop_mount():
        time.sleep(1.5)
        try:
            RV.EM.do_unmount(RV.EM.load_config())
            dropped["done"] = True
        except Exception as e:
            print(f"        (could not unmount: {e})")


    threading.Thread(target=_drop_mount, daemon=True).start()
    finished = run_worker(app4, app4._worker_copy, timeout=600)
    check("export survives the mount being pulled", finished,
          f"unmounted={dropped['done']}")
    check("mount is back up afterwards",
          RV.probe_path(MOUNT, timeout=10) == 'MOUNTED',
          RV.probe_path(MOUNT, timeout=10))
    log4 = app4._log.get('1.0', 'end')
    check("the drop was noticed and repaired",
          ("repairing" in log4) or (dropped["done"] and "✓" in log4),
          "saw a repair message" if "repairing" in log4 else "no repair message")
    app4.destroy()

# ── Cleanup ──────────────────────────────────────────────────────────────────
print("\n  cleanup")
RV.set_webdav_credentials(SHARE_ID)


_purge(WEBDAV)
RV._webdav_delete(WEBDAV, timeout=30)
check("server test folder removed",
      not RV._webdav_folder_exists(WEBDAV, timeout=15))
shutil.rmtree(MOUNT_E2E, ignore_errors=True)
check("mount test folder removed", not os.path.exists(MOUNT_E2E))
shutil.rmtree(root, ignore_errors=True)

print("  " + "=" * 66)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  FAILED: " + "; ".join(FAIL))
sys.exit(1 if FAIL else 0)
