"""
REPL tools: execute Python and Node.js code.
"""
import os
import subprocess
import tempfile
from typing import Any, Dict, List

from .utils import error_result, find_repo_root


def _run_code(
    cmd: List[str],
    cwd: str,
    timeout: int,
    env: Dict[str, str] | None = None,
) -> Dict[str, Any]:
    """Run code and return result in standard format."""
    import time
    start = time.time()

    # Build environment
    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    # Add cwd to PYTHONPATH if needed
    if cmd[0] in ("python", "python3"):
        pythonpath = run_env.get("PYTHONPATH", "")
        path_parts = [cwd, os.path.join(cwd, "src"), os.path.join(cwd, "app")]
        for p in path_parts:
            if p and p not in pythonpath:
                pythonpath = p + os.pathsep + pythonpath if pythonpath else p
        run_env["PYTHONPATH"] = pythonpath

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=run_env,
        )
    except subprocess.TimeoutExpired:
        return error_result(
            cmd[0],
            f"Command timed out after {timeout}s",
            exit_code=1
        )
    except Exception as exc:
        return error_result(cmd[0], str(exc), exit_code=1)

    duration = time.time() - start

    return {
        "ok": proc.returncode == 0,
        "tool": cmd[0],
        "exit_code": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "duration": duration,
        "duration_ms": int(duration * 1000),
        "truncated": False,
        "artifacts": [],
        "command": cmd,
    }


def python_run(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Execute Python code.

    Usage: python_run --code <code>
           python_run --file <path>
           python_run <code>
    """
    code_arg = ""
    file_arg = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--code", "-c") and i + 1 < len(args):
            code_arg = args[i + 1]
            i += 2
            continue
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if not code_arg:
            code_arg = token
        i += 1

    if not code_arg and not file_arg:
        return error_result(
            "python_run",
            "Usage: python_run --code <code> or --file <path>"
        )

    # Determine Python executable
    python_exe = "python"
    for py in ("python3", "python", "py"):
        try:
            subprocess.run([py, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            python_exe = py
            break
        except Exception:
            continue

    root = find_repo_root(cwd)

    if code_arg:
        # Write code to temp file and run it
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(code_arg)
                temp_file = f.name

            try:
                return _run_code([python_exe, temp_file], cwd, timeout)
            finally:
                try:
                    os.unlink(temp_file)
                except Exception:
                    pass
        except Exception as exc:
            return error_result("python_run", str(exc), exit_code=1)

    elif file_arg:
        try:
            full_path = os.path.join(root, file_arg) if not os.path.isabs(file_arg) else file_arg
        except ValueError:
            return error_result("python_run", "Invalid file path")

        if not os.path.isfile(full_path):
            return error_result("python_run", f"File not found: {file_arg}")

        return _run_code([python_exe, full_path], cwd, timeout)

    return error_result("python_run", "No code or file provided")


def node_run(args: List[str], cwd: str, timeout: int) -> Dict[str, Any]:
    """
    Execute Node.js code.

    Usage: node_run --code <code>
           node_run --file <path>
           node_run <code>
    """
    code_arg = ""
    file_arg = ""

    i = 0
    while i < len(args):
        token = args[i]
        if token in ("--code", "-c") and i + 1 < len(args):
            code_arg = args[i + 1]
            i += 2
            continue
        if token in ("--file", "-f") and i + 1 < len(args):
            file_arg = args[i + 1]
            i += 2
            continue
        if not code_arg:
            code_arg = token
        i += 1

    if not code_arg and not file_arg:
        return error_result(
            "node_run",
            "Usage: node_run --code <code> or --file <path>"
        )

    # Determine Node executable
    node_exe = "node"
    for nd in ("node", "nodejs"):
        try:
            subprocess.run([nd, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            node_exe = nd
            break
        except Exception:
            continue

    root = find_repo_root(cwd)

    if code_arg:
        # Write code to temp file and run it
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".js", delete=False, encoding="utf-8"
            ) as f:
                f.write(code_arg)
                temp_file = f.name

            try:
                return _run_code([node_exe, temp_file], cwd, timeout)
            finally:
                try:
                    os.unlink(temp_file)
                except Exception:
                    pass
        except Exception as exc:
            return error_result("node_run", str(exc), exit_code=1)

    elif file_arg:
        try:
            full_path = os.path.join(root, file_arg) if not os.path.isabs(file_arg) else file_arg
        except ValueError:
            return error_result("node_run", "Invalid file path")

        if not os.path.isfile(full_path):
            return error_result("node_run", f"File not found: {file_arg}")

        return _run_code([node_exe, full_path], cwd, timeout)

    return error_result("node_run", "No code or file provided")
