"""TDD coverage for the adapter-owned DEO-2B policy snapshot port."""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import subprocess
from dataclasses import fields, replace
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from polaris.cells.director.runtime.public import (
    DirectedEffectImmutableItemsV1,
    DirectedEffectImmutableMapV1,
    DirectorEffectAuthorizationBindingV1,
    DirectorEffectAuthorizationEvidenceV1,
    DirectorEffectClassificationEvidenceV1,
    DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    DirectorEffectPolicyBoundSnapshotV1,
    DirectorEffectPolicyMemberBindingRequestV1,
    DirectorEffectPolicyOperationSubjectV1,
    DirectorEffectPolicyRevalidationRequestV1,
    DirectorEffectPolicyRevalidationResultV1,
    DirectorEffectPolicySnapshotPortV1,
    DirectorEffectPolicySnapshotRequestV1,
    DirectorEffectPolicySnapshotResultV1,
    DirectorEffectTargetStateEvidenceV1,
    hash_directed_effect_arguments,
    hash_director_effect_authorization_evidence,
    project_director_effect_public_policy_evidence,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    hash_directed_effect_policy_member_binding,
    hash_directed_effect_target_state_components,
)
from polaris.cells.roles.adapters.public import create_director_effect_policy_snapshot_port
from polaris.cells.runtime.task_runtime.public import (
    DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    DirectedEffectClaimGrantV1,
    DirectedEffectInventoryMemberV1,
    DirectedEffectOperationIdentityV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    ParentCorrelationV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.fs.runtime import KernelFileSystem
from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor
from polaris.kernelone.process.command_executor import CommandExecutionService
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

T = TypeVar("T")


def _install_zero_effect_spies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fail immediately if an evidence-only call reaches any physical-effect entrypoint."""
    # This private class is referenced only to guard the authorized adaptation boundary.
    from polaris.cells.roles.adapters.internal.director.execution_tools import DirectorToolExecutor

    calls: list[str] = []

    def forbid(name: str):
        def _forbidden(*args: object, **kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(f"unexpected physical effect: {name}")

        return _forbidden

    def patch_if_present(owner: object, attribute: str, name: str) -> None:
        if hasattr(owner, attribute):
            monkeypatch.setattr(owner, attribute, forbid(name))

    for attribute in (
        "execute_tool",
        "write_file",
        "edit_file",
        "delete_file",
        "_tool_write_file",
        "_tool_edit_file",
        "_tool_delete_file",
    ):
        patch_if_present(DirectorToolExecutor, attribute, f"DirectorToolExecutor.{attribute}")
    for attribute in ("__init__", "execute_tool", "execute_tool_call", "execute_tool_calls"):
        patch_if_present(AgentAccelToolExecutor, attribute, f"AgentAccelToolExecutor.{attribute}")
    monkeypatch.setattr(
        "polaris.kernelone.single_agent.skill_system.install_default_skills",
        forbid("install_default_skills"),
    )
    for attribute in ("write_text", "write_text_atomic", "write_bytes", "write_json", "write_json_atomic"):
        patch_if_present(KernelFileSystem, attribute, f"KernelFileSystem.{attribute}")
    for attribute in ("__init__", "run"):
        patch_if_present(CommandExecutionService, attribute, f"CommandExecutionService.{attribute}")
    for attribute in (
        "system",
        "popen",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execlp",
        "execle",
        "execlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "posix_spawn",
        "posix_spawnp",
        "fork",
        "forkpty",
    ):
        patch_if_present(os, attribute, f"os.{attribute}")
    for attribute in ("run", "Popen", "call", "check_call", "check_output"):
        patch_if_present(subprocess, attribute, f"subprocess.{attribute}")
    for attribute in ("create_subprocess_exec", "create_subprocess_shell"):
        patch_if_present(asyncio, attribute, f"asyncio.{attribute}")
    for attribute in ("__init__", "start", "run"):
        patch_if_present(
            multiprocessing.process.BaseProcess,
            attribute,
            f"multiprocessing.BaseProcess.{attribute}",
        )
    return calls


def _install_pre_observation_spies(
    monkeypatch: pytest.MonkeyPatch,
    port: DirectorEffectPolicySnapshotPortV1,
) -> list[str]:
    """Forbid target, AGENTS, and Director policy observation after static drift."""
    calls: list[str] = []

    def forbid(name: str):
        def _forbidden(*args: object, **kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(f"static denial reached observation: {name}")

        return _forbidden

    for attribute in ("open", "read_bytes", "stat"):
        monkeypatch.setattr(Path, attribute, forbid(f"Path.{attribute}"))
    for attribute in (
        "_read_target_state",
        "_agents_policy_hash",
        "_validate_write_policy",
        "_evaluate",
    ):
        monkeypatch.setattr(type(port), attribute, forbid(attribute))
    return calls


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _forged_replace(instance: T, **changes: object) -> T:
    """Bypass constructor guards to exercise stateless forged-wire rejection."""
    forged = object.__new__(type(instance))
    for field in fields(cast(Any, instance)):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(instance, field.name)))
    return forged


def _rehash_authorization(
    evidence: DirectorEffectAuthorizationEvidenceV1,
    **changes: object,
) -> DirectorEffectAuthorizationEvidenceV1:
    forged = _forged_replace(evidence, **changes)
    authorization_hash = hash_director_effect_authorization_evidence(
        workspace=forged.workspace,
        execution_attempt_id=forged.execution_attempt_id,
        turn_id=forged.turn_id,
        batch_id=forged.batch_id,
        tool_call_id=forged.tool_call_id,
        normalized_tool_name=forged.normalized_tool_name,
        arguments_hash=forged.arguments_hash,
        tool_spec_hash=forged.tool_spec_hash,
        role_policy_id=forged.role_policy_id,
        role_policy_hash=forged.role_policy_hash,
        canonical_allow_list_hash=forged.canonical_allow_list_hash,
        capability_scope=forged.capability_scope,
        capability_scope_hash=forged.capability_scope_hash,
        job_token_id=forged.job_token_id,
        job_token_evidence_hash=forged.job_token_evidence_hash,
        execution_envelope_hash=forged.execution_envelope_hash,
        allowed_command_hash=forged.allowed_command_hash,
        mutation_guard_mode=forged.mutation_guard_mode,
        bound_policy_snapshot_hash=forged.bound_policy_snapshot_hash,
        target_state_hash=forged.target_state_hash,
        normalized_operation_hash=forged.normalized_operation_hash,
        policy_version=forged.policy_version,
        policy_hash=forged.policy_hash,
    )
    return _forged_replace(forged, authorization_hash=authorization_hash)


def _agents_hash(workspace: Path, target_path: str) -> str:
    records: list[dict[str, object]] = []
    root = workspace.resolve()
    parts = Path(target_path).parts[:-1]
    for index in range(len(parts) + 1):
        candidate = workspace.joinpath(*parts[:index], "AGENTS.md")
        try:
            before_lstat = candidate.lstat()
        except FileNotFoundError:
            continue
        before_stat = candidate.stat()
        resolved = candidate.resolve(strict=True)
        try:
            resolved_relative = resolved.relative_to(root).as_posix()
        except ValueError:
            raise ValueError("test AGENTS policy candidate escaped workspace") from None
        content = candidate.read_bytes()
        after_lstat = candidate.lstat()
        after_stat = candidate.stat()
        assert (before_lstat.st_dev, before_lstat.st_ino, before_lstat.st_size, before_lstat.st_mtime_ns) == (
            after_lstat.st_dev,
            after_lstat.st_ino,
            after_lstat.st_size,
            after_lstat.st_mtime_ns,
        )
        assert (before_stat.st_dev, before_stat.st_ino, before_stat.st_size, before_stat.st_mtime_ns) == (
            after_stat.st_dev,
            after_stat.st_ino,
            after_stat.st_size,
            after_stat.st_mtime_ns,
        )
        records.append(
            {
                "content_hash": hashlib.sha256(content).hexdigest(),
                "lstat": (before_lstat.st_dev, before_lstat.st_ino, before_lstat.st_size, before_lstat.st_mtime_ns),
                "path": candidate.relative_to(root).as_posix(),
                "resolved_path": resolved_relative,
                "stat": (before_stat.st_dev, before_stat.st_ino, before_stat.st_size, before_stat.st_mtime_ns),
                "symlink_target": candidate.readlink().as_posix() if candidate.is_symlink() else None,
            }
        )
    return _digest({"candidates": records, "domain": "director_effect_agents_policy_evidence_v1"})


def _target_state(workspace: Path, target_path: str) -> DirectorEffectTargetStateEvidenceV1:
    target = workspace / target_path
    exists = target.is_file()
    content = target.read_text(encoding="utf-8") if exists else ""
    stat = target.stat() if exists else None
    before_hash = _content_hash(content) if exists else "0" * 64
    minimal = (
        ("byte_length", len(content.encode("utf-8"))),
        ("prefix_hash", _content_hash(content[:256])),
        ("stat_dev", stat.st_dev if stat is not None else 0),
        ("stat_ino", stat.st_ino if stat is not None else 0),
        ("stat_mtime_ns", stat.st_mtime_ns if stat is not None else 0),
        ("stat_size", stat.st_size if stat is not None else 0),
    )
    agents_hash = _agents_hash(workspace, target_path)
    state_hash = hash_directed_effect_target_state_components(
        target_path=target_path,
        exists=exists,
        before_content_hash=before_hash,
        minimal_content_evidence=minimal,
        agents_policy_hash=agents_hash,
        is_no_file_state=False,
    )
    return DirectorEffectTargetStateEvidenceV1(
        target_path=target_path,
        exists=exists,
        before_content_hash=before_hash,
        minimal_content_evidence=minimal,
        agents_policy_hash=agents_hash,
        target_state_hash=state_hash,
        is_no_file_state=False,
    )


def _no_file_state(workspace: Path) -> DirectorEffectTargetStateEvidenceV1:
    agents_hash = _agents_hash(workspace, "")
    state_hash = hash_directed_effect_target_state_components(
        target_path="",
        exists=False,
        before_content_hash="0" * 64,
        minimal_content_evidence=(),
        agents_policy_hash=agents_hash,
        is_no_file_state=True,
    )
    return DirectorEffectTargetStateEvidenceV1(
        target_path="",
        exists=False,
        before_content_hash="0" * 64,
        minimal_content_evidence=(),
        agents_policy_hash=agents_hash,
        target_state_hash=state_hash,
        is_no_file_state=True,
    )


async def test_target_mutation_during_single_capture_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target version change during capture is denied before policy success."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    request = _write_request(workspace)
    original_read_bytes = Path.read_bytes
    original_write_text = Path.write_text
    target_reads = 0
    mutation_triggered = False

    def mutate_while_collecting_agents(path: Path, *args: object, **kwargs: object) -> bytes:
        nonlocal mutation_triggered, target_reads
        content = original_read_bytes(path, *args, **kwargs)
        if path.resolve() == target.resolve():
            target_reads += 1
        elif path.name == "AGENTS.md" and not mutation_triggered:
            original_write_text(target, "concurrent\n", encoding="utf-8")
            mutation_triggered = True
        return content

    monkeypatch.setattr(Path, "read_bytes", mutate_while_collecting_agents)
    result = await create_director_effect_policy_snapshot_port(str(workspace)).snapshot(request)

    assert mutation_triggered
    assert target_reads == 1
    assert result.allowed is False
    assert result.error_code == "deo_target_state_drift"


@pytest.mark.parametrize("race", ("replace", "symlink-target", "read-error"))
async def test_agents_observation_races_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    race: str,
) -> None:
    """AGENTS candidates are never omitted when their identity or readability changes."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    agents = workspace / "AGENTS.md"
    agents.write_text("# baseline\n", encoding="utf-8")
    request = _write_request(workspace)
    original_read_bytes = Path.read_bytes
    mutated = False

    if race == "symlink-target":
        first = workspace / "agents-first.md"
        second = workspace / "agents-second.md"
        first.write_text("# first\n", encoding="utf-8")
        second.write_text("# second\n", encoding="utf-8")
        agents.unlink()
        agents.symlink_to(first.name)

    def race_read(path: Path, *args: object, **kwargs: object) -> bytes:
        nonlocal mutated
        content = original_read_bytes(path, *args, **kwargs)
        if path.name != "AGENTS.md":
            return content
        if race == "replace":
            path.unlink()
            path.write_text("# replaced policy with distinct bytes\n", encoding="utf-8")
            mutated = True
        elif race == "symlink-target":
            path.unlink()
            path.symlink_to("agents-second.md")
            mutated = True
        else:
            raise OSError("simulated AGENTS read failure")
        return content

    monkeypatch.setattr(Path, "read_bytes", race_read)
    result = await create_director_effect_policy_snapshot_port(str(workspace)).snapshot(request)

    assert mutated or race == "read-error"
    assert result.allowed is False
    assert result.error_code == "deo_target_state_drift"
    assert target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.parametrize("phase", ("snapshot", "binding", "revalidation"))
async def test_snapshot_and_revalidation_denials_have_full_zero_effect_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    """Every denial is evidence-only across tools, KFS, and process entrypoints."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port = create_director_effect_policy_snapshot_port(str(workspace))
    request = _write_request(workspace)
    result: Any

    if phase == "snapshot":
        calls = _install_zero_effect_spies(monkeypatch)
        result = await port.snapshot(_write_request(workspace, target_path="../outside.py"))
    else:
        snapshot = await port.snapshot(request)
        assert snapshot.allowed
        member = _member(snapshot.normalized_operation_hash)
        authorization = _authorization(request, snapshot, workspace)
        if phase == "binding":
            calls = _install_zero_effect_spies(monkeypatch)
            result = port.bind_member(
                DirectorEffectPolicyMemberBindingRequestV1(
                    snapshot=snapshot,
                    authorization_evidence=authorization,
                    authorization_binding=_authorization_binding(snapshot, authorization),
                    member=_forged_replace(member, ordinal=2),
                )
            )
        else:
            binding = port.bind_member(
                DirectorEffectPolicyMemberBindingRequestV1(
                    snapshot=snapshot,
                    authorization_evidence=authorization,
                    authorization_binding=_authorization_binding(snapshot, authorization),
                    member=member,
                )
            )
            assert binding.bound_snapshot is not None
            revalidation = DirectorEffectPolicyRevalidationRequestV1(
                bound_snapshot=binding.bound_snapshot,
                workspace=str(workspace.resolve()),
                actual_normalized_tool_name=request.normalized_tool_name,
                actual_normalized_arguments=request.normalized_arguments,
                actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
                authorization_evidence=authorization,
                member=member,
                operation_id=member.operation_id,
                claim_grant=_claim_grant(member, workspace),
                current_job_token_restriction_evidence=_job_evidence(),
            )
            object.__setattr__(revalidation, "actual_normalized_tool_name", "edit_file")
            calls = _install_zero_effect_spies(monkeypatch)
            result = await port.revalidate(revalidation)

    if phase == "binding":
        assert result.status == "denied"
        assert result.member is None
        assert result.member_binding_hash is None
        assert result.bound_snapshot is None
    else:
        assert result.allowed is False
    assert calls == []
    assert target.read_text(encoding="utf-8") == "before\n"


def _scope_hash(field_name: str, values: tuple[str, ...]) -> str:
    return hash_directed_effect_arguments(((field_name, values),))


def _job_evidence(
    *,
    allowed_commands: tuple[str, ...] = ("pytest -q",),
    allowed_paths: tuple[str, ...] = ("src/a.py",),
    restriction_nonce: str = "restriction-1",
) -> DirectedEffectImmutableItemsV1:
    return (
        ("allowed_commands", allowed_commands),
        ("allowed_commands_hash", _scope_hash("allowed_commands", allowed_commands)),
        ("allowed_paths", allowed_paths),
        ("allowed_paths_hash", _scope_hash("allowed_paths", allowed_paths)),
        ("job_token_hash", "a" * 64),
        ("job_token_id", "job-1"),
        ("restriction_nonce", restriction_nonce),
    )


def _job_evidence_hash(evidence: DirectedEffectImmutableItemsV1) -> str:
    return hash_directed_effect_arguments(evidence)


def _operation_hash(
    *,
    workspace: Path,
    normalized_tool_name: str,
    normalized_arguments: DirectedEffectImmutableItemsV1,
    effect_type: str,
    execution_mode: str,
    inventory_ordinal: int = 1,
    tool_call_id: str = "call-1",
) -> str:
    return hash_directed_effect_arguments(
        (
            ("batch_id", "batch-1"),
            ("effect_type", effect_type),
            ("execution_mode", execution_mode),
            ("inventory_ordinal", inventory_ordinal),
            ("normalized_arguments", DirectedEffectImmutableMapV1(items=normalized_arguments)),
            ("normalized_tool_name", normalized_tool_name),
            ("tool_call_id", tool_call_id),
            ("turn_id", "turn-1"),
            ("workspace", str(workspace.resolve())),
        )
    )


def _write_request(
    workspace: Path,
    *,
    target_path: str = "src/a.py",
    inventory_ordinal: int = 1,
    tool_call_id: str = "call-1",
) -> DirectorEffectPolicySnapshotRequestV1:
    arguments = (("allowed_scope", ("src/a.py",)), ("content", "after\n"), ("path", target_path))
    operation_hash = _operation_hash(
        workspace=workspace,
        normalized_tool_name="write_file",
        normalized_arguments=arguments,
        effect_type="write",
        execution_mode="write_serial",
        inventory_ordinal=inventory_ordinal,
        tool_call_id=tool_call_id,
    )
    subject = DirectorEffectPolicyOperationSubjectV1(
        workspace=str(workspace.resolve()),
        turn_id="turn-1",
        batch_id="batch-1",
        tool_call_id=tool_call_id,
        inventory_ordinal=inventory_ordinal,
        normalized_tool_name="write_file",
        normalized_arguments=arguments,
        effect_type="write",
        execution_mode="write_serial",
        prospective_operation_hash=operation_hash,
    )
    return DirectorEffectPolicySnapshotRequestV1(
        subject=subject,
        workspace=str(workspace.resolve()),
        normalized_tool_name="write_file",
        normalized_arguments=arguments,
        job_token_restriction_evidence=_job_evidence(),
        expected_policy_version="director-policy-v1",
        canonical_command="",
        path_scope_evidence=(("allowed_paths", ("src/a.py",)),),
        command_scope_evidence=(("allowed_commands", ("pytest -q",)),),
        target_state_evidence=_target_state(workspace, target_path),
    )


def _command_request(
    workspace: Path,
    *,
    command: str = "pytest -q",
    canonical_command: str | None = None,
) -> DirectorEffectPolicySnapshotRequestV1:
    arguments = (("command", command),)
    operation_hash = _operation_hash(
        workspace=workspace,
        normalized_tool_name="execute_command",
        normalized_arguments=arguments,
        effect_type="async",
        execution_mode="async_receipt",
    )
    subject = DirectorEffectPolicyOperationSubjectV1(
        workspace=str(workspace.resolve()),
        turn_id="turn-1",
        batch_id="batch-1",
        tool_call_id="call-1",
        inventory_ordinal=1,
        normalized_tool_name="execute_command",
        normalized_arguments=arguments,
        effect_type="async",
        execution_mode="async_receipt",
        prospective_operation_hash=operation_hash,
    )
    no_file = _no_file_state(workspace)
    return DirectorEffectPolicySnapshotRequestV1(
        subject=subject,
        workspace=str(workspace.resolve()),
        normalized_tool_name="execute_command",
        normalized_arguments=arguments,
        job_token_restriction_evidence=_job_evidence(),
        expected_policy_version="director-policy-v1",
        canonical_command=command if canonical_command is None else canonical_command,
        path_scope_evidence=(),
        command_scope_evidence=(("allowed_commands", ("pytest -q",)),),
        target_state_evidence=no_file,
    )


async def test_command_arguments_and_canonical_command_must_bind_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A safe canonical string cannot authorize different command arguments."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port = create_director_effect_policy_snapshot_port(str(workspace))
    effect_calls = _install_zero_effect_spies(monkeypatch)

    result = await port.snapshot(
        _command_request(
            workspace,
            command="rm -rf /",
            canonical_command="pytest -q",
        )
    )

    assert result.allowed is False
    assert result.error_code in {"deo_operation_hash_mismatch", "deo_command_scope_denied"}
    assert effect_calls == []


async def test_snapshot_recomputes_complete_prospective_operation_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller-provided operation hashes never replace the canonical subject digest."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    request = _write_request(workspace)
    forged_subject = replace(request.subject, prospective_operation_hash="f" * 64)
    effect_calls = _install_zero_effect_spies(monkeypatch)

    result = await create_director_effect_policy_snapshot_port(str(workspace)).snapshot(
        replace(request, subject=forged_subject)
    )

    assert result.allowed is False
    assert result.error_code == "deo_operation_hash_mismatch"
    assert effect_calls == []


async def test_agents_evidence_is_workspace_contained_and_fresh_for_commands(tmp_path: Path) -> None:
    """Caller no-file evidence and external AGENTS symlinks cannot control policy state."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "AGENTS.md").write_text("forbidden external policy\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    write_request = _write_request(workspace)
    command_request = _command_request(workspace)
    (workspace / "AGENTS.md").symlink_to(outside / "AGENTS.md")
    port = create_director_effect_policy_snapshot_port(str(workspace))

    write_snapshot = await port.snapshot(write_request)
    assert write_snapshot.allowed is False
    assert write_snapshot.error_code == "deo_target_state_drift"

    external_command = await port.snapshot(command_request)
    assert external_command.allowed is False
    assert external_command.error_code == "deo_target_state_drift"

    (workspace / "AGENTS.md").unlink()
    (workspace / "AGENTS.md").write_text("# local policy\n", encoding="utf-8")
    command_request = _command_request(workspace)
    command_snapshot = await port.snapshot(command_request)
    assert command_snapshot.allowed
    member = _member(command_snapshot.normalized_operation_hash, tool_name="execute_command")
    authorization = _authorization(command_request, command_snapshot, workspace)
    binding = port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=command_snapshot,
            authorization_evidence=authorization,
            authorization_binding=_authorization_binding(command_snapshot, authorization),
            member=member,
        )
    )
    assert binding.bound_snapshot is not None
    (workspace / "AGENTS.md").write_text("# local policy changed\n", encoding="utf-8")

    revalidated = await port.revalidate(
        DirectorEffectPolicyRevalidationRequestV1(
            bound_snapshot=binding.bound_snapshot,
            workspace=str(workspace.resolve()),
            actual_normalized_tool_name=command_request.normalized_tool_name,
            actual_normalized_arguments=command_request.normalized_arguments,
            actual_arguments_hash=hash_directed_effect_arguments(command_request.normalized_arguments),
            authorization_evidence=authorization,
            member=member,
            operation_id=member.operation_id,
            claim_grant=_claim_grant(member, workspace),
            current_job_token_restriction_evidence=_job_evidence(),
        )
    )

    assert revalidated.allowed is False
    assert revalidated.error_code == "deo_policy_version_drift"


def _member(
    snapshot_hash: str,
    *,
    tool_name: str = "write_file",
    ordinal: int = 1,
    tool_call_id: str = "call-1",
    effect_id: str = "effect-1",
    operation_id: str = "operation-1",
) -> DirectedEffectInventoryMemberV1:
    return DirectedEffectInventoryMemberV1(
        ordinal=ordinal,
        tool_call_id=tool_call_id,
        effect_id=effect_id,
        operation_id=operation_id,
        normalized_tool_name=tool_name,
        effect_type="write" if tool_name == "write_file" else "async",
        execution_mode="write_serial" if tool_name == "write_file" else "async_receipt",
        intended_effect_fingerprint=snapshot_hash,
        policy_verdict_hash=snapshot_hash,
        expected_receipt_binding_hash=snapshot_hash,
    )


def _claim_grant(member: DirectedEffectInventoryMemberV1, workspace: Path) -> DirectedEffectClaimGrantV1:
    attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace=str(workspace.resolve()),
        task_id=1,
        external_task_id="task-1",
        session_id="session-1",
        attempt=1,
        role_id="director",
        worker_id="worker-1",
        run_id="run-1",
        lease_expires_at="2026-07-17T12:00:00+00:00",
    )
    registry = DirectedEffectParentRegistryIdentityV1.from_execution_attempt(attempt)
    binding = DirectedEffectParentBindingV1(
        schema_version=DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
        registry_identity=registry,
        registry_stream_token="registry-stream-1",
        registry_version=1,
        parent_sequence=1,
        binding_id="binding-1",
        operation_stream_token="operation-stream-1",
        binding_hash="a" * 64,
        admission_idempotency_key="parent-admission-1",
        correlation=ParentCorrelationV1(turn_id="turn-1", batch_id="batch-1"),
        actor="roles.kernel",
        source_event_id="parent-event-1",
        source_event_seq=1,
    )
    operation = DirectedEffectOperationIdentityV1(
        workspace=attempt.workspace,
        task_id=attempt.task_id,
        execution_attempt_id=registry.execution_attempt_id,
        parent_binding_id=binding.binding_id,
        parent_sequence=binding.parent_sequence,
        tool_call_id=member.tool_call_id,
        effect_id=member.effect_id,
        operation_id=member.operation_id,
        operation_stream_token=binding.operation_stream_token,
    )
    unsigned = {
        "schema_version": DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
        "execution_attempt": attempt.to_record(),
        "parent_binding": binding.to_record(),
        "operation": operation.to_record(),
        "member": member.to_record(),
        "inventory_hash": "a" * 64,
        "operation_version": 2,
        "claim_event_id": "claim-event-1",
        "claim_event_seq": 3,
        "operation_source_head_seq": 3,
        "parent_registry_source_head_seq": 3,
    }
    return DirectedEffectClaimGrantV1(
        schema_version=DIRECTED_EFFECT_CLAIM_GRANT_SCHEMA_V1,
        execution_attempt=attempt,
        parent_binding=binding,
        operation=operation,
        member=member,
        inventory_hash="a" * 64,
        operation_version=2,
        claim_event_id="claim-event-1",
        claim_event_seq=3,
        operation_source_head_seq=3,
        parent_registry_source_head_seq=3,
        grant_hash=_digest(unsigned),
    )


def _authorization(
    request: DirectorEffectPolicySnapshotRequestV1,
    snapshot: DirectorEffectPolicySnapshotResultV1,
    workspace: Path,
) -> DirectorEffectAuthorizationEvidenceV1:
    job_evidence = _job_evidence()
    allowed_paths = ("src/a.py",)
    allowed_commands = ("pytest -q",)
    values: dict[str, Any] = {
        "workspace": str(workspace.resolve()),
        "execution_attempt_id": "session-1:1",
        "turn_id": "turn-1",
        "batch_id": "batch-1",
        "tool_call_id": request.subject.tool_call_id,
        "normalized_tool_name": request.normalized_tool_name,
        "arguments_hash": hash_directed_effect_arguments(request.normalized_arguments),
        "tool_spec_hash": "a" * 64,
        "role_policy_id": "director",
        "role_policy_hash": "a" * 64,
        "canonical_allow_list_hash": "a" * 64,
        "capability_scope": allowed_paths,
        "capability_scope_hash": _scope_hash("allowed_paths", allowed_paths),
        "job_token_id": "job-1",
        "job_token_evidence_hash": _job_evidence_hash(job_evidence),
        "execution_envelope_hash": "a" * 64,
        "allowed_command_hash": _scope_hash("allowed_commands", allowed_commands),
        "mutation_guard_mode": "strict",
        "bound_policy_snapshot_hash": snapshot.evidence_hash,
        "target_state_hash": snapshot.target_state_hash,
        "normalized_operation_hash": snapshot.normalized_operation_hash,
        "policy_version": snapshot.policy_version,
        "policy_hash": snapshot.policy_hash,
    }
    return DirectorEffectAuthorizationEvidenceV1(
        **values,
        authorization_hash=hash_director_effect_authorization_evidence(**values),
    )


def _authorization_binding(
    snapshot: DirectorEffectPolicySnapshotResultV1,
    authorization: DirectorEffectAuthorizationEvidenceV1,
) -> DirectorEffectAuthorizationBindingV1:
    subject = snapshot.subject
    classification = DirectorEffectClassificationEvidenceV1(
        raw_tool_name=subject.normalized_tool_name,
        canonical_tool_name=subject.normalized_tool_name,
        effect_type=subject.effect_type,
        execution_mode=subject.execution_mode,
        normalized_arguments=subject.normalized_arguments,
        arguments_hash=authorization.arguments_hash,
        tool_spec_hash=authorization.tool_spec_hash,
        tool_spec_snapshot_hash="a" * 64,
        alias_binding_hash="a" * 64,
    )
    return DirectorEffectAuthorizationBindingV1(
        authorization_evidence=authorization,
        classification_evidence=classification,
        tool_spec_hash=authorization.tool_spec_hash,
        tool_spec_snapshot_hash=classification.tool_spec_snapshot_hash,
        alias_binding_hash=classification.alias_binding_hash,
    )


@pytest.mark.parametrize(
    ("forgery", "expected_code"),
    (
        ("authorization-id", "deo_authorization_evidence_drift"),
        ("authorization-hash", "deo_authorization_evidence_drift"),
        ("payload-only", "deo_job_token_invalid"),
    ),
)
async def test_revalidate_binds_complete_job_token_restriction_evidence(
    tmp_path: Path,
    forgery: str,
    expected_code: str,
) -> None:
    """Token identity and every immutable restriction field are hash-bound."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port, request, snapshot, bound = await _bound_write_port(workspace)
    member = bound.member
    evidence = _authorization(request, snapshot, workspace)
    current_job = _job_evidence()
    if forgery == "authorization-id":
        evidence = _forged_replace(evidence, job_token_id="job-forged")
    elif forgery == "authorization-hash":
        evidence = _forged_replace(evidence, job_token_evidence_hash="f" * 64)
    else:
        current_job = _job_evidence(restriction_nonce="restriction-forged")

    result = await port.revalidate(
        DirectorEffectPolicyRevalidationRequestV1(
            bound_snapshot=bound,
            workspace=str(workspace.resolve()),
            actual_normalized_tool_name=request.normalized_tool_name,
            actual_normalized_arguments=request.normalized_arguments,
            actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
            authorization_evidence=evidence,
            member=member,
            operation_id=member.operation_id,
            claim_grant=_claim_grant(member, workspace),
            current_job_token_restriction_evidence=current_job,
        )
    )

    assert result.allowed is False
    assert result.error_code == expected_code


async def test_revalidate_rejects_forged_authorization_before_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A constructor-bypass authorization forgery cannot trigger state or policy work."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port, request, snapshot, bound = await _bound_write_port(workspace)
    member = bound.member
    revalidation_request = DirectorEffectPolicyRevalidationRequestV1(
        bound_snapshot=bound,
        workspace=str(workspace.resolve()),
        actual_normalized_tool_name=request.normalized_tool_name,
        actual_normalized_arguments=request.normalized_arguments,
        actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
        authorization_evidence=_authorization(request, snapshot, workspace),
        member=member,
        operation_id=member.operation_id,
        claim_grant=_claim_grant(member, workspace),
        current_job_token_restriction_evidence=_job_evidence(),
    )
    forged = _forged_replace(revalidation_request.authorization_evidence, policy_hash="f" * 64)
    object.__setattr__(revalidation_request, "authorization_evidence", forged)
    effect_calls = _install_zero_effect_spies(monkeypatch)
    observation_calls = _install_pre_observation_spies(monkeypatch, port)

    result = await port.revalidate(revalidation_request)

    assert result.allowed is False
    assert result.error_code == "deo_authorization_evidence_drift"
    assert result.target_observation_performed is False
    assert result.current_target_state_evidence is snapshot.baseline_target_state_evidence
    assert observation_calls == []
    assert effect_calls == []
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "before\n"


async def test_failed_fresh_target_capture_is_projection_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed target read cannot be reported as a completed observation."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port, request, snapshot, bound = await _bound_write_port(workspace)
    member = bound.member
    original_read_bytes = Path.read_bytes
    target_reads = 0

    def fail_target_read(path: Path, *args: object, **kwargs: object) -> bytes:
        nonlocal target_reads
        if path == target:
            target_reads += 1
            raise OSError("simulated target read failure")
        return original_read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", fail_target_read)
    effect_calls = _install_zero_effect_spies(monkeypatch)
    result = await port.revalidate(
        DirectorEffectPolicyRevalidationRequestV1(
            bound_snapshot=bound,
            workspace=str(workspace.resolve()),
            actual_normalized_tool_name=request.normalized_tool_name,
            actual_normalized_arguments=request.normalized_arguments,
            actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
            authorization_evidence=_authorization(request, snapshot, workspace),
            member=member,
            operation_id=member.operation_id,
            claim_grant=_claim_grant(member, workspace),
            current_job_token_restriction_evidence=_job_evidence(),
        )
    )

    assert result.allowed is False
    assert result.error_code == "deo_target_state_drift"
    assert result.target_observation_performed is False
    assert result.current_target_state_evidence is snapshot.baseline_target_state_evidence
    assert target_reads == 1
    assert effect_calls == []
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("role_policy_hash", "f" * 64),
        ("role_policy_id", "director-forged"),
        ("tool_spec_hash", "e" * 64),
    ),
)
async def test_rehashed_static_authority_drift_is_denied_by_bound_anchor_before_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    forged_value: str,
) -> None:
    """A canonical forged hash cannot replace the authorization sealed into the bound snapshot."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port, request, snapshot, bound = await _bound_write_port(workspace)
    member = bound.member
    revalidation_request = DirectorEffectPolicyRevalidationRequestV1(
        bound_snapshot=bound,
        workspace=str(workspace.resolve()),
        actual_normalized_tool_name=request.normalized_tool_name,
        actual_normalized_arguments=request.normalized_arguments,
        actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
        authorization_evidence=_authorization(request, snapshot, workspace),
        member=member,
        operation_id=member.operation_id,
        claim_grant=_claim_grant(member, workspace),
        current_job_token_restriction_evidence=_job_evidence(),
    )
    forged = _rehash_authorization(
        revalidation_request.authorization_evidence,
        **{field_name: forged_value},
    )
    assert forged.authorization_hash != bound.authorization_evidence_hash
    object.__setattr__(revalidation_request, "authorization_evidence", forged)
    effect_calls = _install_zero_effect_spies(monkeypatch)
    observation_calls = _install_pre_observation_spies(monkeypatch, port)

    result = await port.revalidate(revalidation_request)

    assert result.allowed is False
    assert result.error_code == "deo_authorization_evidence_drift"
    assert result.target_observation_performed is False
    assert result.current_target_state_evidence is snapshot.baseline_target_state_evidence
    assert observation_calls == []
    assert effect_calls == []
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("snapshot-hash", "deo_authorization_evidence_drift"),
        ("member-binding-hash", "deo_member_identity_mismatch"),
        ("retained-subject-member", "deo_member_identity_mismatch"),
        ("request-member", "deo_member_identity_mismatch"),
        ("operation", "deo_operation_hash_mismatch"),
        ("grant", "deo_member_identity_mismatch"),
    ),
)
async def test_static_identity_failures_are_denied_before_all_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_code: str,
) -> None:
    """Snapshot, member, request, operation, and grant drift are fail-fast."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port, request, snapshot, bound = await _bound_write_port(workspace)
    member = bound.member
    revalidation_request = DirectorEffectPolicyRevalidationRequestV1(
        bound_snapshot=bound,
        workspace=str(workspace.resolve()),
        actual_normalized_tool_name=request.normalized_tool_name,
        actual_normalized_arguments=request.normalized_arguments,
        actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
        authorization_evidence=_authorization(request, snapshot, workspace),
        member=member,
        operation_id=member.operation_id,
        claim_grant=_claim_grant(member, workspace),
        current_job_token_restriction_evidence=_job_evidence(),
    )

    if case == "snapshot-hash":
        forged_snapshot = _forged_replace(snapshot, evidence_hash="f" * 64)
        object.__setattr__(revalidation_request, "bound_snapshot", _forged_replace(bound, snapshot=forged_snapshot))
    elif case == "member-binding-hash":
        object.__setattr__(
            revalidation_request,
            "bound_snapshot",
            _forged_replace(bound, member_binding_hash="f" * 64),
        )
    elif case == "retained-subject-member":
        forged_member = _forged_replace(member, ordinal=member.ordinal + 1)
        forged_bound = DirectorEffectPolicyBoundSnapshotV1(
            snapshot=snapshot,
            authorization_evidence_hash=bound.authorization_evidence_hash,
            authorization_binding=bound.authorization_binding,
            authorization_binding_hash=bound.authorization_binding_hash,
            member=forged_member,
            member_binding_hash=hash_directed_effect_policy_member_binding(
                snapshot.evidence_hash,
                bound.authorization_evidence_hash,
                bound.authorization_binding_hash,
                forged_member,
            ),
        )
        object.__setattr__(revalidation_request, "bound_snapshot", forged_bound)
    elif case == "request-member":
        object.__setattr__(revalidation_request, "member", replace(member, effect_id="effect-forged"))
    elif case == "operation":
        object.__setattr__(revalidation_request, "actual_normalized_tool_name", "edit_file")
    else:
        grant = revalidation_request.claim_grant
        object.__setattr__(
            revalidation_request,
            "claim_grant",
            _forged_replace(
                grant,
                parent_binding=replace(grant.parent_binding, binding_id="binding-forged"),
            ),
        )

    effect_calls = _install_zero_effect_spies(monkeypatch)
    observation_calls = _install_pre_observation_spies(monkeypatch, port)
    result = await port.revalidate(revalidation_request)

    assert result.allowed is False
    assert result.error_code == expected_code
    assert result.target_observation_performed is False
    assert observation_calls == []
    assert effect_calls == []
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "before\n"


async def _bound_write_port(
    workspace: Path,
) -> tuple[
    DirectorEffectPolicySnapshotPortV1,
    DirectorEffectPolicySnapshotRequestV1,
    DirectorEffectPolicySnapshotResultV1,
    DirectorEffectPolicyBoundSnapshotV1,
]:
    port = create_director_effect_policy_snapshot_port(str(workspace))
    assert isinstance(port, DirectorEffectPolicySnapshotPortV1)
    request = _write_request(workspace)
    snapshot = await port.snapshot(request)
    assert snapshot.allowed
    member = _member(snapshot.normalized_operation_hash)
    authorization = _authorization(request, snapshot, workspace)
    bound = port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=_authorization_binding(snapshot, authorization),
            member=member,
        )
    )
    assert bound.status == "allowed"
    assert bound.bound_snapshot is not None
    return port, request, snapshot, bound.bound_snapshot


async def _current_policy_capture_request(
    workspace: Path,
) -> tuple[
    DirectorEffectPolicySnapshotPortV1,
    DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
]:
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port = create_director_effect_policy_snapshot_port(str(workspace))
    snapshot_request = _write_request(workspace)
    snapshot = await port.snapshot(snapshot_request)
    assert snapshot.allowed
    member = _member(snapshot.normalized_operation_hash)
    captured_spec = ToolSpecRegistry.capture_effective_spec("write_file")
    authorization = _rehash_authorization(
        _authorization(snapshot_request, snapshot, workspace),
        tool_spec_hash=captured_spec.tool_spec_hash,
    )
    classification = DirectorEffectClassificationEvidenceV1(
        raw_tool_name="write_file",
        canonical_tool_name="write_file",
        effect_type=member.effect_type,
        execution_mode=member.execution_mode,
        normalized_arguments=snapshot.subject.normalized_arguments,
        arguments_hash=authorization.arguments_hash,
        tool_spec_hash=captured_spec.tool_spec_hash,
        tool_spec_snapshot_hash=captured_spec.snapshot_hash,
        alias_binding_hash=captured_spec.alias_binding_hash,
    )
    authorization_binding = DirectorEffectAuthorizationBindingV1(
        authorization_evidence=authorization,
        classification_evidence=classification,
        tool_spec_hash=captured_spec.tool_spec_hash,
        tool_spec_snapshot_hash=captured_spec.snapshot_hash,
        alias_binding_hash=captured_spec.alias_binding_hash,
    )
    bound = port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=authorization_binding,
            member=member,
        )
    )
    assert bound.bound_snapshot is not None
    grant = _claim_grant(member, workspace)
    return port, DirectorEffectCurrentPolicyEvidenceCaptureRequestV1(
        baseline_authorization_binding=authorization_binding,
        baseline_public_policy_evidence=project_director_effect_public_policy_evidence(authorization_binding),
        bound_snapshot=bound.bound_snapshot,
        claimed_member=member,
        claim_grant=grant,
        normalized_tool="write_file",
        normalized_arguments_hash=authorization.arguments_hash,
        current_job_token_restriction_evidence=_job_evidence(),
    )


async def test_current_policy_capture_binds_live_sources_after_claim(tmp_path: Path) -> None:
    port, request = await _current_policy_capture_request(tmp_path / "workspace")

    result = await port.capture_current_policy_evidence(request)

    assert result.status == "captured"
    assert result.error_code is None
    assert result.evidence is not None
    assert result.evidence.claim_grant_hash == request.claim_grant.grant_hash
    assert result.evidence.bound_member_hash == request.bound_snapshot.member_binding_hash
    assert (
        result.evidence.baseline_authorization_binding_hash
        == request.baseline_authorization_binding.authorization_binding_hash
    )
    assert (
        result.evidence.baseline_public_policy_evidence_hash
        == request.baseline_public_policy_evidence.public_policy_evidence_hash
    )


@pytest.mark.parametrize(
    "source_method",
    (
        "_capture_policy_target_source",
        "_capture_operation_source",
        "_capture_capability_scope_source",
        "_capture_job_token_source",
        "_capture_tool_spec_source",
        "_capture_execution_envelope_source",
        "_capture_allowed_commands_source",
    ),
)
async def test_missing_or_unversioned_current_source_denies_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_method: str,
) -> None:
    port, request = await _current_policy_capture_request(tmp_path / source_method)
    monkeypatch.setattr(type(port), source_method, lambda *args, **kwargs: None)
    effect_calls = _install_zero_effect_spies(monkeypatch)

    result = await port.capture_current_policy_evidence(request)

    assert result.status == "denied"
    assert result.evidence is None
    assert result.error_code == "deo_current_policy_evidence_unavailable"
    assert effect_calls == []


async def test_member_binding_is_deterministic_stateless_and_forgery_closed(tmp_path: Path) -> None:
    """Binding hashes need no process state and reject every detached forgery."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    snapshot_port = create_director_effect_policy_snapshot_port(str(workspace))
    request = _write_request(workspace)
    snapshot = await snapshot_port.snapshot(request)
    assert snapshot.allowed
    member = _member(snapshot.normalized_operation_hash)
    authorization = _authorization(request, snapshot, workspace)

    first = snapshot_port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=_authorization_binding(snapshot, authorization),
            member=member,
        )
    )
    fresh_port = create_director_effect_policy_snapshot_port(str(workspace))
    second = fresh_port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=_authorization_binding(snapshot, authorization),
            member=member,
        )
    )

    assert first.status == second.status == "allowed"
    assert first.member_binding_hash == second.member_binding_hash
    assert first.bound_snapshot is not None
    assert second.bound_snapshot is not None

    async def revalidate(
        bound_snapshot: DirectorEffectPolicyBoundSnapshotV1,
        actual_member: DirectedEffectInventoryMemberV1,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        assert second.bound_snapshot is not None
        canonical_bound = second.bound_snapshot
        evidence = _authorization(request, canonical_bound.snapshot, workspace)
        revalidation_request = DirectorEffectPolicyRevalidationRequestV1(
            bound_snapshot=canonical_bound,
            workspace=str(workspace.resolve()),
            actual_normalized_tool_name=request.normalized_tool_name,
            actual_normalized_arguments=request.normalized_arguments,
            actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
            authorization_evidence=evidence,
            member=bound_snapshot.member,
            operation_id=bound_snapshot.member.operation_id,
            claim_grant=_claim_grant(bound_snapshot.member, workspace),
            current_job_token_restriction_evidence=_job_evidence(),
        )
        if bound_snapshot != canonical_bound:
            object.__setattr__(revalidation_request, "bound_snapshot", bound_snapshot)
        if actual_member != bound_snapshot.member:
            object.__setattr__(revalidation_request, "member", actual_member)
        return await fresh_port.revalidate(revalidation_request)

    random_hash = _forged_replace(second.bound_snapshot, member_binding_hash="f" * 64)
    changed_member = replace(member, effect_id="effect-forged")
    changed_snapshot = _forged_replace(snapshot, evidence_hash="e" * 64)
    swapped_snapshot = _forged_replace(second.bound_snapshot, snapshot=changed_snapshot)

    random_hash_result = await revalidate(random_hash, member)
    changed_member_result = await revalidate(second.bound_snapshot, changed_member)
    swapped_snapshot_result = await revalidate(swapped_snapshot, member)

    assert random_hash_result.error_code == "deo_member_identity_mismatch"
    assert changed_member_result.error_code == "deo_member_identity_mismatch"
    assert swapped_snapshot_result.error_code == "deo_authorization_evidence_drift"


@pytest.mark.parametrize(
    ("field_name", "forged_value"),
    (
        ("tool_call_id", "call-2"),
        ("ordinal", 2),
        ("normalized_tool_name", "edit_file"),
        ("effect_type", "async"),
        ("execution_mode", "async_receipt"),
        ("intended_effect_fingerprint", "f" * 64),
        ("policy_verdict_hash", "f" * 64),
        ("expected_receipt_binding_hash", "f" * 64),
    ),
)
async def test_bind_member_rejects_each_known_semantic_forgery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    forged_value: object,
) -> None:
    """Known member semantics must match the snapshot before a binding exists."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port = create_director_effect_policy_snapshot_port(str(workspace))
    request = _write_request(workspace)
    snapshot = await port.snapshot(request)
    assert snapshot.allowed
    member = _member(snapshot.normalized_operation_hash)
    forged_member = _forged_replace(member, **{field_name: forged_value})
    authorization = _authorization(request, snapshot, workspace)
    effect_calls = _install_zero_effect_spies(monkeypatch)

    result = port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=_authorization_binding(snapshot, authorization),
            member=forged_member,
        )
    )

    assert result.status == "denied"
    assert result.error_code == "deo_member_identity_mismatch"
    assert result.member is None
    assert result.member_binding_hash is None
    assert result.bound_snapshot is None
    assert effect_calls == []


@pytest.mark.parametrize("case", ("authorization-hash", "snapshot-anchor"))
async def test_bind_member_rejects_authorization_integrity_drift_without_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Binding accepts only canonical authorization anchored to this snapshot."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port = create_director_effect_policy_snapshot_port(str(workspace))
    request = _write_request(workspace)
    snapshot = await port.snapshot(request)
    assert snapshot.allowed
    member = _member(snapshot.normalized_operation_hash)
    authorization = _authorization(request, snapshot, workspace)
    canonical_binding = _authorization_binding(snapshot, authorization)
    if case == "authorization-hash":
        authorization = _forged_replace(authorization, role_policy_hash="f" * 64)
        authorization_binding = _forged_replace(
            canonical_binding,
            authorization_evidence=authorization,
        )
    else:
        authorization = _rehash_authorization(
            authorization,
            bound_policy_snapshot_hash="f" * 64,
        )
        authorization_binding = _authorization_binding(snapshot, authorization)
    effect_calls = _install_zero_effect_spies(monkeypatch)
    observation_calls = _install_pre_observation_spies(monkeypatch, port)

    result = port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=authorization_binding,
            member=member,
        )
    )

    assert result.status == "denied"
    assert result.error_code == "deo_authorization_evidence_drift"
    assert result.member is None
    assert result.member_binding_hash is None
    assert result.bound_snapshot is None
    assert observation_calls == []
    assert effect_calls == []
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.parametrize(
    "field_name",
    ("tool_spec_hash", "tool_spec_snapshot_hash", "alias_binding_hash", "authorization_binding_hash"),
)
async def test_bind_member_rejects_forged_task4_authorization_binding(
    tmp_path: Path,
    field_name: str,
) -> None:
    """Task5 cannot bind a sealed member to a forged additive Task4 wrapper."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port = create_director_effect_policy_snapshot_port(str(workspace))
    request = _write_request(workspace)
    snapshot = await port.snapshot(request)
    assert snapshot.allowed
    member = _member(snapshot.normalized_operation_hash)
    authorization = _authorization(request, snapshot, workspace)
    binding = _authorization_binding(snapshot, authorization)
    forged_binding = _forged_replace(binding, **{field_name: "f" * 64})

    result = port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=forged_binding,
            member=member,
        )
    )

    assert result.status == "denied"
    assert result.error_code == "deo_authorization_binding_drift"
    assert result.bound_snapshot is None


async def test_zero_ordinal_binds_and_seal_derived_ids_are_not_pre_expected(tmp_path: Path) -> None:
    """Ordinal zero is legal; effect and operation ids remain TaskRuntime-owned."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port = create_director_effect_policy_snapshot_port(str(workspace))
    request = _write_request(workspace, inventory_ordinal=0, tool_call_id="call-0")
    snapshot = await port.snapshot(request)
    assert snapshot.allowed
    assert snapshot.subject.inventory_ordinal == 0
    first_member = _member(
        snapshot.normalized_operation_hash,
        ordinal=0,
        tool_call_id="call-0",
        effect_id="seal-effect-a",
        operation_id="seal-operation-a",
    )
    second_member = replace(
        first_member,
        effect_id="seal-effect-b",
        operation_id="seal-operation-b",
    )
    authorization = _authorization(request, snapshot, workspace)

    first = port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=_authorization_binding(snapshot, authorization),
            member=first_member,
        )
    )
    second = port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=_authorization_binding(snapshot, authorization),
            member=second_member,
        )
    )

    assert first.status == second.status == "allowed"
    assert first.bound_snapshot is not None
    assert second.bound_snapshot is not None
    assert first.member_binding_hash != second.member_binding_hash


async def test_revalidation_needs_no_in_process_snapshot_state(tmp_path: Path) -> None:
    """Detached typed evidence is sufficient across fresh adapter instances."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    request = _write_request(workspace)
    snapshot = await create_director_effect_policy_snapshot_port(str(workspace)).snapshot(request)
    assert snapshot.allowed
    member = _member(snapshot.normalized_operation_hash)
    authorization = _authorization(request, snapshot, workspace)
    binding = create_director_effect_policy_snapshot_port(str(workspace)).bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=snapshot,
            authorization_evidence=authorization,
            authorization_binding=_authorization_binding(snapshot, authorization),
            member=member,
        )
    )
    assert binding.bound_snapshot is not None

    result = await create_director_effect_policy_snapshot_port(str(workspace)).revalidate(
        DirectorEffectPolicyRevalidationRequestV1(
            bound_snapshot=binding.bound_snapshot,
            workspace=str(workspace.resolve()),
            actual_normalized_tool_name=request.normalized_tool_name,
            actual_normalized_arguments=request.normalized_arguments,
            actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
            authorization_evidence=authorization,
            member=member,
            operation_id=member.operation_id,
            claim_grant=_claim_grant(member, workspace),
            current_job_token_restriction_evidence=_job_evidence(),
        )
    )

    assert result.allowed


async def test_public_policy_port_snapshots_and_revalidates_without_physical_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public factory produces evidence only; all physical entrypoints stay untouched."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    calls: list[str] = []
    for module_name, attribute in (
        ("subprocess", "run"),
        ("subprocess", "Popen"),
        ("os", "system"),
    ):
        module = __import__(module_name)
        monkeypatch.setattr(
            module,
            attribute,
            lambda *args, _attribute=attribute, **kwargs: calls.append(_attribute),
        )

    port, request, snapshot, bound = await _bound_write_port(workspace)
    member = bound.member
    revalidation = await port.revalidate(
        DirectorEffectPolicyRevalidationRequestV1(
            bound_snapshot=bound,
            workspace=str(workspace.resolve()),
            actual_normalized_tool_name=request.normalized_tool_name,
            actual_normalized_arguments=request.normalized_arguments,
            actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
            authorization_evidence=_authorization(request, snapshot, workspace),
            member=member,
            operation_id=member.operation_id,
            claim_grant=_claim_grant(member, workspace),
            current_job_token_restriction_evidence=_job_evidence(),
        )
    )

    assert revalidation.allowed
    assert revalidation.target_observation_performed is True
    assert revalidation.current_target_state_evidence == snapshot.baseline_target_state_evidence
    assert revalidation.current_target_state_evidence is not snapshot.baseline_target_state_evidence
    assert calls == []
    assert target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("alias", "deo_operation_hash_mismatch"),
        ("arguments", "deo_operation_hash_mismatch"),
        ("before_state", "deo_target_state_drift"),
        ("agents", "deo_policy_version_drift"),
        ("job_token", "deo_job_token_invalid"),
        ("evidence", "deo_authorization_evidence_drift"),
        ("allowed_command", "deo_command_scope_denied"),
        ("allowed_path", "deo_path_scope_denied"),
        ("grant", "deo_member_identity_mismatch"),
    ),
)
async def test_revalidation_denials_are_closed_and_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_code: str,
) -> None:
    """All post-seal drift is denied before any current effect can begin."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port, request, snapshot, bound = await _bound_write_port(workspace)
    member = bound.member
    current_job = _job_evidence()
    actual_tool = request.normalized_tool_name
    actual_arguments = request.normalized_arguments
    evidence = _authorization(request, snapshot, workspace)
    if case == "alias":
        actual_tool = "edit_file"
    elif case == "before_state":
        target.write_text("drift\n", encoding="utf-8")
    elif case == "agents":
        (workspace / "AGENTS.md").write_text("# changed\n", encoding="utf-8")
    elif case == "job_token":
        current_job = tuple((key, "job-2" if key == "job_token_id" else value) for key, value in current_job)
    elif case == "evidence":
        current_job = tuple((key, "b" * 64 if key == "job_token_hash" else value) for key, value in current_job)
    elif case == "allowed_path":
        current_job = _job_evidence(allowed_paths=("src/other.py",))
    elif case == "allowed_command":
        current_job = _job_evidence(allowed_commands=("python -m pytest",))

    request_tool = request.normalized_tool_name if case == "alias" else actual_tool
    revalidation_request = DirectorEffectPolicyRevalidationRequestV1(
        bound_snapshot=bound,
        workspace=str(workspace.resolve()),
        actual_normalized_tool_name=request_tool,
        actual_normalized_arguments=actual_arguments,
        actual_arguments_hash=hash_directed_effect_arguments(actual_arguments),
        authorization_evidence=evidence,
        member=member,
        operation_id=member.operation_id,
        claim_grant=_claim_grant(member, workspace),
        current_job_token_restriction_evidence=current_job,
    )
    if case == "alias":
        object.__setattr__(revalidation_request, "actual_normalized_tool_name", actual_tool)
    if case == "arguments":
        drifted_arguments = tuple(
            (key, "drifted\n" if key == "content" else value) for key, value in request.normalized_arguments
        )
        object.__setattr__(revalidation_request, "actual_normalized_arguments", drifted_arguments)
        object.__setattr__(
            revalidation_request,
            "actual_arguments_hash",
            hash_directed_effect_arguments(drifted_arguments),
        )
    if case == "evidence":
        object.__setattr__(revalidation_request.authorization_evidence, "policy_hash", "b" * 64)
    if case == "grant":
        grant = revalidation_request.claim_grant
        object.__setattr__(
            revalidation_request,
            "claim_grant",
            _forged_replace(
                grant,
                parent_binding=replace(grant.parent_binding, binding_id="binding-drift"),
            ),
        )
    effect_calls = _install_zero_effect_spies(monkeypatch)
    result = await port.revalidate(revalidation_request)
    assert result.allowed is False
    assert result.error_code == expected_code
    assert effect_calls == []
    pre_observation_denial = case in {"alias", "arguments", "evidence", "grant"}
    assert result.target_observation_performed is (not pre_observation_denial)
    if pre_observation_denial:
        assert result.current_target_state_evidence is snapshot.baseline_target_state_evidence
    else:
        assert result.current_target_state_evidence is not snapshot.baseline_target_state_evidence
    if case == "agents":
        assert (
            result.current_target_state_evidence.agents_policy_hash
            != snapshot.baseline_target_state_evidence.agents_policy_hash
        )
    if case == "before_state":
        assert (
            result.current_target_state_evidence.before_content_hash
            != snapshot.baseline_target_state_evidence.before_content_hash
        )
    assert target.read_text(encoding="utf-8") != "after\n"


async def test_revalidation_rejects_forged_baseline_before_fresh_observation(tmp_path: Path) -> None:
    """A forged baseline is denied as projection-only before any fresh state claim."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port, request, snapshot, bound = await _bound_write_port(workspace)
    member = bound.member
    forged_baseline = _forged_replace(snapshot.baseline_target_state_evidence, agents_policy_hash="f" * 64)
    forged_snapshot = _forged_replace(snapshot, baseline_target_state_evidence=forged_baseline)
    forged_bound = _forged_replace(bound, snapshot=forged_snapshot)

    result = await port.revalidate(
        DirectorEffectPolicyRevalidationRequestV1(
            bound_snapshot=forged_bound,
            workspace=str(workspace.resolve()),
            actual_normalized_tool_name=request.normalized_tool_name,
            actual_normalized_arguments=request.normalized_arguments,
            actual_arguments_hash=hash_directed_effect_arguments(request.normalized_arguments),
            authorization_evidence=_authorization(request, snapshot, workspace),
            member=member,
            operation_id=member.operation_id,
            claim_grant=_claim_grant(member, workspace),
            current_job_token_restriction_evidence=_job_evidence(),
        )
    )

    assert result.allowed is False
    assert result.error_code == "deo_authorization_evidence_drift"
    assert result.target_observation_performed is False
    assert result.current_target_state_evidence is forged_baseline


async def test_snapshot_denials_cover_path_command_director_and_member_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-seal input and pure member binding fail closed with closed DEO codes."""
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "a.py"
    target.parent.mkdir(parents=True)
    target.write_text("before\n", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("# policy\n", encoding="utf-8")
    port = create_director_effect_policy_snapshot_port(str(workspace))
    effect_calls = _install_zero_effect_spies(monkeypatch)

    path_escape = await port.snapshot(_write_request(workspace, target_path="../outside.py"))
    command_denial = await port.snapshot(_command_request(workspace, command="rm -rf /"))
    denied_by_director = await port.snapshot(
        replace(_write_request(workspace), path_scope_evidence=(("allowed_paths", ("other.py",)),))
    )
    invalid_job_token = await port.snapshot(
        replace(
            _write_request(workspace),
            job_token_restriction_evidence=(("job_token_id", "job-1"),),
        )
    )
    allowed_request = _write_request(workspace)
    allowed = await port.snapshot(allowed_request)
    allowed_authorization = _authorization(allowed_request, allowed, workspace)
    member_binding = port.bind_member(
        DirectorEffectPolicyMemberBindingRequestV1(
            snapshot=allowed,
            authorization_evidence=allowed_authorization,
            authorization_binding=_authorization_binding(allowed, allowed_authorization),
            member=_member(allowed.normalized_operation_hash, tool_name="execute_command"),
        )
    )

    assert path_escape.error_code == "deo_path_scope_denied"
    assert command_denial.error_code == "deo_command_scope_denied"
    assert denied_by_director.error_code == "deo_path_scope_denied"
    assert invalid_job_token.error_code == "deo_job_token_invalid"
    assert member_binding.status == "denied"
    assert member_binding.error_code == "deo_member_identity_mismatch"
    assert member_binding.member is None
    assert member_binding.member_binding_hash is None
    assert member_binding.bound_snapshot is None
    assert effect_calls == []
    assert target.read_text(encoding="utf-8") == "before\n"
