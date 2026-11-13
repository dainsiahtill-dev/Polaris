"""Provider binding failover for the engineering run's own client.

The engineering client is the platform's resilient client: it owns provider
failover and keeps the last real failure category.  A single configured
endpoint must not be the only thing standing between a long engineering run
and a provider that stopped serving requests, but only the Director may run
with a degraded binding set, and only over endpoints the host configured.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public import engineering_service as service_module
from polaris.cells.roles.runtime.public.contracts.engineering_run import RunLimits
from polaris.cells.roles.runtime.public.engineering_service import (
    _native_bindings,
    _native_client,
)
from polaris.cells.roles.runtime.tests.engineering_support import (
    isolated_workspace as isolated_workspace,
)
from polaris.kernelone.llm.engine import _executor_base as executor_base_module
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.runtime_config import RoleBindingSlot


def _slots(*pairs: tuple[str, str]) -> tuple[RoleBindingSlot, ...]:
    return tuple(
        RoleBindingSlot(
            role_id="director",
            provider_id=provider_id,
            model=model,
            binding_id=f"director:{index}:{provider_id}:{model}",
            binding_index=index,
        )
        for index, (provider_id, model) in enumerate(pairs)
    )


def test_director_bindings_start_at_the_resolved_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first chain entry is the endpoint the executor routes to today."""
    monkeypatch.setattr(executor_base_module, "get_role_model", lambda _role: ("primary", "primary-model"))
    monkeypatch.setattr(
        service_module,
        "get_role_binding_candidates",
        lambda _role: _slots(("primary", "primary-model"), ("secondary", "secondary-model")),
    )
    assert _native_bindings("director") == (
        ("primary", "primary-model"),
        ("secondary", "secondary-model"),
    )


def test_other_roles_stay_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the Director may run with a degraded binding set."""
    monkeypatch.setattr(executor_base_module, "get_role_model", lambda _role: ("primary", "primary-model"))
    monkeypatch.setattr(
        service_module,
        "get_role_binding_candidates",
        lambda _role: _slots(("primary", "primary-model"), ("secondary", "secondary-model")),
    )
    for role in ("qa", "pm", "chief_engineer"):
        assert _native_bindings(role) == ()


def test_single_binding_director_stays_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Director with one configured endpoint has no alternative to use."""
    monkeypatch.setattr(executor_base_module, "get_role_model", lambda _role: ("primary", "primary-model"))
    monkeypatch.setattr(
        service_module,
        "get_role_binding_candidates",
        lambda _role: _slots(("primary", "primary-model")),
    )
    assert _native_bindings("director") == (("primary", "primary-model"),)


def test_native_client_builds_one_pinned_entry_per_binding(
    isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each chain entry addresses exactly one configured endpoint."""
    monkeypatch.setattr(executor_base_module, "get_role_model", lambda _role: ("primary", "primary-model"))
    monkeypatch.setattr(
        service_module,
        "get_role_binding_candidates",
        lambda _role: _slots(("primary", "primary-model"), ("secondary", "secondary-model")),
    )
    limits = RunLimits(time.time() + 60, 1_000_000, 64_000)
    client = _native_client(isolated_workspace, limits, "director")
    names = [config.provider_name for _provider, config in client._manager._sorted_providers]
    assert names == ["kernelone_native:primary", "kernelone_native:secondary"]


async def test_pinned_provider_sends_its_own_binding(isolated_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pinned entry must address its own endpoint, not the role default."""
    from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType

    observed: list[tuple[str | None, str | None]] = []
    monkeypatch.setattr(executor_base_module, "get_role_model", lambda _role: ("primary", "primary-model"))
    monkeypatch.setattr(
        service_module,
        "get_role_binding_candidates",
        lambda _role: _slots(("primary", "primary-model"), ("secondary", "secondary-model")),
    )
    limits = RunLimits(time.time() + 60, 1_000_000, 64_000)
    client = _native_client(isolated_workspace, limits, "director")
    provider = client._manager._sorted_providers[1][0]

    async def _capture(self: Any, request: Any, **kwargs: Any) -> Any:
        observed.append((request.provider_id, request.model))
        raise RuntimeError("no dispatch in this unit")

    monkeypatch.setattr(AIExecutor, "invoke", _capture)
    request = AIRequest(task_type=TaskType.DIALOGUE, role="director", input="ping")
    with pytest.raises(RuntimeError):
        await provider.invoke(request)
    assert observed == [("secondary", "secondary-model")]
