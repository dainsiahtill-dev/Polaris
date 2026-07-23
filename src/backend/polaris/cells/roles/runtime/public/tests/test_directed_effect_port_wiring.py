"""DEO-2B Task 8 typed runtime-to-kernel composition regressions."""

from __future__ import annotations

from typing import Any

from polaris.cells.roles.kernel.public import DirectedEffectRuntimeDependenciesV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService


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


def test_role_runtime_injects_same_deo_dependencies_into_kernel() -> None:
    dependencies = _dependencies()

    runtime = RoleRuntimeService(
        directed_effect_runtime=dependencies,
        directed_effect_required=True,
    )
    kernel = runtime._get_kernel("/workspace")

    assert kernel.directed_effect_runtime is dependencies
    assert kernel.directed_effect_required is True
    assert not hasattr(runtime, "directed_effect_context")
    assert not hasattr(kernel, "directed_effect_context")
    assert not hasattr(kernel, "claim_grant")
