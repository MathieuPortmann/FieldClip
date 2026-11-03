# DJI flight-log add-on

Optional. It decodes DJI flight records to **rebuild missing SRT files**,
**add telemetry** to each clip and **set the exact drone model** in its name. It
works on Windows, macOS (Intel and Apple silicon) and Linux.

## Requirements

- The `dji-log` binary from
  [dji-log-parser](https://github.com/lvauvillier/dji-log-parser). Put it on the
  PATH, in `bin/djilog/<os>/` (`windows`, `mac_arm`, `mac_x86`, `linux`), or
  point `FIELDCLIP_DJI_LOG` / `tools.dji_log` at it.
- A DJI API key. Version 13 logs are encrypted, so a key is required. It is free
  at [developer.dji.com](https://developer.dji.com): create an *Open API* app.
  Paste the key in the app once. It is stored in your user settings, or read from
  `FIELDCLIP_DJI_API_KEY`, or from the shared file named by
  `tools.dji_api_key_file`.

## How to use it

1. **Keep each flight session's files together** in one folder: the `.MP4`
   and `.SRT` files, and the `DJIFlightRecord_*.txt` logs exported from the DJI
   app on the remote controller. The logs may also be one folder down.
   - Video + flight log are **required** for this step. If a log is missing,
     you get an error message.
   - A missing SRT is **rebuilt from the log**. That is not an error.
2. At step 1, tick *Enable flight-log processing* and enter the API key.
3. Run as usual. The flight-log work happens at step 3.

## What it writes next to each clip

- A rebuilt `.SRT` when there was none (DJI format, GPS and altitude).
- `<clip>.flightlog.csv`: a time series, see below.
- The **exact model** in the file name (`Mini4Pro`, `Mini3Pro`, `Mini3`, or what
  `devices.flightlog_models` maps). A file name alone cannot tell a Mini 3 from a
  Mini 3 Pro. The model is also remembered in the folder (`.fieldclip_device.json`),
  so it survives the logs being archived elsewhere.

## Sidecar CSV: read it by column name, never by position

One schema, 21 columns, the same for every model:

```
video_t_s, flyTime_s, utc, latitude, longitude, rel_alt_m, abs_alt_m, vps_m,
xSpeed, ySpeed, zSpeed, ac_pitch, ac_roll, ac_yaw,
gb_pitch, gb_roll, gb_yaw, gpsNum, isGPSUsed, flycState, battery_pct
```

- `ac_*` is the attitude of the **aircraft**, `gb_*` that of the **gimbal**.
- Typical values in flight: `gb_pitch` about −30° (camera tilted down),
  `gb_roll` about 0°. `ac_yaw` sweeps ±180°: it is the heading.

Earlier versions wrote 18 columns for some models. Code that read columns by
index then read `ac_yaw` in place of `gb_pitch`, and diagnosed a gimbal that was
not upside down at all. Always use `csv.DictReader` or `header.index("name")`.

## How clips and logs are matched

Each clip is matched to the recording segment (`CAMERA.isVideo`) of a flight
log. The matching copes with time zones and with recordings split over several
files. A sidecar that was left under its raw camera name is re-attached on a
later run: the sidecar stores its start time in UTC, the renamed clip carries
local time in its name, so the folder's UTC offset is voted on across all
candidate pairs, and each sidecar is matched within 5 seconds.
