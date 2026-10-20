"""Thin opt-in CLI arguments shared by PM, Chief Engineer and Director.

Legacy CLI defaults remain until the default-engine acceptance switch. New
commands use the same host settings, service, SQLite store and run resources as
HTTP; an explicit old run id is never imported or converted.
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Literal


def add_engineering_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--engine",
        choices=("legacy", "engineering_v2"),
        default="legacy",
        help="Opt in to engineering_v2; legacy remains default until acceptance",
    )
    parser.add_argument(
        "--engineering-action",
        choices=("discuss", "execute", "observe", "cancel", "resume"),
        default="discuss",
        help="New-engine action; discussion creates no run",
    )
    parser.add_argument("--instruction", default="", help="Discussion or execution instruction")
    parser.add_argument("--workflow", choices=("direct", "collaborative"), default="direct")
    parser.add_argument("--permission-profile-id", default=None, help="Exact server-configured permission profile")
    parser.add_argument(
        "--required-check",
        action="append",
        default=[],
        help="Required verification command; repeat to retain multiple checks",
    )
    parser.add_argument("--request-key", default=None, help="Idempotent engineering creation key")
    parser.add_argument("--run-id", default=None, help="Existing engineering run for observe/cancel/resume")


async def run_engineering_action(
    args: argparse.Namespace, *, role: Literal["pm", "chief_engineer", "director", "qa"]
) -> int:
    """Map CLI intent only; the public service owns execution and recovery."""
    from polaris.bootstrap.config import get_settings
    from polaris.bootstrap.engineering_runtime import (
        create_engineering_service,
        engineering_limits,
        observe_engineering_run,
        start_engineering_run,
    )

    action = args.engineering_action
    try:
        # Host/provider diagnostics may print, including enabled native HTTP
        # traces. Preserve them on stderr while stdout remains one CLI result.
        with redirect_stdout(sys.stderr):
            workspace = Path(args.workspace).expanduser().resolve(strict=True)
            settings = get_settings().model_copy(
                deep=True, update={"workspace": workspace, "workspace_path": str(workspace)}
            )
            service = create_engineering_service(settings)
            if action == "discuss":
                if args.run_id:
                    raise ValueError("discussion cannot resume a run; select observe or resume explicitly")
                message = args.instruction or getattr(args, "goal", "")
                if not message.strip():
                    raise ValueError("--instruction is required for engineering discussion")
                discussion = await service.chat_role(role, message, limits=engineering_limits(settings))
                if not discussion.ok:
                    raise RuntimeError(discussion.error or "engineering discussion failed")
                output = discussion.output
                exit_code = 0
            else:
                if action == "execute":
                    if args.run_id or getattr(args, "resume", False):
                        raise ValueError(
                            "execute cannot convert existing runs; select --engineering-action resume --run-id"
                        )
                    instruction = args.instruction or getattr(args, "goal", "")
                    started = await start_engineering_run(
                        service,
                        settings,
                        instruction=instruction,
                        workspace=workspace,
                        workflow=args.workflow,
                        role=role,
                        permission_profile_id=args.permission_profile_id,
                        required_checks=tuple(args.required_check),
                        request_key=args.request_key,
                    )
                    identity = str(started["run_id"])
                    await service.wait(identity, workspace=workspace)
                else:
                    if not args.run_id:
                        raise ValueError("--run-id is required for observe, cancel, or resume")
                    identity = str(args.run_id)
                    if action == "cancel":
                        await service.cancel(identity, workspace=workspace)
                    elif action == "resume":
                        await service.resume(identity, workspace=workspace)
                        await service.wait(identity, workspace=workspace)
                projection = observe_engineering_run(service, identity, workspace)
                output = json.dumps(projection, ensure_ascii=False)
                exit_code = 0 if action in {"observe", "cancel"} or projection["status"] == "completed" else 1
        print(output)
        return exit_code
    except (PermissionError, KeyError, RuntimeError, ValueError, OSError) as error:
        print(json.dumps({"engine_version": "engineering_v2", "error": str(error)}, ensure_ascii=False))
        return 1
