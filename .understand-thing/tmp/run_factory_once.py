from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

from polaris.bootstrap.config import Settings
from polaris.cells.factory.pipeline.public import TERMINAL_RUN_STATUSES
from polaris.cells.factory.pipeline.public.types import FactoryStartRequest
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.delivery.http.routers.factory import _get_service, _start_factory_run_core


def _read_directive(args: argparse.Namespace) -> str:
    if args.directive_file:
        return Path(args.directive_file).read_text(encoding="utf-8")
    return str(args.directive or "").strip()


def _tail_events(events: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    return events[-limit:] if len(events) > limit else events


async def _main_async(args: argparse.Namespace) -> int:
    workspace = str(Path(args.workspace).resolve())
    settings = Settings(workspace=workspace)
    if hasattr(settings, "workspace_path"):
        settings.workspace_path = workspace
    state = AppState(settings=settings)
    payload = FactoryStartRequest(
        workspace=workspace,
        start_from=args.start_from,
        directive=_read_directive(args),
        run_director=not args.no_director,
        director_iterations=int(args.director_iterations),
        loop=bool(args.loop),
    )

    started = await _start_factory_run_core(payload, state)
    service = _get_service(workspace)
    deadline = time.monotonic() + float(args.timeout_seconds)
    last_status = ""
    while time.monotonic() < deadline:
        run = await service.get_run(started.run_id)
        if run is None:
            raise RuntimeError(f"factory run disappeared: {started.run_id}")
        status = run.status.value
        current_stage = str(run.metadata.get("current_stage") or "")
        if status != last_status:
            print(f"[factory] run={run.id} status={status} stage={current_stage}", flush=True)
            last_status = status
        if run.status in TERMINAL_RUN_STATUSES:
            events = await service.get_run_events(run.id)
            print(
                json.dumps(
                    {
                        "run_id": run.id,
                        "status": status,
                        "stages_completed": run.stages_completed,
                        "stages_failed": run.stages_failed,
                        "failure": run.metadata.get("failure"),
                        "summary_json": run.metadata.get("summary_json"),
                        "events_tail": _tail_events(events),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )
            return 0 if status == "completed" else 1
        await asyncio.sleep(float(args.poll_seconds))

    run = await service.get_run(started.run_id)
    events = await service.get_run_events(started.run_id)
    print(
        json.dumps(
            {
                "run_id": started.run_id,
                "status": run.status.value if run else "missing",
                "timeout_seconds": args.timeout_seconds,
                "events_tail": _tail_events(events),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Polaris Factory workflow and wait for a terminal state.")
    parser.add_argument("workspace")
    parser.add_argument("--directive", default="")
    parser.add_argument("--directive-file", default="")
    parser.add_argument("--start-from", choices=["auto", "architect", "pm", "director"], default="architect")
    parser.add_argument("--director-iterations", type=int, default=1)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--no-director", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=5)
    return asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
