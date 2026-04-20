"""
Shell and command execution tools.
"""
import os
import re
import shlex
import subprocess
import time
from typing import Any, Dict, List, Set

from .utils import error_result, find_repo_root

# Dangerous shell metacharacters that could enable injection
# Only allow these when explicitly needed and after validation
_DANGEROUS_SHELL_CHARS: Set[str] = {";", "|", "&", "$", "`", "\\", "\n", "\r"}
_DANGEROUS_SHELL_PATTERNS = [
    r"\$\(",  # Command substitution $(...)
    r"`",      # Command substitution `...`
    r";\s*rm",  # Deletion after semicolon
    r"\|\s*rm",  # Deletion after pipe
    r">\s*/",   # Writing to root paths
    r">>\s*/",  # Appending to root paths
    r"curl\s+.*\|\s*sh",  # Pipe curl to shell
    r"wget\s+.*\|\s*sh",  # Pipe wget to shell
]

# Maximum command length to prevent DoS
_MAX_CMD_LENGTH = 8192


def _validate_shell_command(cmd: str) -> tuple[bool, str]:
    """
    Validate shell command for dangerous patterns.

    Returns:
        (is_safe, reason_if_unsafe)
    """
    if not cmd or not cmd.strip():
        return False, "Empty command"

    if len(cmd) > _MAX_CMD_LENGTH:
        return False, f"Command exceeds maximum length of {_MAX_CMD_LENGTH}"

    # Check for dangerous patterns
    for pattern in _DANGEROUS_SHELL_PATTERNS:
        if re.search(pattern, cmd, re.IGNORECASE):
            return False, f"Command contains dangerous pattern"

    return True, ""


def shell_run(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Run a shell command securely.

    Usage: shell_run --cmd <command>
           shell_run <command>

    Security notes:
    - Commands are validated for dangerous patterns
    - Maximum command length is enforced
    - Command substitution is blocked
    """
    cmd_arg = ""
    env: Dict[str, str] = {}
    # Allow dangerous chars only for trusted commands
    allow_shell_features = False

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--cmd", "-c") and i + 1 < len(args):
            cmd_arg = args[i + 1]
            i += 2
            continue
        if token.startswith("--env="):
            key, val = token[6:].split("=", 1)
            env[key] = val
            i += 1
            continue
        if token == "--allow-shell-features":
            allow_shell_features = True
            i += 1
            continue
        if not cmd_arg:
            cmd_arg = token
        i += 1

    if not cmd_arg:
        return error_result("shell_run", "Usage: shell_run --cmd <command>")

    # Security validation
    is_safe, reason = _validate_shell_command(cmd_arg)
    if not is_safe and not allow_shell_features:
        return error_result(
            "shell_run",
            f"Security validation failed: {reason}. "
            "If this is intentional, use --allow-shell-features flag.",
            exit_code=1
        )

    start = time.time()

    # Build environment
    run_env = os.environ.copy()
    run_env.update(env)

    try:
        # SECURITY FIX: Use shell=False when possible by parsing the command
        # Try to parse as shell command list first
        try:
            cmd_list = shlex.split(cmd_arg)
            if cmd_list and not allow_shell_features:
                # Use shell=False with parsed command list for better security
                proc = subprocess.run(
                    cmd_list,
                    cwd=cwd,
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    env=run_env,
                )
            else:
                # Fall back to shell=True for complex commands
                proc = subprocess.run(
                    cmd_arg,
                    cwd=cwd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    env=run_env,
                )
        except ValueError:
            # shlex.split failed, use shell=True as fallback
            proc = subprocess.run(
                cmd_arg,
                cwd=cwd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=run_env,
            )
    except subprocess.TimeoutExpired:
        return error_result("shell_run", f"Command timed out after {timeout}s", exit_code=1)
    except Exception as exc:
        return error_result("shell_run", str(exc), exit_code=1)

    duration = time.time() - start

    return {
        "ok": proc.returncode == 0,
        "tool": "shell_run",
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "duration": duration,
        "duration_ms": int(duration * 1000),
        "truncated": False,
        "artifacts": [],
        "command": [cmd_arg],
    }


def bash_run(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Run a bash command securely.

    Usage: bash_run --cmd <command>
           bash_run <command>

    Security notes:
    - Commands are validated for dangerous patterns
    - Command substitution is blocked by default
    """
    cmd_arg = ""
    allow_shell_features = False

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--cmd", "-c") and i + 1 < len(args):
            cmd_arg = args[i + 1]
            i += 2
            continue
        if token == "--allow-shell-features":
            allow_shell_features = True
            i += 1
            continue
        if not cmd_arg:
            cmd_arg = token
        i += 1

    if not cmd_arg:
        return error_result("bash_run", "Usage: bash_run --cmd <command>")

    # Security validation
    is_safe, reason = _validate_shell_command(cmd_arg)
    if not is_safe and not allow_shell_features:
        return error_result(
            "bash_run",
            f"Security validation failed: {reason}. "
            "If this is intentional, use --allow-shell-features flag.",
            exit_code=1
        )

    start = time.time()

    # Try bash, sh, or use python's shell
    bash_cmd = "bash"
    for cmd in ("bash", "sh", "shell"):
        try:
            subprocess.run([cmd, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            bash_cmd = cmd
            break
        except Exception:
            continue

    try:
        proc = subprocess.run(
            [bash_cmd, "-c", cmd_arg],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return error_result("bash_run", f"Command timed out after {timeout}s", exit_code=1)
    except Exception as exc:
        return error_result("bash_run", str(exc), exit_code=1)

    duration = time.time() - start

    return {
        "ok": proc.returncode == 0,
        "tool": "bash_run",
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "duration": duration,
        "duration_ms": int(duration * 1000),
        "truncated": False,
        "artifacts": [],
        "command": [bash_cmd, "-c", cmd_arg],
    }
