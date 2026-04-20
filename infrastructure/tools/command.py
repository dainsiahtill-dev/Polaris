import time
import subprocess
from typing import List
from .utils import build_utf8_env, Result

def run_command(cmd: List[str], cwd: str, timeout: int) -> Result:
    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout if timeout > 0 else None,
            check=False,
            env=build_utf8_env(),
        )
        duration = time.time() - start
        return {
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
            "duration": duration,
            "duration_ms": int(duration * 1000),
            "truncated": False,
            "artifacts": [],
            "command": cmd,
        }
    except subprocess.TimeoutExpired as exc:
        duration = time.time() - start
        return {
            "ok": False,
            "exit_code": 124,
            "stdout": exc.stdout or "",
            "stderr": "Timeout expired.",
            "duration": duration,
            "duration_ms": int(duration * 1000),
            "truncated": False,
            "artifacts": [],
            "command": cmd,
        }
