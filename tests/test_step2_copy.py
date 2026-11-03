#!/usr/bin/env python3
"""
Step 2 tests - copy integrity.

The headline case is the half-filled destination. On the original code
copy_with_progress opened it in 'ab' even though the caller passed
overwrite=True, so the result was source-appended-to-junk: a file of plausible
size that the size-only verification accepted, and that the user was then
invited to delete the originals for.
"""

import hashlib
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import RV

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


tmp = tempfile.mkdtemp(prefix="fieldclip_step2_")
src = os.path.join(tmp, "source.MP4")
with open(src, 'wb') as f:
    f.write(os.urandom(5 * 1024 * 1024))
src_hash, src_size = sha(src), os.path.getsize(src)

print("\n  Step 2 - copy integrity")
print("  " + "-" * 60)

# --- 1. Plain copy ---------------------------------------------------------
dst = os.path.join(tmp, "a.MP4")
st = RV.copy_with_progress(src, dst, overwrite=True)
check("1. Plain copy is byte-identical", sha(dst) == src_hash, f"status={st}")

# --- 2. THE BUG: destination pre-filled to 50% -----------------------------
dst = os.path.join(tmp, "b.MP4")
with open(dst, 'wb') as f:
    f.write(b'\xAA' * (src_size // 2))      # junk, not a prefix of the source
st = RV.copy_with_progress(src, dst, overwrite=True)
same_size = os.path.getsize(dst) == src_size
identical = sha(dst) == src_hash
check("2. Half-filled destination is OVERWRITTEN, not appended",
      same_size and identical,
      f"size={os.path.getsize(dst)} expected={src_size} identical={identical}")

# --- 3. Destination already complete ---------------------------------------
dst = os.path.join(tmp, "c.MP4")
shutil.copyfile(src, dst)
st = RV.copy_with_progress(src, dst, overwrite=False)
check("3. Identical destination is skipped", st == 'skipped', f"status={st}")

# --- 4. Resume from our own .part ------------------------------------------
dst = os.path.join(tmp, "d.MP4")
part = dst + '.part'
with open(src, 'rb') as fi, open(part, 'wb') as fo:
    fo.write(fi.read(src_size // 3))        # a genuine prefix, as we would write
st = RV.copy_with_progress(src, dst, overwrite=True, resume=True)
check("4. Resume from .part reconstructs the file exactly",
      sha(dst) == src_hash and st == 'resumed', f"status={st}")

# --- 5. No .part left behind on success ------------------------------------
leftovers = [f for f in os.listdir(tmp) if f.endswith('.part')]
check("5. No .part files left behind", not leftovers, f"found={leftovers}")

# --- 6. copystat failure must not trigger a recopy -------------------------
dst = os.path.join(tmp, "e.MP4")
_real = shutil.copystat
shutil.copystat = lambda *a, **k: (_ for _ in ()).throw(OSError("simulated WinFsp failure"))
try:
    st = RV.copy_with_progress(src, dst, overwrite=True)
    ok = sha(dst) == src_hash
    check("6. copystat failure is swallowed, copy still valid", ok, f"status={st}")
except Exception as e:
    check("6. copystat failure is swallowed, copy still valid", False, f"raised {e}")
finally:
    shutil.copystat = _real

# --- 7. Short copy is detected ---------------------------------------------
dst = os.path.join(tmp, "f.MP4")
_open = RV.open if hasattr(RV, 'open') else open


class _TruncatingFile:
    """Silently drops writes after the 3rd chunk, simulating a mount that dies
    mid-write while still reporting success."""
    def __init__(self, real): self._r = real; self._n = 0
    def write(self, b):
        # 5 MB source in 4 MB chunks = 2 writes, so drop everything after the
        # first one. (Was > 3, i.e. never triggered - the test proved nothing.)
        self._n += 1
        return len(b) if self._n > 1 else self._r.write(b)
    # Dunder methods are looked up on the type, so __getattr__ never sees them.
    def __enter__(self): self._r.__enter__(); return self
    def __exit__(self, *a): return self._r.__exit__(*a)
    def __getattr__(self, k): return getattr(self._r, k)


import builtins
_real_open = builtins.open


def _patched_open(path, mode='r', *a, **k):
    f = _real_open(path, mode, *a, **k)
    if str(path).endswith('.part') and 'w' in mode:
        return _TruncatingFile(f)
    return f


builtins.open = _patched_open
try:
    RV.copy_with_progress(src, dst, overwrite=True)
    check("7. Truncated write raises instead of publishing a short file", False,
          "no exception raised")
except IOError as e:
    check("7. Truncated write raises instead of publishing a short file",
          "short copy" in str(e), str(e))
except Exception as e:
    check("7. Truncated write raises instead of publishing a short file", False,
          f"unexpected {type(e).__name__}: {e}")
finally:
    builtins.open = _real_open

check("7b. No partial file published at the final path", not os.path.exists(dst))

shutil.rmtree(tmp, ignore_errors=True)
print("  " + "-" * 60)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("  FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
