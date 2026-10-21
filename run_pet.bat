@echo off
rem Start the desktop pet on Windows.
rem Everything (virtual env, pip cache, temp files) stays inside this folder,
rem so nothing is written to the C: drive except what Python itself already has.
setlocal
cd /d "%~dp0"

set "PIP_CACHE_DIR=%~dp0.pip-cache"
set "TMP=%~dp0.tmp"
set "TEMP=%~dp0.tmp"
if not exist "%TMP%" mkdir "%TMP%"

rem Which Python to use: set PYTHON=... before running to override.
if "%PYTHON%"=="" set "PYTHON=python"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] creating .venv with %PYTHON% ...
    "%PYTHON%" -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
)
rem (Re)install dependencies when pyproject.toml changed since the last install.
fc /b pyproject.toml ".venv\pyproject.installed" >nul 2>&1
if errorlevel 1 (
    echo [setup] installing dependencies ...
    ".venv\Scripts\python.exe" -m pip install -e ".[app,psd]" || goto :error
    copy /y pyproject.toml ".venv\pyproject.installed" >nul
)

set "MODEL=%~1"
if "%MODEL%"=="" set "MODEL=build\placeholder_model"
rem python.exe (not pythonw) keeps a console open so errors are visible in v0.1
".venv\Scripts\python.exe" -m chibi_pet.app "%MODEL%" || goto :error
goto :eof

:error
echo.
echo Something failed (see the messages above).
echo If Python was not found: install Python 3.11+ or run
echo   set PYTHON=D:\path\to\python.exe
echo before this script.
pause
