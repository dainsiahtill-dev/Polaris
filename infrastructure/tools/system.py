"""
System information tools.
"""
import os
import platform
import sys
from typing import Any, Dict, List

from .utils import error_result


def env_list(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    List environment variables.

    Usage: env_list [--prefix <prefix>]
    """
    _ = cwd
    _ = timeout
    prefix = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--prefix", "-p") and i + 1 < len(args):
            prefix = args[i + 1]
            i += 2
            continue
        i += 1

    env_vars: Dict[str, str] = {}
    for key, value in os.environ.items():
        if not prefix or key.startswith(prefix):
            # Mask sensitive values
            if any(s in key.lower() for s in ("password", "secret", "key", "token", "api")):
                value = "***"
            env_vars[key] = value

    output_lines = ["Environment variables:"]
    for key, value in sorted(env_vars.items()):
        output_lines.append(f"{key}={value}")

    return {
        "ok": True,
        "tool": "env_list",
        "variables": env_vars,
        "count": len(env_vars),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["env_list"],
    }


def system_info(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Get system information.

    Usage: system_info
    """
    _ = args
    _ = cwd
    _ = timeout

    info = {
        "platform": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "architecture": platform.machine(),
        "processor": platform.processor(),
        "python_version": sys.version,
        "python_executable": sys.executable,
        "cwd": os.getcwd(),
    }

    # Try to get CPU and memory info
    try:
        import psutil
        info["cpu_count"] = psutil.cpu_count()
        info["memory_total"] = psutil.virtual_memory().total
        info["memory_available"] = psutil.virtual_memory().available
    except ImportError:
        pass

    output_lines = ["System information:"]
    for key, value in info.items():
        output_lines.append(f"{key}: {value}")

    return {
        "ok": True,
        "tool": "system_info",
        "info": info,
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["system_info"],
    }


def process_list(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    List running processes.

    Usage: process_list [--limit <n>]
    """
    _ = cwd
    limit = 20

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--limit", "-l") and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except Exception:
                pass
            i += 2
            continue
        i += 1

    processes: List[Dict[str, Any]] = []

    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                processes.append({
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "cpu": proc.info.get("cpu_percent"),
                    "memory": proc.info.get("memory_percent"),
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except ImportError:
        return error_result(
            "process_list",
            "psutil not installed. Install with: pip install psutil"
        )

    # Sort by CPU usage
    processes.sort(key=lambda x: x.get("cpu", 0) or 0, reverse=True)
    processes = processes[:limit]

    output_lines = ["Running processes:"]
    for p in processes:
        output_lines.append(f"PID: {p['pid']}, Name: {p['name']}, CPU: {p.get('cpu', 'N/A')}%, Memory: {p.get('memory', 'N/A')}%")

    return {
        "ok": True,
        "tool": "process_list",
        "processes": processes,
        "count": len(processes),
        "error": None,
        "exit_code": 0,
        "stdout": "\n".join(output_lines),
        "stderr": "",
        "duration": 0.0,
        "duration_ms": 0,
        "truncated": False,
        "artifacts": [],
        "command": ["process_list"],
    }
