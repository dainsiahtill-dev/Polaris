"""Probe ACGA embedding runtime environment and print JSON diagnostics."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

# Ensure backend root is importable when executed as a script.
BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _probe_provider_environment() -> dict[str, object]:
    try:
        module = importlib.import_module("polaris.infrastructure.llm.embedding_runtime")
        probe_embedding_environment = module.probe_embedding_environment
        return dict(probe_embedding_environment())
    except Exception as exc:
        return {
            "available": False,
            "reason": "embedding_provider_probe_unavailable",
            "error": str(exc),
        }


def _probe_runtime_health() -> dict[str, object]:
    try:
        module = importlib.import_module("polaris.kernelone.llm.embedding_runtime")
        get_health = module.get_health
        query_type = module.GetEmbeddingRuntimeHealthQueryV1

        health = get_health(query_type(include_device_inventory=True))
        if hasattr(health, "__dict__"):
            return dict(vars(health))
        return {"raw": str(health)}
    except Exception as exc:
        return {
            "available": False,
            "reason": "embedding_runtime_health_unavailable",
            "error": str(exc),
        }


def main() -> int:
    payload = {
        "runtime_health": _probe_runtime_health(),
        "provider_environment": _probe_provider_environment(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
