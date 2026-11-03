# -*- coding: utf-8 -*-
"""Checks for the companion + drone-model fixes, on a throwaway folder.
Only the file NAMES matter here, so the SRT / sidecar contents are stand-ins."""
import os, sys, shutil, tempfile, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import RV as R

fails = []
def check(label, got, want):
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}\n         got  {got!r}\n         want {want!r}"
          if not ok else f"  [ok ] {label}")
    if not ok:
        fails.append(label)

tmp = tempfile.mkdtemp(prefix="fieldclip_test_")
try:
    # --- build a folder that reproduces the reported state: videos renamed by an
    #     earlier run, telemetry sidecars left under their raw camera name ------
    stem_a = "Mini3Pro_2026-07-14_11-08-49_Herd-A_Lake-Shore"
    stem_b = "Mini3Pro_2026-07-14_12-46-18_Herd-B_Lake-Shore"
    case = os.path.join(tmp, "Rey_moessoe_mini3pro")
    os.makedirs(case)
    # The sidecar carries its own UTC start (Denmark summer = UTC+2): that is
    # what re-attaches it to a clip renamed with local time.
    utc_of = {stem_a: "2026-07-14T09:08:49", stem_b: "2026-07-14T10:46:18"}
    for stem, raw in ((stem_a, "DJI_0089"), (stem_b, "DJI_0091")):
        open(os.path.join(case, stem + ".MP4"), "wb").write(b"\0" * 32)
        with open(os.path.join(case, stem + ".SRT"), "w") as fh:
            fh.write("1\n00:00:00,000 --> 00:00:00,033\n<font>2026-07-14 11:08:49.000</font>\n")
        with open(os.path.join(case, raw + ".flightlog.csv"), "w") as fh:
            fh.write("video_t_s,utc,latitude,longitude\n"
                     f"0.0,{utc_of[stem]}.000Z,55.0,9.0\n"
                     f"2.0,{utc_of[stem][:-2]}51.000Z,55.0,9.0\n")
    open(os.path.join(case, "field_notes.csv"), "w").write("a,b\n1,2\n")

    print("\n1. stale sidecar re-attached to a renamed clip")
    got = sorted(os.path.basename(c)
                 for c in R.find_companion_files(os.path.join(case, stem_a + ".MP4")))
    check("companions of clip A", got, ["DJI_0089.flightlog.csv", stem_a + ".SRT"])
    got = sorted(os.path.basename(c)
                 for c in R.find_companion_files(os.path.join(case, stem_b + ".MP4")))
    check("companions of clip B", got, ["DJI_0091.flightlog.csv", stem_b + ".SRT"])

    print("\n2. an unrelated csv stays an orphan, the sidecars do not")
    vids, orph = R.find_videos_and_companions(case)
    check("videos found", len(vids), 2)
    check("orphans", sorted(os.path.basename(o) for o in orph), ["field_notes.csv"])

    print("\n3. renaming carries the compound suffix")
    comp = os.path.join(case, "DJI_0089.flightlog.csv")
    check("suffix split", R.split_companion_name(comp)[1], ".flightlog.csv")

    print("\n4. drone model from the folder name")
    v = os.path.join(case, stem_a + ".MP4")
    raw_named = os.path.join(case, "DJI_0400.MP4")
    open(raw_named, "wb").write(b"\0" * 32)
    g = R.guess_from_path(raw_named, [])
    check("raw clip in a *_mini3pro folder", g["device"], "Mini3Pro")
    check("source badge", "device_from_path" in g["guessed"], True)

    print("\n5. the flight-log memo outranks the folder name")
    R.write_device_memo(case, "Mini4Pro", source="flightlog")
    check("memo read back", R.read_device_memo(case), "Mini4Pro")
    g = R.guess_from_path(raw_named, [])
    check("memo wins", g["device"], "Mini4Pro")
    check("source badge", "device_from_memo" in g["guessed"], True)
    check("memo is hidden from scans", R.is_hidden(os.path.join(case, R.DEVICE_MEMO_FILE)), True)
    os.remove(os.path.join(case, R.DEVICE_MEMO_FILE))

    print("\n6. fallback to the already-renamed files when the folder says nothing")
    plain = os.path.join(tmp, "session_01")
    os.makedirs(plain)
    open(os.path.join(plain, stem_a + ".MP4"), "wb").write(b"\0")
    open(os.path.join(plain, "DJI_0400.MP4"), "wb").write(b"\0")
    g = R.guess_from_path(os.path.join(plain, "DJI_0400.MP4"), [])
    check("inherited from siblings", g["device"], "Mini3Pro")
    check("source badge", "device_from_siblings" in g["guessed"], True)

    print("\n7. a name that is already correct is left alone")
    g = R.guess_from_path(os.path.join(case, stem_a + ".MP4"), [])
    check("explicit token wins", g["device"], "Mini3Pro")
    check("not overridden by path", "device_from_path" not in g["guessed"], True)
    check("Mini3 folder is not read as Mini3Pro",
          R.detect_device_from_path("C:/x/site_mini3/DJI_1.MP4"), "Mini3")

    print("\n8. only the current device tokens are written")
    check("built-in list", R.BUILTIN_DEVICES,
          ["Mini4Pro", "Mini3Pro", "Mini3", "DJI_Other", "Phone"])
    check("no old token left", [d for d in R.BUILTIN_DEVICES if d.startswith("DJI_Mini")], [])
    for old, new in (("DJI_Mini_3_Pro", "Mini3Pro"), ("DJI_Mini_4_Pro", "Mini4Pro"),
                     ("DJI_Mini_3", "DJI_Other"), ("DJI_Mini_4", "DJI_Other")):
        g = R.guess_from_filename(f"{old}_2026-04-11_09-32-46_Herd-A_Lake.MP4", [])
        check(f"old name {old} read as {new}", g["device"], new)
        name = R.build_new_basename(g["device"], g["timestamp"], False,
                                    group="Herd-A", site="Lake")
        check(f"renamed {old} carries no old token", "DJI_Mini" in name, False)
    check("raw Mini 3 name is generic", R.detect_device("DJI_0083.MP4"), "DJI_Other")
    check("raw Mini 3 family", R.dji_family("DJI_0083.MP4"), "Mini 3 series")
    check("raw Mini 4 family", R.dji_family("DJI_20260331134254_0158_D.MP4"), "Mini 4 series")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("ALL PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
