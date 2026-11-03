# -*- coding: utf-8 -*-
"""
FieldClip - Flight-log module (step 3 add-on)
===============================================
Decode DJI RC flight logs, reconstruct missing SRTs, write per-clip telemetry
sidecars, and identify the true drone model (Mini4Pro / Mini3Pro / Mini3).

Pure functions + a FolderLogs helper. No third-party deps: only the Python
standard library and the `dji-log` binary (bin/djilog/<os>/ or the PATH).

Works on Windows, macOS (arm/x86) and Linux. All paths are built with
os.path.join / os.path.dirname(__file__) so nothing is OS-specific.
"""
import os, re, csv, math, glob, struct, platform, subprocess, tempfile, datetime, statistics

try:
    from . import settings as S
except ImportError:                       # run as a plain script
    import settings as S

# On Windows, keep the dji-log console window from flashing when the GUI (pythonw)
# spawns it. getattr(...,0) => harmless no-op on macOS / Linux.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# --------------------------------------------------------------------------- #
#  DJI API key. Order: FIELDCLIP_DJI_API_KEY, the per-user secrets file, then
#  an optional key file shared by one installation (tools.dji_api_key_file,
#  e.g. on a network share so every user of the share has it).
# --------------------------------------------------------------------------- #
def _base_dir(base_dir=None):
    return base_dir or os.path.dirname(os.path.abspath(__file__))

def shared_key_path(base_dir=None) -> str:
    return S.resolve_path(S.active()["tools"].get("dji_api_key_file", ""))

def read_api_key(base_dir=None) -> str:
    key = S.get_secret("dji_api_key")
    if key:
        return key
    path = shared_key_path(base_dir)
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""

def write_api_key(key, base_dir=None) -> bool:
    """Saved where it was read from: the shared file if the profile names one
    and it already holds a key, otherwise the per-user secrets file."""
    key = (key or "").strip()
    path = shared_key_path(base_dir)
    if path and os.path.isfile(path) and not S.get_secret("dji_api_key"):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(key)
            return True
        except Exception:
            pass
    return S.set_secret("dji_api_key", key)

# --------------------------------------------------------------------------- #
#  Bundled binary (per-OS, mirrors the ffmpeg bundling pattern)
# --------------------------------------------------------------------------- #
def _prepare_unix_binary(path):
    try: os.chmod(path, 0o755)
    except Exception: pass
    if platform.system() == "Darwin":
        try: subprocess.run(["xattr", "-d", "com.apple.quarantine", path],
                            capture_output=True, stdin=subprocess.DEVNULL, timeout=5)
        except Exception: pass

def find_dji_log_binary(base_dir=None) -> str:
    sysname = platform.system()
    explicit = os.environ.get(S.ENV_DJI_LOG) or S.active()["tools"].get("dji_log", "")
    cands = [S.resolve_path(explicit)] if explicit else []
    # bin/djilog/<os>/ in the FieldClip root, or djilog/<os>/ beside this file
    # (the layout of older installations).
    for base in (os.path.join(S.ROOT_DIR, "bin"), _base_dir(base_dir)):
        if sysname == "Windows":
            cands.append(os.path.join(base, "djilog", "windows", "dji-log.exe"))
        elif sysname == "Darwin":
            order = ["mac_arm", "mac_x86"] if platform.machine() == "arm64" else ["mac_x86", "mac_arm"]
            cands += [os.path.join(base, "djilog", s, "dji-log") for s in order]
        else:
            cands.append(os.path.join(base, "djilog", "linux", "dji-log"))
    for c in cands:
        if os.path.isfile(c):
            if sysname != "Windows": _prepare_unix_binary(c)
            return c
    for name in ("dji-log", "dji-log.exe"):          # fall back to PATH
        try:
            if subprocess.run([name, "--version"], capture_output=True, timeout=5,
                              creationflags=_CREATE_NO_WINDOW).returncode == 0:
                return name
        except Exception: pass
    return ""

# --------------------------------------------------------------------------- #
#  Log discovery / decoding
# --------------------------------------------------------------------------- #
LOG_GLOBS = ("DJIFlightRecord_*.txt", "FlightRecord_*.txt")
LOG_RE    = re.compile(r'(?:DJI)?FlightRecord_(\d{4}-\d{2}-\d{2})_\[(\d{2}-\d{2}-\d{2})\]', re.I)

def find_logs(folder) -> list:
    """Records beside the clips, and one level down: the DJI apps drop them in a
    subfolder (FlightRecord/, DJIFlightRecord/) about as often as in the root."""
    out = []
    for g in LOG_GLOBS:
        out += glob.glob(os.path.join(folder, g))
        out += glob.glob(os.path.join(folder, "*", g))
    return sorted({p for p in out if p.lower().endswith(".txt")})

# CANONICAL sidecar schema - keep this order stable across every producer.
# Consumers MUST read by column NAME, never by index.
CORE = [("OSD.flyTime","flyTime_s"),("CUSTOM.dateTime","utc"),
        ("OSD.latitude","latitude"),("OSD.longitude","longitude"),
        ("OSD.height","rel_alt_m"),("OSD.altitude","abs_alt_m"),("OSD.vpsHeight","vps_m"),
        ("OSD.xSpeed","xSpeed"),("OSD.ySpeed","ySpeed"),("OSD.zSpeed","zSpeed"),
        ("OSD.pitch","ac_pitch"),("OSD.roll","ac_roll"),("OSD.yaw","ac_yaw"),
        ("GIMBAL.pitch","gb_pitch"),("GIMBAL.roll","gb_roll"),("GIMBAL.yaw","gb_yaw"),
        ("OSD.gpsNum","gpsNum"),("OSD.isGPSUsed","isGPSUsed"),("OSD.flycState","flycState"),
        ("BATTERY.chargeLevel","battery_pct"),("CAMERA.isVideo","isVideo")]
SIDECAR_COLS = [o for _, o in CORE if o != "isVideo"]   # 20 cols + video_t_s = 21

def decode_log(binary, log_path, api_key, timeout=180):
    """Decode one DJI log to (rows, meta); on failure returns (None, error_message)."""
    fd, tmp = tempfile.mkstemp(suffix=".csv"); os.close(fd)
    try:
        proc = subprocess.run([binary, "--api-key", api_key, "--csv", tmp, log_path],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              stdin=subprocess.DEVNULL, text=True, timeout=timeout,
                              creationflags=_CREATE_NO_WINDOW)
        if proc.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
            err = (proc.stderr or "").strip().splitlines()
            return None, (err[-1] if err else f"return code {proc.returncode}")
        return _read_decoded_csv(tmp)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except Exception as e:
        return None, str(e)
    finally:
        try: os.remove(tmp)
        except Exception: pass

def _read_decoded_csv(path):
    rows = []; meta = {}
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh); hdr = next(rd)
        ix = {n: i for i, n in enumerate(hdr)}
        ci = [(ix.get(s, -1), o) for s, o in CORE]
        fi = ix.get("OSD.flyTime", 1)
        mcols = {"aircraftName": ix.get("DETAILS.aircraftName", ix.get("RECOVER.aircraftName", -1)),
                 "aircraftSerial": ix.get("RECOVER.aircraftSerial", -1),
                 "droneType": ix.get("OSD.droneType", -1)}
        for row in rd:
            try: ft = float(row[fi])
            except Exception: continue
            rec = {o: (row[i] if 0 <= i < len(row) else "") for i, o in ci}
            rec["_ft"] = ft; rows.append(rec)
            if not meta:
                meta = {k: (row[i] if 0 <= i < len(row) else "") for k, i in mcols.items()}
    rows.sort(key=lambda r: r["_ft"])
    return rows, meta

# --------------------------------------------------------------------------- #
#  isVideo segments + geometry helpers
# --------------------------------------------------------------------------- #
def _isvideo_segments(rows, start_local):
    ft0 = rows[0]["_ft"] if rows else 0.0
    segs = []; on = False; st = None
    for r in rows:
        v = str(r.get("isVideo", "")).strip().lower() == "true"
        if v and not on: on = True; st = r["_ft"]
        elif not v and on: on = False; segs.append((st, r["_ft"]))
    if on: segs.append((st, rows[-1]["_ft"]))
    out = []
    for a, b in segs:
        out.append(dict(seg_a=a, seg_b=b, dur=b - a,
                        wall=start_local + datetime.timedelta(seconds=a - ft0),
                        ft0=ft0, rows=rows))
    return out

def _hav(a, b, c, d):
    R = 6371000.0; p = math.radians
    x = math.sin(p(c - a) / 2) ** 2 + math.cos(p(a)) * math.cos(p(c)) * math.sin(p(d - b) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))

def _interp(rows, ft, keys):
    lo = hi = None
    for r in rows:
        if r["_ft"] <= ft: lo = r
        if r["_ft"] >= ft: hi = r; break
    lo = lo or hi; hi = hi or lo
    if lo is None: return {k: "" for k in keys}
    span = (hi["_ft"] - lo["_ft"]) or 1.0; w = (ft - lo["_ft"]) / span; o = {}
    for k in keys:
        try: o[k] = float(lo[k]) + (float(hi[k]) - float(lo[k])) * w
        except Exception: o[k] = lo.get(k, "")
    return o

def model_token(meta) -> str:
    """Map decoded log identity to a device token (devices.flightlog_models).

    aircraftName rules are tried before droneType rules, as the name is the
    more specific of the two."""
    dev = S.active()["devices"]
    rules = dev["flightlog_models"]
    an = (meta.get("aircraftName") or "").lower()
    for r in rules:
        if r.get("aircraft") and r["aircraft"].lower() in an:
            return r["token"]
    dt = str(meta.get("droneType") or "")
    for r in rules:
        if r.get("drone_type") is not None and str(r["drone_type"]) == dt:
            return r["token"]
    return dev["dji_fallback"]

# --------------------------------------------------------------------------- #
#  Minimal MP4 reader (duration / fps / nframes / creation) - clips without SRT
# --------------------------------------------------------------------------- #
_MAC_EPOCH = 2082844800
def mp4_meta(path):
    try:
        with open(path, "rb") as f:
            def boxes(start, end):
                off = start
                while off < end - 8:
                    f.seek(off); hdr = f.read(8)
                    if len(hdr) < 8: break
                    size = struct.unpack(">I", hdr[:4])[0]; typ = hdr[4:8]; hs = 8
                    if size == 1: size = struct.unpack(">Q", f.read(8))[0]; hs = 16
                    elif size == 0: size = end - off
                    if size < hs: break
                    yield typ, off + hs, off + size
                    off += size
            fsize = os.path.getsize(path); moov = None
            for typ, s, e in boxes(0, fsize):
                if typ == b"moov": moov = (s, e); break
            if not moov: return (None, None, None, None)
            dur = creation = None
            for typ, s, e in boxes(*moov):
                if typ == b"mvhd":
                    f.seek(s); ver = f.read(1)[0]; f.read(3)
                    if ver == 1:
                        ct = struct.unpack(">Q", f.read(8))[0]; f.read(8)
                        ts = struct.unpack(">I", f.read(4))[0]; d = struct.unpack(">Q", f.read(8))[0]
                    else:
                        ct = struct.unpack(">I", f.read(4))[0]; f.read(4)
                        ts = struct.unpack(">I", f.read(4))[0]; d = struct.unpack(">I", f.read(4))[0]
                    dur = d / ts if ts else None
                    if ct:
                        try: creation = datetime.datetime.utcfromtimestamp(ct - _MAC_EPOCH)
                        except Exception: creation = None
            fps = nframes = None
            for typ, s, e in boxes(*moov):
                if typ == b"trak":
                    mts = None; vid = False; nsamp = 0; dm = None
                    for t2, s2, e2 in boxes(s, e):
                        if t2 == b"mdia":
                            for t3, s3, e3 in boxes(s2, e2):
                                if t3 == b"mdhd":
                                    f.seek(s3); vv = f.read(1)[0]; f.read(3)
                                    if vv == 1: f.read(16); mts = struct.unpack(">I", f.read(4))[0]; dm = struct.unpack(">Q", f.read(8))[0]
                                    else:       f.read(8);  mts = struct.unpack(">I", f.read(4))[0]; dm = struct.unpack(">I", f.read(4))[0]
                                elif t3 == b"hdlr":
                                    f.seek(s3 + 8); vid = (f.read(4) == b"vide")
                                elif t3 == b"minf":
                                    for t4, s4, e4 in boxes(s3, e3):
                                        if t4 == b"stbl":
                                            for t5, s5, e5 in boxes(s4, e4):
                                                if t5 == b"stts":
                                                    f.seek(s5); f.read(4); ncnt = struct.unpack(">I", f.read(4))[0]; tot = 0
                                                    for _ in range(ncnt):
                                                        cnt = struct.unpack(">I", f.read(4))[0]; f.read(4); tot += cnt
                                                    nsamp = tot
                    if vid and mts and dm:
                        md = dm / mts; nframes = nsamp; fps = round(nsamp / md, 3) if md else None
                        if not dur: dur = md
                        break
            return (dur, fps, nframes, creation)
    except Exception:
        return (None, None, None, None)

# --------------------------------------------------------------------------- #
#  SRT reconstruction + telemetry sidecar
# --------------------------------------------------------------------------- #
def _td(s):
    if s < 0: s = 0
    h = int(s // 3600); m = int(s % 3600 // 60); sec = int(s % 60); ms = int(round((s - int(s)) * 1000))
    if ms == 1000: ms = 0; sec += 1
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"
def _r1(x):
    try: return f"{float(x):.1f}"
    except Exception: return str(x)

def reconstruct_srt_text(seg, pos, clip_wall, duration, fps, nframes) -> str:
    if not fps or fps <= 0: fps = 29.97
    if not nframes or nframes <= 0: nframes = int(round((duration or 0) * fps))
    rows = seg["rows"]; keys = ["latitude", "longitude", "rel_alt_m", "abs_alt_m", "gb_pitch", "ac_yaw"]
    out = []
    for i in range(nframes):
        t = i / fps; v = _interp(rows, pos + t, keys)
        stamp = (clip_wall + datetime.timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S.") + f"{int((t-int(t))*1000):03d}"
        try: lat = f"{v['latitude']:.6f}"; lon = f"{v['longitude']:.6f}"
        except Exception: lat = v.get('latitude', ''); lon = v.get('longitude', '')
        try: rel = f"{v['rel_alt_m']:.3f}"; ab = f"{v['abs_alt_m']:.3f}"
        except Exception: rel = v.get('rel_alt_m', ''); ab = v.get('abs_alt_m', '')
        out.append(f"{i+1}\n{_td(t)} --> {_td((i+1)/fps)}\n<font size=\"28\">SrtCnt : {i+1}, DiffTime : {int(round(1000/fps))}ms\n"
                   f"{stamp}\n[latitude: {lat}] [longitude: {lon}] [rel_alt: {rel} abs_alt: {ab}] "
                   f"[gb_pitch: {_r1(v.get('gb_pitch',''))}] [ac_yaw: {_r1(v.get('ac_yaw',''))}] "
                   f"[srt_source: reconstructed_from_flightlog] </font>\n")
    return "\n".join(out) + "\n"

def write_sidecar(sidecar_path, seg, pos, duration):
    rows = seg["rows"]; a, b = pos, pos + (duration or 0)
    win = [r for r in rows if a - 1 <= r["_ft"] <= b + 1]
    with open(sidecar_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["video_t_s"] + SIDECAR_COLS)
        for r in win:
            w.writerow([round(r["_ft"] - pos, 2)] + [r.get(c, "") for c in SIDECAR_COLS])
    return len(win)

# --------------------------------------------------------------------------- #
#  Folder-level orchestration
# --------------------------------------------------------------------------- #
class FolderLogs:
    """Decode every DJI log in a session folder once, expose segment matching."""
    def __init__(self, folder, binary, api_key, log=None):
        self.folder = folder; self.segments = []; self.decoded = []
        self.errors = []; self.warnings = []
        say = log or (lambda *a, **k: None)
        logs = find_logs(folder)
        if not logs:
            self.errors.append("Aucun log DJI (DJIFlightRecord_*.txt) dans le dossier."); return
        if not api_key:
            self.errors.append("Cle API DJI manquante (requise pour dechiffrer les logs v13)."); return
        if not binary:
            self.errors.append("dji-log binary not found (set FIELDCLIP_DJI_LOG or tools.dji_log, or put it on the PATH)."); return
        for lp in logs:
            m = LOG_RE.search(os.path.basename(lp))
            if not m:
                self.warnings.append(f"{os.path.basename(lp)} : nom de log non reconnu."); continue
            start = datetime.datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H-%M-%S")
            rows, meta = decode_log(binary, lp, api_key)
            if rows is None:
                self.warnings.append(f"{os.path.basename(lp)} : decodage echoue ({meta})."); continue
            self.decoded.append((os.path.basename(lp), meta))
            for seg in _isvideo_segments(rows, start):
                seg["meta"] = meta; seg["log"] = os.path.basename(lp)
                self.segments.append(seg)
            say(f"    log decode : {os.path.basename(lp)}  ({len(rows)} pts, {meta.get('aircraftName','?')})")

    def ok(self): return not self.errors

    def calibrate_offset(self, clips):
        """Derive the camera-clock -> log-local-time offset (seconds) for clips
        WITHOUT an SRT, using clips whose duration uniquely matches one segment.
        clips: list of dict(mvhd=datetime|None, duration=float|None, ...)."""
        offs = []
        for c in clips:
            mv = c.get("mvhd"); dur = c.get("duration")
            if mv is None or not dur: continue
            # Same day only (the camera clock is off by a time zone at most):
            # among weeks of logs, 5-min clips are never unique by length.
            near = [s for s in self.segments if abs(s["dur"] - dur) <= 3
                    and abs((s["wall"] - mv).total_seconds()) <= 15 * 3600]
            if len(near) == 1:
                offs.append((near[0]["wall"] - mv).total_seconds())
        self.cam_offset = statistics.median(offs) if offs else None
        return self.cam_offset

    def anchor(self, clip):
        """Best wall-clock start for a clip: SRT start if present, else the
        camera creation time shifted by the calibrated offset, else the time in
        the filename (a clip whose header time a remux has erased)."""
        ss = clip.get("srt_start")
        if ss is not None: return ss
        mv = clip.get("mvhd")
        if mv is not None and getattr(self, "cam_offset", None) is not None:
            return mv + datetime.timedelta(seconds=self.cam_offset)
        return clip.get("name_ts")

    def match(self, anchor_dt, duration, gps=None):
        """Return dict(seg,pos,gps_dist) for the recording segment containing anchor_dt, else None."""
        if anchor_dt is None: return None
        cont = [s for s in self.segments
                if s["wall"] - datetime.timedelta(seconds=15) <= anchor_dt
                <= s["wall"] + datetime.timedelta(seconds=s["dur"] + 15)]
        if not cont and duration:
            # The anchor can miss by tens of seconds: after a power cycle the
            # camera clock (mvhd) is not yet synced. One segment within 10 min
            # whose length is the clip's is then the clip, from its start.
            near = [s for s in self.segments
                    if abs((anchor_dt - s["wall"]).total_seconds()) <= 600
                    and abs(s["dur"] - duration) <= 3]
            if len(near) == 1:
                return dict(seg=near[0], pos=near[0]["seg_a"], gps_dist=None)
        if not cont: return None
        if gps:
            scored = []
            for s in cont:
                pos = s["seg_a"] + max(0.0, (anchor_dt - s["wall"]).total_seconds())
                v = _interp(s["rows"], pos, ["latitude", "longitude"])
                try: d = _hav(gps[0], gps[1], float(v["latitude"]), float(v["longitude"]))
                except Exception: d = 9e9
                scored.append((d, s, pos))
            scored.sort(key=lambda x: x[0])
            if scored[0][0] <= 500:
                d, s, pos = scored[0]; return dict(seg=s, pos=pos, gps_dist=d)
        cont.sort(key=lambda s: abs((anchor_dt - s["wall"]).total_seconds()))
        s = cont[0]; pos = s["seg_a"] + max(0.0, (anchor_dt - s["wall"]).total_seconds())
        return dict(seg=s, pos=pos, gps_dist=None)

def validate_session(folder):
    """Return (ok, errors, warnings). Rule: MP4 + DJI log required; SRT may be reconstructed."""
    errors = []; warnings = []
    isdir = os.path.isdir(folder)
    vids = [f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in (".mp4", ".mov")] if isdir else []
    logs = find_logs(folder)
    srts = [f for f in os.listdir(folder) if f.lower().endswith(".srt")] if isdir else []
    if not vids: errors.append("Aucune video (.MP4/.MOV) dans le dossier.")
    if not logs: errors.append("Aucun log de vol DJI (DJIFlightRecord_*.txt) dans le dossier.")
    if vids and not srts:
        warnings.append("Aucun SRT : ils seront reconstruits depuis les logs.")
    return (not errors, errors, warnings)
