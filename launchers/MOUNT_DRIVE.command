#!/bin/bash
# =============================================================================
#  FieldClip - Mount Manager  (macOS)
#  Just double-click this file. Server settings come from the active
#  profile; the credential is asked once and kept in your user folder.
#
#  First time only, once, in Terminal:
#      chmod +x "MOUNT_DRIVE.command"
#
#  macOS prerequisites:
#      1) macFUSE          https://osxfuse.github.io
#         then System Settings > Privacy & Security > Allow,
#         and restart the Mac.
#      2) brew install gromgit/fuse/sshfs-mac
# =============================================================================

here="$(cd "$(dirname "$0")" && pwd)"
for d in "$here" "$here/.."; do
    [ -d "$d/fieldclip" ] && { cd "$d" || exit 1; break; }
done
if [ ! -d fieldclip ]; then
    echo "  ERROR: the fieldclip folder was not found next to this launcher."
    read -r -p "  -- Press Enter to close --"
    exit 1
fi

# Homebrew is not on PATH for a script started by double-click
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONIOENCODING=utf-8

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
    echo
    echo "  Python 3 not found."
    echo
    echo "  Recent macOS ships one. Otherwise:  brew install python3"
    echo
    read -r -p "  -- Press Enter to close --"
    exit 1
fi

"$PY" -m fieldclip.remote_mount "$@"
status=$?

if [ $status -ne 0 ]; then
    read -r -p "  -- Press Enter to close --"
fi
exit $status
