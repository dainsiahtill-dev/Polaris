import importlib.util
import logging
import shutil
from typing import Any

from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest

logger = logging.getLogger(__name__)


def detect_gpus() -> dict[str, Any]:
    """
    Detect NVIDIA GPUs and RAPIDS stack availability.
    Returns a capability report.
    """
    report: dict[str, Any] = {
        "available": False,
        "count": 0,
        "devices": [],
        "driver_version": "unknown",
        "cuda_version": "unknown",
        "rapids_available": False,
        "error": None,
    }

    # 1. Check for NVIDIA Driver via nvidia-smi
    smi_path = shutil.which("nvidia-smi")
    if not smi_path:
        report["error"] = "nvidia-smi not found"
        return report

    cmd_svc = CommandExecutionService(".")
    try:
        request = CommandRequest(
            executable="nvidia-smi",
            args=[
                "--query-gpu=driver_version,name,memory.total,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            timeout_seconds=15,
        )
        result = cmd_svc.run(request)
        if not result.get("ok") or result.get("returncode", -1) != 0:
            report["error"] = f"nvidia-smi query failed: {result.get('stderr', '')}"
            return report

        lines = result.get("stdout", "").strip().split("\n")
        devices = []
        for i, line in enumerate(lines):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                devices.append(
                    {
                        "index": i,
                        "name": parts[1],
                        "memory_total_mb": int(parts[2]),
                        "driver_version": parts[0],
                        "compute_cap": parts[3] if len(parts) > 3 else "unknown",
                    }
                )

        report["count"] = len(devices)
        report["devices"] = devices
        report["available"] = len(devices) > 0
        if devices:
            report["driver_version"] = devices[0]["driver_version"]

    except (RuntimeError, ValueError) as e:
        report["error"] = f"Failed to query nvidia-smi: {e}"
        return report

    # 2. Check for RAPIDS (cudf)
    report["rapids_available"] = importlib.util.find_spec("cudf") is not None
    if not report["rapids_available"]:
        report["rapids_error"] = "cudf module not found"

    return report


if __name__ == "__main__":
    import json

    logger.info("%s", json.dumps(detect_gpus(), indent=2))
