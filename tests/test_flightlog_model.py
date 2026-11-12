# -*- coding: utf-8 -*-
"""Flight-log discovery and model mapping, offline (no dji-log, no API key)."""
import os, sys, shutil, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: F401  (isolated config + test profile)
from fieldclip import flightlog as F

fails = []
def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}" + ("" if ok else f"\n         got  {got!r}\n         want {want!r}"))
    if not ok:
        fails.append(label)

def touch(*parts):
    p = os.path.join(*parts)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").close()
    return p

tmp = tempfile.mkdtemp(prefix="fieldclip_fl_")
try:
    LOG = "DJIFlightRecord_2026-11-12_[09-14-39].txt"

    print("\n1. log discovery")
    # Clips in 100MEDIA/, records beside it (card copied as-is).
    sess = os.path.join(tmp, "Session_A"); media = os.path.join(sess, "100MEDIA")
    touch(media, "DJI_0142.MP4"); up = touch(sess, LOG)
    check("log one level up is found", F.find_logs(media), [up])
    # Records in the clip folder: the parent is not searched as well.
    sess_b = os.path.join(tmp, "Session_B"); media_b = os.path.join(sess_b, "100MEDIA")
    here = touch(media_b, LOG); touch(sess_b, "DJIFlightRecord_2026-01-01_[00-00-00].txt")
    check("log beside the clips wins over the parent", F.find_logs(media_b), [here])
    # Records one level down still work.
    sess_c = os.path.join(tmp, "Session_C")
    down = touch(sess_c, "FlightRecord", LOG)
    check("log one level down is found", F.find_logs(sess_c), [down])
    # Another session next to this one is not pulled in.
    sess_d = os.path.join(tmp, "Session_D", "100MEDIA"); os.makedirs(sess_d)
    check("sibling session's logs are not searched", F.find_logs(sess_d), [])

    print("\n2. model from decoded log identity")
    cases = [
        ({"aircraftName": "DJI Mini 3", "droneType": ""}, "Mini3"),
        ({"aircraftName": "DJI Mini 3 Pro", "droneType": ""}, "Mini3Pro"),
        ({"aircraftName": "My drone", "droneType": "87"}, "Mini3"),
        ({"aircraftName": "My drone", "droneType": "Unknown(87)"}, "Mini3"),
        ({"aircraftName": "My drone", "droneType": "Unknown(73)"}, "Mini3Pro"),
        ({"aircraftName": "My drone", "droneType": "Mini4Pro"}, "Mini4Pro"),
        ({"aircraftName": "My drone", "droneType": "Unknown(1)"}, "DJI_Other"),
    ]
    for meta, want in cases:
        check(f"{meta} -> {want}", F.model_token(meta), want)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nFAILED: " + ", ".join(fails) if fails else "\nall passed")
sys.exit(1 if fails else 0)
