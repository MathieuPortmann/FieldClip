#!/bin/bash
# =============================================================================
#  FieldClip  |  Linux launcher
#  Make it executable once:   chmod +x LAUNCH_LINUX.sh
#  Then double-click it (or run ./LAUNCH_LINUX.sh from a terminal).
#  Extra arguments are passed on, e.g.  ./LAUNCH_LINUX.sh --profile myproject
#
#  Prerequisites (Debian / Ubuntu; Fedora: dnf, Arch: pacman):
#      sudo apt install python3 python3-tk ffmpeg
# =============================================================================

# Works from launchers/ or copied next to the fieldclip/ folder.
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
    echo "  Python 3 not found.    sudo apt install python3 python3-tk"
    echo
    read -r -p "  -- Press Enter to close --"
    exit 1
fi

if ! "$PY" -c "import tkinter" >/dev/null 2>&1; then
    echo
    echo "  Tkinter is missing.    sudo apt install python3-tk"
    echo
    read -r -p "  -- Press Enter to close --"
    exit 1
fi

command -v ffmpeg >/dev/null 2>&1 || \
    echo "  WARNING: ffmpeg not found (sudo apt install ffmpeg) - faststart and re-encode disabled."

"$PY" -m fieldclip "$@"
