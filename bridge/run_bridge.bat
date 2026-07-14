@echo off
setlocal
cd /d "%~dp0"

title Companion Bridge
echo.
echo  Companion Bridge — polling Railway and sending OSC to Companion 3.x
echo  Close this window to stop the bridge.
echo.

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python "companion_bridge.py"
  goto :end
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 "companion_bridge.py"
  goto :end
)

echo ERROR: Python was not found on this PC.
echo Install Python 3 from https://www.python.org/downloads/
echo and check "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:end
echo.
echo Bridge stopped.
pause
