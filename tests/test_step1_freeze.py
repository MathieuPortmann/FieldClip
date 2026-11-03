#!/usr/bin/env python3
"""
Step 1 regression tests - the end-of-export freeze.

Drives the real App class with a hidden window and replaces only the worker
body, so the queue, the polling loop and the terminal contract under test are
exactly the ones that ship.

Scenario A reproduces the original bug: a worker that floods the queue and then
posts 'done'. On the old list+clear() code the 'done' was destroyed and the UI
never advanced.
"""

import os
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import RV

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def make_app():
    app = RV.App()
    app.withdraw()                     # keep it off screen
    # Minimal widgets the transfer UI expects
    app._log = RV.scrolledtext.ScrolledText(app)
    app._prog = RV.ttk.Progressbar(app, maximum=100, value=0)
    app._prog_lbl = tk.Label(app)
    app._eta_lbl = tk.Label(app)
    app._file_lbl = tk.Label(app)
    app._btn_cancel = tk.Button(app)
    app.renamed = []
    return app


def run_until(app, predicate, timeout=20.0):
    """Pump the Tk event loop until predicate() or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.update()
        if predicate():
            return True
        time.sleep(0.02)
    return False


print("\n  Step 1 - end-of-export freeze regression tests")
print("  " + "-" * 60)

# --- A. Flood then done: the exact original race ---------------------------
app = make_app()

def flood_worker():
    for i in range(5000):              # far more than MAX_MESSAGES_PER_TICK
        app._q_log(f"line {i}")
        app._q_update_prog(i / 5000, i, 5000, "flooding")
    app._queue.put(('done', None))

app._proc_thread = app._start_worker(flood_worker, "copy")
app._poll_queue()
ok = run_until(app, lambda: app._terminal_received, timeout=30)
check("A. 'done' survives a 5000-message flood", ok,
      f"terminal_received={app._terminal_received}")
check("A. Next button re-enabled", str(app.btn_next['state']) == 'normal',
      f"state={app.btn_next['state']}, text={app.btn_next['text']!r}")
app.destroy()

# --- B. Worker raises: must produce 'fatal', not silence --------------------
app = make_app()
fatal_seen = {"v": False}
_orig_popup = RV.App._show_fatal_popup
RV.App._show_fatal_popup = lambda self, k, e, tb: fatal_seen.__setitem__("v", True)

def crashing_worker():
    app._q_log("starting")
    raise OSError("simulated SSHFS drop inside os.makedirs")

app._proc_thread = app._start_worker(crashing_worker, "copy")
app._poll_queue()
ok = run_until(app, lambda: app._terminal_received, timeout=20)
check("B. Crash produces a terminal message", ok)
check("B. Fatal popup invoked", fatal_seen["v"])
check("B. Next button re-enabled", str(app.btn_next['state']) == 'normal')
RV.App._show_fatal_popup = _orig_popup
app.destroy()

# --- C. Worker returns silently: watchdog must release the UI ---------------
app = make_app()

def silent_worker():
    app._q_log("doing nothing then returning without a terminal message")

app._proc_thread = app._start_worker(silent_worker, "copy")
app._poll_queue()
ok = run_until(app, lambda: app._terminal_received, timeout=20)
check("C. Silent exit unblocks the UI via watchdog", ok)
check("C. Next button re-enabled", str(app.btn_next['state']) == 'normal')
app.destroy()

# --- D. Cancellation path still works --------------------------------------
app = make_app()

def cancel_worker():
    for i in range(100):
        app._q_log(f"work {i}")
    app._queue.put(('cancelled', None))

app._proc_thread = app._start_worker(cancel_worker, "copy")
app._poll_queue()
ok = run_until(app, lambda: app._terminal_received, timeout=20)
check("D. Cancellation reaches the UI", ok)
app.destroy()

print("  " + "-" * 60)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
