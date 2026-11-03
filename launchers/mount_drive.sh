#!/bin/bash
# =============================================================================
#  FieldClip - Mount Manager  (Linux)
#  Make it executable once:   chmod +x mount_drive.sh
#  Then double-click it (or run ./mount_drive.sh from a terminal).
#
#  Prerequisites:
#      sudo apt install sshfs        (Debian / Ubuntu)
#      sudo dnf install fuse-sshfs   (Fedora)
#      sudo pacman -S sshfs          (Arch)
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
export PYTHONIOENCODING=utf-8

PY=""
for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
    echo
    echo "  Python 3 not found."
    echo "    sudo apt install python3     (Debian / Ubuntu)"
    echo "    sudo dnf install python3     (Fedora)"
    echo
    read -r -p "  -- Press Enter to close --"
    exit 1
fi

# When launched by double-click from a file manager there is no terminal
# attached, so open one - otherwise the menu would be invisible.
if [ ! -t 0 ] && [ -z "$FIELDCLIP_IN_TERM" ]; then
    export FIELDCLIP_IN_TERM=1
    for t in x-terminal-emulator gnome-terminal konsole xfce4-terminal xterm; do
        if command -v "$t" >/dev/null 2>&1; then
            exec "$t" -e "bash '$0'" 2>/dev/null || exec "$t" -- bash "$0"
        fi
    done
fi

"$PY" -m fieldclip.remote_mount "$@"
status=$?

if [ $status -ne 0 ]; then
    read -r -p "  -- Press Enter to close --"
fi
exit $status
