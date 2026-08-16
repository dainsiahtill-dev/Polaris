from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.runtime.task_runtime.public.contracts import BindRuntimeTaskToFactoryRunCommandV1
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService


def _create_bootstrapped_task_runtime_service(workspace: Path) -> TaskRuntimeService:
    workspace.mkdir(parents=True, exist_ok=True)
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            maintenance_reason="task-runtime-bind-observer-gap-test",
            streams=fact_stream_bootstrap_streams(),
        )
    )
    return TaskRuntimeService(str(workspace))


def test_bind_task_to_factory_run_finds_external_owner_hidden_from_observer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L2-14 QA retry: bind must not use the fact-only observer.

    After COMPLETED_VERIFIED drain, ``list_observable_task_rows`` hid the
    still-authoritative terminal TASK-N owner. Quality repair found that
    owner via ``list_task_rows(include_terminal=True)`` then failed at
    ``bind_task_to_factory_run`` with ``task_not_found``, so crate rewrite
    never claimed.
    """

    workspace = tmp_path / "workspace"
    service = _create_bootstrapped_task_runtime_service(workspace)
    owner = service.ensure_task_row(external_task_id="TASK-3", subject="crate rewrite owner")
    assert owner is not None
    monkeypatch.setattr(service, "list_observable_task_rows", lambda: [])

    assert service.get_task("TASK-3") is None
    mutation_row = service._resolve_task_row_for_mutation("TASK-3")
    assert mutation_row is not None
    assert service.normalize_task_id(mutation_row.get("id")) == service.normalize_task_id(owner.get("id"))

    binding = service.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(workspace),
            task_id="TASK-3",
            factory_run_id="factory_b177d6fb4a8f",
        )
    )

    assert binding.ok is True
    assert binding.code == "factory_run_bound"
    assert binding.task_id == str(service.normalize_task_id(owner.get("id")))
