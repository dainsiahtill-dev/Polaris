"""DEO-2B Task 8 exact dependency and attempt-identity wiring regressions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel
from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)


class _PolicyPort:
    async def capture_baseline_snapshot(self, request: Any) -> Any:
        raise AssertionError(request)

    async def snapshot(self, request: Any) -> Any:
        raise AssertionError(request)

    async def capture_current_policy_evidence(self, request: Any) -> Any:
        raise AssertionError(request)

    def bind_member(self, request: Any) -> Any:
        raise AssertionError(request)

    async def revalidate(self, request: Any) -> Any:
        raise AssertionError(request)


class _FenceAdmin:
    def register(self, context: Any) -> Any:
        raise AssertionError(context)

    def release_batch(self, batch_id: str, execution_attempt: Any) -> Any:
        raise AssertionError((batch_id, execution_attempt))


class _MutationPort:
    async def execute_mutation(
        self,
        context: Any,
        normalized_tool_name: str,
        normalized_arguments: Any,
        repair_effect_binding: Any = None,
    ) -> Any:
        raise AssertionError((context, normalized_tool_name, normalized_arguments, repair_effect_binding))


def _dependencies() -> DirectedEffectRuntimeDependenciesV1:
    return DirectedEffectRuntimeDependenciesV1(
        policy_snapshot_port=_PolicyPort(),
        fence_admin_port=_FenceAdmin(),
        mutation_port=_MutationPort(),
    )


def _attempt() -> TaskRuntimeExecutionAttemptIdentityV1:
    return TaskRuntimeExecutionAttemptIdentityV1(
        workspace="/workspace",
        task_id=8,
        external_task_id="TASK-8",
        session_id="session-8",
        attempt=1,
        role_id="director",
        worker_id="worker-8",
        run_id="run-8",
        lease_expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )


async def _llm_provider(_request: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("provider must not run in wiring tests")


async def _tool_runtime(_tool_name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("tool runtime must not run in wiring tests")


def test_exact_bundle_and_attempt_reach_every_tool_runtime_boundary() -> None:
    dependencies = _dependencies()
    attempt = _attempt()
    authority = TaskRuntimeExecutionAttemptAuthorityV1(attempt)

    kernel = TransactionKernel(
        llm_provider=_llm_provider,
        tool_runtime=_tool_runtime,
        directed_effect_runtime=dependencies,
        directed_effect_required=True,
        directed_effect_execution_attempt=attempt,
        directed_effect_execution_attempt_authority=authority,
    )

    assert kernel.directed_effect_runtime is dependencies
    assert kernel.directed_effect_execution_attempt is attempt
    assert kernel.directed_effect_execution_attempt_authority is authority
    assert kernel._tool_batch_executor.directed_effect_runtime is dependencies
    assert kernel._tool_batch_executor.directed_effect_execution_attempt is attempt
    assert kernel._tool_batch_executor.directed_effect_execution_attempt_authority is authority

    authoritative_runtime = kernel._tool_batch_executor._build_tool_batch_runtime("/workspace")
    speculative_runtime = kernel._build_tool_batch_runtime("/workspace")
    for runtime in (authoritative_runtime, speculative_runtime):
        assert runtime.directed_effect_runtime is dependencies
        assert runtime.directed_effect_required is True
        assert runtime.directed_effect_execution_attempt is attempt
        assert runtime.directed_effect_execution_attempt_authority is authority


def test_required_wiring_rejects_missing_attempt_before_any_tool_boundary() -> None:
    with pytest.raises(ValueError, match="runtime dependencies and attempt identity"):
        TransactionKernel(
            llm_provider=_llm_provider,
            tool_runtime=_tool_runtime,
            directed_effect_runtime=_dependencies(),
            directed_effect_required=True,
        )


def test_legacy_transaction_kernel_keeps_directed_effects_disabled() -> None:
    kernel = TransactionKernel(llm_provider=_llm_provider, tool_runtime=_tool_runtime)
    runtime = kernel._tool_batch_executor._build_tool_batch_runtime("/workspace")

    assert runtime.directed_effect_runtime is None
    assert runtime.directed_effect_required is False
    assert runtime.directed_effect_execution_attempt is None


def test_transaction_factory_fails_before_provider_dependency_without_attempt_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.roles.kernel.internal.kernel import transaction_factory

    provider_dependency_requested = False

    def _forbidden_get_llm_invoker(_kernel: Any) -> Any:
        nonlocal provider_dependency_requested
        provider_dependency_requested = True
        raise AssertionError("provider dependency must not be requested")

    monkeypatch.setattr(transaction_factory, "get_llm_invoker", _forbidden_get_llm_invoker)
    kernel = RoleExecutionKernel(
        workspace="/workspace",
        directed_effect_runtime=_dependencies(),
        directed_effect_required=True,
    )
    request = SimpleNamespace(
        workspace="/workspace",
        context_override={},
        metadata={},
        message="implement",
        task_id="TASK-8",
        run_id="run-8",
    )

    with pytest.raises(RuntimeError, match="execution_attempt_authority_required"):
        create_transaction_kernel(kernel, "director", SimpleNamespace(), request)  # type: ignore[arg-type]

    assert provider_dependency_requested is False
