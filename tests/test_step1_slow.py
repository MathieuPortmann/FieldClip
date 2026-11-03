#!/usr/bin/env python3
"""
The exact scenario that reproduces the freeze 4 times out of 5 on the original
code, run against the FIXED code. Must pass every time.
"""

import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import RV

app = RV.App()
app.withdraw()
app._log = RV.scrolledtext.ScrolledText(app)
app._prog = RV.ttk.Progressbar(app, maximum=100, value=0)
app._prog_lbl = tk.Label(app)
app._eta_lbl = tk.Label(app)
app._file_lbl = tk.Label(app)
app._btn_cancel = tk.Button(app)
app.renamed = []

finished = {"v": False}


def slow_worker():
    for i in range(4000):
        app._q_log(f"[3/60]  DJI_2026_{i:04d}.MP4")
        app._q_update_prog(i / 4000, i, 4000, f"{i*4} MB / 3584 MB")
        if i % 50 == 0:
            time.sleep(0.005)
    app._queue.put(('done', None))
    finished["v"] = True


app._proc_thread = app._start_worker(slow_worker, "copy")
app._poll_queue()

deadline = time.time() + 40
while time.time() < deadline:
    app.update()
    if app._terminal_received:
        break
    time.sleep(0.01)

state = str(app.btn_next['state'])
text  = str(app.btn_next['text'])
ok = app._terminal_received and state == 'normal' and 'summary' in text.lower()

print(f"  worker completed={finished['v']}  terminal={app._terminal_received}  "
      f"next={state}/{text!r}  -> {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
