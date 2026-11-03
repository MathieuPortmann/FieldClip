#!/usr/bin/env python3
"""Run every test script; network tests skip themselves when no server is set.

    python tests/run_all.py            all tests
    python tests/run_all.py --offline  skip the scripts that need a server
"""
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NETWORK = {"test_steps3456.py", "test_e2e_export.py"}

scripts = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
if "--offline" in sys.argv:
    scripts = [s for s in scripts if os.path.basename(s) not in NETWORK]

failed = []
for s in scripts:
    name = os.path.basename(s)
    t0 = time.time()
    r = subprocess.run([sys.executable, s], cwd=HERE,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, encoding="utf-8", errors="replace")
    status = "ok" if r.returncode == 0 else "FAILED"
    skipped = "SKIPPED" in r.stdout
    print(f"  {('skipped' if skipped else status):8} {name}  ({time.time() - t0:.0f} s)")
    if r.returncode != 0:
        failed.append(name)
        print("\n".join("      " + l for l in r.stdout.splitlines()[-30:]))

print(f"\n  {len(scripts) - len(failed)} of {len(scripts)} scripts passed")
sys.exit(1 if failed else 0)
