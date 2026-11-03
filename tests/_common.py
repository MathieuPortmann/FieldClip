# -*- coding: utf-8 -*-
"""
Shared setup for the test scripts. Import it before anything from fieldclip:

    from _common import RV, S

- The per-user config goes to a throw-away folder, so a test run never reads
  or overwrites your own settings or credentials.
- The profile is tests/test_profile.json (fictional sites), unless
  FIELDCLIP_PROFILE is set.
- Network tests read their server from the environment and are skipped when
  it is not set:

    FIELDCLIP_TEST_WEBDAV_URL   share root, e.g. https://dav.example.org/
    FIELDCLIP_TEST_SECRET       credential for it
    FIELDCLIP_TEST_AUTH         sharelink | basic | none   (default sharelink)
    FIELDCLIP_TEST_USER         user name for basic auth
    FIELDCLIP_TEST_MOUNT        where the same share is mounted (optional)
    FIELDCLIP_TEST_SUBDIR       writable folder inside the share (optional)
    FIELDCLIP_TEST_CA_BUNDLE    extra CA certificate (optional), e.g.
                                profiles/certs/GEANT_TLS_RSA_1.pem for ERDA
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

_TMP_CFG = tempfile.mkdtemp(prefix="fieldclip_testcfg_")
os.environ["FIELDCLIP_CONFIG_DIR"] = _TMP_CFG

WEBDAV_URL = os.environ.get("FIELDCLIP_TEST_WEBDAV_URL", "").strip()
if WEBDAV_URL and not WEBDAV_URL.endswith("/"):
    WEBDAV_URL += "/"
SECRET = os.environ.get("FIELDCLIP_TEST_SECRET", "").strip()
MOUNT = os.path.expanduser(os.environ.get("FIELDCLIP_TEST_MOUNT", "").strip())
SUBDIR = os.environ.get("FIELDCLIP_TEST_SUBDIR", "").strip().strip("/")

if not os.environ.get("FIELDCLIP_PROFILE"):
    prof = {"extends": os.path.join(HERE, "test_profile.json")}
    if WEBDAV_URL:
        prof["remote"] = {"auth": os.environ.get("FIELDCLIP_TEST_AUTH", "sharelink"),
                          "username": os.environ.get("FIELDCLIP_TEST_USER", ""),
                          "webdav": {"url": WEBDAV_URL,
                                     "ca_bundle": os.environ.get("FIELDCLIP_TEST_CA_BUNDLE", "")}}
        if MOUNT:
            drive = MOUNT[:1] if len(MOUNT) >= 2 and MOUNT[1] == ":" else ""
            prof["remote"]["mount"] = ({"windows_drive": drive} if drive
                                       else {"unix_mountpoint": MOUNT})
    path = os.path.join(_TMP_CFG, "test.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(prof, f)
    os.environ["FIELDCLIP_PROFILE"] = path
if SECRET:
    os.environ["FIELDCLIP_REMOTE_SECRET"] = SECRET

from fieldclip import settings as S      # noqa: E402
import fieldclip.app as RV               # noqa: E402


def require_network(need_mount=False):
    """Exit with 'skipped' (status 0) when the network test server is not set."""
    missing = [n for n, v in (("FIELDCLIP_TEST_WEBDAV_URL", WEBDAV_URL),
                              ("FIELDCLIP_TEST_SECRET", SECRET or
                               os.environ.get("FIELDCLIP_TEST_AUTH") == "none"))
               if not v]
    if need_mount and not MOUNT:
        missing.append("FIELDCLIP_TEST_MOUNT")
    if missing:
        print(f"  SKIPPED: set {', '.join(missing)} to run this network test.")
        sys.exit(0)


def has_display() -> bool:
    """Tk needs a display; CI on Linux runs under xvfb-run."""
    try:
        import tkinter
        tkinter.Tk().destroy()
        return True
    except Exception:
        return False
