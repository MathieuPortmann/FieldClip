#!/usr/bin/env python3
"""Offline tests for fieldclip/naming.py: templates, build -> parse round trips."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: F401  (isolated config dir, test profile)
from fieldclip import naming as NM
from fieldclip import settings as S

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def profile(**over):
    s = S.deep_merge(S.load_profile(os.path.join(_common.HERE, "test_profile.json")), over)
    S.validate(s)
    return NM.Naming(s)


print("\n  Naming")
print("  " + "=" * 66)

# ── template validation ──────────────────────────────────────────────────────
for bad, why in [("{device}_{date}_{site}", "missing {time}"),
                 ("{device}_{time}_{date}", "time before date"),
                 ("{date}_{site}_{time}", "date and time not adjacent"),
                 ("{date}_{time}_{nope}", "unknown field"),
                 ("{date}__{time}", "doubled separator"),
                 ("{site}_word_{date}_{time}", "literal between fields"),
                 ("{date}_{time}_{site}_{site}", "duplicate field")]:
    try:
        NM.parse_template(bad, "_")
        check(f"rejects template: {why}", False, bad)
    except ValueError:
        check(f"rejects template: {why}", True)

# ── default template, round trip ─────────────────────────────────────────────
nm = profile()
ts = "2025-11-05_10-05-49"
for dev in ("Mini4Pro", "Phone", "DJI_Other"):
    for site in ("North-Meadow", "Lake-Shore", "Lake", "Old-Forest"):
        for grp in ("Herd-A", "Herd-B", "Mixed"):
            name = nm.build(dev, ts, False, grp, site) + ".MP4"
            r = nm.parse(name, [])
            ok = (r["matched"] and r["device"] == dev and r["site"] == site
                  and r["group"] == grp and r["timestamp"] == ts and not r["is_training"])
            if not ok:
                check(f"round trip {name}", False, str(r))
check("default template: every build parses back", not [f for f in FAIL if f.startswith("round")])

name = nm.build("Mini3", ts, True, None, "Lake")
check("training name", name == "Mini3_2025-11-05_10-05-49_Lake_Training", name)
r = nm.parse(name + ".MP4", [])
check("training name parses back", r["is_training"] and r["site"] == "Lake", str(r))
check("is_training_name", nm.is_training_name(name + ".MP4")
      and not nm.is_training_name("Mini3_2025-11-05_10-05-49_Herd-A_Lake.MP4"))
name = nm.build("Mini3", ts, True, None, "")
check("training without site drops the optional field",
      name == "Mini3_2025-11-05_10-05-49_Training", name)

# "Lake" must not swallow "Lake-Shore" (longest match first)
r = nm.parse("Mini3_2025-11-05_10-05-49_Herd-A_Lake-Shore.MP4", [])
check("longest site wins", r["site"] == "Lake-Shore", r["site"])
# aliases and legacy device tokens
r = nm.parse("Mini3_2025-11-05_10-05-49_Mixed_Old_Forrest.MP4", [])
check("site alias is read as the canonical name", r["site"] == "Old-Forest", r["site"])
r = nm.parse("DJI_Mini_4_Pro_2025-11-05_10-05-49_Herd-A_Lake.MP4", [])
check("legacy device token mapped", r["device"] == "Mini4Pro", r["device"])
r = nm.parse("DJI_Mini_3_2025-11-05_10-05-49_Herd-A_Lake.MP4", [])
check("family-only legacy token -> fallback", r["device"] == "DJI_Other", r["device"])
r = nm.parse("DJI_0083.MP4", [])
check("raw camera name is not matched", not r["matched"])
r = nm.parse("MyCam_2025-11-05_10-05-49_Herd-A_Lake.MP4", ["MyCam"])
check("custom device recognised", r["device"] == "MyCam", r["device"])

# ── other templates ──────────────────────────────────────────────────────────
nm2 = profile(naming={"template": "{site}_{date}_{time}_{device}",
                      "training_template": "TR_{date}_{time}_{device}",
                      "separator": "_", "date_format": "%Y%m%d", "time_format": "%H%M%S"})
name = nm2.build("Mini4Pro", ts, False, "Herd-A", "Lake-Shore")
check("custom template + compact date", name == "Lake-Shore_20251105_100549_Mini4Pro", name)
r = nm2.parse(name + ".mp4", [])
check("custom template parses back", r["site"] == "Lake-Shore" and r["device"] == "Mini4Pro"
      and r["timestamp"] == ts, str(r))
name = nm2.build("Phone", ts, True)
check("prefix-literal training name", name == "TR_20251105_100549_Phone", name)
r = nm2.parse(name + ".mp4", [])
check("prefix-literal training parses back", r["is_training"] and r["device"] == "Phone", str(r))

nm3 = profile(naming={"separator": "-", "template": "{device}-{date}-{time}-{site}",
                      "training_template": "{device}-{date}-{time}-train",
                      "date_format": "%Y%m%d", "time_format": "%H%M%S"},
              fields={"site": {"values": []}})
name = nm3.build("Phone", ts, False, None, "free text")
check("free-text site + dash separator", name == "Phone-20251105-100549-free_text", name)
r = nm3.parse(name + ".mp4", [])
check("free-text site parses back", r["site"] == "free_text", r["site"])

# ── destination folders ──────────────────────────────────────────────────────
check("dest_parts main", nm.dest_parts(False, {"site": "Lake"}) == ["Main", "Lake"])
check("dest_parts training", nm.dest_parts(True, {"site": "Lake"}) == ["Training", "Lake"])
nm4 = profile(layout={"path": "{category}/{date}/{group}"})
check("dest_parts with date and group",
      nm4.dest_parts(False, {"site": "Lake", "group": "Herd-A", "timestamp": ts})
      == ["Main", "2025-11-05", "Herd-A"],
      str(nm4.dest_parts(False, {"site": "Lake", "group": "Herd-A", "timestamp": ts})))

print("  " + "=" * 66)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
