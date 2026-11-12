#!/usr/bin/env python3
"""
Parallel WebDAV upload (step 4), against a small WebDAV server started on
127.0.0.1 by this script - no network or credentials needed.

The server caps the speed of EACH connection, like the long route to a remote
share does, keeps whatever part of a body it received when a connection drops
(as real servers do), and can be told to fail a given file once or for good.

Checks: N files really go up N at a time, every byte arrives intact with its
companion file, a dropped connection is retried, a permanent error fails only
its own file, Cancel stops mid-file without leaving a partial copy, a second
run skips what is already there, and the step-1 control and step-4 display
behave.
"""

import os
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common
from _common import RV

if not _common.has_display():
    print("  SKIPPED: no display for Tk.")
    sys.exit(0)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


# ── A minimal WebDAV server ──────────────────────────────────────────────────

class DavState:
    def __init__(self):
        self.lock = threading.Lock()
        self.files = {}            # path -> bytes
        self.dirs = {"/"}
        self.rate = 4 * 1024 * 1024        # bytes/s per connection
        self.active_puts = 0
        self.max_puts = 0
        self.drop_once = set()     # file names whose first PUT is cut halfway
        self.fail_status = {}      # file name -> HTTP status, every time
        self.put_count = {}        # file name -> PUTs received

    def reset(self):
        with self.lock:
            self.files.clear(); self.dirs = {"/"}
            self.max_puts = 0; self.drop_once.clear()
            self.fail_status.clear(); self.put_count.clear()


DAV = DavState()


class DavHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _path(self):
        return urllib.parse.unquote(urllib.parse.urlparse(self.path).path)

    def _reply(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        p = self._path()
        with DAV.lock:
            if p in DAV.files:
                n = len(DAV.files[p])
                self.send_response(200)
                self.send_header("Content-Length", str(n))
                self.end_headers()
                return
            known = p.rstrip("/") + "/" in DAV.dirs or p == "/"
        self._reply(200 if known else 404)

    def do_MKCOL(self):
        p = self._path().rstrip("/") + "/"
        parent = p.rstrip("/").rsplit("/", 1)[0] + "/"
        with DAV.lock:
            if p in DAV.dirs:
                return self._reply(405)
            if parent not in DAV.dirs:
                return self._reply(409)
            DAV.dirs.add(p)
        self._reply(201)

    def do_DELETE(self):
        p = self._path()
        with DAV.lock:
            if p in DAV.files:
                del DAV.files[p]
                return self._reply(204)
        self._reply(404)

    def do_PROPFIND(self):
        self._reply(207, b"<?xml version='1.0'?><multistatus xmlns='DAV:'/>")

    def do_PUT(self):
        p = self._path()
        name = p.rsplit("/", 1)[-1]
        length = int(self.headers.get("Content-Length", "0"))
        with DAV.lock:
            DAV.put_count[name] = DAV.put_count.get(name, 0) + 1
            first = DAV.put_count[name] == 1
            drop = name in DAV.drop_once and first
            status = DAV.fail_status.get(name)
            DAV.active_puts += 1
            DAV.max_puts = max(DAV.max_puts, DAV.active_puts)
        buf = bytearray()
        try:
            t0 = time.monotonic()
            while len(buf) < length:
                if drop and len(buf) >= length // 2:
                    # Keep the half received, then cut the connection.
                    with DAV.lock:
                        DAV.files[p] = bytes(buf)
                    self.close_connection = True
                    self.connection.shutdown(2)
                    return
                piece = self.rfile.read(min(65536, length - len(buf)))
                if not piece:
                    # Client went away: keep the part, a moment later - real
                    # servers often finalise it after the client's DELETE.
                    time.sleep(0.5)
                    with DAV.lock:
                        DAV.files[p] = bytes(buf)
                    return
                buf += piece
                ahead = len(buf) / DAV.rate - (time.monotonic() - t0)
                if ahead > 0:
                    time.sleep(ahead)
        except OSError:
            time.sleep(0.5)
            with DAV.lock:
                DAV.files[p] = bytes(buf)
            return
        finally:
            with DAV.lock:
                DAV.active_puts -= 1
        if status:
            return self._reply(status)
        parent = p.rsplit("/", 1)[0] + "/"
        with DAV.lock:
            if parent not in DAV.dirs:
                return self._reply(409)
            DAV.files[p] = bytes(buf)
        self._reply(201)


server = ThreadingHTTPServer(("127.0.0.1", 0), DavHandler)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"
DEST = BASE + "/share/Videos/"

# Fast retries for the test; the real delay is 15 s.
RV.WEBDAV_RETRY_DELAY = 1
RV.set_webdav_credentials("")

# ── Test data ────────────────────────────────────────────────────────────────

tmp = tempfile.mkdtemp(prefix="fieldclip_par_")
MB = 1024 * 1024


def make_files(n, size):
    out = []
    for k in range(n):
        name = f"Mini4Pro_2025-11-05_10-0{k}-00_Herd-A_North-Meadow.MP4"
        path = os.path.join(tmp, name)
        with open(path, "wb") as f:
            f.write(os.urandom(size + k * 1000))
        with open(path[:-4] + ".SRT", "w") as f:
            f.write(f"1\n00:00:00,000 --> 00:00:01,000\nclip {k}\n")
        out.append(path)
    return out


def entries(paths):
    return [{'path': p, 'filename': os.path.basename(p), 'status': 'ok',
             'site': 'North-Meadow', 'group': 'Herd-A',
             'is_training': False, 'corrupt': False} for p in paths]


def make_app(parallel):
    app = RV.App()
    app.withdraw()
    app._log = RV.scrolledtext.ScrolledText(app)
    app._prog = RV.ttk.Progressbar(app, maximum=100, value=0)
    app._prog_lbl = tk.Label(app); app._eta_lbl = tk.Label(app)
    app._file_lbl = tk.Label(app); app._btn_cancel = tk.Button(app)
    app._cancel_flag.clear()
    app._snap = {'dest_root': DEST, 'parallel': parallel}
    return app


def run(app, on_tick=None, timeout=120):
    """Run the WebDAV worker as step 4 does; return (terminal message, seconds)."""
    seen = []
    real_put = app._queue.put

    def spy(item):
        if item[0] in ('done', 'cancelled', 'fatal'):
            seen.append(item[0])
        real_put(item)
    app._queue.put = spy
    t0 = time.time()
    app._proc_thread = app._start_worker(app._worker_copy_webdav, "upload")
    app._poll_queue()
    while time.time() - t0 < timeout:
        app.update()
        if on_tick:
            on_tick(app, time.time() - t0)
        if app._terminal_received:
            break
        time.sleep(0.01)
    return (seen[0] if seen else None), time.time() - t0


def remote_path(local):
    # Layout of test_profile.json, as the worker computes it.
    r = entries([local])[0]
    parts = RV.NAMING.dest_parts(False, r)
    return "/share/Videos/" + "/".join(parts) + "/" + os.path.basename(local)


def log_text(app):
    return app._log.get("1.0", "end")


print("\n  Parallel WebDAV upload")
print("  " + "=" * 66)

# 1. Five files, three at a time ------------------------------------------------
DAV.reset(); DAV.rate = 4 * MB
files = make_files(5, 3 * MB)
app = make_app(3)
app.renamed = entries(files)
term, secs = run(app)
check("1. ends with 'done'", term == 'done', str(term))
check("1. at most and at least 3 uploads at once", DAV.max_puts == 3, str(DAV.max_puts))
intact = all(DAV.files.get(remote_path(p)) == open(p, "rb").read() for p in files)
check("1. every video arrived byte for byte", intact)
srt_ok = all(remote_path(p)[:-4] + ".SRT" in DAV.files for p in files)
check("1. every companion .SRT arrived", srt_ok)
check("1. every entry has new_path", all(r.get('new_path') for r in app.renamed))
seq_time = sum(os.path.getsize(p) for p in files) / DAV.rate
check("1. faster than one at a time", secs < seq_time * 0.75,
      f"{secs:.1f} s vs {seq_time:.1f} s sequential")
check("1. progress bar at 100 %", float(app._prog['value']) == 100.0,
      str(app._prog['value']))
check("1. per-file lines cleared at the end", app._file_lbl.cget("text") == "")
check("1. log says 3 at a time", "3 at a time" in log_text(app))
app.destroy()

# 2. One at a time ---------------------------------------------------------------
DAV.reset()
app = make_app(1)
app.renamed = entries(files[:3])
term, _ = run(app)
check("2. parallel=1 never overlaps", term == 'done' and DAV.max_puts == 1,
      f"{term}, max {DAV.max_puts}")
app.destroy()

# 3. Dropped connection is retried, partial copy replaced -----------------------
DAV.reset()
victim = os.path.basename(files[1])
DAV.drop_once.add(victim)
app = make_app(3)
app.renamed = entries(files[:3])
retry_popups = []
real_popup = app._show_retry_popup
app._show_retry_popup = lambda f, e: retry_popups.append(f)
term, _ = run(app)
check("3. ends with 'done'", term == 'done', str(term))
check("3. dropped file sent twice", DAV.put_count.get(victim) == 2,
      str(DAV.put_count.get(victim)))
check("3. dropped file intact on the server",
      DAV.files.get(remote_path(files[1])) == open(files[1], "rb").read())
check("3. retry announced to the UI", retry_popups == [victim], str(retry_popups))
check("3. log names the file in the warning",
      "Upload error (attempt 1/5)" in log_text(app))
app.destroy()

# 4. A permanent error fails only its own file ----------------------------------
DAV.reset()
bad = os.path.basename(files[2])
DAV.fail_status[bad] = 507
app = make_app(3)
app.renamed = entries(files[:4])
term, _ = run(app)
st = {r['filename']: r['status'] for r in app.renamed}
check("4. ends with 'done'", term == 'done', str(term))
check("4. failing file marked error, not retried", st[bad] == 'error'
      and DAV.put_count.get(bad) == 1, f"{st[bad]}, PUTs {DAV.put_count.get(bad)}")
check("4. the other files uploaded",
      all(v == 'ok' for k, v in st.items() if k != bad), str(st))
check("4. summary reports the failure", "Finished with 1 failure(s)" in log_text(app))
app.destroy()

# 5. Cancel mid-file -------------------------------------------------------------
DAV.reset(); DAV.rate = 1 * MB
big = make_files(4, 6 * MB)
app = make_app(2)
app.renamed = entries(big)
cancel_at = {}


def _cancel(a, t):
    if t > 1.5 and not cancel_at:
        cancel_at['t'] = t
        a._request_cancel()


term, secs = run(app, on_tick=_cancel)
check("5. ends with 'cancelled'", term == 'cancelled', str(term))
check("5. stops promptly (does not finish the files)", secs - cancel_at.get('t', 0) < 5,
      f"{secs - cancel_at.get('t', 0):.1f} s after Cancel")
partial = [p for p, b in DAV.files.items() if p.endswith(".MP4")
           and len(b) != os.path.getsize(os.path.join(tmp, p.rsplit('/', 1)[-1]))]
mp4_on_server = [p for p in DAV.files if p.endswith(".MP4")]
check("5. no partial copy left on the server", not partial,
      f"{len(mp4_on_server)} complete file(s) kept, partial: {partial}")
check("5. some uploads had really started", sum(DAV.put_count.values()) >= 2,
      str(DAV.put_count))
check("5. log explains the cancel", "Cancelled" in log_text(app))
app.destroy()

# 6. Second run skips what is already there -------------------------------------
DAV.reset(); DAV.rate = 8 * MB
app = make_app(3)
app.renamed = entries(files)
run(app)
app.destroy()
before = dict(DAV.put_count)
app = make_app(3)
app.renamed = entries(files)
term, _ = run(app)
mp4_puts = sum(v for k, v in DAV.put_count.items() if k.endswith(".MP4")) \
    - sum(v for k, v in before.items() if k.endswith(".MP4"))
check("6. rerun: nothing re-sent", term == 'done' and mp4_puts == 0,
      f"{term}, {mp4_puts} PUTs")
check("6. rerun: skipped files still get new_path",
      all(r.get('new_path') for r in app.renamed))
app.destroy()

# 7. Progress snapshot arithmetic -----------------------------------------------
jobs = [RV._UploadJob({'filename': f"f{k}"}, 100, f"[{k+1}/3]") for k in range(3)]
tr = RV._UploadTracker(jobs)
tr.start(jobs[0]); tr.set_phase(jobs[0], 'sending'); tr.add_sent(jobs[0], 40)
tr.start(jobs[1]); tr.finish(jobs[1], 'ok')
snap = tr.snapshot()
check("7. accounted = finished + sent so far", snap['accounted'] == 140, str(snap['accounted']))
check("7. one done, one active", snap['n_done'] == 1 and len(snap['active']) == 1)
tr.set_phase(jobs[0], 'sending')
check("7. a new attempt restarts the file's count", tr.snapshot()['accounted'] == 100)
check("7. not all finished", not tr.all_finished())
check("7. clamp", (RV._clamp_parallel("x"), RV._clamp_parallel(0),
                   RV._clamp_parallel(9), RV._clamp_parallel("2")) == (1, 1, 4, 2))

# 8. Retry popup is not stacked --------------------------------------------------
app = make_app(3)
app.deiconify()
app._show_retry_popup("a.MP4", "boom")
app._show_retry_popup("b.MP4", "boom again")
app.update()
tops = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
check("8. one popup for several failures", len(tops) == 1, str(len(tops)))
check("8. popup shows the latest file",
      tops and tops[0]._file_lbl.cget("text") == "File: b.MP4")
gen = app._retry_gen
btn = [b for b in tops[0].winfo_children()[-1].winfo_children()
       if isinstance(b, tk.Button) and b.cget("text") == "Force retry now"]
btn[0].invoke()
check("8. Force retry bumps the retry counter", app._retry_gen == gen + 1)
app.destroy()

# 9. Step 1: the control ---------------------------------------------------------
app = RV.App()
app.withdraw()
app.transport.set("webdav"); app._on_transport_change(); app.update()
check("9. row shown for HTTPS upload", app._par_row.winfo_manager() == "pack")
check("9. spinbox is arrows-only", str(app._par_spin.cget("state")) == "readonly")
app.transport.set("mount"); app._on_transport_change(); app.update()
check("9. row hidden for folder copy", app._par_row.winfo_manager() == "")
app.parallel_uploads.set(2)
check("9. value read back", app._get_parallel() == 2)
app.op_mode.set("rename"); app._toggle_dest_card()
check("9. disabled in rename-only mode", str(app._par_spin.cget("state")) == "disabled")
app.op_mode.set("copy"); app._toggle_dest_card()
check("9. back to arrows-only", str(app._par_spin.cget("state")) == "readonly")
app.destroy()

server.shutdown()
import shutil
shutil.rmtree(tmp, ignore_errors=True)
print("  " + "-" * 60)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
