"""Canonical Polaris CLI host.

Product direction:
  - one host
  - multi-role
  - multi-mode

This CLI is the main product-facing shell for Polaris roles with a pure
terminal console host. Legacy test-window routing remains as compatibility
surface for non-canonical hosts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.orchestration.workflow_runtime.public.service import (
    PMWorkflowInput,
    cancel_workflow_sync,
    describe_workflow_sync,
    query_workflow_sync,
    submit_pm_workflow_sync,
    wait_for_workflow_completion_sync,
)
from polaris.cells.roles.runtime.public.contracts import GetRoleRuntimeStatusQueryV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService, query_role_runtime_status
from polaris.delivery.cli.logging_policy import (
    CLI_LOG_LEVEL_CHOICES,
    configure_cli_logging,
)
from polaris.delivery.cli.terminal_console import run_role_console
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS
from polaris.kernelone.fs.encoding import enforce_utf8
from polaris.kernelone.fs.runtime import KernelFileSystem

if TYPE_CHECKING:
    from collections.abc import Sequence

_DEFAULT_PM_CONTRACTS_FILE = "runtime/contracts/pm_tasks.contract.json"


def _add_workspace_argument(
    parser: argparse.ArgumentParser,
    *,
    default: str | object = ".",
) -> None:
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=default,
        help="Workspace directory",
    )


def _add_log_level_argument(
    parser: argparse.ArgumentParser,
    *,
    default: str | None | object = None,
) -> None:
    parser.add_argument(
        "--log-level",
        choices=CLI_LOG_LEVEL_CHOICES,
        default=default,
        help=("CLI logging level. Supports debug/info/warn/warning/error/critical (or env KERNELONE_CLI_LOG_LEVEL)."),
    )


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="polaris-cli",
        description="Polaris unified host: one host, multi-role, multi-mode",
    )
    _add_workspace_argument(parser)
    _add_log_level_argument(parser, default=None)

    subparsers = parser.add_subparsers(dest="command", required=True)

    chat_parser = subparsers.add_parser("chat", help="Run a role through the canonical Polaris host")
    _add_workspace_argument(chat_parser, default=argparse.SUPPRESS)
    _add_log_level_argument(chat_parser, default=argparse.SUPPRESS)
    chat_parser.add_argument(
        "--role", type=str, default="director", help="Role id (director, pm, architect, chief_engineer, qa)"
    )
    chat_parser.add_argument(
        "--mode",
        choices=["interactive", "oneshot", "server", "console"],
        default="interactive",
        help="Host mode",
    )
    chat_parser.add_argument("--goal", type=str, default="", help="Goal/message for oneshot mode")
    chat_parser.add_argument("--host", type=str, default="127.0.0.1", help="Server bind host")
    chat_parser.add_argument("--port", type=int, default=50000, help="Server bind port")
    chat_parser.add_argument(
        "--backend",
        choices=["auto", "textual", "rich", "plain"],
        default="auto",
        help="Console backend selection (console mode)",
    )
    chat_parser.add_argument(
        "--prompt-style",
        choices=["plain", "omp"],
        default="plain",
        help="Prompt style for console mode",
    )
    chat_parser.add_argument(
        "--omp-config",
        type=str,
        default="",
        help="Optional Oh My Posh config path for console mode",
    )
    chat_parser.add_argument(
        "--json-render",
        choices=["raw", "pretty", "pretty-color"],
        default="raw",
        help="Tool event JSON render mode for console mode",
    )
    chat_parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable unified debug/observability stream for console mode",
    )
    chat_parser.add_argument("--session-id", type=str, default="", help="Reuse a console session when supported")
    chat_parser.add_argument("--session-title", type=str, default="", help="Title for a new console session")

    status_parser = subparsers.add_parser("status", help="Query runtime status for available roles")
    _add_workspace_argument(status_parser, default=argparse.SUPPRESS)
    _add_log_level_argument(status_parser, default=argparse.SUPPRESS)
    status_parser.add_argument("--role", type=str, default="", help="Optional role filter")

    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Run or inspect canonical Polaris workflow executions",
    )
    _add_workspace_argument(workflow_parser, default=argparse.SUPPRESS)
    _add_log_level_argument(workflow_parser, default=argparse.SUPPRESS)
    workflow_parser.add_argument(
        "workflow_action",
        choices=["run", "status", "events", "cancel"],
        help="Workflow action",
    )
    workflow_parser.add_argument(
        "workflow_target",
        nargs="?",
        default="",
        help="Workflow target for run actions (currently: pm)",
    )
    workflow_parser.add_argument(
        "--workflow-id",
        type=str,
        default="",
        help="Workflow id for status/events/cancel",
    )
    workflow_parser.add_argument(
        "--contracts-file",
        type=str,
        default=_DEFAULT_PM_CONTRACTS_FILE,
        help="PM contract JSON file relative to workspace",
    )
    workflow_parser.add_argument(
        "--run-id",
        type=str,
        default="",
        help="Explicit workflow run id",
    )
    workflow_parser.add_argument(
        "--message",
        type=str,
        default="",
        help="Optional operator note attached to workflow metadata",
    )
    workflow_parser.add_argument(
        "--wait",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Wait for terminal workflow completion after submission",
    )
    workflow_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Wait timeout in seconds when --wait is enabled",
    )
    workflow_parser.add_argument(
        "--event-limit",
        type=int,
        default=100,
        help="Maximum number of workflow events to load",
    )
    workflow_parser.add_argument(
        "--reason",
        type=str,
        default="operator_cancelled",
        help="Cancellation reason for workflow cancel",
    )
    workflow_parser.add_argument(
        "--execution-mode",
        choices=["parallel", "serial"],
        default="parallel",
        help="Director execution mode threaded into the PM workflow metadata",
    )
    workflow_parser.add_argument(
        "--max-parallel-tasks",
        type=int,
        default=3,
        help="Maximum parallel Director tasks for PM workflow execution",
    )
    workflow_parser.add_argument(
        "--ready-timeout-seconds",
        type=int,
        default=30,
        help="Ready-task resolution timeout for Director workflow",
    )
    workflow_parser.add_argument(
        "--task-timeout-seconds",
        type=int,
        default=3600,
        help="Per-task timeout for Director workflow",
    )

    test_window_parser = subparsers.add_parser(
        "test-window",
        help=argparse.SUPPRESS,
        description="Compatibility-only legacy test window surface.",
    )
    _add_workspace_argument(test_window_parser, default=argparse.SUPPRESS)
    _add_log_level_argument(test_window_parser, default=argparse.SUPPRESS)
    test_window_parser.add_argument("--role", type=str, default="director", help="Role id for the legacy window")
    test_window_parser.add_argument(
        "--surface",
        choices=["tui"],
        default="tui",
        help="Legacy test surface",
    )

    return parser


def _resolve_workspace(workspace: str) -> str:
    return str(Path(workspace).resolve())


def _ensure_cli_runtime_bindings() -> None:
    from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings
    from polaris.infrastructure.llm.provider_bootstrap import inject_kernelone_provider_runtime

    ensure_minimal_kernelone_bindings()
    inject_kernelone_provider_runtime()


def _bind_workspace_environment(workspace: str) -> None:
    workspace_root = _resolve_workspace(workspace)
    os.environ["KERNELONE_CONTEXT_ROOT"] = workspace_root
    if not str(os.environ.get("KERNELONE_RUNTIME_DB") or "").strip():
        os.environ["KERNELONE_RUNTIME_ROOT"] = str(Path(workspace_root) / "runtime")


def _kernel_fs_for_workspace(workspace: str) -> KernelFileSystem:
    return KernelFileSystem(workspace, LocalFileSystemAdapter())


def _default_workflow_run_id() -> str:
    return datetime.now(timezone.utc).strftime("cli-%Y%m%d%H%M%S")


def _read_workspace_json(workspace: str, relative_path: str) -> dict[str, Any]:
    fs = _kernel_fs_for_workspace(workspace)
    logical_path = str(relative_path or "").strip()
    if not logical_path:
        raise SystemExit("--contracts-file is required")
    try:
        raw = fs.workspace_read_text(logical_path, encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(
            f"Workflow contract file not found: {logical_path}. "
            "Generate PM contracts first or pass --contracts-file explicitly."
        ) from exc
    except ValueError as exc:
        raise SystemExit(f"Unsupported workflow contract path: {logical_path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Workflow contract file is not valid JSON: {logical_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Workflow contract file must contain a JSON object: {logical_path}")
    return payload


def _serialize_workflow_submission(submission: Any) -> dict[str, Any]:
    details = getattr(submission, "details", {})
    return {
        "submitted": bool(getattr(submission, "submitted", False)),
        "status": str(getattr(submission, "status", "") or "").strip(),
        "workflow_id": str(getattr(submission, "workflow_id", "") or "").strip(),
        "workflow_run_id": str(getattr(submission, "workflow_run_id", "") or "").strip(),
        "error": str(getattr(submission, "error", "") or "").strip(),
        "details": dict(details) if isinstance(details, dict) else {},
    }


async def _run_chat(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    role = str(args.role or "").strip() or "director"
    mode = str(args.mode or "").strip() or "interactive"
    runtime = RoleRuntimeService()

    if mode == "console":
        raise SystemExit("console mode must be dispatched through the synchronous host path")

    if mode == "interactive":
        await runtime.run_interactive(
            role=role,
            workspace=workspace,
            welcome_message=(f"[polaris-cli] role={role} workspace={workspace}\nCanonical terminal host active."),
        )
        return 0

    if mode == "oneshot":
        goal = str(args.goal or "").strip()
        if not goal:
            raise SystemExit("--goal is required for oneshot mode")
        result = await runtime.run_oneshot(
            role=role,
            workspace=workspace,
            goal=goal,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if mode == "server":
        await runtime.run_server(
            role=role,
            workspace=workspace,
            host=str(args.host or "127.0.0.1").strip() or "127.0.0.1",
            port=int(args.port),
        )
        return 0

    raise SystemExit(f"Unsupported mode: {mode}")


async def _run_status(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    role = str(getattr(args, "role", "") or "").strip() or None
    status = await query_role_runtime_status(
        GetRoleRuntimeStatusQueryV1(
            workspace=workspace,
            role=role,
            include_agent_health=True,
            include_queue=True,
            include_tools=True,
        )
    )
    print(json.dumps(dict(status), ensure_ascii=False, indent=2))
    return 0


def _run_test_window(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    role = str(args.role or "").strip() or "director"
    surface = str(args.surface or "tui").strip() or "tui"
    if surface != "tui":
        raise SystemExit(f"Unsupported test window surface: {surface}")

    from polaris.cells.roles.runtime.public.service import run_tui  # via __getattr__ lazy facade

    run_tui(role=role, workspace=workspace)
    return 0


def _run_console_chat(args: argparse.Namespace) -> int:
    import os

    workspace = _resolve_workspace(args.workspace)
    role = str(args.role or "").strip() or "director"
    backend = str(args.backend or "auto").strip() or "auto"
    debug_enabled = bool(getattr(args, "debug", False))
    cognitive_mode = str(getattr(args, "cognitive_mode", "basic") or "basic").strip()

    # Determine enable_cognitive based on mode
    enable_cognitive: bool | None = None
    if cognitive_mode == "off":
        enable_cognitive = False
    elif cognitive_mode == "full":
        # Enable all advanced cognitive features via env vars
        os.environ["COGNITIVE_ENABLE_EVOLUTION"] = "true"
        os.environ["COGNITIVE_ENABLE_GOVERNANCE"] = "true"
        os.environ["COGNITIVE_ENABLE_VALUE_ALIGNMENT"] = "true"
        os.environ["COGNITIVE_USE_LLM"] = "true"
        # enable_cognitive stays None (default enabled)

    return run_role_console(
        workspace=workspace,
        role=role,
        backend=backend,
        session_id=str(args.session_id or "").strip() or None,
        session_title=str(args.session_title or "").strip() or None,
        prompt_style=str(getattr(args, "prompt_style", "plain") or "plain").strip() or "plain",
        omp_config=str(getattr(args, "omp_config", "") or "").strip() or None,
        json_render=str(getattr(args, "json_render", "raw") or "raw").strip() or "raw",
        debug=debug_enabled,
        enable_cognitive=enable_cognitive,
    )


def _run_workflow(args: argparse.Namespace) -> int:
    workspace = _resolve_workspace(args.workspace)
    action = str(getattr(args, "workflow_action", "") or "").strip().lower()
    target = str(getattr(args, "workflow_target", "") or "").strip().lower() or "pm"

    if action == "run":
        if target != "pm":
            raise SystemExit(f"Unsupported workflow target: {target}. Only `pm` is implemented.")
        contracts_rel = str(getattr(args, "contracts_file", "") or "").strip()
        contract_payload = _read_workspace_json(workspace, contracts_rel)
        tasks = contract_payload.get("tasks") if isinstance(contract_payload.get("tasks"), list) else []
        if not tasks:
            raise SystemExit("PM workflow requires a precomputed task contract with a non-empty `tasks` list.")
        run_id = str(getattr(args, "run_id", "") or "").strip() or _default_workflow_run_id()
        workflow_input = PMWorkflowInput(
            workspace=workspace,
            run_id=run_id,
            precomputed_payload=contract_payload,
            metadata={
                "source": "polaris-cli",
                "message": str(getattr(args, "message", "") or "").strip(),
                "docs_stage": (
                    contract_payload.get("docs_stage") if isinstance(contract_payload.get("docs_stage"), dict) else {}
                ),
                "director_config": {
                    "execution_mode": str(getattr(args, "execution_mode", "parallel") or "parallel").strip().lower()
                    or "parallel",
                    "max_parallel_tasks": max(1, int(getattr(args, "max_parallel_tasks", 3) or 3)),
                    "ready_timeout_seconds": max(1, int(getattr(args, "ready_timeout_seconds", 30) or 30)),
                    "task_timeout_seconds": max(
                        1,
                        int(
                            getattr(args, "task_timeout_seconds", MAX_WORKFLOW_TIMEOUT_SECONDS)
                            or MAX_WORKFLOW_TIMEOUT_SECONDS
                        ),
                    ),
                },
            },
        )
        submission = submit_pm_workflow_sync(workflow_input)
        payload = {
            "ok": bool(getattr(submission, "submitted", False)),
            "workflow_type": "pm",
            "workspace": workspace,
            "run_id": run_id,
            "contracts_file": str(Path(contracts_rel).as_posix()),
            "contract_task_count": len(tasks),
            "submission": _serialize_workflow_submission(submission),
        }
        exit_code = 0 if payload.get("ok") else 1
        if payload.get("ok") and bool(getattr(args, "wait", False)):
            submission_data: dict[str, Any] | None = payload.get("submission")
            workflow_id_val = str(submission_data.get("workflow_id") or "") if isinstance(submission_data, dict) else ""
            wait_payload = wait_for_workflow_completion_sync(
                workflow_id_val,
                timeout_seconds=float(getattr(args, "timeout_seconds", 300.0) or 300.0),
            )
            payload["final"] = wait_payload
            final_status = str(wait_payload.get("status") or "").strip().lower()
            if not bool(wait_payload.get("ok", True)) or final_status in {
                "failed",
                "cancelled",
                "canceled",
                "terminated",
                "timed_out",
            }:
                exit_code = 1
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return exit_code

    workflow_id = str(getattr(args, "workflow_id", "") or "").strip()
    if not workflow_id:
        raise SystemExit("--workflow-id is required for workflow status/events/cancel")

    if action == "status":
        payload = describe_workflow_sync(workflow_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if bool(payload.get("ok")) else 1

    if action == "events":
        limit = max(1, int(getattr(args, "event_limit", 100) or 100))
        payload = query_workflow_sync(workflow_id, "events", limit)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if bool(payload.get("ok")) else 1

    if action == "cancel":
        payload = cancel_workflow_sync(
            workflow_id,
            reason=str(getattr(args, "reason", "operator_cancelled") or "operator_cancelled").strip(),
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if bool(payload.get("ok")) else 1

    raise SystemExit(f"Unsupported workflow action: {action}")


async def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "chat":
        return await _run_chat(args)
    if args.command == "status":
        return await _run_status(args)
    if args.command == "workflow":
        return _run_workflow(args)
    if args.command == "test-window":
        return _run_test_window(args)
    raise SystemExit(f"Unsupported command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    enforce_utf8()
    parser = create_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        configure_cli_logging(getattr(args, "log_level", None))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    args.workspace = _resolve_workspace(args.workspace)
    _bind_workspace_environment(args.workspace)
    _ensure_cli_runtime_bindings()
    if args.command == "chat" and str(getattr(args, "mode", "") or "").strip() == "console":
        return _run_console_chat(args)
    if args.command == "test-window":
        return _run_test_window(args)
    if args.command == "workflow":
        return _run_workflow(args)
    return asyncio.run(_dispatch(args))


if __name__ == "__main__":
    raise SystemExit(main())
