from __future__ import annotations

import asyncio
import json
from pathlib import Path

from polaris.bootstrap.config import Settings
from polaris.cells.llm.evaluation.public.service import run_readiness_tests


async def main() -> None:
    workspace = str(Path(r"C:\Users\dains\Documents\GitLab\fashion-gen-studio"))
    settings = Settings(workspace=workspace)
    try:
        report = await run_readiness_tests(
            workspace=workspace,
            settings=settings,
            provider_id="codex_cli",
            model="gpt-5.3-codex",
            role="director",
            suites=None,
            skip_persistence=False,
        )
        suites = report.get("suites") or {}
        payload = {
            "role": "director",
            "provider_id": "codex_cli",
            "model": "gpt-5.3-codex",
            "ready": (report.get("final") or {}).get("ready"),
            "grade": (report.get("final") or {}).get("grade"),
            "run_id": report.get("test_run_id"),
            "suites": {
                name: {
                    "ok": value.get("ok") if isinstance(value, dict) else None,
                    "status": value.get("status") if isinstance(value, dict) else None,
                    "error": value.get("error") if isinstance(value, dict) else None,
                }
                for name, value in suites.items()
            },
        }
    except Exception as exc:  # noqa: BLE001 - audit script captures live provider failures.
        payload = {
            "role": "director",
            "provider_id": "codex_cli",
            "model": "gpt-5.3-codex",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1200],
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
