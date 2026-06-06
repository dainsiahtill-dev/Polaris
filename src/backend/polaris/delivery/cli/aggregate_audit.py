"""Aggregate LLM runtime audit package CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatCompletionsCommandV1,
    AggregateChatMessageV1,
    AuditAggregateRuntimeIntegrationsQueryV1,
)
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.kernelone.storage import resolve_runtime_path

_DEFAULT_OBJECTIVE = "Audit Polaris aggregate LLM runtime integrations."


def _status_counts(integrations: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in integrations:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _full_chain_env_preflight() -> dict[str, Any]:
    env_flag = str(os.environ.get("KERNELONE_E2E_USE_REAL_SETTINGS") or "").strip()
    env_path = Path(".env")
    return {
        "kernelone_e2e_use_real_settings": env_flag,
        "env_file_exists": env_path.exists(),
        "ready": env_flag == "1",
        "reason": "ready" if env_flag == "1" else "KERNELONE_E2E_USE_REAL_SETTINGS is not set to 1",
    }


def _default_output_path(workspace: str) -> Path:
    return Path(resolve_runtime_path(workspace, "runtime/aggregate_llm/audit/AGGREGATE_RUNTIME_AUDIT.json"))


async def build_aggregate_runtime_audit_package(
    *,
    workspace: str,
    objective: str = _DEFAULT_OBJECTIVE,
    execution_mode: str = "plan_only",
    max_lobe_turns: int = 1,
) -> dict[str, Any]:
    """Build a machine-readable aggregate runtime audit package."""
    service = RoleRuntimeService()
    integration_audit = await service.audit_aggregate_runtime_integrations(
        AuditAggregateRuntimeIntegrationsQueryV1(workspace=workspace)
    )
    command = AggregateChatCompletionsCommandV1(
        workspace=workspace,
        messages=(AggregateChatMessageV1(role="user", content=objective),),
        domain="code",
        execution_mode=execution_mode,
        failure_signals=("compile_failure",) if execution_mode != "plan_only" else (),
        failure_evidence=(
            {
                "compiler_output": "aggregate_audit_synthetic_compiler_signal",
                "changed_files": [],
                "test_command": "aggregate_audit",
            }
            if execution_mode != "plan_only"
            else {}
        ),
        metadata={"max_lobe_turns": max_lobe_turns},
    )
    completion = await service.chat_completions(command)
    integration_payload = [asdict(item) for item in integration_audit.integrations]
    execution_result = asdict(completion.execution_result) if completion.execution_result is not None else None
    execution_results = [asdict(item) for item in completion.execution_results]
    runtime_integrations_wired: list[str] = []
    if execution_result:
        metadata = execution_result.get("metadata")
        if isinstance(metadata, dict):
            aggregate_runtime = metadata.get("aggregate_runtime")
            if isinstance(aggregate_runtime, dict):
                runtime_integrations_wired = [
                    str(item) for item in aggregate_runtime.get("runtime_integrations_wired") or []
                ]
    return {
        "status": "PASS" if integration_audit.ok else "FAIL",
        "workspace": workspace,
        "objective": objective,
        "aggregate_model_id": completion.aggregate_plan.aggregate_model_id if completion.aggregate_plan else "",
        "execution_mode": execution_mode,
        "integration_audit": {
            "ok": integration_audit.ok,
            "integrations": len(integration_audit.integrations),
            "status_counts": _status_counts(integration_payload),
            "verified_entrypoint_count": integration_audit.verified_entrypoint_count,
            "missing_entrypoint_count": integration_audit.missing_entrypoint_count,
            "warnings": list(integration_audit.warnings),
            "priority_wired": list(integration_audit.priority_wired),
        },
        "runtime_materialization": {
            "executed": execution_result is not None,
            "executed_turns": len(execution_results),
            "runtime_integrations_wired": runtime_integrations_wired,
            "all_unique_technology_ids_materialized": len(set(runtime_integrations_wired)) == 16,
        },
        "chat_completion": asdict(completion),
        "full_chain_env_preflight": _full_chain_env_preflight(),
    }


def write_audit_package(payload: dict[str, Any], output_path: Path) -> Path:
    """Persist an aggregate audit package as UTF-8 JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a Polaris Aggregate LLM runtime audit package")
    parser.add_argument("--workspace", "-w", default=".", help="Workspace directory")
    parser.add_argument("--objective", default=_DEFAULT_OBJECTIVE, help="Audit objective")
    parser.add_argument(
        "--execution-mode",
        default="plan_only",
        choices=("plan_only", "single_turn", "lobe_chain"),
        help="Aggregate execution mode. Stateful modes require configured LLM runtime.",
    )
    parser.add_argument("--max-lobe-turns", type=int, default=1, help="Bound for lobe_chain execution")
    parser.add_argument("--output", default="", help="Output JSON path; defaults to workspace runtime")
    parser.add_argument("--pretty", action="store_true", help="Print the full package to stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    workspace = str(Path(args.workspace).resolve())
    output_path = Path(args.output) if args.output else _default_output_path(workspace)
    payload = asyncio.run(
        build_aggregate_runtime_audit_package(
            workspace=workspace,
            objective=str(args.objective),
            execution_mode=str(args.execution_mode),
            max_lobe_turns=max(1, int(args.max_lobe_turns)),
        )
    )
    written = write_audit_package(payload, output_path)
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        audit = payload["integration_audit"]
        materialization = payload["runtime_materialization"]
        print(
            "[aggregate-audit] "
            f"status={payload['status']} mode={payload['execution_mode']} "
            f"wired={audit['status_counts'].get('wired', 0)}/16 "
            f"verified={audit['verified_entrypoint_count']} missing={audit['missing_entrypoint_count']} "
            f"executed_turns={materialization['executed_turns']} output={written}"
        )
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
