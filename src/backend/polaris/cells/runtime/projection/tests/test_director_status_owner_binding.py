"""Focused GR1C-FIX tests for bootstrap-bound Director status observation."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest
from polaris.cells.runtime.projection.internal import (
    director_status_owner,
    runtime_projection_service as projection_service,
)
from polaris.cells.runtime.projection.public import (
    DirectorStatusObservationPortV1,
    DirectorStatusObservationV1,
    DirectorStatusObservationV1Error,
)
from polaris.cells.runtime.projection.public.bootstrap import (
    bind_director_status_observation_port,
)


class _DirectorStatusPort:
    def __init__(
        self,
        observation: DirectorStatusObservationV1 | None = None,
        error: Exception | None = None,
    ) -> None:
        self.observation = observation
        self.error = error
        self.calls: list[str] = []

    async def observe_director_status(
        self,
        *,
        workspace: str,
    ) -> DirectorStatusObservationV1:
        self.calls.append(workspace)
        if self.error is not None:
            raise self.error
        assert self.observation is not None
        return self.observation


@pytest.fixture(autouse=True)
def _reset_director_status_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        director_status_owner,
        "_director_status_observation_port",
        None,
        raising=False,
    )


def _observation(
    workspace: Path,
    *,
    state: str = "RUNNING",
    available: bool = True,
) -> DirectorStatusObservationV1:
    return DirectorStatusObservationV1(
        workspace=str(workspace.resolve()),
        available=available,
        status={"state": state, "workspace": str(workspace.resolve())} if available else None,
    )


@pytest.mark.asyncio
async def test_bound_exact_observation_projects_status_without_caller_authority_injection(tmp_path: Path) -> None:
    port = _DirectorStatusPort(_observation(tmp_path))
    bind_director_status_observation_port(port)

    result = await projection_service.get_director_local_status(str(tmp_path))

    assert isinstance(port, DirectorStatusObservationPortV1)
    assert [field.name for field in fields(DirectorStatusObservationV1)] == [
        "workspace",
        "available",
        "status",
    ]
    assert port.calls == [str(tmp_path.resolve())]
    assert result["running"] is True
    assert result["source"] == "v2_service"
    assert result["status"] == {"state": "RUNNING", "workspace": str(tmp_path.resolve())}
    assert "projection_error" not in result


@pytest.mark.asyncio
async def test_unbound_port_projects_explicit_unavailable_and_never_ready(tmp_path: Path) -> None:
    result = await projection_service.get_director_local_status(str(tmp_path))

    assert result["running"] is False
    assert result["source"] == "none"
    assert result["status"] is None
    assert "projection_error" in result
    assert "unbound" in result["projection_error"]


def test_same_port_rebind_is_idempotent_and_conflicting_rebind_fails(tmp_path: Path) -> None:
    first = _DirectorStatusPort(_observation(tmp_path))
    second = _DirectorStatusPort(_observation(tmp_path))

    bind_director_status_observation_port(first)
    bind_director_status_observation_port(first)
    with pytest.raises(DirectorStatusObservationV1Error) as exc_info:
        bind_director_status_observation_port(second)

    assert exc_info.value.error_code == "director_status_port_conflicting_rebind"


@pytest.mark.asyncio
async def test_wrong_exact_result_type_or_workspace_fails_closed(tmp_path: Path) -> None:
    wrong_type = _DirectorStatusPort()
    wrong_type.observation = object()  # type: ignore[assignment]
    bind_director_status_observation_port(wrong_type)
    with pytest.raises(DirectorStatusObservationV1Error) as type_error:
        await director_status_owner.observe_director_status_owner(str(tmp_path))
    assert type_error.value.error_code == "invalid_director_status_owner_result_type"

    director_status_owner._director_status_observation_port = None
    bind_director_status_observation_port(_DirectorStatusPort(_observation(tmp_path / "other")))
    with pytest.raises(DirectorStatusObservationV1Error) as identity_error:
        await director_status_owner.observe_director_status_owner(str(tmp_path))
    assert identity_error.value.error_code == "director_status_owner_identity_mismatch"


@pytest.mark.asyncio
async def test_port_failure_is_typed_and_does_not_project_ready(tmp_path: Path) -> None:
    bind_director_status_observation_port(_DirectorStatusPort(error=RuntimeError("owner unavailable")))

    result = await projection_service.get_director_local_status(str(tmp_path))

    assert result["running"] is False
    assert result["source"] == "none"
    assert result["status"] is None
    assert "owner unavailable" in result["projection_error"]


def test_unavailable_observation_forbids_status_payload(tmp_path: Path) -> None:
    with pytest.raises(DirectorStatusObservationV1Error) as exc_info:
        DirectorStatusObservationV1(
            workspace=str(tmp_path),
            available=False,
            status={"state": "RUNNING"},
        )

    assert exc_info.value.error_code == "invalid_director_status_owner_availability"
