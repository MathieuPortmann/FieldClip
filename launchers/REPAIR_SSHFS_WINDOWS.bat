@echo off
REM ============================================================================
REM  FieldClip - Repair the WinFsp / SSHFS configuration  (Windows, ADMIN)
REM
REM  Run this ONCE, or again after reinstalling SSHFS-Win.
REM  It fixes the two bugs that freeze the whole of Windows:
REM    - the SSH options were written to GlobalOptions, a key the WinFsp
REM      launcher does not read, so they were never applied;
REM    - and into the 64-bit registry view, while the sshfs service definition
REM      lives in the 32-bit view (WOW6432Node).
REM  Result: SSH with no keepalive -> a dead socket is never detected -> every
REM  I/O on the drive enters an uninterruptible kernel wait -> system freeze.
REM ============================================================================
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
title FieldClip - Repair (admin)
cd /d "%~dp0"
if not exist "fieldclip\remote_mount.py" cd /d "%~dp0.."

net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Administrator elevation required - relaunching...
    powershell -NoProfile -Command "Start-Process -Verb RunAs -FilePath '%~f0'"
    exit /b 0
)

if not exist "fieldclip\remote_mount.py" (
    echo.
    echo   ERROR: the fieldclip folder was not found.
    echo   Keep this launcher inside the FieldClip folder.
    echo.
    pause
    exit /b 1
)

set PY=
py -3 --version >nul 2>&1
if not errorlevel 1 set PY=py -3
if not defined PY (
    python --version >nul 2>&1
    if not errorlevel 1 set PY=python
)
if not defined PY (
    echo   Python not found. Install Python 3 and try again.
    pause
    exit /b 1
)

echo.
echo   Repairing the WinFsp / SSHFS configuration...
echo.
%PY% -m fieldclip.remote_mount repair
echo.
echo   Next: run MOUNT_DRIVE.bat, choose [2] Unmount, then [1] Mount.
echo.
pause
