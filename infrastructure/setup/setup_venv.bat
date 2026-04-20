@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
set "VENV_DIR=%REPO_ROOT%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "SERVICE_REQUIREMENTS=%REPO_ROOT%\src\backend\app\services\requirements.txt"

set "PYTHON_CMD="
py -3 --version >nul 2>nul
if %ERRORLEVEL% EQU 0 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
  python --version >nul 2>nul
  if %ERRORLEVEL% EQU 0 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo [setup_venv] ERROR: Python 3.10+ is required but was not found in PATH.
  exit /b 1
)

if not exist "%VENV_PYTHON%" (
  echo [setup_venv] Creating virtual environment: "%VENV_DIR%"
  %PYTHON_CMD% -m venv "%VENV_DIR%"
  if %ERRORLEVEL% NEQ 0 (
    echo [setup_venv] ERROR: Failed to create virtual environment.
    exit /b 1
  )
)

echo [setup_venv] Upgrading pip/setuptools/wheel
"%VENV_PYTHON%" -m pip install --upgrade pip setuptools wheel
if %ERRORLEVEL% NEQ 0 (
  echo [setup_venv] ERROR: Failed to upgrade pip tooling.
  exit /b 1
)

echo [setup_venv] Installing Polaris package (editable)
"%VENV_PYTHON%" -m pip install -e "%REPO_ROOT%"
if %ERRORLEVEL% NEQ 0 (
  echo [setup_venv] ERROR: Failed to install Polaris Python dependencies.
  exit /b 1
)

if exist "%SERVICE_REQUIREMENTS%" (
  echo [setup_venv] Installing service requirements
  "%VENV_PYTHON%" -m pip install -r "%SERVICE_REQUIREMENTS%"
  if %ERRORLEVEL% NEQ 0 (
    echo [setup_venv] ERROR: Failed to install service requirements.
    exit /b 1
  )
)

echo [setup_venv] Running pip check
"%VENV_PYTHON%" -m pip check
if %ERRORLEVEL% NEQ 0 (
  echo [setup_venv] ERROR: Dependency validation failed.
  exit /b 1
)

echo [setup_venv] DONE
echo [setup_venv] Python: "%VENV_PYTHON%"
exit /b 0
