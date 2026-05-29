from __future__ import annotations

import asyncio
import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.bootstrap.config import Settings
from polaris.cells.factory.pipeline.public.types import FactoryStartRequest
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.delivery.http.routers.factory import (
    _get_factory_run_audit_bundle_core,
    _get_factory_run_status_core,
    _start_factory_run_core,
)


POLARIS_ROOT = Path("C:/Users/dains/Documents/GitLab/polaris")
TARGET_WORKSPACE = Path("C:/Users/dains/Documents/GitLab/fashion-gen-studio")
STATUS_PATH = POLARIS_ROOT / ".understand-thing" / "tmp" / "strict_factory_status.json"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def write_status(payload: dict[str, Any]) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def main() -> None:
    settings = Settings(workspace=str(TARGET_WORKSPACE))
    state = AppState(settings=settings)
    start_from = os.environ.get("POLARIS_AUDIT_FACTORY_START_FROM", "architect").strip().lower() or "architect"
    status_name = os.environ.get("POLARIS_AUDIT_FACTORY_STATUS_NAME", "strict_factory_status").strip()
    if status_name:
        global STATUS_PATH
        STATUS_PATH = POLARIS_ROOT / ".understand-thing" / "tmp" / f"{status_name}.json"

    payload = FactoryStartRequest(
        workspace=str(TARGET_WORKSPACE),
        start_from=start_from,  # type: ignore[arg-type]
        input_source="docs",
        run_director=True,
        director_iterations=0,
        loop=False,
        directive=(
            "Strictly follow the FashionGen Studio project document under .polaris/docs, "
            "use the Polaris workflow Architect -> PM -> ChiefEngineer -> Director -> QA, "
            "and continue closing the real production gaps for the apparel AI desktop tool."
        ),
    )

    started = await _start_factory_run_core(payload, state)
    run_id = started.run_id
    write_status(
        {
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "latest": _jsonable(started),
            "terminal": False,
        }
    )

    terminal = {"completed", "failed", "cancelled", "canceled"}
    for _ in range(720):
        await asyncio.sleep(10)
        status = await _get_factory_run_status_core(run_id, state)
        data = _jsonable(status)
        write_status(
            {
                "run_id": run_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "latest": data,
                "terminal": str(data.get("status") or "").lower() in terminal,
            }
        )
        if str(data.get("status") or "").lower() in terminal:
            try:
                bundle = await _get_factory_run_audit_bundle_core(run_id, limit=200, state=state)
                bundle_path = POLARIS_ROOT / ".understand-thing" / f"strict_factory_audit_{run_id}.json"
                bundle_path.write_text(
                    json.dumps(_jsonable(bundle), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:  # noqa: BLE001 - audit script must preserve failure evidence.
                write_status(
                    {
                        "run_id": run_id,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "latest": data,
                        "terminal": True,
                        "audit_bundle_error": str(exc),
                    }
                )
            return

    write_status(
        {
            "run_id": run_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "terminal": False,
            "error": "strict factory audit timed out after 7200 seconds",
        }
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 - top-level audit failure capture.
        write_status(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "terminal": True,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=30),
            }
        )
        raise
