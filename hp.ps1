# Polaris PowerShell 快捷入口
# 用法: .\hp.ps1 <command> [options]

$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Definition
$env:PYTHONPATH = "$SCRIPT_DIR\src\backend\scripts\pm;$SCRIPT_DIR\src\backend;$SCRIPT_DIR;$env:PYTHONPATH"

python "$SCRIPT_DIR\polaris.py" @args
