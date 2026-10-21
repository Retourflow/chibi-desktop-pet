@echo off
rem Check the connection to VTube Studio and print what it sends.
rem Run run_pet.bat once first (it sets up .venv). Keep VTube Studio open with
rem "Allow Plugin API access" turned on in its settings.
setlocal
cd /d "%~dp0"
set "TMP=%~dp0.tmp"
set "TEMP=%~dp0.tmp"
if not exist ".venv\Scripts\python.exe" (
    echo Run run_pet.bat first.
    pause
    goto :eof
)
".venv\Scripts\python.exe" -m chibi_pet.drive.vts --dump %*
pause
