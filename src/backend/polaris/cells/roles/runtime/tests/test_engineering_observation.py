"""Real Store/KFS query boundaries; QA decisions below are synthetic fixtures."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    BrowserObservationConfig,
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.test_engineering_visual_service import _image
from polaris.kernelone.fs.runtime import KernelFileSystem


async def seed_current_candidate(root: Path, *, visual: bool = False) -> tuple[EngineeringRunService, PermissionScope]:
    workspace = root / "workspace"
    dependency = root / "dependency"
    workspace.mkdir()
    dependency.mkdir()
    (workspace / "source.txt").write_text("original source", encoding="utf-8")
    (dependency / "library.txt").write_text("original dependency", encoding="utf-8")
    output = workspace / "output"
    output.mkdir()
    (output / "result.txt").write_text("original output", encoding="utf-8")
    scope = PermissionScope(
        (workspace, dependency),
        (workspace,),
        frozenset({"execute_command", "verify_project", "browser_observe", "report_review"}),
        False,
        BrowserObservationConfig("/usr/bin/true", "/usr/bin/python3", (), True) if visual else None,
    )
    service = EngineeringRunService(
        workspace, permission_resolver=lambda _row: scope, writable_output_roots=(output,), require_qa=True
    )
    limits = RunLimits(None, 100000, 20000)
    check = "python3 -B -c 'assert 2 + 2 == 4'"
    service.store.create_run(
        RoleRunCommand("observation", "director", "Private module assertion", workspace, required_checks=(check,)),
        scope=scope,
        limits=limits,
        engine_version=service.engine_version,
        request_key="observation-key",
        require_qa=True,
        verification_output_roots=(output,),
    )
    verifier = service._verification(scope, limits, asyncio.Event())
    with service.store.acquire_workspace(workspace):
        checks = await verifier.run_verification((check,), session_id="observation", workspace_revision=0)
    assert checks and checks[0].ok and checks[0].exit_code == 0
    # Synthetic QA/native-wire journals exercise applicability, never native quality.
    qa_details: dict[str, Any] = {}
    if visual:
        frame = service.store.append_event("observation", kind="role_started", role="qa", payload={})
        capture_turn = service.store.append_event("observation", kind="turn_started", role="qa", payload={})
        service.store.record_tool_dispatch(
            "observation",
            turn_seq=capture_turn,
            call_id="pixels",
            tool="browser_observe",
            arguments={},
            role="qa",
        )
        image = _image()
        service.store.append_event(
            "observation",
            kind="visual_observation_completed",
            role="qa",
            payload={
                "ok": True,
                "requested_by": [capture_turn, "pixels"],
                "image_sha256": [image.sha256],
                "input_binding": verifier.review_binding("observation"),
            },
        )
        service.store.record_tool_observation(
            "observation",
            turn_seq=capture_turn,
            role="qa",
            observation=ToolObservation("pixels", "browser_observe", True, "fixture pixels", 0, False, images=(image,)),
        )
        review_turn = service.store.append_event("observation", kind="turn_started", role="qa", payload={})
        service.store.append_event(
            "observation",
            kind="llm_request_observed",
            role="qa",
            payload={
                "turn_seq": review_turn,
                "phase": "terminal",
                "status": "completed",
                "audit": {"audit_scope": "provider_native_wire", "image_sha256": [image.sha256]},
            },
        )
        service.store.record_tool_dispatch(
            "observation",
            turn_seq=review_turn,
            call_id="report",
            tool="report_review",
            arguments={},
            role="qa",
        )
        service.store.record_tool_observation(
            "observation",
            turn_seq=review_turn,
            role="qa",
            observation=ToolObservation("report", "report_review", True, json.dumps({"approved": True}), None, False),
        )
        qa_details = {"frame_seq": frame, "report_call_id": "report"}
    service.store.append_event(
        "observation",
        kind="qa_review_completed",
        role="qa",
        payload={
            "verdict": "approved" if visual else "rejected",
            "input_binding": verifier.review_binding("observation"),
            **qa_details,
        },
    )
    service.store.update_run("observation", status="completed")
    return service, scope


def database_dump(service: EngineeringRunService) -> list[str]:
    with sqlite3.connect(service.store.database_path) as connection:
        return list(connection.iterdump())


@pytest.mark.parametrize("visual", [False, True])
async def test_one_public_observation_reads_actual_inputs_once_and_is_read_only(tmp_path, monkeypatch, visual):
    service, _scope = await seed_current_candidate(tmp_path, visual=visual)
    captures: list[bool] = []
    reads: list[str] = []
    original_manifest = EngineeringVerification._manifest
    original_read = KernelFileSystem.workspace_read_bytes

    def manifest(self, *, include_outputs):
        captures.append(include_outputs)
        return original_manifest(self, include_outputs=include_outputs)

    def read(self, path, *args, **kwargs):
        reads.append(path)
        return original_read(self, path, *args, **kwargs)

    monkeypatch.setattr(EngineeringVerification, "_manifest", manifest)
    monkeypatch.setattr(KernelFileSystem, "workspace_read_bytes", read)
    before = database_dump(service)
    row = service.snapshot("observation", workspace=service.workspace)
    assert row["verification"] == "passed" and row["qa_verdict"] == ("approved" if visual else "rejected")
    assert row["project_outcome"] == ("accepted" if visual else "rejected")
    assert captures == [True]
    assert len(reads) == len(set(reads)) == 3
    assert database_dump(service) == before
    assert not service._tasks and not service._cancellations


@pytest.mark.parametrize("missing", ["visual_observation_completed", "llm_request_observed"])
async def test_required_visual_observation_and_physical_request_are_not_replaced_by_cached_inputs(tmp_path, missing):
    service, _scope = await seed_current_candidate(tmp_path, visual=True)
    assert service.observe("observation", workspace=service.workspace)["qa_verdict"] == "approved"
    with sqlite3.connect(service.store.database_path) as connection:
        connection.execute("DELETE FROM events WHERE session_id=? AND kind=?", ("observation", missing))
    row = service.observe("observation", workspace=service.workspace)
    assert row["qa_verdict"] == row["project_outcome"] == "unknown"


@pytest.mark.parametrize("changed", ["source", "output", "dependency"])
@pytest.mark.parametrize("query", ["observe", "snapshot", "current_snapshot", "find_request", "cold"])
@pytest.mark.parametrize("visual", [False, True])
async def test_separate_queries_reobserve_source_outputs_and_external_dependencies(tmp_path, changed, query, visual):
    service, scope = await seed_current_candidate(tmp_path, visual=visual)
    assert service.observe("observation", workspace=service.workspace)["verification"] == "passed"
    paths = {
        "source": service.workspace / "source.txt",
        "output": service.workspace / "output/result.txt",
        "dependency": tmp_path / "dependency/library.txt",
    }
    paths[changed].write_text("changed current bytes", encoding="utf-8")
    before = database_dump(service)
    if query == "cold":
        service = EngineeringRunService(
            service.workspace,
            permission_resolver=lambda _row: scope,
            writable_output_roots=(service.workspace / "output",),
            require_qa=True,
        )
        row = service.observe("observation", workspace=service.workspace)
    elif query == "current_snapshot":
        row = service.current_snapshot(workspace=service.workspace)
    elif query == "find_request":
        row = service.find_request("observation-key", workspace=service.workspace)
    else:
        row = getattr(service, query)("observation", workspace=service.workspace)
    assert row["verification"] == row["qa_verdict"] == row["project_outcome"] == "unknown"
    assert row["acceptance_basis"] is None
    assert database_dump(service) == before


async def test_unavailable_capture_and_current_host_revocation_cannot_preserve_pass(tmp_path, monkeypatch):
    service, _scope = await seed_current_candidate(tmp_path)
    before = database_dump(service)

    def unavailable(*_args, **_kwargs):
        raise OSError("controlled unavailable current bytes")

    with monkeypatch.context() as patch:
        patch.setattr(KernelFileSystem, "workspace_read_bytes", unavailable)
        row = service.observe("observation", workspace=service.workspace)
        assert row["verification"] == row["qa_verdict"] == row["project_outcome"] == "unknown"
    service._permission_resolver = lambda _row: PermissionScope((), (), frozenset(), False)
    row = service.observe("observation", workspace=service.workspace)
    assert row["verification"] == row["qa_verdict"] == row["project_outcome"] == "unknown"
    assert database_dump(service) == before


async def test_default_verifier_calls_remain_fresh_after_a_public_query(tmp_path):
    service, scope = await seed_current_candidate(tmp_path)
    verifier = service._verification(scope, RunLimits(None, 100000, 20000), asyncio.Event())
    assert verifier.current_verification("observation") == "passed"
    original = verifier.review_binding("observation")
    assert service.observe("observation", workspace=service.workspace)["verification"] == "passed"
    (service.workspace / "source.txt").write_text("later source", encoding="utf-8")
    assert verifier.current_verification("observation") == "unknown"
    assert verifier.review_binding("observation") != original


@pytest.mark.parametrize("visual", [False, True])
async def test_current_checks_ignore_unrelated_invalid_auxiliary_json(tmp_path, visual):
    service, _scope = await seed_current_candidate(tmp_path, visual=visual)
    seq = service.store.append_event("observation", kind="request_prepared", role="director", payload={})
    with sqlite3.connect(service.store.database_path) as connection:
        connection.execute("UPDATE events SET payload=? WHERE seq=? AND session_id=?", ("{broken", seq, "observation"))
    # observe has no public event window to hydrate; unrelated payloads are irrelevant.
    row = service.observe("observation", workspace=service.workspace)
    assert row["verification"] == "passed" and row["qa_verdict"] == ("approved" if visual else "rejected")


async def test_query_capture_is_context_local_and_released_after_exception(tmp_path):
    service, scope = await seed_current_candidate(tmp_path)
    verifier = service._verification(scope, RunLimits(None, 100000, 20000), asyncio.Event())
    with pytest.raises(RuntimeError, match="controlled query failure"), verifier._observe_current_inputs():
        binding = verifier.review_binding("observation")
        (service.workspace / "source.txt").write_text("new source during captured query", encoding="utf-8")
        assert verifier.review_binding("observation") == binding
        # run_in_executor does not copy the current thread's query context.
        from polaris.kernelone.concurrency import get_concurrency_manager

        current = await asyncio.get_running_loop().run_in_executor(
            get_concurrency_manager().get_io_pool(), verifier.current_verification, "observation"
        )
        assert current == "unknown"
        raise RuntimeError("controlled query failure")
    assert verifier.current_verification("observation") == "unknown"
    assert verifier.review_binding("observation") != binding
