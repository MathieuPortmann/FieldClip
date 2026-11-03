#!/usr/bin/env python3
"""
Step 6 - the delete gate.

The originals must be impossible to delete unless every file has passed the
integrity check. This is the last line of defence against the failure mode that
used to exist: a corrupt copy of exactly the right size, accepted by a size-only
check, followed by deletion of the only good copy.
"""

import os
import shutil
import sys
import tempfile
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import RV

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def build(good: bool):
    """Set up step 6 with either a faithful copy or a corrupt one."""
    tmp = tempfile.mkdtemp(prefix="gate_")
    src = os.path.join(tmp, "a.MP4")
    with open(src, 'wb') as f:
        f.write(os.urandom(2 * 1024 * 1024))
    n = os.path.getsize(src)
    dst = os.path.join(tmp, "copy_a.MP4")
    if good:
        shutil.copyfile(src, dst)
    else:
        # Exactly the right size, wrong first half - the old append bug.
        with open(src, 'rb') as fi:
            tail = fi.read()[n // 2:]
        with open(dst, 'wb') as f:
            f.write(b'\xAA' * (n // 2)); f.write(tail)

    app = RV.App(); app.withdraw()
    app.op_mode.set("copy")
    app.renamed = [{'path': src, 'filename': "a.MP4", 'status': 'ok',
                    'new_path': dst, 'site': 'North-Meadow'}]
    app._show_step_validate()
    t0 = time.time()
    while time.time() - t0 < 60:
        app.update()
        if getattr(app, '_verify_status', None) and \
           "Verifying" not in app._verify_status.cget('text'):
            break
        time.sleep(0.02)
    return app, tmp, src


print("\n  Step 6 - delete gate")
print("  " + "-" * 62)

# --- Faithful copy: gate should open ---------------------------------------
app, tmp, src = build(good=True)
check("verification passes on a faithful copy", app._verify_passed,
      app._verify_status.cget('text')[:60])
check("delete checkbox becomes enabled",
      str(app._chk_delete_widget.cget('state')) == 'normal',
      str(app._chk_delete_widget.cget('state')))
app.destroy(); shutil.rmtree(tmp, ignore_errors=True)

# --- Corrupt copy of the exact right size: gate must stay shut -------------
app, tmp, src = build(good=False)
check("verification FAILS on a same-size corrupt copy", not app._verify_passed,
      app._verify_status.cget('text')[:70])
check("delete checkbox stays disabled",
      str(app._chk_delete_widget.cget('state')) == 'disabled',
      str(app._chk_delete_widget.cget('state')))

# Force the checkbox anyway and try to finish - _finish must refuse.
app._chk_delete.set(True)
app._chk_count.set(True); app._chk_play.set(True); app._chk_names.set(True)
refused = {"v": False}
RV.messagebox.showerror = lambda *a, **k: refused.__setitem__("v", True)
RV.messagebox.askyesno = lambda *a, **k: True
RV.messagebox.showinfo = lambda *a, **k: None
app._finish()
check("_finish refuses to delete when verification failed", refused["v"])
check("the original file is still on disk", os.path.exists(src))
app.destroy(); shutil.rmtree(tmp, ignore_errors=True)

print("  " + "-" * 62)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  FAILED: " + "; ".join(FAIL))
sys.exit(1 if FAIL else 0)
