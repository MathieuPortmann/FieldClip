@echo off
REM ============================================================================
REM  FieldClip - Mount Manager  (Windows)
REM  Just double-click this file. Server settings come from the active
REM  profile; the credential is asked once and kept in your user folder.
REM
REM  IMPORTANT: this launcher does NOT elevate to administrator, on purpose.
REM  A network drive mapped from an elevated session is invisible from the
REM  user's normal session. For the registry repair, which does need admin,
REM  use REPAIR_SSHFS_WINDOWS.bat instead.
REM ============================================================================
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
title FieldClip - Mount Manager
cd /d "%~dp0"
if not exist "fieldclip\remote_mount.py" cd /d "%~dp0.."

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
    python3 --version >nul 2>&1
    if not errorlevel 1 set PY=python3
)

if not defined PY (
    echo.
    echo   Python not found.
    echo.
    echo   Install Python 3 from https://www.python.org/downloads/
    echo   Make sure you tick "Add python.exe to PATH" during setup.
    echo.
    pause
    exit /b 1
)

%PY% -m fieldclip.remote_mount %*
if errorlevel 1 pause
