@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"
set "VENV_DIR=%REPO_ROOT%\.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "SERVICE_REQUIREMENTS=%REPO_ROOT%\src\backend\app\services\requirements.txt"
set "UV_CACHE_DIR=%UV_CACHE_DIR%"
if not defined UV_CACHE_DIR set "UV_CACHE_DIR=%REPO_ROOT%\.uv_cache"

set "PYTHON_CMD="
call :try_python "%KERNELONE_BOOTSTRAP_PYTHON%"
if not defined PYTHON_CMD call :try_python "%PYTHON%"
if not defined PYTHON_CMD call :try_python "%LOCALAPPDATA%\Python\bin\python.exe"
if not defined PYTHON_CMD call :try_python "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PYTHON_CMD call :try_python "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PYTHON_CMD call :try_python "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PYTHON_CMD call :try_python "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYTHON_CMD call :try_python "C:\Python314\python.exe"
if not defined PYTHON_CMD call :try_python "C:\Python313\python.exe"
if not defined PYTHON_CMD call :try_python "C:\Python312\python.exe"
if not defined PYTHON_CMD call :try_python "C:\Python311\python.exe"

if not defined PYTHON_CMD (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
  echo [setup_venv] ERROR: Python 3.10+ is required but was not found in PATH.
  exit /b 1
)

if exist "%VENV_PYTHON%" (
  "%VENV_PYTHON%" -c "import sys; print(sys.executable)" >nul 2>nul
  if errorlevel 1 (
    echo [setup_venv] Existing virtual environment is invalid; rebuilding: "%VENV_DIR%"
    rmdir /s /q "%VENV_DIR%"
    if exist "%VENV_DIR%" (
      echo [setup_venv] ERROR: Failed to remove invalid virtual environment.
      exit /b 1
    )
  )
)

if not exist "%VENV_PYTHON%" (
  echo [setup_venv] Creating virtual environment: "%VENV_DIR%"
  %PYTHON_CMD% -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo [setup_venv] ERROR: Failed to create virtual environment.
    exit /b 1
  )
)

set "UV_CMD="
for /f "delims=" %%I in ('where uv 2^>nul') do if not defined UV_CMD set "UV_CMD=%%~fI"
if defined UV_CMD echo [setup_venv] Using uv installer: "%UV_CMD%"

echo [setup_venv] Upgrading pip/setuptools/wheel
if defined UV_CMD (
  "%UV_CMD%" pip install --python "%VENV_PYTHON%" --upgrade pip setuptools wheel
) else (
  "%VENV_PYTHON%" -m pip install --upgrade pip setuptools wheel
)
if errorlevel 1 (
  echo [setup_venv] ERROR: Failed to upgrade pip tooling.
  exit /b 1
)

echo [setup_venv] Installing Polaris package (editable with dev extras)
if defined UV_CMD (
  "%UV_CMD%" pip install --python "%VENV_PYTHON%" -e "%REPO_ROOT%[dev]"
) else (
  "%VENV_PYTHON%" -m pip install -e "%REPO_ROOT%[dev]"
)
if errorlevel 1 (
  echo [setup_venv] ERROR: Failed to install Polaris Python dependencies.
  exit /b 1
)

if exist "%SERVICE_REQUIREMENTS%" (
  echo [setup_venv] Installing service requirements
  if defined UV_CMD (
    "%UV_CMD%" pip install --python "%VENV_PYTHON%" -r "%SERVICE_REQUIREMENTS%"
  ) else (
    "%VENV_PYTHON%" -m pip install -r "%SERVICE_REQUIREMENTS%"
  )
  if errorlevel 1 (
    echo [setup_venv] ERROR: Failed to install service requirements.
    exit /b 1
  )
)

echo [setup_venv] Running pip check
if defined UV_CMD (
  "%UV_CMD%" pip check --python "%VENV_PYTHON%"
) else (
  "%VENV_PYTHON%" -m pip check
)
if errorlevel 1 (
  echo [setup_venv] ERROR: Dependency validation failed.
  exit /b 1
)

echo [setup_venv] DONE
echo [setup_venv] Python: "%VENV_PYTHON%"
exit /b 0

:try_python
set "CANDIDATE=%~1"
if not defined CANDIDATE exit /b 0
if not exist "%CANDIDATE%" exit /b 0
"%CANDIDATE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD="%CANDIDATE%""
exit /b 0
