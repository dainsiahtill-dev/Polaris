import contextlib
import json
import logging
import os
import shutil
import tempfile
from typing import Any

from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest

logger = logging.getLogger("app.services.quality_service")


class QualityService:
    def __init__(self) -> None:
        self.ruff_executable = shutil.which("ruff")
        self.available = self.ruff_executable is not None

    def lint_code(self, code: str, extension: str = ".py", fix: bool = False) -> dict[str, Any]:
        """
        Runs Ruff on the provided code.
        Returns lint errors or fixed code.
        """
        if not self.available or extension != ".py":
            return {"success": False, "reason": "ruff_missing_or_not_python"}

        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=extension, delete=False, encoding="utf-8") as tmp:
                tmp.write(code)
                tmp_path = tmp.name

            cmd = [self.ruff_executable, "check", tmp_path, "--output-format", "json"]
            if fix:
                cmd.append("--fix")

            # Run Ruff
            cmd_svc = CommandExecutionService(".")
            executable = cmd[0]
            if executable is None:
                return {"success": False, "reason": "ruff_executable_resolved_to_none"}
            request = CommandRequest(
                executable=executable,
                args=[str(a) for a in cmd[1:]],
                timeout_seconds=30,
            )
            run_result = cmd_svc.run(request)

            # Read back file if satisfied
            fixed_code = None
            if fix:
                with open(tmp_path, encoding="utf-8") as f:
                    fixed_code = f.read()

            os.unlink(tmp_path)

            lints = []
            if run_result.get("stdout", ""):
                with contextlib.suppress(json.JSONDecodeError):
                    lints = json.loads(run_result.get("stdout", ""))

            return {"success": True, "lints": lints, "fixed_code": fixed_code if fix else None}
        except (RuntimeError, ValueError) as e:
            logger.error("Ruff execution failed: %s", e)
            return {"success": False, "error": str(e)}

    def get_status(self) -> dict[str, Any]:
        return {"available": self.available, "path": self.ruff_executable}


_service = QualityService()


def get_quality_service() -> QualityService:
    return _service
