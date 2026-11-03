@echo off
REM ============================================================
REM  FieldClip  |  Windows launcher
REM  Double-click this file to launch the application.
REM  Works from launchers\ or copied next to the fieldclip\ folder.
REM ============================================================
chcp 65001 >nul 2>&1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist "fieldclip\__main__.py" cd /d "%~dp0.."

if not exist "fieldclip\__main__.py" (
    echo.
    echo  ERROR: the fieldclip folder was not found.
    echo  Keep this launcher inside the FieldClip folder.
    echo.
    pause
    exit /b 1
)

where py >nul 2>&1
if %ERRORLEVEL% == 0 ( py -3 -m fieldclip %* & goto :end )

where python >nul 2>&1
if %ERRORLEVEL% == 0 ( python -m fieldclip %* & goto :end )

where python3 >nul 2>&1
if %ERRORLEVEL% == 0 ( python3 -m fieldclip %* & goto :end )

echo.
echo  ERROR: Python not found.
echo  Please install Python 3.8+ from https://www.python.org
echo  and tick "Add Python to PATH" during installation.
echo.
pause
:end
