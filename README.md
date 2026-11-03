# FieldClip

**Rename, organise, verify and export drone and phone field videos, with their
sidecar files, under one consistent naming scheme you can search.**

[![tests](https://github.com/MathieuPortmann/FieldClip/actions/workflows/tests.yml/badge.svg)](https://github.com/MathieuPortmann/FieldClip/actions/workflows/tests.yml)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

```
DJI_0083.MP4  DJI_0083.SRT  DJI_0083.LRF        →   Survey/North-Meadow/
                                                       Mini4Pro_2025-11-05_10-05-49_Herd-A_North-Meadow.MP4
                                                       Mini4Pro_2025-11-05_10-05-49_Herd-A_North-Meadow.SRT
                                                       Mini4Pro_2025-11-05_10-05-49_Herd-A_North-Meadow.LRF
```

---

## Why

After a day in the field you come back with hundreds of `DJI_0083.MP4` and
`IMG_4120.MOV` files. Their names say nothing about where or what was filmed.
Their subtitle and telemetry files get separated from them. A network copy that
drops halfway can leave a corrupt video that still has the right size.

FieldClip asks a few questions per clip (site, group, training clip or not), and
pre-fills the answers it can guess. It then renames every clip and its companion
files together. It files them into a folder tree, copies or uploads them, and
checks each copy before it offers to delete the originals.

It was written for drone-based behavioural studies of free-ranging animals, but
nothing in it is specific to them. The vocabulary, the naming scheme, the folder
layout, the encoding settings and the server all come from a **profile**, a small
JSON file. Wildlife surveys, site inspections and any other project that films
the same places again and again can use it.

## Features

- **A guided 6-step workflow**: source → questions per clip → processing →
  transfer → summary → verification and clean-up. Previous answers are
  pre-selected, so a batch from one site takes a few clicks.
- **Companion sets**: `.MP4` + `.SRT` + `.LRF` + `.flightlog.csv` are renamed,
  copied and verified together. A sidecar left behind under its raw camera name
  is re-attached automatically.
- **Drone-model detection**. A raw DJI file name only tells the family apart. The
  exact model is read from the DJI flight record, from the folder name, from
  sibling clips or from the SRT colour profile.
- **DJI flight logs (optional)**: rebuilds missing `.SRT` files and writes a
  per-clip telemetry CSV. See [docs/flightlog.md](docs/flightlog.md).
- **Web-friendly videos**: *faststart* (moves the index to the front, lossless,
  seconds). Optional re-encode, by default only for DJI crash-recovery files that
  browsers cannot play.
- **Three ways out**: a local or USB folder, a **WebDAV** server over HTTPS, or a
  drive mounted with **SSHFS**. Each mode handles interruptions safely.
- **Verified copies**: size **and** a hash of the first and last megabyte, on the
  destination side. The "delete originals" box stays disabled until every file
  passes.
- **Will not sleep mid-transfer**: the computer is kept awake during long
  exports, and a dropped connection becomes an error message, not a frozen
  machine.
- **Logs** of every run, with full tracebacks and secrets removed.

## Quick start

1. **Install the prerequisites**: Python 3.8 or newer with Tk, and ffmpeg.

   | System | Command |
   |---|---|
   | Ubuntu / Debian | `sudo apt install python3 python3-tk ffmpeg` |
   | Fedora | `sudo dnf install python3 python3-tkinter ffmpeg` |
   | macOS (Homebrew) | `brew install python python-tk ffmpeg` |
   | Windows | Python from [python.org](https://www.python.org/downloads/) (tick *Add python.exe to PATH*), then `winget install ffmpeg` |

   FieldClip uses the standard library only: there is nothing to `pip install`.
   ffmpeg is optional; without it, faststart and re-encoding are switched off.

2. **Get FieldClip**: `git clone https://github.com/MathieuPortmann/FieldClip.git`,
   or download the ZIP from the green *Code* button and unzip it.

3. **Start it**: double-click the launcher for your system in `launchers/`.

   | System | Launcher |
   |---|---|
   | Windows | `LAUNCH_WINDOWS.bat` |
   | macOS | `LAUNCH_MAC.command` (once, in Terminal: `chmod +x LAUNCH_MAC.command`) |
   | Linux | `LAUNCH_LINUX.sh` (once: `chmod +x LAUNCH_LINUX.sh`) |

   Or, from a terminal in the FieldClip folder: `python3 -m fieldclip`.

The first run uses `profiles/example.json`, which has made-up sites and copies to
a folder. Make your own profile next.

## Your first profile

```bash
cp profiles/example.json profiles/myproject.local.json
```

Files ending in `.local.json` are ignored by git, so your site names stay on your
machine. Edit the copy:

```json
{
  "profile_name": "Coastal survey 2026",
  "fields": {
    "site":  {"label": "Site",    "values": ["Dune-North", "Dune-South",
                                             {"name": "Salt-Marsh", "aliases": ["Saltmarsh"]}]},
    "group": {"label": "Species", "values": ["Godwit", "Oystercatcher", "Mixed"]}
  },
  "layout": {"main_dir": "Survey", "training_dir": "Training"}
}
```

Anything you leave out keeps its default. Check the file, then use it:

```bash
python3 -m fieldclip --validate-profile myproject
python3 -m fieldclip --profile myproject
```

`--validate-profile` prints example names, so you see the result before touching
a single video. The profile you start with is remembered for next time. A private
`*.local.json` profile is picked automatically when there is one.

A profile can start from another one with `"extends": "erda"`, for example to
reuse a server preset.

## Naming scheme

Names come from two templates:

```json
"naming": {
  "template":          "{device}_{date}_{time}_{group}_{site}",
  "training_template": "{device}_{date}_{time}_{site?}_Training",
  "separator":   "_",
  "date_format": "%Y-%m-%d",
  "time_format": "%H-%M-%S"
}
```

- Fields: `{device}`, `{date}`, `{time}`, `{site}`, `{group}`. `{site?}` is
  optional and dropped when empty. A bare word (`Training`, `TR`) is written as is.
- `{date}` and `{time}` are required and must be next to each other: they are
  the anchor used to read a name back. Literal words go at the start or the end.
- The **same template is used to read names back**, so FieldClip recognises its
  own files after you change the template, as long as the old files follow the
  old one. Values may declare `aliases` (old spellings that are recognised but
  never written). `devices.legacy_aliases` does the same for device tokens.

| Template | Result |
|---|---|
| `{device}_{date}_{time}_{group}_{site}` | `Mini4Pro_2025-11-05_10-05-49_Herd-A_North-Meadow.MP4` |
| `{site}_{date}_{time}_{device}` with `%Y%m%d` / `%H%M%S` | `North-Meadow_20251105_100549_Mini4Pro.MP4` |
| training: `TR_{date}_{time}_{device}` | `TR_20251105_100549_Phone.MP4` |

**Training clips** (calibration, practice or test flights) get their own
template and folder. A clip is pre-ticked as training when it is shorter than
`training.auto_max_duration_s` (300 s by default, 0 turns this off), or when its
folder path contains a word from `training.path_keywords`.

**Folders** come from `layout.path`, by default `{category}/{site}`, where
`{category}` is `main_dir` or `training_dir`. Any naming field works, for example
`{category}/{date}/{site}`.

## Video processing

Step 3 runs on each clip, in place, before the transfer:

- **Faststart**: moves the MP4 index (`moov` atom) to the front, so the video
  starts playing in a browser before it has fully downloaded. Lossless, takes a
  few seconds.
- **Re-encode**: set by `encoding.reencode.when`:
  - `broken_h264_10bit` (default): only H.264 10-bit `avc1` files, which DJI
    drones write when recording is interrupted and which browsers cannot play;
  - `always`;
  - `never`.

  The ffmpeg options are in the profile:

  ```json
  "encoding": {"reencode": {"when": "broken_h264_10bit", "codec": "libx265",
               "preset": "fast", "bitrate": "90M", "crf": null,
               "pix_fmt": "yuv420p10le", "tag": "hvc1", "extra_args": []}}
  ```

  Set `crf` to use constant quality instead of a bitrate. Expect about 5 minutes
  per 4K clip.

## Exporting

Pick the transport at step 1.

| | Folder / mounted drive | WebDAV over HTTPS |
|---|---|---|
| Needs | a path (local, USB, network or SSHFS drive) | a WebDAV URL and a credential |
| Interrupted file | resumes where it stopped | restarts from zero (most servers refuse ranged uploads) |
| Network drop | depends on the drive driver | a clean error, never a frozen machine |

**WebDAV** settings live in the profile. The credential does not: you type it in
the app (masked), or set `FIELDCLIP_REMOTE_SECRET`.

```json
"remote": {
  "display_name": "Lab server",
  "auth": "basic",
  "username": "alice",
  "secret_label": "Password",
  "webdav": {"url": "https://dav.example.org/", "subdir": "Project/Videos/"}
}
```

`auth` is `basic` (user name + password), `sharelink` (one token used as both user
name and password, as ERDA share links do) or `none`. If the server does not send
its full certificate chain (Python reports `CERTIFICATE_VERIFY_FAILED` while your
browser is fine), save the missing intermediate certificate as a PEM file and set
`webdav.ca_bundle` to its path.

**SSHFS** mounts the server as a drive, handled by `launchers/MOUNT_DRIVE.*`
(menu: mount, unmount, status, diagnostics, mount at login). It reads the
`remote.mount` section of the profile:

| System | Needs |
|---|---|
| Windows | [WinFsp](https://winfsp.dev) + [SSHFS-Win](https://github.com/winfsp/sshfs-win), then run `REPAIR_SSHFS_WINDOWS.bat` **once as administrator**. It fixes the registry entry that otherwise leaves SSH without a keepalive, and a dead connection can then freeze Windows. |
| macOS | [macFUSE](https://osxfuse.github.io) + `brew install gromgit/fuse/sshfs-mac` |
| Linux | `sudo apt install sshfs` |

**ERDA** (the research data store of Aarhus University and partners) is
included as an example preset: [`profiles/erda.json`](profiles/erda.json).
Use it from your own profile with `"extends": "erda"` and enter your Share ID in
the app.

## DJI flight logs

Optional. With the `dji-log` binary from
[dji-log-parser](https://github.com/lvauvillier/dji-log-parser) and a free DJI
API key, step 3 matches each clip with its `DJIFlightRecord_*.txt` and:

- rebuilds a missing `.SRT`;
- writes `<clip>.flightlog.csv`;
- sets the exact drone model in the name.

How the matching works, and the CSV columns, are in
[docs/flightlog.md](docs/flightlog.md).

- **Binary**: put `dji-log` on your PATH, in `bin/djilog/<os>/`, or point
  `FIELDCLIP_DJI_LOG` (or `tools.dji_log`) at it.
- **API key**: create an *Open API* app at
  [developer.dji.com](https://developer.dji.com). Paste the key in the app once.
  It is stored in your user settings, never in a profile or in the repository.
  `FIELDCLIP_DJI_API_KEY` works too.

## Configuration reference

All profile keys, with defaults, are in
[docs/configuration.md](docs/configuration.md). What you are most likely to
change:

| Key | Default | What it does |
|---|---|---|
| `fields.site.values`, `fields.group.values` | `[]` (free text) | the lists offered at step 2 |
| `fields.*.label` | `Site`, `Group` | names shown in the UI |
| `naming.*` | see above | file names |
| `layout.*` | `{category}/{site}`, `Main`, `Training` | folders |
| `training.auto_max_duration_s` | `300` | pre-tick short clips as training |
| `devices.builtin` | DJI Mini models, `Phone` | device list |
| `files.video_ext`, `files.companion_ext` | MP4/MOV/AVI/MKV; SRT/LRF/CSV | what is picked up |
| `encoding.*` | faststart on, re-encode broken files only | ffmpeg |
| `remote.*` | none | WebDAV / SSHFS server |
| `logging.retention_days` | `30` | log clean-up |

**Environment variables**

| Variable | Meaning |
|---|---|
| `FIELDCLIP_PROFILE` | profile name or path |
| `FIELDCLIP_REMOTE_SECRET` | server password / token |
| `FIELDCLIP_REMOTE_USER` | server user name (`basic` auth) |
| `FIELDCLIP_DJI_API_KEY` | DJI API key |
| `FIELDCLIP_FFMPEG`, `FIELDCLIP_DJI_LOG` | paths to the binaries |
| `FIELDCLIP_CONFIG_DIR` | use another per-user folder |

**Per-user folder** (last answers, chosen profile, secrets, logs):

| System | Folder |
|---|---|
| Windows | `%APPDATA%\FieldClip` |
| macOS | `~/Library/Application Support/FieldClip` |
| Linux | `~/.config/FieldClip` |

## Security and privacy

- **Secrets are never in a profile.** A profile that contains `password`,
  `share_id`, `api_key`, `secret` or `token` is refused at load time.
- Secrets are kept in `secrets.json` in the per-user folder. On macOS and Linux
  the file can only be read by you (mode 600). Environment variables take
  precedence. A team working from one shared installation can instead point
  `remote.secret_file` / `tools.dji_api_key_file` at a file on that share.
- Credential fields in the app are masked. Logs and the mount manager's output
  replace every known secret with `***`.
- `.gitignore` excludes `*.local.json`, logs, binaries and anything named like a
  key file. Run `git status` before every push anyway.

## Troubleshooting

| Symptom | What to do |
|---|---|
| Something failed | Open the log of the day: `logs/fieldclip_YYYY-MM-DD.log` in the per-user folder. Send that file when asking for help. |
| `No module named tkinter` | Install Tk: `sudo apt install python3-tk` / `brew install python-tk` |
| "ffmpeg not found" badge | Install ffmpeg, or set `FIELDCLIP_FFMPEG` |
| WebDAV `401` | Wrong credential, or the wrong `auth` mode for this server |
| WebDAV `507` | The server is full |
| `CERTIFICATE_VERIFY_FAILED` | Set `remote.webdav.ca_bundle`, see *Exporting* |
| Mounted drive shows *BLOCKED* | Click *Remount*. On Windows, run `REPAIR_SSHFS_WINDOWS.bat` once as administrator |
| A profile is rejected | `python3 -m fieldclip --validate-profile NAME` says why |

## Development

```
fieldclip/
  __main__.py      command line: python -m fieldclip
  app.py           Tk interface and the 6-step workflow
  settings.py      defaults, profiles, per-user config, secrets
  naming.py        templates: build and parse names
  flightlog.py     DJI flight-log add-on
  remote_mount.py  SSHFS mount manager (also a standalone menu)
  log.py           daily log files
profiles/          example.json, erda.json (+ your *.local.json)
launchers/         double-click launchers per OS
tests/             test scripts (no pytest needed)
```

```bash
python3 tests/run_all.py --offline    # what CI runs on Linux, Windows and macOS
python3 tests/run_all.py              # also the network tests, if a server is set
```

The network tests (`test_steps3456.py`, `test_e2e_export.py`) run against a real
WebDAV server and skip themselves unless you point them at one with
`FIELDCLIP_TEST_WEBDAV_URL`, `FIELDCLIP_TEST_SECRET` and, for the end-to-end
test, `FIELDCLIP_TEST_MOUNT`. They only write inside a `_fieldclip_*` folder and
delete it afterwards. See `tests/_common.py`. Tests never touch your own
settings: they use a throw-away config folder and a made-up profile.

Issues and pull requests are welcome. Please keep the standard-library-only
rule, and add a test with your change.

## Credits and citation

FieldClip grew out of the video workflow of the HERDWISE project on free-ranging
horses. If you use it in research, please cite it. GitHub's *Cite this repository*
button reads [CITATION.cff](CITATION.cff).

Licensed under the [MIT License](LICENSE).

Third-party tools are **not** included and keep their own licences:
[FFmpeg](https://ffmpeg.org/legal.html) (LGPL/GPL),
[dji-log-parser](https://github.com/lvauvillier/dji-log-parser) (MIT),
[WinFsp](https://winfsp.dev) and [SSHFS-Win](https://github.com/winfsp/sshfs-win) (GPL),
[macFUSE](https://osxfuse.github.io). `profiles/certs/GEANT_TLS_RSA_1.pem` is a
public CA certificate, included for the ERDA preset.
