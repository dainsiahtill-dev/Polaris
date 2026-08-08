"""Focused baseline-only tests for the Task4 directed-effect guard."""

from __future__ import annotations

import ast
import inspect
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Literal

import polaris.cells.roles.kernel.internal.directed_effect_policy_guard as guard_module
import polaris.cells.roles.kernel.internal.transaction.tool_batch_executor as tool_batch_executor_module
import pytest
from polaris.cells.director.runtime.public import (
    DirectedEffectErrorCodeV1,
    DirectorEffectAuthorizationEvidenceV1,
    DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    DirectorEffectCurrentPolicyEvidenceCaptureResultV1,
    DirectorEffectPolicyBaselineCaptureRequestV1,
    DirectorEffectPolicyMemberBindingRequestV1,
    DirectorEffectPolicyMemberBindingResultV1,
    DirectorEffectPolicyOperationSubjectV1,
    DirectorEffectPolicyRevalidationRequestV1,
    DirectorEffectPolicyRevalidationResultV1,
    DirectorEffectPolicySnapshotRequestV1,
    DirectorEffectPolicySnapshotResultV1,
    DirectorEffectTargetStateEvidenceV1,
    hash_directed_effect_arguments,
    hash_director_effect_authorization_evidence,
)
from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
    hash_directed_effect_policy_snapshot_evidence,
    hash_directed_effect_target_state_components,
)
from polaris.cells.roles.kernel.internal.directed_effect_policy_guard import (
    DirectedEffectAuthoritativePolicyGuardRequestV1,
    DirectedEffectPolicyGuard,
    DirectedEffectPolicyGuardRequestV1,
    DirectedEffectPolicyGuardResultV1,
)
from polaris.cells.roles.kernel.internal.tool_gateway import (
    DirectedEffectGatewayPolicyInputsV1,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import (
    ToolBatchExecutor,
    _is_mutation_for_speculative_routing,
)
from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    classify_tool_invocation,
)
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.kernelone.tool_execution.contracts import (
    CapturedToolSpecSnapshotV1,
    FrozenMapEntryV1,
    FrozenMapV1,
    FrozenScalarV1,
    FrozenSequenceV1,
)
from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

_HASH = "a" * 64
_ARGUMENTS = {"path": "src/a.py"}


def _with_semantic_fields(
    snapshot: CapturedToolSpecSnapshotV1,
    *,
    category: str | None,
    categories: tuple[str, ...] | None,
    effect_type: str | None,
) -> CapturedToolSpecSnapshotV1:
    entries = {entry.key: entry.value for entry in snapshot.canonical_effective_spec.entries}
    if category is None:
        entries.pop("category", None)
    else:
        entries["category"] = FrozenScalarV1("string", category)
    if categories is None:
        entries.pop("categories", None)
    else:
        entries["categories"] = FrozenSequenceV1(tuple(FrozenScalarV1("string", value) for value in categories))
    if effect_type is None:
        entries.pop("effect_type", None)
    else:
        entries["effect_type"] = FrozenScalarV1("string", effect_type)
    return replace(
        snapshot,
        canonical_effective_spec=FrozenMapV1(
            tuple(FrozenMapEntryV1(key, value) for key, value in sorted(entries.items()))
        ),
    )


def _capture(snapshot: CapturedToolSpecSnapshotV1):
    def capture_effective_spec(_: str) -> CapturedToolSpecSnapshotV1:
        return snapshot

    return capture_effective_spec


class _Calls:
    def __init__(self) -> None:
        self.gateway = 0
        self.policy = 0


class _Gateway(guard_module.RoleToolGateway):
    def __init__(self, calls: _Calls, allowed: bool = True, refusal: str = "") -> None:
        self._calls = calls
        self._allowed = allowed
        self._refusal = refusal

    def check_tool_permission_from_snapshot(self, **_: object) -> tuple[bool, str]:
        self._calls.gateway += 1
        return self._allowed, self._refusal

    def capture_directed_effect_policy_inputs(self) -> DirectedEffectGatewayPolicyInputsV1:
        restrictions = (
            ("allowed_commands", ()),
            ("allowed_commands_hash", _HASH),
            ("allowed_paths", ("src/",)),
            ("allowed_paths_hash", _HASH),
            ("job_token_hash", _HASH),
            ("job_token_id", "job-1"),
        )
        return DirectedEffectGatewayPolicyInputsV1(
            role_policy_id="director",
            role_policy_hash=_HASH,
            canonical_allow_list_hash=_HASH,
            capability_scope=("src/",),
            capability_scope_hash=_HASH,
            job_token_id="job-1",
            job_token_evidence_hash=hash_directed_effect_arguments(restrictions),
            job_token_restriction_evidence=restrictions,
            execution_envelope_hash=_HASH,
            allowed_command_hash=_HASH,
            policy_version="v1",
        )


class _PolicyPort:
    def __init__(self, calls: _Calls, result: DirectorEffectPolicySnapshotResultV1) -> None:
        self._calls = calls
        self._result = result

    async def capture_baseline_snapshot(
        self,
        request: DirectorEffectPolicyBaselineCaptureRequestV1,
    ) -> DirectorEffectPolicySnapshotResultV1:
        self._calls.policy += 1
        result = self._result
        return replace(
            result,
            subject=request.subject,
            normalized_operation_hash=request.subject.prospective_operation_hash,
            evidence_hash=hash_directed_effect_policy_snapshot_evidence(
                status=result.status,
                allowed=result.allowed,
                error_code=result.error_code,
                policy_version=result.policy_version,
                policy_hash=result.policy_hash,
                subject=request.subject,
                baseline_target_state_evidence=result.baseline_target_state_evidence,
                normalized_operation_hash=request.subject.prospective_operation_hash,
            ),
        )

    async def snapshot(self, _: DirectorEffectPolicySnapshotRequestV1) -> DirectorEffectPolicySnapshotResultV1:
        self._calls.policy += 1
        return self._result

    async def capture_current_policy_evidence(
        self,
        request: DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    ) -> DirectorEffectCurrentPolicyEvidenceCaptureResultV1:
        raise AssertionError(request)

    def bind_member(
        self,
        _: DirectorEffectPolicyMemberBindingRequestV1,
    ) -> DirectorEffectPolicyMemberBindingResultV1:
        raise AssertionError("preflight must not bind a sealed member")

    async def revalidate(
        self,
        _: DirectorEffectPolicyRevalidationRequestV1,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        raise AssertionError("preflight must not revalidate current policy")


def _invocation(raw_tool_name: str = "write_file") -> ToolInvocation:
    classification = classify_tool_invocation(raw_tool_name)
    return ToolInvocation(
        call_id=ToolCallId("call-1"),
        tool_name=classification.canonical_tool_name,
        raw_tool_name=raw_tool_name,
        arguments=_ARGUMENTS,
    )


def _forged(instance: object, **changes: object) -> object:
    model_copy = getattr(instance, "model_copy", None)
    if callable(model_copy):
        return model_copy(update=changes)
    field_names = getattr(instance, "__dataclass_fields__", None)
    if not isinstance(field_names, dict):
        raise TypeError("forged test values must be dataclasses or Pydantic models")
    forged = object.__new__(type(instance))
    for field_name in field_names:
        object.__setattr__(forged, field_name, changes.get(field_name, getattr(instance, field_name)))
    return forged


def _snapshot_request_and_result(
    invocation: ToolInvocation,
    *,
    allowed: bool = True,
    error_code: DirectedEffectErrorCodeV1 | None = None,
) -> tuple[DirectorEffectPolicySnapshotRequestV1, DirectorEffectPolicySnapshotResultV1]:
    classification = invocation.classification
    assert classification is not None
    normalized_arguments = tuple(sorted(invocation.arguments.items()))
    effect_type: Literal["write", "async"] = "async" if classification.effect_type is ToolEffectType.ASYNC else "write"
    execution_mode: Literal["write_serial", "async_receipt"] = (
        "async_receipt" if effect_type == "async" else "write_serial"
    )
    subject = DirectorEffectPolicyOperationSubjectV1(
        workspace="/workspace",
        turn_id="turn-1",
        batch_id="batch-1",
        tool_call_id=invocation.call_id,
        inventory_ordinal=0,
        normalized_tool_name=classification.canonical_tool_name,
        normalized_arguments=normalized_arguments,
        effect_type=effect_type,
        execution_mode=execution_mode,
        prospective_operation_hash=_HASH,
    )
    target_state_hash = hash_directed_effect_target_state_components(
        target_path="src/a.py",
        exists=True,
        before_content_hash=_HASH,
        minimal_content_evidence=(),
        agents_policy_hash=_HASH,
        is_no_file_state=False,
    )
    target = DirectorEffectTargetStateEvidenceV1(
        target_path="src/a.py",
        exists=True,
        before_content_hash=_HASH,
        minimal_content_evidence=(),
        agents_policy_hash=_HASH,
        target_state_hash=target_state_hash,
        is_no_file_state=False,
    )
    status: Literal["allowed", "denied"] = "allowed" if allowed else "denied"
    # Construct the real result with its canonical domain hash.
    from polaris.cells.director.runtime.public.directed_effect_policy_contracts import (
        hash_directed_effect_policy_snapshot_evidence,
    )

    evidence_hash = hash_directed_effect_policy_snapshot_evidence(
        status=status,
        allowed=allowed,
        error_code=error_code,
        policy_version="v1",
        policy_hash=_HASH,
        subject=subject,
        baseline_target_state_evidence=target,
        normalized_operation_hash=_HASH,
    )
    result = DirectorEffectPolicySnapshotResultV1(
        status=status,
        allowed=allowed,
        error_code=error_code,
        policy_version="v1",
        policy_hash=_HASH,
        subject=subject,
        baseline_target_state_evidence=target,
        target_state_hash=target_state_hash,
        normalized_operation_hash=_HASH,
        evidence_hash=evidence_hash,
    )
    request = DirectorEffectPolicySnapshotRequestV1(
        subject=subject,
        workspace="/workspace",
        normalized_tool_name=classification.canonical_tool_name,
        normalized_arguments=normalized_arguments,
        job_token_restriction_evidence=(),
        expected_policy_version="v1",
        canonical_command="",
        path_scope_evidence=(),
        command_scope_evidence=(),
        target_state_evidence=target,
    )
    return request, result


def _authorization(
    invocation: ToolInvocation,
    snapshot: DirectorEffectPolicySnapshotResultV1,
) -> DirectorEffectAuthorizationEvidenceV1:
    classification = invocation.classification
    assert classification is not None and classification.snapshot is not None
    arguments_hash = hash_directed_effect_arguments(tuple(sorted(invocation.arguments.items())))
    values = {
        "workspace": "/workspace",
        "execution_attempt_id": "session-1:1",
        "turn_id": "turn-1",
        "batch_id": "batch-1",
        "tool_call_id": invocation.call_id,
        "normalized_tool_name": classification.canonical_tool_name,
        "arguments_hash": arguments_hash,
        "tool_spec_hash": classification.snapshot.tool_spec_hash,
        "role_policy_id": "director",
        "role_policy_hash": _HASH,
        "canonical_allow_list_hash": _HASH,
        "capability_scope": ("src/",),
        "capability_scope_hash": _HASH,
        "job_token_id": "job-1",
        "job_token_evidence_hash": _HASH,
        "execution_envelope_hash": _HASH,
        "allowed_command_hash": _HASH,
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


def _guard_request(
    invocation: ToolInvocation,
    *,
    snapshot_allowed: bool = True,
    snapshot_error: DirectedEffectErrorCodeV1 | None = None,
    authorization: DirectorEffectAuthorizationEvidenceV1 | None | object = ...,
) -> tuple[DirectedEffectPolicyGuardRequestV1, _Calls, _Gateway, _PolicyPort]:
    request, snapshot = _snapshot_request_and_result(
        invocation,
        allowed=snapshot_allowed,
        error_code=snapshot_error,
    )
    calls = _Calls()
    gateway = _Gateway(calls)
    policy = _PolicyPort(calls, snapshot)
    evidence = _authorization(invocation, snapshot) if authorization is ... else authorization
    return (
        DirectedEffectPolicyGuardRequestV1(
            invocation=invocation,
            workspace="/workspace",
            inventory_ordinal=0,
            authorization_evidence=evidence,  # type: ignore[arg-type]
            snapshot_request=request,
        ),
        calls,
        gateway,
        policy,
    )


class _NoCallGateway(guard_module.RoleToolGateway):
    def __init__(self) -> None:
        pass

    def check_tool_permission_from_snapshot(self, **_: object) -> tuple[bool, str]:
        raise AssertionError("READ must not enter the gateway")


class _NoCallPolicyPort:
    async def capture_baseline_snapshot(
        self,
        request: DirectorEffectPolicyBaselineCaptureRequestV1,
    ) -> DirectorEffectPolicySnapshotResultV1:
        raise AssertionError(request)

    async def snapshot(self, _: DirectorEffectPolicySnapshotRequestV1) -> DirectorEffectPolicySnapshotResultV1:
        raise AssertionError("READ must not capture a policy snapshot")

    async def capture_current_policy_evidence(
        self,
        request: DirectorEffectCurrentPolicyEvidenceCaptureRequestV1,
    ) -> DirectorEffectCurrentPolicyEvidenceCaptureResultV1:
        raise AssertionError(request)

    def bind_member(
        self,
        _: DirectorEffectPolicyMemberBindingRequestV1,
    ) -> DirectorEffectPolicyMemberBindingResultV1:
        raise AssertionError("READ must not bind a sealed member")

    async def revalidate(
        self,
        _: DirectorEffectPolicyRevalidationRequestV1,
    ) -> DirectorEffectPolicyRevalidationResultV1:
        raise AssertionError("READ must not revalidate current policy")


@pytest.mark.asyncio
async def test_read_invocation_is_typed_not_applicable_without_dependencies() -> None:
    """READ and READ aliases end before normalization, gateway, or policy work."""
    invocation = ToolInvocation(
        call_id=ToolCallId("call-read"),
        tool_name="read_file",
        raw_tool_name="read_file",
        arguments={"path": "src/a.py"},
    )
    guard = DirectedEffectPolicyGuard(_NoCallGateway(), _NoCallPolicyPort())

    result = await guard.evaluate(
        DirectedEffectPolicyGuardRequestV1(
            invocation=invocation,
            workspace="/workspace",
            inventory_ordinal=0,
        )
    )

    assert result.status == "not_applicable"
    assert result.error_code is None
    assert result.authorization_binding is None


@pytest.mark.asyncio
async def test_guard_denies_forged_write_as_read_before_not_applicable() -> None:
    """A low-level copied invocation cannot bypass mutation checks as READ."""
    invocation = _invocation("write_file")
    classification = invocation.classification
    assert classification is not None
    forged = _forged(
        invocation,
        effect_type=ToolEffectType.READ,
        execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        classification=replace(
            classification,
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
            normalization_required=False,
        ),
    )
    assert isinstance(forged, ToolInvocation)
    guard = DirectedEffectPolicyGuard(_NoCallGateway(), _NoCallPolicyPort())

    result = await guard.evaluate(
        DirectedEffectPolicyGuardRequestV1(
            invocation=forged,
            workspace="/workspace",
            inventory_ordinal=0,
        )
    )

    assert result.status == "denied"
    assert result.error_code == "deo_tool_normalization_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("category", "categories", "effect_type"),
    (
        ("read", None, "write"),
        (None, ("read", "write"), None),
    ),
)
async def test_guard_rejects_conflicting_captured_spec_before_read_early_exit(
    monkeypatch: pytest.MonkeyPatch,
    category: str | None,
    categories: tuple[str, ...] | None,
    effect_type: str | None,
) -> None:
    captured = classify_tool_invocation("write_file").snapshot
    assert captured is not None
    monkeypatch.setattr(
        ToolSpecRegistry,
        "capture_effective_spec",
        _capture(
            _with_semantic_fields(
                captured,
                category=category,
                categories=categories,
                effect_type=effect_type,
            )
        ),
    )
    invocation = ToolInvocation(
        call_id=ToolCallId("call-conflicting-spec"),
        tool_name="write_file",
        arguments=_ARGUMENTS,
    )
    guard = DirectedEffectPolicyGuard(_NoCallGateway(), _NoCallPolicyPort())

    result = await guard.evaluate(
        DirectedEffectPolicyGuardRequestV1(
            invocation=invocation,
            workspace="/workspace",
            inventory_ordinal=0,
        )
    )

    assert result.status == "denied"
    assert result.error_code == "deo_tool_normalization_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_tool_name", ("read_file", "cat"))
async def test_read_variants_bypass_normalization_preflight_and_all_dependencies(
    raw_tool_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """READ aliases end before any mutation-stage calculation or dependency call."""
    calls = _Calls()
    forbidden: list[str] = []

    def forbid(name: str):
        def _forbidden(*_: object, **__: object) -> object:
            forbidden.append(name)
            raise AssertionError(f"READ reached {name}")

        return _forbidden

    monkeypatch.setattr(guard_module, "_immutable_arguments", forbid("normalizer"))
    monkeypatch.setattr(guard_module, "hash_directed_effect_arguments", forbid("arguments_hash"))
    monkeypatch.setattr(guard_module, "create_directed_effect_inventory_intent", forbid("task_runtime"))
    monkeypatch.setattr(guard_module, "DirectorEffectPreflightResultV1", forbid("preflight"))
    guard = DirectedEffectPolicyGuard(_Gateway(calls), _NoCallPolicyPort())

    result = await guard.evaluate(
        DirectedEffectPolicyGuardRequestV1(
            invocation=_invocation(raw_tool_name),
            workspace="/workspace",
            inventory_ordinal=0,
        )
    )

    assert result.status == "not_applicable"
    assert result.preflight is None
    assert calls.gateway == calls.policy == 0
    assert forbidden == []


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_tool_name", ("write_file", "execute_command", "run_command"))
async def test_mutation_and_alias_variants_authorize_with_no_effect(
    raw_tool_name: str,
) -> None:
    """Write, async-capable, and alias invocations retain a successful pure baseline."""
    invocation = _invocation(raw_tool_name)
    request, calls, gateway, policy = _guard_request(invocation)

    result = await DirectedEffectPolicyGuard(gateway, policy).evaluate(request)

    assert result.status == "authorized"
    assert result.preflight is not None and result.preflight.intent is not None
    assert result.authorization_binding is not None
    assert calls.gateway == calls.policy == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ("missing", "unregistered", "empty", "invalid", "alias_drift"),
)
async def test_normalization_rejections_precede_gateway_and_policy(case: str) -> None:
    """Invalid classification forms fail closed before every downstream guard."""
    invocation = _invocation()
    request, calls, gateway, policy = _guard_request(invocation)
    if case == "missing":
        invocation = _forged(invocation, classification=None)  # type: ignore[assignment]
    elif case == "unregistered":
        invocation = _invocation("search_files")
    elif case == "empty":
        invocation = _forged(invocation, raw_tool_name="")  # type: ignore[assignment]
    elif case == "invalid":
        invocation = _forged(invocation, tool_name="execute_command")  # type: ignore[assignment]
    else:
        classification = invocation.classification
        assert classification is not None and classification.snapshot is not None
        invocation = _forged(
            invocation,
            classification=_forged(
                classification,
                snapshot=_forged(classification.snapshot, raw_tool_name="run_command"),
            ),
        )  # type: ignore[assignment]
    request = replace(request, invocation=invocation)

    result = await DirectedEffectPolicyGuard(gateway, policy).evaluate(request)

    assert result.status == "denied"
    assert result.error_code == "deo_tool_normalization_failed"
    assert calls.gateway == calls.policy == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("refusal", "expected_code"),
    (
        ("allow-list denied", "deo_tool_not_allowed"),
        ("path scope denied", "deo_path_scope_denied"),
        ("command denied", "deo_command_scope_denied"),
        ("non-strict guard", "deo_mutation_guard_denied"),
        ("JobToken invalid", "deo_job_token_invalid"),
    ),
)
async def test_gateway_denials_preserve_closed_codes_without_policy_or_effect(
    refusal: str,
    expected_code: str,
) -> None:
    """Gateway denials cannot reach policy capture or create mutation evidence."""
    request, calls, gateway, policy = _guard_request(_invocation())
    gateway._allowed = False
    gateway._refusal = refusal

    result = await DirectedEffectPolicyGuard(gateway, policy).evaluate(request)

    assert result.status == "denied"
    assert result.error_code == expected_code
    assert result.snapshot is result.authorization_binding is result.public_policy_evidence is None
    assert calls.gateway == 1 and calls.policy == 0


@pytest.mark.asyncio
async def test_missing_authorization_and_policy_denial_preserve_no_effect_boundary() -> None:
    """Missing authority and policy-port denial remain typed and effect-free."""
    missing, missing_calls, missing_gateway, missing_policy = _guard_request(_invocation(), authorization=None)
    denied, denied_calls, denied_gateway, denied_policy = _guard_request(
        _invocation(),
        snapshot_allowed=False,
        snapshot_error="deo_director_policy_denied",
    )

    missing_result = await DirectedEffectPolicyGuard(missing_gateway, missing_policy).evaluate(missing)
    denied_result = await DirectedEffectPolicyGuard(denied_gateway, denied_policy).evaluate(denied)

    assert (missing_result.status, missing_result.error_code) == ("denied", "deo_authorization_hash_drift")
    assert (denied_result.status, denied_result.error_code) == ("denied", "deo_director_policy_denied")
    assert (missing_calls.gateway, missing_calls.policy) == (1, 0)
    assert (denied_calls.gateway, denied_calls.policy) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ("raw_tool_name", "tool_name", "classification", "arguments"))
async def test_forged_classification_identity_rejects_before_gateway(field: str) -> None:
    """Raw/canonical/snapshot/argument substitution cannot cross the guard boundary."""
    invocation = _invocation()
    request, calls, gateway, policy = _guard_request(invocation)
    if field == "raw_tool_name":
        invocation = _forged(invocation, raw_tool_name="run_command")  # type: ignore[assignment]
    elif field == "tool_name":
        invocation = _forged(invocation, tool_name="execute_command")  # type: ignore[assignment]
    elif field == "classification":
        classification = invocation.classification
        assert classification is not None and classification.snapshot is not None
        invocation = _forged(
            invocation,
            classification=_forged(classification, canonical_tool_name="execute_command"),
        )  # type: ignore[assignment]
    else:
        invocation = _forged(invocation, arguments={"path": "src/substituted.py"})  # type: ignore[assignment]
    request = replace(request, invocation=invocation)

    result = await DirectedEffectPolicyGuard(gateway, policy).evaluate(request)

    assert result.status == "denied"
    assert result.error_code in {"deo_tool_normalization_failed", "deo_authorization_hash_drift"}
    assert calls.gateway == calls.policy == 0


@pytest.mark.asyncio
async def test_authoritative_guard_sources_gateway_policy_and_adapter_baseline() -> None:
    """Production guard accepts no caller-built auth or target-state evidence."""

    invocation = _invocation()
    _, result = _snapshot_request_and_result(invocation)
    calls = _Calls()
    gateway = _Gateway(calls)
    policy = _PolicyPort(calls, result)
    attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=9,
        external_task_id="TASK-9",
        session_id="session-9",
        attempt=1,
        role_id="director",
        worker_id="worker-9",
        run_id="run-9",
        lease_expires_at="2026-07-20T12:00:00+00:00",
    )

    verdict = await DirectedEffectPolicyGuard(gateway, policy).evaluate_authoritative(
        DirectedEffectAuthoritativePolicyGuardRequestV1(
            invocation=invocation,
            workspace="/workspace",
            inventory_ordinal=0,
            execution_attempt=attempt,
            turn_id="turn-1",
            batch_id="batch-1",
        )
    )

    assert (verdict.status, verdict.error_code) == ("authorized", None)
    assert verdict.current_job_token_restriction_evidence is not None
    assert verdict.authorization_binding is not None
    assert verdict.authorization_binding.authorization_evidence.execution_attempt_id
    assert calls.gateway == 1 and calls.policy == 1
    assert "target_state_evidence" not in DirectedEffectAuthoritativePolicyGuardRequestV1.__dataclass_fields__
    assert "authorization_evidence" not in DirectedEffectAuthoritativePolicyGuardRequestV1.__dataclass_fields__


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preserve_same_path_inventory", "expected_write_ids"),
    (
        (False, ("write-2",)),
        (True, ("write-1", "write-2")),
    ),
)
async def test_mutation_batch_authorizes_exact_post_collapse_inventory(
    monkeypatch: pytest.MonkeyPatch,
    preserve_same_path_inventory: bool,
    expected_write_ids: tuple[str, ...],
) -> None:
    """Normal batches collapse same-path writes; typed rollback inventories do not."""

    events: list[tuple[str, object]] = []
    _, baseline = _snapshot_request_and_result(_invocation())
    calls = _Calls()
    gateway = _Gateway(calls)
    policy = _PolicyPort(calls, baseline)

    class _RecordingGuard:
        def __init__(self) -> None:
            self._guard = DirectedEffectPolicyGuard(gateway, policy)

        async def evaluate_authoritative(
            self,
            request: DirectedEffectAuthoritativePolicyGuardRequestV1,
        ) -> DirectedEffectPolicyGuardResultV1:
            events.append(("authorize", str(request.invocation.call_id)))
            return await self._guard.evaluate_authoritative(request)

    class _ToolRuntime:
        def directed_effect_policy_guard(self, injected_policy: object) -> _RecordingGuard:
            assert injected_policy is policy
            return _RecordingGuard()

        async def __call__(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError((tool_name, arguments))

    class _FenceAdmin:
        def register(self, context: object) -> object:
            raise AssertionError(context)

        def release_batch(self, batch_id: str, execution_attempt: object) -> object:
            raise AssertionError((batch_id, execution_attempt))

    class _MutationPort:
        async def execute_mutation(
            self,
            context: object,
            normalized_tool_name: str,
            normalized_arguments: object,
            repair_effect_binding: object | None = None,
        ) -> object:
            raise AssertionError((context, normalized_tool_name, normalized_arguments, repair_effect_binding))

    prepared_sentinel = object()

    class _Lifecycle:
        def __init__(self, *, policy_snapshot_port: object) -> None:
            assert policy_snapshot_port is policy

        def prepare_batch(self, **kwargs: object) -> object:
            candidates = kwargs["candidates"]
            assert isinstance(candidates, tuple)
            events.append(
                (
                    "prepare",
                    tuple(candidate.preflight.intent.tool_call_id for candidate in candidates),
                )
            )
            return SimpleNamespace(
                status="ready",
                prepared_batch=prepared_sentinel,
                error_code=None,
            )

    monkeypatch.setattr(
        tool_batch_executor_module,
        "DirectedEffectLifecycleService",
        _Lifecycle,
    )
    attempt = TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=9,
        external_task_id="TASK-9",
        session_id="session-9",
        attempt=1,
        role_id="director",
        worker_id="worker-9",
        run_id="run-9",
        lease_expires_at="2026-07-20T12:00:00+00:00",
    )
    runtime = DirectedEffectRuntimeDependenciesV1(
        policy_snapshot_port=policy,
        fence_admin_port=_FenceAdmin(),
        mutation_port=_MutationPort(),
    )
    executor = ToolBatchExecutor(
        tool_runtime=_ToolRuntime(),
        config=TransactionConfig(
            workspace="/workspace",
            role_id="director",
            mutation_guard_mode="strict",
        ),
        emit_event=lambda _event: None,
        guard_assert_single_tool_batch=lambda **_kwargs: None,
        finalization_handler=object(),
        handoff_handler=object(),
        directed_effect_runtime=runtime,
        directed_effect_required=True,
        directed_effect_execution_attempt=attempt,
        directed_effect_execution_attempt_authority=TaskRuntimeExecutionAttemptAuthorityV1(attempt),
    )
    invocations = [
        {"call_id": "read-1", "tool_name": "read_file", "arguments": {"path": "src/a.py"}},
        {
            "call_id": "write-1",
            "tool_name": "write_file",
            "arguments": {"path": "src/a.py"},
        },
        {
            "call_id": "write-2",
            "tool_name": "write_file",
            "arguments": {"path": "src/a.py"},
        },
    ]

    canonical, prepared = await executor._prepare_directed_effect_dispatch(
        invocations=invocations,
        workspace="/workspace",
        turn_id="turn-1",
        batch_id="batch-1",
        preserve_same_path_inventory=preserve_same_path_inventory,
    )

    assert [item.effect_type for item in canonical] == [
        ToolEffectType.READ,
        ToolEffectType.WRITE,
        ToolEffectType.WRITE,
    ]
    assert events == [
        *(("authorize", call_id) for call_id in expected_write_ids),
        ("prepare", expected_write_ids),
    ]
    assert prepared is not None and prepared.batch is prepared_sentinel
    assert tuple(call_id for call_id, _ in prepared.restrictions_by_call_id) == expected_write_ids
    assert calls.gateway == calls.policy == len(expected_write_ids)


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    (("read_file", False), ("write_file", True), ("deploy", True), ("unknown_custom_tool", True)),
)
def test_required_deo_speculation_uses_authoritative_effect_classification(
    tool_name: str,
    expected: bool,
) -> None:
    """No required mutation can be adopted through the legacy speculative READ path."""

    invocation = ToolInvocation(
        call_id=ToolCallId(f"call-{tool_name}"),
        raw_tool_name=tool_name,
        tool_name=tool_name,
        arguments={},
    )

    assert (
        _is_mutation_for_speculative_routing(
            invocation,
            directed_effect_required=True,
        )
        is expected
    )


def test_guard_static_fence_excludes_effect_runtime_and_adapter_dependencies() -> None:
    """The real guard module remains an evidence-only policy boundary."""
    tree = ast.parse(inspect.getsource(guard_module))
    imports = {
        alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
    }
    source = inspect.getsource(guard_module)

    assert not any("roles.adapters.internal" in name for name in imports)
    assert "TaskRuntimeExecutionAttemptAuthorityV1" not in source
    assert "DirectorToolExecutor" not in source
    assert not any(name.startswith(("os", "subprocess", "asyncio", "pathlib")) for name in imports)
    assert "claim_" not in source and "admit_" not in source
