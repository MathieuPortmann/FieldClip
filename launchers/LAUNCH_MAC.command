#!/bin/bash
# ============================================================
#  FieldClip  |  macOS launcher
#
#  One-time setup (run once in Terminal):
#    chmod +x LAUNCH_MAC.command
#  After that: double-click in Finder to launch.
#
#  Prerequisites:  brew install python-tk ffmpeg
# ============================================================

here="$(cd "$(dirname "$0")" && pwd)"
for d in "$here" "$here/.."; do
    [ -d "$d/fieldclip" ] && { cd "$d" || exit 1; break; }
done
if [ ! -d fieldclip ]; then
    osascript -e 'display alert "fieldclip folder not found" message "Keep this launcher inside the FieldClip folder." as critical' 2>/dev/null
    exit 1
fi

# Homebrew is not on PATH for a script started by double-click
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export PYTHONIOENCODING=utf-8

PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(sys.version_info.major)" 2>/dev/null)
        if [ "$VER" = "3" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    osascript -e 'display alert "Python 3 not found" message "Please install Python 3.8+ from https://www.python.org or via Homebrew:\n\nbrew install python python-tk" as critical'
    exit 1
fi

"$PYTHON" -m fieldclip "$@"
