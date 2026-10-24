"""Verifier shell policy over actual K1 processes and SQLite, with model stubs only.

These synthetic checks do not establish native model judgment or delivery.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from polaris.cells.roles.runtime.internal import engineering_tools
from polaris.cells.roles.runtime.internal.engineering_roles import _VERIFICATION_GUIDANCE
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.internal.engineering_tools import EngineeringTools
from polaris.cells.roles.runtime.internal.engineering_verification import EngineeringVerification
from polaris.cells.roles.runtime.public.contracts.engineering_run import (
    PermissionScope,
    RoleRunCommand,
    RunLimits,
    ToolObservation,
    tool_observation_payload,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.cells.roles.runtime.tests.engineering_support import (
    ScriptedProvider,
    scripted_provider as scripted_provider,
)
from polaris.cells.roles.runtime.tests.test_engineering_autonomous_verification import _response
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.fs import guarded_create_directory
from polaris.kernelone.fs.runtime import KernelFileSystem

_REPOSITORY = Path(__file__).resolve().parents[7]
_AUDIT = _REPOSITORY / ".polaris/audits/verification-pipeline-boundary-20261024/implementation"


@pytest.fixture
def isolated_workspace(monkeypatch: pytest.MonkeyPatch) -> Path:
    guarded_create_directory(str(_REPOSITORY), str(_AUDIT.relative_to(_REPOSITORY)), parents=True, exist_ok=True)
    relative = "fixtures/" + uuid4().hex
    guarded_create_directory(str(_AUDIT), relative, parents=True, exist_ok=True)
    monkeypatch.delenv("KERNELONE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
    return _AUDIT / relative


def _scope(workspace: Path) -> PermissionScope:
    return PermissionScope((workspace,), (), frozenset({"execute_command", "verify_project"}), False)


async def test_new_service_failed_pipeline_is_observed_before_same_director_next_verification(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Losing strict host dispatch must expose exit0/passed for the first check."""
    for response in (
        _response(("failed-check", "verify_project", {"commands": ["(exit 17) | cat"]})),
        _response(("legal-check", "verify_project", {"commands": ["printf legal | cat"]})),
        _response(text="The corrected finite check passed."),
    ):
        scripted_provider.enqueue_json(response)
    service = EngineeringRunService(
        isolated_workspace, client=scripted_provider.client("openai", isolated_workspace, monkeypatch)
    )
    physical: list[list[str]] = []
    actual_run = engineering_tools.run_process_tree_safe

    def record_launch(argv: list[str], **kwargs: Any) -> Any:
        # Observe only; the actual sandbox/process owner remains unchanged.
        saved = service.store.load_run("pipeline-policy", workspace=isolated_workspace)
        started = service.store.load_events("pipeline-policy", kinds=("verification_started",))[-1]
        assert saved["verification_execution_policy"] == "bash_pipefail_v1"
        assert argv[-7:] == started["payload"]["input_binding"]["execution_policy"]["argv"][0]
        physical.append(argv[-7:])
        return actual_run(argv, **kwargs)

    monkeypatch.setattr(engineering_tools, "run_process_tree_safe", record_launch)
    command = RoleRunCommand("pipeline-policy", "director", "Observe finite check results.", isolated_workspace)
    await service.start(
        command, scope=_scope(isolated_workspace), limits=RunLimits(time.time() + 25, 1_000_000, 64_000)
    )
    await service.wait(command.session_id, workspace=isolated_workspace)
    completions = service.store.load_events(command.session_id, kinds=("verification_completed",))
    first, second = (event["payload"] for event in completions)
    assert first["checks"][0]["exit_code"] == 17, first
    assert first["verification"] == "failed"
    assert second["checks"][0]["exit_code"] == 0 and second["verification"] == "passed"
    assert [event["role"] for event in service.store.load_events(command.session_id, kinds=("role_started",))] == [
        "director"
    ]
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["verification"] == "passed" and row["project_outcome"] == "accepted"
    assert physical == [
        ["/bin/bash", "--noprofile", "--norc", "-o", "pipefail", "-c", "(exit 17) | cat"],
        ["/bin/bash", "--noprofile", "--norc", "-o", "pipefail", "-c", "printf legal | cat"],
    ]
    # The next actual request carries the first failed tool result to the same role.
    feedback = next(
        message
        for message in scripted_provider.requests[1]["body"]["messages"]
        if message.get("tool_call_id") == "failed-check"
    )
    failed_summary = json.loads(json.loads(feedback["content"])["content"])
    assert failed_summary["verification"] == "failed" and failed_summary["checks"][0]["exit_code"] == 17
    assert first["input_binding"]["commands"] == ["(exit 17) | cat"]
    cold = EngineeringRunService(isolated_workspace).snapshot(command.session_id, workspace=isolated_workspace)
    assert cold["verification_execution_policy"] == "bash_pipefail_v1"
    assert cold["project_outcome"] == "accepted"


def _seed(
    workspace: Path,
    command: str,
    *,
    policy: str | None = "bash_pipefail_v1",
    scope: PermissionScope | None = None,
    deadline: float | None = None,
) -> tuple[EngineeringSessionStore, EngineeringVerification]:
    scope = scope if scope is not None else _scope(workspace)
    limits = RunLimits(deadline, 1_000_000, 64_000)
    store = EngineeringSessionStore(workspace)
    store.create_run(
        RoleRunCommand(
            "policy-fixture", "director", "Synthetic shell policy check.", workspace, required_checks=(command,)
        ),
        scope=scope,
        limits=limits,
        engine_version="engineering_v2",
        request_key=None,
        verification_execution_policy=policy,
    )
    return store, EngineeringVerification(workspace, store=store, scope=scope, limits=limits, cancel=asyncio.Event())


async def test_ordinary_command_keeps_posix_and_model_cannot_select_policy(isolated_workspace: Path) -> None:
    """A global shell change or model-settable override would break this boundary."""
    EngineeringSessionStore(isolated_workspace)
    tools = EngineeringTools(
        isolated_workspace, _scope(isolated_workspace), RunLimits(None, 10000, 10000), asyncio.Event()
    )
    ordinary = await tools.execute("ordinary", "execute_command", {"command": "(exit 17) | cat"})
    assert ordinary.ok and ordinary.exit_code == 0
    denied = await tools.execute(
        "policy-injection", "execute_command", {"command": "true", "execution_policy": "bash_pipefail_v1"}
    )
    assert not denied.ok and denied.exit_code is None
    schema = next(item["function"] for item in tools.schemas() if item["function"]["name"] == "execute_command")
    assert set(schema["parameters"]["properties"]) == {"command", "timeout"}


@pytest.mark.parametrize("policy", [None, "posix_sh_v1"])
async def test_legacy_policy_remains_posix_after_cold_read(isolated_workspace: Path, policy: str | None) -> None:
    """A silent strict upgrade must not reinterpret or replay old observed effects."""
    store, verifier = _seed(isolated_workspace, "(exit 17) | cat", policy=policy)
    observations = await verifier.run_verification(
        ("(exit 17) | cat",), session_id="policy-fixture", workspace_revision=0
    )
    assert observations[0].exit_code == 0
    cold = EngineeringRunService(isolated_workspace)
    row = cold.snapshot("policy-fixture", workspace=isolated_workspace)
    assert row["verification_execution_policy"] == policy and row["verification"] == "passed"
    assert verifier.current_completed_result("policy-fixture") is not None
    assert len(store.load_events("policy-fixture", kinds=("tool_started",))) == 1


async def test_future_policy_denies_resume_and_old_pass_projection_without_effect(isolated_workspace: Path) -> None:
    """An unsupported policy cannot borrow old success or dispatch ordinary tools."""
    store, verifier = _seed(isolated_workspace, "printf legal | cat")
    await verifier.run_verification(("printf legal | cat",), session_id="policy-fixture", workspace_revision=0)
    old_events = store.load_events("policy-fixture")
    with store._transaction() as connection:
        connection.execute(
            "UPDATE runs SET verification_execution_policy='future_v9' WHERE session_id='policy-fixture'"
        )
    cold = EngineeringRunService(isolated_workspace)
    assert cold.snapshot("policy-fixture", workspace=isolated_workspace)["verification"] == "unknown"
    assert verifier.current_completed_result("policy-fixture") is None
    with pytest.raises(ValueError, match="unsupported verification execution policy"):
        await cold.resume("policy-fixture", workspace=isolated_workspace)
    with pytest.raises(ValueError, match="unsupported verification execution policy"):
        await verifier.run_verification(("printf legal | cat",), session_id="policy-fixture", workspace_revision=0)
    assert store.load_events("policy-fixture") == old_events


@pytest.mark.parametrize("restriction", ["empty-tools", "deadline", "authorization", "cancel"])
async def test_strict_verifier_preserves_pre_effect_restrictions(isolated_workspace: Path, restriction: str) -> None:
    """Strict shell selection must never bypass the existing command admission gates."""
    scope = replace(_scope(isolated_workspace), enabled_tools=frozenset()) if restriction == "empty-tools" else None
    store, verifier = _seed(
        isolated_workspace,
        "printf forbidden > source.txt | cat",
        scope=scope,
        deadline=time.time() - 1 if restriction == "deadline" else None,
    )
    if restriction == "authorization":

        def revoke() -> None:
            raise ValueError("host permission revoked")

        verifier.tools.authorize = revoke
    if restriction == "cancel":
        verifier.cancel.set()
    observations = await verifier.run_verification(
        ("printf forbidden > source.txt | cat",), session_id="policy-fixture", workspace_revision=0
    )
    assert not (isolated_workspace / "source.txt").exists()
    assert all(not observation.ok and observation.exit_code is None for observation in observations)
    assert store.load_run("policy-fixture", workspace=isolated_workspace)["verification"] == "unknown"


async def test_strict_verifier_still_contains_source_writes(isolated_workspace: Path) -> None:
    """A strict launcher cannot acquire write authority from Director scope."""
    scope = replace(_scope(isolated_workspace), write_roots=(isolated_workspace,))
    store, verifier = _seed(isolated_workspace, "printf forbidden > source.txt | cat", scope=scope)
    result = await verifier.run_verification(
        ("printf forbidden > source.txt | cat",), session_id="policy-fixture", workspace_revision=0
    )
    assert result[0].exit_code != 0 and not result[0].ok
    assert "Read-only file system" in result[0].content
    assert not (isolated_workspace / "source.txt").exists()
    assert store.load_run("policy-fixture", workspace=isolated_workspace)["verification"] == "failed"


async def test_pre_column_legacy_history_remains_readable_without_replay(isolated_workspace: Path) -> None:
    """Nullable migration must preserve absent old policy and old attempt evidence."""
    store, verifier = _seed(isolated_workspace, "(exit 17) | cat", policy=None)
    await verifier.run_verification(("(exit 17) | cat",), session_id="policy-fixture", workspace_revision=0)
    # Downgrade only this synthetic fixture to the previous on-disk shape.
    with store._transaction() as connection:
        for event in connection.execute("SELECT seq,payload FROM events WHERE kind LIKE 'verification_%'").fetchall():
            payload = json.loads(event["payload"])
            payload.get("input_binding", {}).pop("execution_policy", None)
            connection.execute("UPDATE events SET payload=? WHERE seq=?", (json.dumps(payload), event["seq"]))
        connection.execute("ALTER TABLE runs DROP COLUMN verification_execution_policy")
    cold = EngineeringRunService(isolated_workspace)
    row = cold.snapshot("policy-fixture", workspace=isolated_workspace)
    assert row["verification_execution_policy"] is None and row["verification"] == "passed"
    history = cold.store.load_events("policy-fixture", kinds=("verification_completed",))[-1]["payload"]
    assert "execution_policy" not in history["input_binding"]
    assert history["checks"][0]["exit_code"] == 0
    assert len(cold.store.load_events("policy-fixture", kinds=("tool_started",))) == 1


async def test_required_checks_and_independent_qa_keep_policy_across_cold_read(
    isolated_workspace: Path, scripted_provider: ScriptedProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Required recipes cannot disappear; QA ordinary checks must keep POSIX."""
    for response in (
        _response(("selected-check", "verify_project", {"commands": ["printf selected | cat"]})),
        _response(text="Current checks ready for independent review."),
        _response(("qa-posix", "execute_command", {"command": "(exit 17) | cat"})),
        _response(("qa-source", "read_file", {"file": "artifact.txt"})),
        _response(("qa-report", "report_review", {"approved": True, "feedback": "Observed finite checks."})),
    ):
        scripted_provider.enqueue_json(response)
    scope = replace(
        _scope(isolated_workspace),
        enabled_tools=frozenset({"execute_command", "verify_project", "report_review", "read_file"}),
    )
    KernelFileSystem(str(isolated_workspace), LocalFileSystemAdapter()).workspace_write_text_atomic(
        "artifact.txt", "Synthetic QA candidate.\n", encoding="utf-8"
    )
    service = EngineeringRunService(
        isolated_workspace,
        require_qa=True,
        client=scripted_provider.client("openai", isolated_workspace, monkeypatch),
        permission_resolver=lambda _row: scope,
    )
    command = RoleRunCommand(
        "required-qa",
        "director",
        "Observe required and selected checks.",
        isolated_workspace,
        required_checks=("printf required | cat",),
    )
    await service.start(command, scope=scope, limits=RunLimits(time.time() + 25, 1_000_000, 64_000))
    await service.wait(command.session_id, workspace=isolated_workspace)
    row = service.snapshot(command.session_id, workspace=isolated_workspace)
    assert row["verification_commands"] == ["printf required | cat", "printf selected | cat"]
    assert row["project_outcome"] == "accepted" and row["qa_verdict"] == "approved", row
    qa_commands = [
        event["payload"]
        for event in service.store.load_events(command.session_id, kinds=("tool_observed",), role="qa")
        if event["payload"]["tool"] == "execute_command"
    ]
    assert len(qa_commands) == 1 and qa_commands[0]["exit_code"] == 0
    cold = EngineeringRunService(isolated_workspace, permission_resolver=lambda _row: scope).snapshot(
        command.session_id, workspace=isolated_workspace
    )
    assert cold["qa_verdict"] == "approved" and cold["project_outcome"] == "accepted"


@pytest.mark.parametrize("change_saved_policy", [False, True])
async def test_cold_parent_recovery_uses_same_policy_and_never_reexecutes(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch, change_saved_policy: bool
) -> None:
    """A composite result must neither borrow a different policy nor replay effects."""
    store, verifier = _seed(isolated_workspace, "(exit 17) | cat")
    turn = store.append_event("policy-fixture", kind="turn_started", role="director", payload={})
    store.record_tool_dispatch(
        "policy-fixture",
        turn_seq=turn,
        call_id="parent",
        tool="verify_project",
        arguments={"commands": ["(exit 17) | cat"]},
        role="director",
    )
    physical: list[list[str]] = []
    actual_run = engineering_tools.run_process_tree_safe

    def record_launch(argv: list[str], **kwargs: Any) -> Any:
        physical.append(argv)
        return actual_run(argv, **kwargs)

    monkeypatch.setattr(engineering_tools, "run_process_tree_safe", record_launch)
    observations = await verifier.run_verification(
        ("(exit 17) | cat",), session_id="policy-fixture", workspace_revision=0, requested_by=(turn, "parent")
    )
    assert observations[0].exit_code == 17 and len(physical) == 1
    if change_saved_policy:
        with store._transaction() as connection:
            connection.execute(
                "UPDATE runs SET verification_execution_policy='posix_sh_v1' WHERE session_id='policy-fixture'"
            )
    cold = EngineeringSessionStore(isolated_workspace)
    cold.reconcile_verification_calls("policy-fixture", max_output_bytes=64_000)
    cold.reconcile_verification_calls("policy-fixture", max_output_bytes=64_000)
    assert len(physical) == 1
    if change_saved_policy:
        assert [call["call_id"] for call in cold.pending_tool_calls("policy-fixture")] == ["parent"]
    else:
        assert cold.pending_tool_calls("policy-fixture") == []
        parent = [
            event["payload"]
            for event in cold.load_events("policy-fixture", kinds=("tool_observed",))
            if event["payload"]["call_id"] == "parent"
        ]
        assert len(parent) == 1 and not parent[0]["ok"]
        summary = json.loads(parent[0]["content"])
        assert summary["verification"] == "failed" and summary["execution_policy"]["name"] == "bash_pipefail_v1"


async def test_strict_running_command_cancellation_keeps_unknown_after_drain(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strict launcher must retain K1 cancellation and physical cleanup ownership."""
    store, verifier = _seed(isolated_workspace, "sleep 30 | cat")
    started, loop = asyncio.Event(), asyncio.get_running_loop()
    actual_run = engineering_tools.run_process_tree_safe

    def record_launch(argv: list[str], **kwargs: Any) -> Any:
        loop.call_soon_threadsafe(started.set)
        return actual_run(argv, **kwargs)

    monkeypatch.setattr(engineering_tools, "run_process_tree_safe", record_launch)
    task = asyncio.create_task(
        verifier.run_verification(("sleep 30 | cat",), session_id="policy-fixture", workspace_revision=0)
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    verifier.cancel.set()
    observations = await asyncio.wait_for(task, timeout=5)
    assert observations[0].interrupted and not observations[0].ok
    assert store.pending_tool_calls("policy-fixture") == []
    assert store.load_run("policy-fixture", workspace=isolated_workspace)["verification"] == "unknown"


async def test_unavailable_strict_interpreter_does_not_fall_back_to_posix(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed strict launch must not retry with weaker shell semantics."""
    store, verifier = _seed(isolated_workspace, "(exit 17) | cat")
    attempted: list[list[str]] = []

    def unavailable(argv: list[str], **_kwargs: Any) -> Any:
        # Controlled OS failure only; no synthetic successful process result.
        attempted.append(argv)
        raise FileNotFoundError("synthetic strict interpreter unavailable")

    monkeypatch.setattr(engineering_tools, "run_process_tree_safe", unavailable)
    observations = await verifier.run_verification(
        ("(exit 17) | cat",), session_id="policy-fixture", workspace_revision=0
    )
    assert len(attempted) == 1 and attempted[0][-7] == "/bin/bash"
    assert not observations[0].ok and observations[0].exit_code is None
    assert "strict interpreter unavailable" in observations[0].content
    assert store.load_run("policy-fixture", workspace=isolated_workspace)["verification"] == "unknown"


async def test_legacy_observed_parent_missing_message_recovers_original_encoding_once_without_replay(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adding null metadata must not strand an already observed old composite result."""
    command = "printf legacy | cat"
    store, verifier = _seed(isolated_workspace, command, policy=None)
    turn = store.append_event("policy-fixture", kind="turn_started", role="director", payload={})
    store.record_tool_dispatch(
        "policy-fixture",
        turn_seq=turn,
        call_id="legacy-parent",
        tool="verify_project",
        arguments={"commands": [command]},
        role="director",
    )
    physical: list[list[str]] = []
    actual_run = engineering_tools.run_process_tree_safe

    def record_launch(argv: list[str], **kwargs: Any) -> Any:
        physical.append(argv)
        return actual_run(argv, **kwargs)

    monkeypatch.setattr(engineering_tools, "run_process_tree_safe", record_launch)
    checks = await verifier.run_verification(
        (command,), session_id="policy-fixture", workspace_revision=0, requested_by=(turn, "legacy-parent")
    )
    assert len(checks) == 1 and checks[0].exit_code == 0
    # Downgrade only this synthetic receipt to the old absent-policy shape.
    with store._transaction() as connection:
        for event in connection.execute("SELECT seq,payload FROM events WHERE kind LIKE 'verification_%'").fetchall():
            payload = json.loads(event["payload"])
            payload.get("input_binding", {}).pop("execution_policy", None)
            connection.execute("UPDATE events SET payload=? WHERE seq=?", (json.dumps(payload), event["seq"]))
    completed = store.load_events("policy-fixture", kinds=("verification_completed",))[-1]["payload"]
    binding = completed["input_binding"]
    # Hand-encode the historical public shape independently of the new projector.
    legacy_summary = {
        "verification": "passed",
        "execution_scope": binding["scope"],
        "verification_output_policy": binding["verification_output_policy"],
        "detail": f"{_VERIFICATION_GUIDANCE}\n{completed['detail']}",
        "verification_commands": [command],
        "checks": completed["checks"],
        "workspace_revision": 0,
        "attempt_seq": binding["attempt_seq"],
    }
    original = ToolObservation(
        "legacy-parent", "verify_project", True, json.dumps(legacy_summary, ensure_ascii=False), None, False
    )
    store.record_tool_observation("policy-fixture", turn_seq=turn, observation=original, role="director")
    assert store.load_message_history("policy-fixture") == []
    observed_count = len(store.load_events("policy-fixture", kinds=("tool_observed",)))
    cold = EngineeringSessionStore(isolated_workspace)
    cold.reconcile_verification_calls("policy-fixture", max_output_bytes=64_000)
    cold.reconcile_verification_calls("policy-fixture", max_output_bytes=64_000)
    messages = [
        message
        for message in cold.load_message_history("policy-fixture")
        if message.get("tool_call_id") == "legacy-parent"
    ]
    assert len(messages) == 1
    assert json.loads(messages[0]["content"]) == tool_observation_payload(original)
    assert len(cold.load_events("policy-fixture", kinds=("tool_observed",))) == observed_count
    assert cold.pending_tool_calls("policy-fixture") == []
    assert len(physical) == 1


def test_fixture_initializes_absent_audit_root_from_existing_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean checkout must create its disposable fixture root before KFS binding."""
    missing_root = _AUDIT / ("clean-fixture-" + uuid4().hex)
    assert not missing_root.exists()
    monkeypatch.setattr(sys.modules[__name__], "_AUDIT", missing_root)
    workspace = isolated_workspace.__wrapped__(monkeypatch)
    assert workspace.is_dir() and workspace.parent == missing_root / "fixtures"
