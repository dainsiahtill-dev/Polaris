"""Host-required independent QA through real native calls and read-only tools."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope, RoleRunCommand, RunLimits
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    isolated_workspace as isolated_workspace,
    scripted_provider as scripted_provider,
)


def _text(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}


def _calls(*calls: tuple[str, str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": identity,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                        for identity, name, arguments in calls
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope(
        (workspace,),
        (workspace,),
        frozenset({"read_file", "repo_rg", "repo_tree", "write_file", "execute_command", "report_review"}),
        False,
    )


def _service(
    workspace: Path, provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch, *, required: bool = True
) -> EngineeringRunService:
    provider.client("openai", workspace, monkeypatch)  # Configuration only; default native client remains real.
    return EngineeringRunService(workspace, require_qa=required, permission_resolver=lambda _row: _scope(workspace))


async def _run(service: EngineeringRunService, *, key: str | None = None) -> tuple[dict[str, Any], RunLimits]:
    limits = RunLimits(time.time() + 45, 1_000_000, 64_000)
    command = RoleRunCommand(
        "qa-task",
        "director",
        "The artifact must contain value2. Independently review real implementation.",
        service.workspace,
        required_checks=(
            "python3 -c \"from pathlib import Path; assert int(Path('value.txt').read_text(encoding='utf-8'))>0\"",
        ),
    )
    await service.start(command, scope=_scope(service.workspace), limits=limits, request_key=key)
    await service.wait(command.session_id, workspace=service.workspace)
    return service.snapshot(command.session_id, workspace=service.workspace), limits


async def test_required_qa_reads_actual_file_cannot_write_and_reports_native_decision(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    scripted_provider.enqueue_json(_text("Director done; not acceptance evidence."))
    scripted_provider.enqueue_json(
        _calls(
            ("read", "read_file", {"file": "value.txt"}),
            ("denied-write", "write_file", {"file": "value.txt", "content": "bad"}),
        )
    )
    scripted_provider.enqueue_json(
        _calls(
            (
                "review",
                "report_review",
                {"approved": True, "feedback": "Read current value2 and actual successful command."},
            )
        )
    )
    row, _limits = await _run(service)
    assert row["status"] == "completed" and row["verification"] == "passed", row
    assert row["require_qa"] is True and row["qa_verdict"] == "approved"
    assert row["project_outcome"] == "accepted" and row["acceptance_basis"] == "independent_qa"
    assert (isolated_workspace / "value.txt").read_text(encoding="utf-8") == "2"
    events = service.store.load_events("qa-task")
    denied = [
        event for event in events if event["kind"] == "tool_observed" and event["payload"]["call_id"] == "denied-write"
    ]
    assert denied and denied[0]["payload"]["ok"] is False
    decision = next(event for event in events if event["kind"] == "qa_review_completed")
    assert not any(event["kind"] == "tool_started" for event in events if event["seq"] > decision["seq"])
    assert "report_review" not in {
        tool["function"]["name"] for tool in scripted_provider.requests[0]["body"].get("tools", [])
    }
    qa_tools = {tool["function"]["name"] for tool in scripted_provider.requests[1]["body"]["tools"]}
    assert {"report_review", "execute_command"} <= qa_tools and not qa_tools & {"write_file", "edit_file"}
    assert "Director done; not acceptance evidence." not in json.dumps(
        scripted_provider.requests[1]["body"], ensure_ascii=False
    )


async def test_qa_text_claim_without_observed_report_is_unknown(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    scripted_provider.enqueue_json(_text("Ready."))
    scripted_provider.enqueue_json(_text("Approved. Everything passed."))
    row, _limits = await _run(service)
    assert row["qa_verdict"] == "unknown" and row["project_outcome"] == "unknown", row
    assert row["verification"] == "passed" and row["status"] == "interrupted"


async def test_negative_qa_returns_same_director_and_revalidates_without_restarting_planning(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("1", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    scripted_provider.enqueue_json(_text("Ready."))
    scripted_provider.enqueue_json(_calls(("read1", "read_file", {"file": "value.txt"})))
    scripted_provider.enqueue_json(
        _calls(
            (
                "reject",
                "report_review",
                {
                    "approved": False,
                    "feedback": "Actual source value1 violates original goal value2 despite command passing.",
                },
            )
        )
    )
    scripted_provider.enqueue_json(_calls(("repair", "write_file", {"file": "value.txt", "content": "2"})))
    scripted_provider.enqueue_json(_text("Repaired current artifact."))
    scripted_provider.enqueue_json(_calls(("read2", "read_file", {"file": "value.txt"})))
    scripted_provider.enqueue_json(
        _calls(
            (
                "approve",
                "report_review",
                {
                    "approved": True,
                    "feedback": "Current source value2 now satisfies original goal and actual revalidation.",
                },
            )
        )
    )
    row, limits = await _run(service, key="same-goal")
    assert row["project_outcome"] == "accepted" and row["qa_verdict"] == "approved", row
    assert row["limits"]["deadline"] == limits.deadline and row["request_key"] == "same-goal"
    events = service.store.load_events("qa-task")
    assert [event["payload"]["verdict"] for event in events if event["kind"] == "qa_review_completed"] == [
        "rejected",
        "approved",
    ]
    assert len([event for event in events if event["kind"] == "verification_completed"]) == 2
    assert {event["role"] for event in events if event["kind"] == "request_prepared"} == {"director", "qa"}
    assert "Actual source value1" in json.dumps(scripted_provider.requests[3]["body"], ensure_ascii=False)


async def test_default_checks_only_outcome_does_not_fake_qa(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch, required=False)
    scripted_provider.enqueue_json(_text("Ready."))
    row, _limits = await _run(service)
    assert row["project_outcome"] == "accepted" and row["acceptance_basis"] == "checks_only"
    assert row["qa_verdict"] == "not_run" and row["require_qa"] is False
    assert len(scripted_provider.requests) == 1


async def test_explicit_empty_report_grant_cannot_admit_required_qa(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    with pytest.raises(PermissionError, match="report_review"):
        await service.start(
            RoleRunCommand("empty", "director", "goal", isolated_workspace),
            scope=PermissionScope((), (), frozenset(), False),
            limits=RunLimits(None, 10000, 1000),
        )
    assert scripted_provider.requests == []


async def test_external_edit_invalidates_all_current_acceptance_fields_but_retains_approval(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = isolated_workspace / "value.txt"
    artifact.write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    scripted_provider.enqueue_json(_text("Ready."))
    scripted_provider.enqueue_json(_calls(("read", "read_file", {"file": "value.txt"})))
    scripted_provider.enqueue_json(
        _calls(("report", "report_review", {"approved": True, "feedback": "Current value2 observed."}))
    )
    row, _ = await _run(service)
    assert row["project_outcome"] == "accepted"
    artifact.write_text("3", encoding="utf-8")
    fresh = service.observe("qa-task", workspace=isolated_workspace)
    assert (fresh["verification"], fresh["qa_verdict"], fresh["project_outcome"], fresh["acceptance_basis"]) == (
        "unknown",
        "unknown",
        "unknown",
        None,
    )
    cold = _service(isolated_workspace, scripted_provider, monkeypatch)
    assert cold.observe("qa-task", workspace=isolated_workspace)["project_outcome"] == "unknown"
    assert [
        event["payload"]["verdict"]
        for event in cold.store.load_events("qa-task")
        if event["kind"] == "qa_review_completed"
    ] == ["approved"]


async def test_revoked_policy_cannot_reuse_current_qa_approval(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    for reply in (
        _text("Ready."),
        _calls(("read", "read_file", {"file": "value.txt"})),
        _calls(("report", "report_review", {"approved": True, "feedback": "Read current implementation."})),
    ):
        scripted_provider.enqueue_json(reply)
    await _run(service)
    cold = EngineeringRunService(
        isolated_workspace, require_qa=True, permission_resolver=lambda _: PermissionScope((), (), frozenset(), False)
    )
    row = cold.observe("qa-task", workspace=isolated_workspace)
    assert row["qa_verdict"] == "unknown" and row["project_outcome"] == "unknown" and row["acceptance_basis"] is None
    count = len(scripted_provider.requests)
    with pytest.raises(PermissionError):
        await cold.resume("qa-task", workspace=isolated_workspace)
    assert len(scripted_provider.requests) == count


async def test_report_cannot_approve_before_previous_turn_source_read(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    scripted_provider.enqueue_json(_text("Ready."))
    scripted_provider.enqueue_json(
        _calls(
            ("read", "read_file", {"file": "value.txt"}),
            ("premature", "report_review", {"approved": True, "feedback": "Claim without seeing current tool result."}),
        )
    )
    scripted_provider.enqueue_json(_text("Approved."))
    row, _ = await _run(service)
    assert row["qa_verdict"] == "unknown" and row["project_outcome"] == "unknown"
    rejected = next(
        event
        for event in service.store.load_events("qa-task")
        if event["kind"] == "tool_observed" and event["payload"]["call_id"] == "premature"
    )
    assert rejected["payload"]["ok"] is False


def test_host_file_is_strict_startup_input_for_same_typed_server_owner(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.bootstrap.config import Settings

    host = isolated_workspace / "host.json"
    host.write_text(
        json.dumps(
            {
                "require_qa": True,
                "run_timeout_seconds": 5400,
                "permission_profiles": {},
                "verification_output_roots": ["dist"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KERNELONE_ENGINEERING_HOST_CONFIG", str(host))
    server = Settings.from_env().server
    assert server.engineering_require_qa is True and server.engineering_run_timeout_seconds == 5400
    assert server.engineering_permission_profiles == {} and server.engineering_verification_output_roots == ("dist",)
    monkeypatch.setenv("KERNELONE_ENGINEERING_REQUIRE_QA", "false")
    assert Settings.from_env().server.engineering_require_qa is False
    host.write_text(json.dumps({"workspace": "/not-host-authority"}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        Settings.from_env()


@pytest.mark.parametrize("external_edit", [False, True])
async def test_cold_report_recovery_binds_exact_inputs_without_replaying_tools(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    external_edit: bool,
) -> None:
    import os
    import subprocess
    import sys

    artifact = isolated_workspace / "value.txt"
    artifact.write_text("2", encoding="utf-8")
    for reply in (
        _text("Ready."),
        _calls(("read-before-crash", "read_file", {"file": "value.txt"})),
        _calls(("durable-report", "report_review", {"approved": True, "feedback": "Read exact current value2."})),
    ):
        scripted_provider.enqueue_json(reply)
    child = r"""
import asyncio, os, sys, time
from pathlib import Path
from polaris.cells.roles.runtime.tests.test_engineering_qa import _scope
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService, AIExecutor
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.kernelone.llm.engine import _executor_base
workspace = Path(sys.argv[1])
_executor_base.get_role_model = lambda role: ("synthetic", "synthetic-model")
AIExecutor._get_provider_config = lambda self, provider: {"type":"openai_compat","base_url":sys.argv[2],"api_path":"/v1/chat/completions","api_key":"synthetic-only","timeout":3,"max_context_tokens":32768,"max_output_tokens":1024}
async def main():
    service = EngineeringRunService(workspace, require_qa=True, permission_resolver=lambda row: _scope(workspace))
    original = service.store.record_tool_observation
    def crash_after_commit(session_id, **kwargs):
        original(session_id, **kwargs)
        if kwargs["observation"].tool == "report_review" and kwargs["observation"].ok:
            os._exit(87)
    service.store.record_tool_observation = crash_after_commit
    command = RoleRunCommand("qa-task", "director", "The artifact must contain value2. Independently review real implementation.", workspace, required_checks=("python3 -c \"from pathlib import Path; assert int(Path('value.txt').read_text(encoding='utf-8'))>0\"",))
    await service.start(command, scope=_scope(workspace), limits=RunLimits(time.time()+45,1000000,64000), request_key="cold")
    await service.wait("qa-task", workspace=workspace)
asyncio.run(main())
"""
    completed = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", child, str(isolated_workspace), scripted_provider.base_url],
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    assert completed.returncode == 87, completed.stderr
    assert len(scripted_provider.requests) == 3
    cold = _service(isolated_workspace, scripted_provider, monkeypatch)
    before = cold.observe("qa-task", workspace=isolated_workspace)
    assert before["require_qa"] is True and before["qa_verdict"] == "unknown"
    if external_edit:
        artifact.write_text("3", encoding="utf-8")
        for reply in (
            _calls(("repair-after-edit", "write_file", {"file": "value.txt", "content": "2"})),
            _text("Current source repaired."),
            _calls(("fresh-read", "read_file", {"file": "value.txt"})),
            _calls(
                (
                    "fresh-report",
                    "report_review",
                    {"approved": True, "feedback": "Fresh review of repaired current value2."},
                )
            ),
        ):
            scripted_provider.enqueue_json(reply)
    await cold.resume("qa-task", workspace=isolated_workspace)
    await cold.wait("qa-task", workspace=isolated_workspace)
    row = cold.observe("qa-task", workspace=isolated_workspace)
    assert row["project_outcome"] == "accepted", row
    assert len(scripted_provider.requests) == (7 if external_edit else 3)
    decisions = [event for event in cold.store.load_events("qa-task") if event["kind"] == "qa_review_completed"]
    assert decisions[-1]["payload"]["report_call_id"] == ("fresh-report" if external_edit else "durable-report")
    if external_edit:
        assert decisions[0]["payload"]["verdict"] == "unknown"


async def test_qa_report_cannot_survive_policy_revocation_during_review(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    for reply in (
        _text("Ready."),
        _calls(("read", "read_file", {"file": "value.txt"})),
        _calls(("report", "report_review", {"approved": True, "feedback": "Actual source read."})),
    ):
        scripted_provider.enqueue_json(reply)
    original = service.store.record_tool_observation

    def revoke_after_report(session_id: str, **kwargs: Any) -> None:
        original(session_id, **kwargs)
        if kwargs["observation"].tool == "report_review":
            service._permission_resolver = lambda _: PermissionScope((), (), frozenset(), False)

    monkeypatch.setattr(service.store, "record_tool_observation", revoke_after_report)
    row, _ = await _run(service)
    assert (row["qa_verdict"], row["project_outcome"], row["acceptance_basis"]) == ("unknown", "unknown", None)
    assert len(scripted_provider.requests) == 3


async def test_major_finding_cannot_be_overridden_by_report_approved_flag(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    for reply in (
        _text("Ready."),
        _calls(("read", "read_file", {"file": "value.txt"})),
        _calls(
            (
                "report",
                "report_review",
                {
                    "approved": True,
                    "feedback": "A major issue remains.",
                    "major_findings": ["Original semantic behavior is absent."],
                },
            )
        ),
        _text("Cannot finish current correction."),
        _text("No new review report."),
    ):
        scripted_provider.enqueue_json(reply)
    row, _ = await _run(service)
    decisions = [
        event["payload"] for event in service.store.load_events("qa-task") if event["kind"] == "qa_review_completed"
    ]
    assert decisions[0]["verdict"] == "rejected"
    assert row["project_outcome"] != "accepted" and row["acceptance_basis"] is None


@pytest.mark.parametrize("correction", ["failed", "cancelled"])
async def test_negative_qa_noncompleted_correction_preserves_current_checks_and_edit_invalidates_rejection(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    correction: str,
) -> None:
    import asyncio

    artifact = isolated_workspace / "value.txt"
    artifact.write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    for reply in (
        _text("Ready."),
        _calls(("read", "read_file", {"file": "value.txt"})),
        _calls(
            (
                "reject",
                "report_review",
                {"approved": False, "feedback": "Current behavior still misses original requirements."},
            )
        ),
    ):
        scripted_provider.enqueue_json(reply)
    if correction == "cancelled":
        scripted_provider.enqueue_json(_text("Correction must be cancelled before any action."))
        original = service.store.append_event
        cancellation: list[asyncio.Task[None]] = []

        def cancel_correction(session_id: str, **kwargs: Any) -> int:
            seq = original(session_id, **kwargs)
            if (
                kwargs["kind"] == "request_prepared"
                and kwargs["role"] == "director"
                and any(event["kind"] == "qa_review_completed" for event in service.store.load_events(session_id))
            ):
                cancellation.append(asyncio.create_task(service.cancel(session_id, workspace=isolated_workspace)))
            return seq

        monkeypatch.setattr(service.store, "append_event", cancel_correction)
    row, _ = await _run(service)
    if correction == "cancelled":
        await asyncio.gather(*cancellation)
    assert row["status"] == correction, row
    assert row["verification"] == "passed" and len(row["checks"]) == 1, row
    assert row["qa_verdict"] == "rejected" and row["project_outcome"] == "rejected" and row["acceptance_basis"] is None
    decisions = [event for event in service.store.load_events("qa-task") if event["kind"] == "qa_review_completed"]
    assert [event["payload"]["verdict"] for event in decisions] == ["rejected"]
    artifact.write_text("3", encoding="utf-8")
    fresh = service.observe("qa-task", workspace=isolated_workspace)
    assert (fresh["verification"], fresh["qa_verdict"], fresh["project_outcome"], fresh["acceptance_basis"]) == (
        "unknown",
        "unknown",
        "unknown",
        None,
    )
    assert [
        event["payload"]["verdict"]
        for event in service.store.load_events("qa-task")
        if event["kind"] == "qa_review_completed"
    ] == ["rejected"]


@pytest.mark.parametrize(
    "reports",
    [
        (
            (
                "reject",
                {
                    "approved": False,
                    "feedback": "Required behavior absent.",
                    "major_findings": ["Unresolved current semantic requirement."],
                },
            ),
            ("approve", {"approved": True, "feedback": "Accept despite unresolved finding."}),
        ),
        (
            ("approve", {"approved": True, "feedback": "Accept first."}),
            (
                "reject",
                {
                    "approved": False,
                    "feedback": "Required behavior absent.",
                    "major_findings": ["Unresolved current semantic requirement."],
                },
            ),
        ),
        (
            (
                "major",
                {
                    "approved": True,
                    "feedback": "A major requirement is still absent.",
                    "major_findings": ["Unresolved current semantic requirement."],
                },
            ),
            ("approve", {"approved": True, "feedback": "Accept despite unresolved finding."}),
        ),
        (
            ("approve", {"approved": True, "feedback": "Accept first."}),
            (
                "major",
                {
                    "approved": True,
                    "feedback": "A major requirement is still absent.",
                    "major_findings": ["Unresolved current semantic requirement."],
                },
            ),
        ),
    ],
)
async def test_same_qa_batch_cannot_erase_any_current_negative_or_major_finding(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    reports: tuple[tuple[str, dict[str, Any]], ...],
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    for reply in (
        _text("Ready."),
        _calls(("read", "read_file", {"file": "value.txt"})),
        _calls(*((identity, "report_review", arguments) for identity, arguments in reports)),
    ):
        scripted_provider.enqueue_json(reply)
    row, _ = await _run(service)
    assert row["qa_verdict"] == "rejected" and row["project_outcome"] == "rejected", row
    assert row["acceptance_basis"] is None and row["verification"] == "passed"
    events = service.store.load_events("qa-task")
    observed = [
        event for event in events if event["kind"] == "tool_observed" and event["payload"]["tool"] == "report_review"
    ]
    assert [event["payload"]["call_id"] for event in observed] == [identity for identity, _ in reports]
    assert all(event["payload"]["ok"] for event in observed)
    decision = next(event["payload"] for event in events if event["kind"] == "qa_review_completed")
    assert decision["report_call_id"] == next(
        identity for identity, arguments in reports if not arguments["approved"] or arguments.get("major_findings")
    )
    assert "Unresolved current semantic requirement." in decision["feedback"]


async def test_duplicate_qa_approval_calls_cohere_without_duplicate_execution_or_report_loss(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    report = {"approved": True, "feedback": "Current value2 source read and actual check reviewed."}
    for reply in (
        _text("Ready."),
        _calls(("read", "read_file", {"file": "value.txt"})),
        _calls(("first", "report_review", report), ("duplicate", "report_review", report)),
    ):
        scripted_provider.enqueue_json(reply)
    row, _ = await _run(service)
    assert row["project_outcome"] == "accepted"
    decisions = [
        event["payload"] for event in service.store.load_events("qa-task") if event["kind"] == "qa_review_completed"
    ]
    assert len(decisions) == 1 and decisions[0]["report_call_id"] == "first"
    observations = [
        event
        for event in service.store.load_events("qa-task")
        if event["kind"] == "tool_observed" and event["payload"]["tool"] == "report_review"
    ]
    assert [event["payload"]["call_id"] for event in observations] == ["first", "duplicate"]
    assert len(scripted_provider.requests) == 3


async def test_conflicting_qa_batch_requires_actual_director_repair_and_a_fresh_review(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("1", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    for reply in (
        _text("Ready."),
        _calls(("read1", "read_file", {"file": "value.txt"})),
        _calls(
            (
                "reject",
                "report_review",
                {
                    "approved": False,
                    "feedback": "Actual value1 violates original goal value2.",
                    "major_findings": ["Wrong current implementation."],
                },
            ),
            ("overrule", "report_review", {"approved": True, "feedback": "Accept current value1 anyway."}),
        ),
        _calls(("repair", "write_file", {"file": "value.txt", "content": "2"})),
        _text("Current artifact repaired."),
        _calls(("read2", "read_file", {"file": "value.txt"})),
        _calls(
            (
                "fresh",
                "report_review",
                {"approved": True, "feedback": "Fresh current source value2 and revalidation now meet original goal."},
            )
        ),
    ):
        scripted_provider.enqueue_json(reply)
    row, _ = await _run(service)
    assert row["project_outcome"] == "accepted" and len(scripted_provider.requests) == 7
    events = service.store.load_events("qa-task")
    assert [event["payload"]["verdict"] for event in events if event["kind"] == "qa_review_completed"] == [
        "rejected",
        "approved",
    ]
    assert len([event for event in events if event["kind"] == "verification_completed"]) == 2
    assert (isolated_workspace / "value.txt").read_text(encoding="utf-8") == "2"


@pytest.mark.parametrize("batch", ["negative_first", "negative_last", "major_first", "duplicate_approval"])
async def test_cold_durable_qa_batch_uses_same_decision_without_replaying_observed_calls(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
    batch: str,
) -> None:
    import asyncio
    import os
    import subprocess
    import sys

    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    approval = ("approve", "report_review", {"approved": True, "feedback": "Current value2 is sufficient."})
    negative = (
        "reject",
        "report_review",
        {
            "approved": False,
            "feedback": "Current required behavior remains absent.",
            "major_findings": ["Current major semantic requirement missing."],
        },
    )
    major = (
        "major",
        "report_review",
        {
            "approved": True,
            "feedback": "Current required behavior remains absent.",
            "major_findings": ["Current major semantic requirement missing."],
        },
    )
    reports = {
        "negative_first": (negative, approval),
        "negative_last": (approval, negative),
        "major_first": (major, approval),
        "duplicate_approval": (approval, ("duplicate", "report_review", approval[2])),
    }[batch]
    for reply in (_text("Ready."), _calls(("read", "read_file", {"file": "value.txt"})), _calls(*reports)):
        scripted_provider.enqueue_json(reply)
    child = r"""
import asyncio, os, sys, time
from pathlib import Path
from polaris.cells.roles.runtime.tests.test_engineering_qa import _scope
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService, AIExecutor
from polaris.cells.roles.runtime.public.contracts.engineering_run import RoleRunCommand, RunLimits
from polaris.kernelone.llm.engine import _executor_base
workspace=Path(sys.argv[1])
_executor_base.get_role_model=lambda role:("synthetic","synthetic-model")
AIExecutor._get_provider_config=lambda self,provider:{"type":"openai_compat","base_url":sys.argv[2],"api_path":"/v1/chat/completions","api_key":"synthetic-only","timeout":3,"max_context_tokens":32768,"max_output_tokens":1024}
async def main():
    service=EngineeringRunService(workspace, require_qa=True, permission_resolver=lambda row:_scope(workspace))
    original=service.store.append_event
    def crash_after_batch(session_id, **kwargs):
        seq=original(session_id, **kwargs)
        if kwargs["kind"]=="turn_finished" and kwargs["role"]=="qa" and any(event["kind"]=="tool_observed" and event["payload"].get("tool")=="report_review" and event["payload"].get("ok") for event in service.store.load_events(session_id)):
            os._exit(87)
        return seq
    service.store.append_event=crash_after_batch
    command=RoleRunCommand("qa-task","director","The artifact must contain value2. Independently review real implementation.",workspace,required_checks=("python3 -c \"from pathlib import Path; assert int(Path('value.txt').read_text(encoding='utf-8'))>0\"",))
    await service.start(command,scope=_scope(workspace),limits=RunLimits(time.time()+45,1000000,64000),request_key="cold-batch")
    await service.wait("qa-task",workspace=workspace)
asyncio.run(main())
"""
    child_result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", child, str(isolated_workspace), scripted_provider.base_url],
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    assert child_result.returncode == 87, child_result.stderr
    assert len(scripted_provider.requests) == 3
    cold = _service(isolated_workspace, scripted_provider, monkeypatch)
    saved = [
        event
        for event in cold.store.load_events("qa-task")
        if event["kind"] == "tool_observed" and event["payload"]["tool"] == "report_review"
    ]
    assert len(saved) == 2 and all(event["payload"]["ok"] for event in saved)
    await cold.resume("qa-task", workspace=isolated_workspace)
    await cold.wait("qa-task", workspace=isolated_workspace)
    row = cold.observe("qa-task", workspace=isolated_workspace)
    expected = "approved" if batch == "duplicate_approval" else "rejected"
    assert row["qa_verdict"] == expected, row
    assert row["project_outcome"] == ("accepted" if expected == "approved" else "rejected")
    assert len(scripted_provider.requests) == (3 if expected == "approved" else 4)
    events = cold.store.load_events("qa-task")
    assert [
        event for event in events if event["kind"] == "tool_observed" and event["payload"]["tool"] == "report_review"
    ] == saved
    decision = next(event["payload"] for event in events if event["kind"] == "qa_review_completed")
    assert decision["report_call_id"] == (
        "approve" if expected == "approved" else "major" if batch == "major_first" else "reject"
    )


async def test_duplicate_native_report_ids_reject_the_entire_undecodable_batch(
    isolated_workspace: Path,
    scripted_provider: ScriptedProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (isolated_workspace / "value.txt").write_text("2", encoding="utf-8")
    service = _service(isolated_workspace, scripted_provider, monkeypatch)
    report = {"approved": True, "feedback": "Current source read."}
    for reply in (
        _text("Ready."),
        _calls(("read", "read_file", {"file": "value.txt"})),
        _calls(("same-id", "report_review", report), ("same-id", "report_review", report)),
    ):
        scripted_provider.enqueue_json(reply)
    row, _ = await _run(service)
    assert row["qa_verdict"] == "unknown" and row["project_outcome"] == "unknown"
    events = service.store.load_events("qa-task")
    assert not [
        event for event in events if event["kind"] == "tool_started" and event["payload"]["tool"] == "report_review"
    ]
    assert any(event["kind"] == "role_error" and "duplicate call id" in event["payload"]["error"] for event in events)
