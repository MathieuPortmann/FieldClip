#!/usr/bin/env python3
"""Offline tests for fieldclip/settings.py: layers, profiles, secrets."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common  # noqa: F401  (isolated config dir)
from fieldclip import settings as S

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def write(d, name, data):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return p


print("\n  Settings")
print("  " + "=" * 66)
tmp = tempfile.mkdtemp(prefix="fieldclip_settings_")

check("config dir is the isolated one", S.user_config_dir() == os.environ["FIELDCLIP_CONFIG_DIR"])

s = S.load_profile("")
check("defaults alone are valid", s["naming"]["template"] == S.DEFAULTS["naming"]["template"])

for name in ("example", "erda"):
    p = S.find_profile(name)
    try:
        S.load_profile(p); ok = True
    except S.ProfileError as e:
        ok = False; p = str(e)
    check(f"shipped profile {name}.json loads", ok, p)

# deep merge keeps untouched defaults
p = write(tmp, "p.json", {"encoding": {"reencode": {"codec": "libsvtav1"}}})
s = S.load_profile(p)
check("profile overrides one nested key", s["encoding"]["reencode"]["codec"] == "libsvtav1")
check("... and keeps the others", s["encoding"]["reencode"]["preset"] == "fast")

# extends
base = write(tmp, "base.json", {"remote": {"display_name": "Base", "webdav": {"url": "https://x/"}}})
child = write(tmp, "child.json", {"extends": base, "remote": {"display_name": "Child"}})
s = S.load_profile(child)
check("extends: child wins", s["remote"]["display_name"] == "Child")
check("extends: parent kept", s["remote"]["webdav"]["url"] == "https://x/")
loop = write(tmp, "loop.json", {"extends": os.path.join(tmp, "loop.json")})
try:
    S.load_profile(loop); check("extends loop rejected", False)
except S.ProfileError:
    check("extends loop rejected", True)

# secrets never in a profile
for key in ("share_id", "password", "api_key"):
    p = write(tmp, f"leak_{key}.json", {"remote": {key: "x"}})
    try:
        S.load_profile(p); check(f"profile with {key} rejected", False)
    except S.ProfileError:
        check(f"profile with {key} rejected", True)

# invalid values
for bad in ({"naming": {"template": "{device}_{site}"}},
            {"encoding": {"reencode": {"when": "sometimes"}}},
            {"remote": {"auth": "kerberos"}},
            {"naming": {"date_format": "%Y_%m_%d"}}):
    p = write(tmp, "bad.json", bad)
    try:
        S.load_profile(p); check(f"invalid profile rejected: {bad}", False)
    except S.ProfileError:
        check(f"invalid profile rejected: {list(bad)[0]}", True)
p = write(tmp, "badjson.json", {})
open(p, "w").write("{not json")
try:
    S.load_profile(p); check("broken JSON rejected", False)
except S.ProfileError:
    check("broken JSON rejected", True)

# secrets store
S.set_secret("remote_secret", "s3cr3t-value")
check("secret stored and read back", S.get_secret("remote_secret") == "s3cr3t-value")
if os.name == "posix":
    mode = os.stat(S.secrets_file()).st_mode & 0o777
    check("secrets file is owner-only", mode == 0o600, oct(mode))
cfg = S.load_user_config(); cfg["share_id"] = "leak"; cfg["remote_secret"] = "leak"
S.save_user_config(cfg)
raw = open(S.user_config_file(), encoding="utf-8").read()
check("user config never stores a secret", "leak" not in raw and "s3cr3t" not in raw)
os.environ[S.ENV_REMOTE_SECRET] = "from-env"
check("environment variable wins over the file", S.get_secret("remote_secret") == "from-env")
del os.environ[S.ENV_REMOTE_SECRET]
check("mask hides the middle", "s3cr3t" not in S.mask("s3cr3t-value"))

s = S.load_profile(write(tmp, "share.json", {"remote": {"auth": "sharelink"}}))
check("sharelink: user == password", S.remote_credentials(s) == ("s3cr3t-value", "s3cr3t-value"))
s = S.load_profile(write(tmp, "basic.json", {"remote": {"auth": "basic", "username": "ann"}}))
check("basic: user from profile", S.remote_credentials(s) == ("ann", "s3cr3t-value"))
s = S.load_profile(write(tmp, "none.json", {"remote": {"auth": "none"}}))
check("none: no credentials", S.remote_credentials(s) == ("", ""))

# shared secret file (one installation, many users)
shared = write(tmp, "shared_secret.json", {"share_id": "team-token"})
saved_active = S._ACTIVE
S.set_secret("remote_secret", "")
S.set_active(S.load_profile(write(tmp, "sf.json", {"remote": {"secret_file": shared}})))
check("secret_file read when the user has none", S.get_secret("remote_secret") == "team-token")
S.set_secret("remote_secret", "team-token")
check("shared secret not copied per user", "remote_secret" not in S._load_secrets())
S.set_secret("remote_secret", "mine")
check("own secret wins over secret_file", S.get_secret("remote_secret") == "mine")
with open(shared, "w", encoding="utf-8") as f:
    f.write("plain-token\n")
S.set_secret("remote_secret", "")
check("secret_file as plain text", S.get_secret("remote_secret") == "plain-token")
S.set_active(saved_active)
S.set_secret("remote_secret", "s3cr3t-value")

# profile choice
check("--profile wins", S.choose_profile_path(["--profile", "erda"], {}).endswith("erda.json"))

print("  " + "=" * 66)
print(f"  {len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
