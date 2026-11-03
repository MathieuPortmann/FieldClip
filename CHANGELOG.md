# Changelog

## 1.0.0 — 2026-11-02

First public release. FieldClip is the generalised version of an in-house tool
used to rename and archive drone videos for a field project.

- Everything project-specific moved into JSON **profiles**: sites, groups and
  their labels, devices, naming templates, folder layout, file extensions,
  ffmpeg settings, server.
- **Naming templates** drive both writing and reading names. Values and device
  tokens can have aliases for old spellings.
- **Generic transports**: folder copy, WebDAV (`basic`, `sharelink` or `none`
  auth, optional CA bundle) and SSHFS mounts. ERDA ships as an example preset.
- **Secrets** (server credential, DJI API key) are kept in an owner-only file in
  the per-user folder, or read from environment variables. They are refused in
  profiles, masked in the UI and removed from logs.
- `python -m fieldclip` with `--profile`, `--validate-profile`,
  `--list-profiles`.
- Fixed: while the mount status badge was refreshing, it could swallow the
  transfer's "done" message, so step 4 never finished.
- Fixed: the mount manager logged the credential as part of the `net use`
  command line, and printed it in `status --json`.
- Tests: offline suite on Linux, Windows and macOS in CI. Network tests are
  configured through `FIELDCLIP_TEST_*` and skip themselves when no server is set.
