# Configuration reference

A profile is a JSON file in `profiles/`. Everything it leaves out keeps the
default below. Check a profile with:

```bash
python3 -m fieldclip --validate-profile NAME
```

## How settings are combined

From lowest to highest priority:

1. the built-in defaults (below);
2. the profile it `extends`, if any, then the profile itself;
3. environment variables `FIELDCLIP_*`.

The profile in use is chosen in this order: `--profile`, then
`FIELDCLIP_PROFILE`, then the one used last time, then the first
`profiles/*.local.json`, then `profiles/example.json`.

Keys starting with `_` (such as `_comment`) are ignored, so you can annotate a
profile. Secrets (`password`, `share_id`, `secret`, `api_key`, `dji_api_key`,
`token`) are **refused** anywhere in a profile.

## Keys

### `profile_name`
Shown in the window title.

### `fields.site`, `fields.group`
- `label`: name shown in the UI ("Site", "Species", "Transect"…).
- `values`: the list offered at step 2. An empty list means free text. Each value
  is a string, or `{"name": "...", "aliases": ["old spelling"]}`. Aliases are
  recognised when reading old names and never written.

Matching is case-insensitive and treats `-` and `_` alike. When several values
could match, the longest one wins (`Lake-Shore` before `Lake`).

### `devices`
- `builtin`: device tokens offered in the device list. Tokens typed by the user
  are added to the per-user settings.
- `generic`: placeholders that are not a real model. The user is warned once
  before they are written.
- `dji_fallback`: token used when a raw DJI name only tells the family apart.
- `phone`, `phone_prefixes`: raw names starting with one of these prefixes
  (`IMG_`, `PXL_`…) are treated as phone videos.
- `legacy_aliases`: `{"OldToken": "NewToken"}` lets old names still be read.
  `null` means "family only": the model must be found again.
- `folder_patterns`: `[token, regex]` pairs. A folder name matching the regex
  (case-insensitive) gives the device. The most specific pattern goes first.
- `flightlog_models`: maps DJI flight-log identities to tokens. `aircraft` is a
  lower-case substring of the aircraft name; `drone_type` is the raw value. The
  first match wins.

### `naming`
- `template`, `training_template`: see the README, section *Naming scheme*.
- `separator`: between tokens. It must not appear inside `date_format` or
  `time_format`.
- `date_format`, `time_format`: `strftime` codes. Supported: `%Y %y %m %d %H %M
  %S %j %b %B %p %I`.

### `training`
- `label`: what training clips are called in the UI.
- `auto_max_duration_s`: clips shorter than this are pre-ticked as training. `0`
  turns this off.
- `path_keywords`: a folder path containing one of these words pre-ticks
  training.

### `layout`
- `path`: folders under the destination. You can use `{category}`, `{site}`,
  `{group}`, `{device}`, `{date}` and `{time}`. Empty parts are skipped.
- `main_dir`, `training_dir`: the two values of `{category}`.

### `files`
- `video_ext`, `companion_ext`: extensions picked up (case does not matter).
- `compound_suffixes`: multi-dot suffixes such as `.flightlog.csv`, so that
  `NAME.flightlog.csv` belongs to `NAME.MP4`.
- `device_memo`, `legacy_device_memos`: the small file in which a folder
  remembers its drone model.

### `encoding`
- `faststart`: default state of the faststart checkbox.
- `reencode.when`: `broken_h264_10bit`, `always` or `never`.
- `reencode.codec`, `preset`, `bitrate` or `crf`, `pix_fmt`, `tag`,
  `extra_args`: passed to ffmpeg (`extra_args` is a list of strings).
- `reencode.timeout_s`, `faststart_min_timeout_s`: safety limits per file.

### `tools`
- `ffmpeg`, `dji_log`: explicit paths. When empty, FieldClip looks in
  `bin/ffmpeg/<os>/` and `bin/djilog/<os>/` (`<os>`: `windows`, `mac_arm`,
  `mac_x86`, `linux`), then on the PATH.
- `dji_api_key_file`: optional key file shared by every user of one
  installation, for example on a network share. Relative paths start from the
  FieldClip folder. A key in the user's own settings takes precedence.

### `remote`
- `display_name`: name shown in the UI.
- `auth`: `basic`, `sharelink` (the secret is both user name and password) or
  `none`.
- `username`: for `basic` (or `FIELDCLIP_REMOTE_USER`).
- `secret_label`: what the credential field is called ("Password", "Share ID",
  "Token").
- `secret_file`: optional file shared by every user of one installation that
  holds the secret (plain text, or JSON with a `secret` key). Keep it outside
  any repository, on a share only your team can read. A secret in the user's own
  settings or in `FIELDCLIP_REMOTE_SECRET` takes precedence.
- `webdav.url`: root of the share, e.g. `https://dav.example.org/`. Mind the
  port: some servers answer WebDAV on a non-standard one.
- `webdav.subdir`: suggested destination inside the share.
- `webdav.ca_bundle`: extra PEM certificate, for servers that do not send their
  intermediate certificate.
- `webdav.response_timeout_s`: how long to wait for the server's answer after
  an upload. Some servers checksum the whole file first.
- `mount.host`, `mount.sftp_port`: SSH server for SSHFS. The user name is the
  credential (`sharelink`) or `username`.
- `mount.windows_drive`, `mount.unix_mountpoint`, `mount.volume_label`: where
  and under which name the drive appears.

The root of the WebDAV share and the root of the mounted drive are assumed to
be the same folder. Switching transport keeps the same destination.

### `logging`
- `dir`: where logs go. Empty means `logs/` in the per-user folder.
- `retention_days`: older logs are deleted at start-up.

## Defaults

```json
{
  "profile_name": "Default",
  "fields": {
    "site": {
      "label": "Site",
      "values": []
    },
    "group": {
      "label": "Group",
      "values": []
    }
  },
  "devices": {
    "builtin": [
      "Mini4Pro",
      "Mini3Pro",
      "Mini3",
      "DJI_Other",
      "Phone"
    ],
    "generic": [
      "Unknown",
      "DJI_Other"
    ],
    "dji_fallback": "DJI_Other",
    "phone": "Phone",
    "phone_prefixes": [
      "IMG",
      "VID",
      "VIDEO",
      "MOV",
      "DCIM",
      "MVI",
      "GOPRO",
      "GH",
      "PXL"
    ],
    "legacy_aliases": {},
    "folder_patterns": [
      [
        "Mini4Pro",
        "mini[\\s_\\-]*4[\\s_\\-]*pro"
      ],
      [
        "Mini3Pro",
        "mini[\\s_\\-]*3[\\s_\\-]*pro"
      ],
      [
        "Mini3",
        "mini[\\s_\\-]*3(?![\\s_\\-]*pro)"
      ]
    ],
    "flightlog_models": [
      {
        "aircraft": "mini 4",
        "token": "Mini4Pro"
      },
      {
        "aircraft": "mini 3 pro",
        "token": "Mini3Pro"
      },
      {
        "aircraft": "mini 3",
        "token": "Mini3"
      },
      {
        "drone_type": "Mini4Pro",
        "token": "Mini4Pro"
      },
      {
        "drone_type": "73",
        "token": "Mini3Pro"
      },
      {
        "drone_type": "87",
        "token": "Mini3"
      }
    ]
  },
  "naming": {
    "template": "{device}_{date}_{time}_{group}_{site}",
    "training_template": "{device}_{date}_{time}_{site?}_Training",
    "separator": "_",
    "date_format": "%Y-%m-%d",
    "time_format": "%H-%M-%S"
  },
  "training": {
    "label": "Training",
    "auto_max_duration_s": 300,
    "path_keywords": [
      "training"
    ]
  },
  "layout": {
    "path": "{category}/{site}",
    "main_dir": "Main",
    "training_dir": "Training"
  },
  "files": {
    "video_ext": [
      ".mp4",
      ".mov",
      ".avi",
      ".mkv"
    ],
    "companion_ext": [
      ".srt",
      ".lrf",
      ".flightlog.csv",
      ".csv"
    ],
    "compound_suffixes": [
      ".flightlog.csv"
    ],
    "device_memo": ".fieldclip_device.json",
    "legacy_device_memos": []
  },
  "encoding": {
    "faststart": true,
    "reencode": {
      "when": "broken_h264_10bit",
      "codec": "libx265",
      "preset": "fast",
      "bitrate": "90M",
      "crf": null,
      "pix_fmt": "yuv420p10le",
      "tag": "hvc1",
      "extra_args": [],
      "timeout_s": 1800
    },
    "faststart_min_timeout_s": 600
  },
  "tools": {
    "ffmpeg": "",
    "dji_log": "",
    "dji_api_key_file": ""
  },
  "remote": {
    "display_name": "Server",
    "auth": "basic",
    "username": "",
    "secret_label": "Password",
    "secret_file": "",
    "webdav": {
      "url": "",
      "subdir": "",
      "ca_bundle": "",
      "response_timeout_s": 600
    },
    "mount": {
      "host": "",
      "sftp_port": 22,
      "windows_drive": "W",
      "unix_mountpoint": "~/fieldclip_mount",
      "volume_label": "FieldClip"
    }
  },
  "logging": {
    "dir": "",
    "retention_days": 30
  }
}
```
