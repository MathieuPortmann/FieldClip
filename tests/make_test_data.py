#!/usr/bin/env python3
"""
Generate a synthetic dataset that exercises the awkward paths of the export.

Uses the ffmpeg FieldClip finds (bin/ or the PATH), so it needs nothing extra.
Everything lands under a folder you pass on the command line (default: a temp
directory), and nothing here ever touches real footage.
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import RV

FFMPEG = RV.find_bundled_ffmpeg()


def _mp4(path, seconds=2, faststart=True, size_hint="320x240"):
    """Render a small real MP4 so ffprobe and the moov checks have something
    genuine to look at."""
    args = [FFMPEG, "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size_hint}:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if faststart:
        args += ["-movflags", "+faststart"]
    args += [path]
    subprocess.run(args, check=True, timeout=120)


def _srt(path, start="2026-06-10 18:58:20"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "1\n00:00:00,000 --> 00:00:01,000\n"
            f"<font size=\"28\">FrameCnt: 1, DiffTime: 1ms\n{start},000\n"
            "[latitude: 54.9876] [longitude: 11.2345] [rel_alt: 40.0 abs_alt: 42.0] </font>\n\n")


def build(root: str) -> dict:
    src = os.path.join(root, "source")
    dst = os.path.join(root, "dest")
    os.makedirs(src, exist_ok=True)
    os.makedirs(dst, exist_ok=True)
    made = {}

    # 1-3. Healthy DJI-named clips, each with its .SRT companion
    for i in range(1, 4):
        name = f"DJI_202606101858{i:02d}_000{i}_D"
        p = os.path.join(src, name + ".MP4")
        _mp4(p, seconds=1)
        _srt(os.path.join(src, name + ".SRT"))
        made[f"healthy_{i}"] = p

    # 4. moov deliberately at the end -> should trigger the faststart path
    p = os.path.join(src, "DJI_20260610185810_0010_D.MP4")
    _mp4(p, seconds=1, faststart=False)
    made["moov_at_end"] = p
    _srt(os.path.join(src, "DJI_20260610185810_0010_D.SRT"))

    # 5. Truncated file: header only, no moov -> must be SKIPPED, not crash
    p = os.path.join(src, "DJI_20260610185811_0011_D.MP4")
    whole = os.path.join(root, "_whole.mp4")
    _mp4(whole, seconds=2, faststart=False)
    with open(whole, "rb") as fi, open(p, "wb") as fo:
        fo.write(fi.read(os.path.getsize(whole) // 3))
    os.remove(whole)
    made["truncated"] = p

    # 6. A larger file, to exercise multi-chunk transfer and the throttling
    p = os.path.join(src, "DJI_20260610185812_0012_D.MP4")
    _mp4(p, seconds=1)
    with open(p, "ab") as f:
        f.write(os.urandom(24 * 1024 * 1024))     # ~24 MB => 6 chunks
    made["large"] = p

    # 7. Orphan companion: an .SRT with no matching video
    _srt(os.path.join(src, "DJI_20260610185899_0099_D.SRT"))
    made["orphan_srt"] = os.path.join(src, "DJI_20260610185899_0099_D.SRT")

    # 8. Destination pre-filled to 50% -- THE case that used to corrupt
    half_src = made["healthy_1"]
    half_dst = os.path.join(dst, "half.MP4")
    with open(half_src, "rb") as fi:
        data = fi.read()
    with open(half_dst, "wb") as f:
        f.write(b"\xAA" * (len(data) // 2))
    made["half_filled_dest"] = half_dst

    # 9. Destination already complete -> must be skipped
    done_dst = os.path.join(dst, "complete.MP4")
    shutil.copyfile(half_src, done_dst)
    made["complete_dest"] = done_dst

    made["_source_dir"] = src
    made["_dest_dir"] = dst
    return made


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="fieldclip_testdata_")
    os.makedirs(root, exist_ok=True)
    if not FFMPEG:
        print("ffmpeg not found - cannot build the dataset.")
        sys.exit(1)
    made = build(root)
    print(f"\n  Test dataset in {root}\n  " + "-" * 58)
    for k, v in made.items():
        if k.startswith("_"):
            continue
        print(f"  {k:20} {os.path.basename(v):42} {os.path.getsize(v):>10,} B")
    print(f"  source dir: {made['_source_dir']}")
    print(f"  dest   dir: {made['_dest_dir']}")
