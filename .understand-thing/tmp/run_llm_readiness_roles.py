from __future__ import annotations

import asyncio
import json
from pathlib import Path

from polaris.bootstrap.config import Settings
from polaris.cells.llm.evaluation.public.service import run_readiness_tests


async def main() -> None:
    workspace = str(Path("C:/Users/dains/Documents/GitLab/fashion-gen-studio"))
    settings = Settings(workspace=workspace)
    provider = "anthropic_compat-1779808433822"
    model = "deepseek-v4-pro"
    roles = ["architect", "qa", "chief_engineer"]
    results: list[dict[str, object]] = []

    for role in roles:
        try:
            report = await run_readiness_tests(
                workspace=workspace,
                settings=settings,
                provider_id=provider,
                model=model,
                role=role,
                suites=None,
                skip_persistence=False,
            )
            suites = report.get("suites") or {}
            results.append(
                {
                    "role": role,
                    "ready": (report.get("final") or {}).get("ready"),
                    "grade": (report.get("final") or {}).get("grade"),
                    "run_id": report.get("test_run_id"),
                    "suites": {
                        name: {
                            "ok": value.get("ok") if isinstance(value, dict) else None,
                            "status": value.get("status") if isinstance(value, dict) else None,
                        }
                        for name, value in suites.items()
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 - audit script must capture provider failures.
            results.append(
                {
                    "role": role,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1000],
                }
            )

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
