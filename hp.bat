@echo off
REM Polaris Windows 快捷入口
REM 用法: hp <command> [options]

set "SCRIPT_DIR=%~dp0"
set "PYTHONPATH=%SCRIPT_DIR%src\backend\scripts\pm;%SCRIPT_DIR%src\backend;%SCRIPT_DIR%"

python "%SCRIPT_DIR%polaris.py" %*
