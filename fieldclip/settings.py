# -*- coding: utf-8 -*-
"""
FieldClip settings: one place for everything that used to be hard-coded.

Layers, lowest priority first:

    1. DEFAULTS (below)          generic, work for anyone
    2. a profile (JSON file)     project vocabulary, naming, encoding, server
    3. environment variables     FIELDCLIP_* (secrets, profile choice, tools)

The user's own state (last answers, chosen profile) lives in the per-user
config directory, never in the profile. Secrets (WebDAV/SSHFS credential,
DJI API key) live in a separate secrets file there, written owner-only; they
are never read from, nor written to, a profile.

Standard library only, Python 3.8+.
"""
import copy
import json
import os
import platform
import sys

APP_NAME = "FieldClip"

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(PACKAGE_DIR)
PROFILES_DIR = os.path.join(ROOT_DIR, "profiles")

ENV_PROFILE = "FIELDCLIP_PROFILE"
ENV_REMOTE_SECRET = "FIELDCLIP_REMOTE_SECRET"
ENV_REMOTE_USER = "FIELDCLIP_REMOTE_USER"
ENV_DJI_API_KEY = "FIELDCLIP_DJI_API_KEY"
ENV_FFMPEG = "FIELDCLIP_FFMPEG"
ENV_DJI_LOG = "FIELDCLIP_DJI_LOG"
ENV_CONFIG_DIR = "FIELDCLIP_CONFIG_DIR"

# =============================================================================
#  Defaults
# =============================================================================
DEFAULTS = {
    "profile_name": "Default",

    # Vocabulary asked for at step 2. A value is either a plain string or
    # {"name": "...", "aliases": ["old spelling", ...]}; aliases are only used
    # to recognise names written earlier, never written.
    "fields": {
        "site":  {"label": "Site",  "values": []},
        "group": {"label": "Group", "values": []},
    },

    "devices": {
        "builtin": ["Mini4Pro", "Mini3Pro", "Mini3", "DJI_Other", "Phone"],
        # Placeholders that are not a real model: a real model found elsewhere
        # (flight log, folder name, siblings) replaces them.
        "generic": ["Unknown", "DJI_Other"],
        # Token used when a raw DJI filename only tells the family apart.
        "dji_fallback": "DJI_Other",
        "phone": "Phone",
        "phone_prefixes": ["IMG", "VID", "VIDEO", "MOV", "DCIM", "MVI", "GOPRO", "GH", "PXL"],
        # Old tokens still recognised when parsing; null = family only.
        "legacy_aliases": {},
        # Model as it may be written in a folder name. Most specific first.
        "folder_patterns": [
            ["Mini4Pro", "mini[\\s_\\-]*4[\\s_\\-]*pro"],
            ["Mini3Pro", "mini[\\s_\\-]*3[\\s_\\-]*pro"],
            ["Mini3",    "mini[\\s_\\-]*3(?![\\s_\\-]*pro)"],
        ],
        # DJI flight-log identity -> device token. First match wins.
        # "aircraft" is a lower-case substring of aircraftName, "drone_type"
        # the raw droneType value.
        "flightlog_models": [
            {"aircraft": "mini 4",     "token": "Mini4Pro"},
            {"aircraft": "mini 3 pro", "token": "Mini3Pro"},
            {"aircraft": "mini 3",     "token": "Mini3"},
            {"drone_type": "Mini4Pro", "token": "Mini4Pro"},
            {"drone_type": "73",       "token": "Mini3Pro"},
            {"drone_type": "87",       "token": "Mini3"},
        ],
    },

    # File names. Tokens are joined by `separator`; {field?} is optional and
    # dropped when empty; a bare word is a literal. {date} and {time} must be
    # adjacent. See docs/configuration.md.
    "naming": {
        "template":          "{device}_{date}_{time}_{group}_{site}",
        "training_template": "{device}_{date}_{time}_{site?}_Training",
        "separator":         "_",
        "date_format":       "%Y-%m-%d",
        "time_format":       "%H-%M-%S",
    },

    "training": {
        "label": "Training",
        # Clips shorter than this are pre-ticked as training (0 = never).
        "auto_max_duration_s": 300,
        # A folder path containing one of these pre-ticks training.
        "path_keywords": ["training"],
    },

    # Where renamed files go under the destination folder.
    # {category} = training_dir or main_dir; any naming field can be used.
    "layout": {
        "path":         "{category}/{site}",
        "main_dir":     "Main",
        "training_dir": "Training",
    },

    "files": {
        "video_ext":         [".mp4", ".mov", ".avi", ".mkv"],
        "companion_ext":     [".srt", ".lrf", ".flightlog.csv", ".csv"],
        "compound_suffixes": [".flightlog.csv"],
        "device_memo":       ".fieldclip_device.json",
        "legacy_device_memos": [],
    },

    "encoding": {
        "faststart": True,
        "reencode": {
            # broken_h264_10bit: only DJI crash-recovery files (H.264 10-bit,
            # avc1) that browsers cannot play; always; never.
            "when":     "broken_h264_10bit",
            "codec":    "libx265",
            "preset":   "fast",
            "bitrate":  "90M",
            "crf":      None,
            "pix_fmt":  "yuv420p10le",
            "tag":      "hvc1",
            "extra_args": [],
            "timeout_s": 1800,
        },
        "faststart_min_timeout_s": 600,
    },

    # Empty = search the bundled bin/ folder, then the PATH.
    "tools": {
        "ffmpeg":  "",
        "dji_log": "",
        # Optional key file shared by every user of one installation (e.g. on
        # a network share). Relative paths are relative to the FieldClip root.
        # The per-user secret and FIELDCLIP_DJI_API_KEY take precedence.
        "dji_api_key_file": "",
    },

    "remote": {
        "display_name": "Server",
        # sharelink : one secret used as both user name and password
        # basic     : user name (profile or FIELDCLIP_REMOTE_USER) + secret
        # none      : anonymous (WebDAV only)
        "auth": "basic",
        "username": "",
        "secret_label": "Password",
        # Optional file shared by every user of one installation that holds
        # the secret: plain text, or JSON with a "secret" (or legacy
        # "share_id") key. The per-user secret and FIELDCLIP_REMOTE_SECRET
        # take precedence. Relative paths are relative to the FieldClip root.
        "secret_file": "",
        "webdav": {
            "url":     "",          # e.g. https://dav.example.org:443/
            "subdir":  "",          # default folder inside the share
            "ca_bundle": "",        # extra PEM file, relative to the root or absolute
            "response_timeout_s": 600,
            # Files uploaded at the same time (1-4). On a long or lossy route
            # one connection rarely fills the line; several do. The user can
            # change it in the destination card; this is the first-run value.
            "parallel_uploads": 3,
        },
        "mount": {
            "host":            "",
            "sftp_port":       22,
            "windows_drive":   "W",
            "unix_mountpoint": "~/fieldclip_mount",
            "volume_label":    "FieldClip",
        },
    },

    "logging": {
        "dir": "",                  # empty = <user config dir>/logs
        "retention_days": 30,
    },
}


# =============================================================================
#  Helpers
# =============================================================================
def deep_merge(base: dict, override: dict) -> dict:
    """Return base updated recursively with override (neither is modified)."""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _os_config_base() -> str:
    system = platform.system()
    if system == "Windows":
        return os.environ.get("APPDATA", os.path.expanduser("~"))
    if system == "Darwin":
        return os.path.expanduser("~/Library/Application Support")
    return os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))


def user_config_dir() -> str:
    """Per-user directory for state and secrets."""
    d = os.environ.get(ENV_CONFIG_DIR) or os.path.join(_os_config_base(), APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def resolve_path(p: str) -> str:
    """Absolute path; relative paths are taken from the FieldClip root."""
    if not p:
        return ""
    p = os.path.expanduser(p)
    return p if os.path.isabs(p) else os.path.join(ROOT_DIR, p)


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: str, data: dict, private: bool = False) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if private and os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# =============================================================================
#  User state (last answers, chosen profile)
# =============================================================================
USER_DEFAULTS = {
    "profile": "",
    "custom_devices": [], "last_site": "", "last_group": "",
    "last_training": False, "op_mode": "copy",
    "do_faststart": True, "do_reencode_hevc": True,
    "last_dest_dir": "",
}


def user_config_file() -> str:
    return os.path.join(user_config_dir(), "config.json")


def load_user_config() -> dict:
    cfg = dict(USER_DEFAULTS)
    try:
        cfg.update(_read_json(user_config_file()))
    except (OSError, ValueError):
        pass
    # A secret saved by an older version in the plain config moves to secrets.
    legacy_sid = cfg.pop("share_id", "")
    if legacy_sid and not get_secret("remote_secret"):
        set_secret("remote_secret", legacy_sid)
        save_user_config(cfg)
    return cfg


def save_user_config(cfg: dict) -> None:
    cfg = {k: v for k, v in cfg.items() if k not in SECRET_KEYS}
    try:
        _write_json(user_config_file(), cfg)
    except OSError:
        pass


# =============================================================================
#  Secrets
# =============================================================================
SECRET_KEYS = ("remote_secret", "dji_api_key", "share_id")
_SECRET_ENV = {"remote_secret": ENV_REMOTE_SECRET, "dji_api_key": ENV_DJI_API_KEY}


def secrets_file() -> str:
    return os.path.join(user_config_dir(), "secrets.json")


_SECRETS_CACHE = (None, {})            # (stamp, data): logging asks on every line


def _secrets_stamp(path: str):
    """Identifies one version of the file; mtime alone can repeat within a tick."""
    st = os.stat(path)
    return (path, st.st_mtime_ns, st.st_size)


def _load_secrets() -> dict:
    global _SECRETS_CACHE
    path = secrets_file()
    try:
        stamp = _secrets_stamp(path)
    except OSError:
        return {}
    if _SECRETS_CACHE[0] != stamp:
        try:
            _SECRETS_CACHE = (stamp, _read_json(path))
        except (OSError, ValueError):
            return {}
    return dict(_SECRETS_CACHE[1])


def _shared_secret(name: str) -> str:
    """The remote secret from remote.secret_file of the active profile, or ""."""
    if name != "remote_secret" or _ACTIVE is None:
        return ""
    path = resolve_path(_ACTIVE["remote"].get("secret_file", ""))
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return ""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if isinstance(data, dict):
        return str(data.get("secret") or data.get("share_id") or "").strip()
    return ""


def get_secret(name: str) -> str:
    env = _SECRET_ENV.get(name)
    if env and os.environ.get(env):
        return os.environ[env].strip()
    return str(_load_secrets().get(name, "") or "").strip() or _shared_secret(name)


def set_secret(name: str, value: str) -> bool:
    global _SECRETS_CACHE
    data = _load_secrets()
    value = (value or "").strip()
    if data.get(name, "") == value:
        return True
    if name not in data and value and value == _shared_secret(name):
        return True                     # already provided by the shared file
    if value:
        data[name] = value
    else:
        data.pop(name, None)
    try:
        _write_json(secrets_file(), data, private=True)
        _SECRETS_CACHE = (_secrets_stamp(secrets_file()), dict(data))
        return True
    except OSError:
        return False


def mask(secret: str) -> str:
    """Printable stand-in for a secret: never show more than its length."""
    return f"(set, {len(secret)} characters)" if secret else "(not set)"


# =============================================================================
#  Profiles
# =============================================================================
def list_profiles() -> list:
    """Profile files shipped or added in profiles/ (private *.local.json first)."""
    try:
        names = sorted(f for f in os.listdir(PROFILES_DIR) if f.endswith(".json"))
    except OSError:
        return []
    names.sort(key=lambda f: (not f.endswith(".local.json"), f))
    return [os.path.join(PROFILES_DIR, f) for f in names]


def _profile_from_argv(argv) -> str:
    for i, a in enumerate(argv):
        if a == "--profile" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--profile="):
            return a.split("=", 1)[1]
    return ""


def find_profile(name_or_path: str) -> str:
    """Path of a profile given as a path, a file name or a bare name."""
    if not name_or_path:
        return ""
    for cand in (name_or_path,
                 resolve_path(name_or_path),
                 os.path.join(PROFILES_DIR, name_or_path),
                 os.path.join(PROFILES_DIR, name_or_path + ".json"),
                 os.path.join(PROFILES_DIR, name_or_path + ".local.json")):
        if cand and os.path.isfile(os.path.expanduser(cand)):
            return os.path.abspath(os.path.expanduser(cand))
    return ""


def choose_profile_path(argv=None, user_cfg=None) -> str:
    """--profile > FIELDCLIP_PROFILE > last used > first private > example."""
    argv = sys.argv[1:] if argv is None else argv
    for cand in (_profile_from_argv(argv),
                 os.environ.get(ENV_PROFILE, ""),
                 (user_cfg or {}).get("profile", "")):
        p = find_profile(cand)
        if p:
            return p
    shipped = list_profiles()
    for p in shipped:
        if p.endswith(".local.json"):
            return p
    return find_profile("example") or (shipped[0] if shipped else "")


class ProfileError(ValueError):
    pass


def _load_raw(path: str, seen=()) -> dict:
    """One profile file, merged over the profile it "extends" (if any)."""
    try:
        prof = _read_json(path)
    except ValueError as e:
        raise ProfileError(f"{os.path.basename(path)} is not valid JSON: {e}")
    except OSError as e:
        raise ProfileError(f"cannot read {path}: {e}")
    for k in [k for k in prof if k.startswith("_") or k == "$schema"]:
        prof.pop(k)                         # "_comment", "_note", ...
    leaked = _find_secret_keys(prof)
    if leaked:
        raise ProfileError(
            f"{os.path.basename(path)} contains {', '.join(leaked)}. Secrets do "
            "not belong in a profile: enter them in the app or set "
            f"{ENV_REMOTE_SECRET} / {ENV_DJI_API_KEY}.")
    parent = prof.pop("extends", "")
    if not parent:
        return prof
    ppath = find_profile(parent)
    if not ppath:
        raise ProfileError(f"{os.path.basename(path)} extends unknown profile {parent!r}")
    if ppath in seen:
        raise ProfileError(f"profile inheritance loop through {os.path.basename(ppath)}")
    return deep_merge(_load_raw(ppath, seen + (path,)), prof)


def load_profile(path: str = "") -> dict:
    """Defaults merged with the profile at `path` (defaults alone if empty).

    A profile may say "extends": "<other profile>" to start from another one,
    e.g. a private project profile extending the public server preset."""
    prof = _load_raw(path) if path else {}
    s = deep_merge(DEFAULTS, prof)
    s["_path"] = path
    validate(s)
    return s


def _find_secret_keys(d, prefix="") -> list:
    out = []
    for k, v in d.items():
        if k in ("share_id", "password", "secret", "api_key", "dji_api_key", "token"):
            out.append(prefix + k)
        elif isinstance(v, dict):
            out += _find_secret_keys(v, prefix + k + ".")
    return out


def value_names(values) -> list:
    """Display names of a field's values (strings or {name, aliases})."""
    out = []
    for v in values or []:
        name = v.get("name", "") if isinstance(v, dict) else str(v)
        if name:
            out.append(name)
    return out


def value_aliases(values) -> dict:
    """{alias: name} for every value that declares aliases."""
    out = {}
    for v in values or []:
        if isinstance(v, dict):
            for a in v.get("aliases", []) or []:
                out[str(a)] = v.get("name", "")
    return out


def _naming_module():
    """naming.py, whether this file is imported as a package or run as a script
    (remote_mount.py's autostart entry runs it as a plain script)."""
    try:
        from . import naming
    except ImportError:
        import naming
    return naming


def validate(s: dict) -> None:
    """Raise ProfileError on anything that would make the app misbehave."""
    naming = _naming_module()
    n = s["naming"]
    for key in ("template", "training_template"):
        try:
            naming.parse_template(n[key], n["separator"])
        except ValueError as e:
            raise ProfileError(f"naming.{key}: {e}")
    for key in ("date_format", "time_format"):
        if n["separator"] in n[key]:
            raise ProfileError(f"naming.{key} must not contain the separator "
                               f"'{n['separator']}'")
    when = s["encoding"]["reencode"]["when"]
    if when not in ("broken_h264_10bit", "always", "never"):
        raise ProfileError("encoding.reencode.when must be broken_h264_10bit, "
                           "always or never")
    if s["remote"]["auth"] not in ("sharelink", "basic", "none"):
        raise ProfileError("remote.auth must be sharelink, basic or none")
    for tok, pat in s["devices"]["folder_patterns"]:
        try:
            import re
            re.compile(pat)
        except Exception as e:
            raise ProfileError(f"devices.folder_patterns {tok}: {e}")


# =============================================================================
#  The active settings
# =============================================================================
_ACTIVE = None


def active() -> dict:
    """Settings for this run (loaded once)."""
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = load_profile(choose_profile_path(user_cfg=load_user_config()))
    return _ACTIVE


def set_active(s: dict) -> None:
    global _ACTIVE
    _ACTIVE = s


def remote_credentials(s: dict = None) -> tuple:
    """(user, password) for the remote server, per remote.auth; ("", "") if unset."""
    s = s or active()
    r = s["remote"]
    secret = get_secret("remote_secret")
    if r["auth"] == "none":
        return "", ""
    if r["auth"] == "sharelink":
        return secret, secret
    user = os.environ.get(ENV_REMOTE_USER, "") or r.get("username", "")
    return user, secret


def _main(argv) -> int:
    """python -m fieldclip.settings --validate [profile]"""
    path = find_profile(argv[0]) if argv else choose_profile_path([])
    try:
        s = load_profile(path)
    except ProfileError as e:
        print(f"INVALID: {e}")
        return 1
    print(f"OK: {path or '(defaults)'}  -  {s['profile_name']}")
    print(f"  sites: {len(value_names(s['fields']['site']['values']))}, "
          f"groups: {len(value_names(s['fields']['group']['values']))}, "
          f"devices: {len(s['devices']['builtin'])}")
    nm = _naming_module().Naming(s)
    print("  example:", nm.build("Mini4Pro", "2025-11-05_10-05-49", False,
                                  group=(value_names(s['fields']['group']['values']) or ["Group"])[0],
                                  site=(value_names(s['fields']['site']['values']) or ["Site"])[0]))
    print("  training:", nm.build("Mini4Pro", "2025-11-05_10-05-49", True,
                                   site=(value_names(s['fields']['site']['values']) or ["Site"])[0]))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
