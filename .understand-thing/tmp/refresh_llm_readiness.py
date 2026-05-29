from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from polaris.bootstrap.config import Settings
from polaris.cells.llm.evaluation.public.service import run_readiness_tests
from polaris.cells.llm.provider_config.public.service import resolve_llm_test_execution_context
from polaris.kernelone.storage.io_paths import build_cache_root


async def _run_role(workspace: str, settings: Settings, role: str) -> dict[str, Any]:
    cache_root = build_cache_root(settings.ramdisk_root or "", workspace)
    context = resolve_llm_test_execution_context(workspace, cache_root, {"role": role})
    report = await run_readiness_tests(
        workspace=workspace,
        settings=settings,
        provider_id=context.effective_provider_id,
        model=context.model,
        role=context.role,
        suites=list(context.suites),
        provider_cfg=context.provider_cfg if context.use_direct_config else None,
        skip_persistence=False,
    )
    final = report.get("final") if isinstance(report.get("final"), dict) else {}
    return {
        "role": role,
        "provider_id": context.effective_provider_id,
        "model": context.model,
        "ready": bool(final.get("ready")),
        "grade": str(final.get("grade") or ""),
        "run_id": str(report.get("run_id") or report.get("report_id") or ""),
        "suites": report.get("suites") if isinstance(report.get("suites"), dict) else {},
    }


async def main() -> int:
    workspace = sys.argv[1]
    roles = sys.argv[2:] or ["pm", "director", "qa"]
    settings = Settings(workspace=workspace)
    results: list[dict[str, Any]] = []
    for role in roles:
        try:
            results.append(await _run_role(workspace, settings, role))
        except Exception as exc:  # noqa: BLE001 - diagnostic script must record all failures.
            results.append({"role": role, "ready": False, "error": f"{type(exc).__name__}: {exc}"})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(bool(item.get("ready")) for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
