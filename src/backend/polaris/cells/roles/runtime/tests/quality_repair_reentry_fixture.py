"""Finite synthetic decisions, real engineering KFS/process effects and SQLite facts.

This component fixture is neither real-model nor Bench qualification. Optional
repair-producer context remains an observation; it does not grant new-run rights
or become a legacy Kernel commit receipt.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shlex
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import scripted_provider
from polaris.cells.roles.runtime.tests.test_engineering_loop import _native
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

BROKEN_SOURCE = "import { renderer } from '../engine/renderer.js';\nexport const view = renderer;\n"
REPAIRED_SOURCE = "export const view = 1;\n"
READ_ONLY_REFERENCE = "export const readOnlyReference = 41;\n"
AUTHORIZED_TOOLS = frozenset({"edit_file", "read_file", "execute_command"})


def seed_quality_repair_inputs(workspace: Path, owned: list[str], reference: str) -> None:
    """Explicit upstream test inputs, written through the existing KFS."""
    filesystem = KernelFileSystem(str(workspace), get_default_adapter())
    filesystem.workspace_write_text(owned[0], "<!doctype html><main></main>\n", encoding="utf-8")
    filesystem.workspace_mkdir(str(Path(owned[1]).parent), parents=True, exist_ok=True)
    filesystem.workspace_write_text(owned[1], BROKEN_SOURCE, encoding="utf-8")
    filesystem.workspace_mkdir(str(Path(reference).parent), parents=True, exist_ok=True)
    filesystem.workspace_write_text(reference, READ_ONLY_REFERENCE, encoding="utf-8")


async def run_actual_authorized_repair(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    owned: list[str],
    reference: str,
    instruction: str,
) -> dict[str, Any]:
    """Run the current owner; return its actual observations without legacy conversion."""
    node = shutil.which("node", path=os.defpath)
    compiler = shutil.which("tsc", path=os.defpath)
    if node is None or compiler is None:
        raise RuntimeError("actual TypeScript verifier is unavailable")
    gate = shlex.join(
        [
            str(Path(node).resolve()),
            str(Path(compiler).resolve()),
            "--noEmit",
            "--skipLibCheck",
            "--target",
            "ES2020",
            "--module",
            "ESNext",
            owned[1],
        ]
    )
    filesystem = KernelFileSystem(str(workspace), get_default_adapter())
    reference_before = filesystem.workspace_read_bytes(reference) if filesystem.workspace_exists(reference) else None
    reads = tuple(workspace / path for path in [*owned, reference])
    writes = tuple(workspace / path for path in owned)
    scope = PermissionScope(reads, writes, AUTHORIZED_TOOLS, False)
    # Reuse the existing loopback fixture implementation. No global workspace
    # fixture or model configuration is changed outside this private invocation.
    provider_fixture = contextmanager(inspect.unwrap(scripted_provider))
    with provider_fixture(monkeypatch) as provider:
        for response in [
            _native(("before-check", "execute_command", {"command": gate})),
            _native(("read-owned", "read_file", {"file": owned[1]})),
            _native(
                (
                    "deny-reference-write",
                    "edit_file",
                    {"file": reference, "search": "readonly", "replace": "UNAUTHORIZED"},
                )
            ),
            _native(
                ("repair-owned", "edit_file", {"file": owned[1], "search": BROKEN_SOURCE, "replace": REPAIRED_SOURCE})
            ),
            _native(("read-after-commit", "read_file", {"file": owned[1]})),
            _native(("after-check", "execute_command", {"command": gate})),
            _native(text="Repaired the owned importer; actual TypeScript check completed."),
        ]:
            provider.enqueue_json(response)
        service = EngineeringRunService(
            workspace,
            client=provider.client("openai", workspace, monkeypatch),
            permission_resolver=lambda _saved: scope,
        )
        command = RoleRunCommand(
            "quality-reentry-component", "director", instruction, workspace, required_checks=(gate,)
        )
        result = await service.run_role(
            command, scope=scope, limits=RunLimits(time.time() + 90, 256_000, 64_000), cancel=asyncio.Event()
        )
        snapshot = service.snapshot(command.session_id, workspace=workspace)
        evidence = {
            "kind": "synthetic-decisions-with-real-engineering-effects",
            "result_status": result.status,
            "verification": result.verification,
            "snapshot": snapshot,
            "native_requests": [record["body"] for record in provider.requests],
            "gate": gate,
            "reference_before": reference_before.decode("utf-8") if reference_before is not None else None,
            "reference_after": filesystem.workspace_read_text(reference, encoding="utf-8")
            if filesystem.workspace_exists(reference)
            else None,
            "current_source": filesystem.workspace_read_text(owned[1], encoding="utf-8"),
            "declared_reads": [str(path) for path in reads],
            "declared_writes": [str(path) for path in writes],
        }
    audit_dir = os.environ.get("QUALITY_REENTRY_AUDIT_DIR")
    if audit_dir:
        KernelFileSystem(audit_dir, get_default_adapter()).workspace_write_text(
            "actual-engineering-reentry.json", json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return evidence
