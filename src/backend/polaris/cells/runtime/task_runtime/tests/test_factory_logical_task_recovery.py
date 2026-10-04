"""Factory recovery must preserve the physical prerequisite and attempt fence."""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.runtime.task_runtime.public.contracts import BindRuntimeTaskToFactoryRunCommandV1
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.cells.runtime.task_runtime.tests.test_service_sub2 import (
    _create_bootstrapped_task_runtime_service,
    _settle_claimed_execution_attempt,
)


def _bound_owner(tmp_path: Path) -> tuple[TaskRuntimeService, dict[str, Any], dict[str, Any]]:
    service = _create_bootstrapped_task_runtime_service(tmp_path)
    row = service.ensure_task_row(
        external_task_id="WORK-A",
        subject="Prerequisite",
        metadata={
            "external_task_id": "WORK-A",
            "factory_run_id": "factory-one",
            "task_completion_projection": {
                "schema_version": "polaris.task_completion_projection.v1",
                "task_id": "WORK-A",
                "run_id": "factory-one",
                "project_contract_hash": "a" * 64,
                "projection_hash": "b" * 64,
            },
        },
    )
    binding = service.bind_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(
            workspace=str(tmp_path), task_id=str(row["id"]), factory_run_id="factory-one"
        )
    )
    assert binding.ok
    claim = service.claim_execution(
        row["id"],
        worker_id="director",
        role_id="director",
        run_id="director-before",
        selection_source="test",
        external_task_id="WORK-A",
    )
    assert claim["success"]
    return service, row, claim


def _recover(service: TaskRuntimeService, **overrides: str) -> dict[str, Any]:
    intent = {
        "external_task_id": "WORK-A",
        "factory_run_id": "factory-one",
        "project_contract_hash": "a" * 64,
        "projection_hash": "b" * 64,
    }
    intent.update(overrides)
    return service.prepare_factory_task_recovery(**intent)


def test_recovery_preserves_dependency_row_and_rejects_previous_attempt(tmp_path: Path) -> None:
    service, owner, old_claim = _bound_owner(tmp_path)
    child = service.create_task_row(
        subject="Dependent",
        blocked_by=[int(owner["id"])],
        metadata={"external_task_id": "WORK-B", "resolved_depends_on_task_ids": [int(owner["id"])]},
    )
    assert _settle_claimed_execution_attempt(service, old_claim, outcome="failed", summary="verifier failed")["success"]
    recovered = _recover(service)
    assert recovered["id"] == owner["id"]
    new_claim = service.claim_execution(
        recovered["id"],
        worker_id="director",
        role_id="director",
        run_id="director-recovery",
        selection_source="test",
        external_task_id="WORK-A",
    )
    assert new_claim["success"]
    assert new_claim["execution_attempt"]["attempt"] == old_claim["execution_attempt"]["attempt"] + 1
    assert not _settle_claimed_execution_attempt(service, old_claim, outcome="completed", summary="stale")["success"]
    assert _settle_claimed_execution_attempt(service, new_claim, outcome="completed", summary="valid recovery")[
        "success"
    ]
    service.refresh_dependency_unblocks()
    child_row = service.get_task(child["id"])
    assert child_row is not None and child_row["blocked_by"] == []
    assert service.claim_execution(
        child["id"], worker_id="director", role_id="director", run_id="director-recovery", external_task_id="WORK-B"
    )["success"]
    assert len(service.list_task_rows(include_terminal=True)) == 2


@pytest.mark.parametrize(
    "override",
    [
        {"factory_run_id": "factory-other"},
        {"project_contract_hash": "c" * 64},
        {"projection_hash": "d" * 64},
        {"project_contract_hash": ""},
    ],
)
def test_recovery_rejects_foreign_or_changed_binding_without_mutation(tmp_path: Path, override: dict[str, str]) -> None:
    service, owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="failed")["success"]
    before = service.list_observable_task_rows()
    with pytest.raises((RuntimeError, ValueError)):
        _recover(service, **override)
    assert service.list_observable_task_rows() == before
    assert (current := service.get_task(owner["id"])) is not None
    assert current["status"] == "failed"


def test_recovery_does_not_steal_an_active_attempt(tmp_path: Path) -> None:
    service, owner, _claim = _bound_owner(tmp_path)
    before = service.list_observable_task_rows()
    with pytest.raises(RuntimeError):
        _recover(service)
    assert service.list_observable_task_rows() == before
    assert (current := service.get_task(owner["id"])) is not None
    assert current["status"] == "in_progress"


def test_recovery_never_overrides_user_cancellation(tmp_path: Path) -> None:
    service, owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="suspended", summary="operator requested stop")[
        "success"
    ]
    cancelled = service.cancel_task_row_for_factory_abort(
        owner["id"], factory_run_id="factory-one", reason="user_cancelled", source="test_user_stop"
    )
    assert cancelled is not None and cancelled["status"] == "cancelled"
    before = service.list_observable_task_rows()
    with pytest.raises(RuntimeError, match="owner_not_failed"):
        _recover(service)
    assert service.list_observable_task_rows() == before


def test_creation_only_default_does_not_implicitly_reopen(tmp_path: Path) -> None:
    service, owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="failed")["success"]
    created = service.ensure_task_row(external_task_id="WORK-A", subject="Ordinary creation-only ensure")
    assert created["id"] != owner["id"]
    assert (current := service.get_task(owner["id"])) is not None
    assert current["status"] == "failed"


def test_ambiguous_logical_owner_is_not_chosen_by_status_or_row_number(tmp_path: Path) -> None:
    service, owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="failed")["success"]
    duplicate = service.create_task_row(subject="Ambiguous sibling", metadata=dict(owner["metadata"]))
    assert duplicate["id"] != owner["id"]
    before = service.list_observable_task_rows()
    with pytest.raises(RuntimeError):
        _recover(service)
    assert service.list_observable_task_rows() == before


def test_recovery_refuses_missing_board_entity(tmp_path: Path) -> None:
    service, owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="failed")["success"]
    (tmp_path / ".polaris/runtime/tasks" / f"task_{owner['id']}.json").unlink()
    restarted = TaskRuntimeService(str(tmp_path))
    before = restarted.list_observable_task_rows()
    with pytest.raises(RuntimeError, match="entity_missing"):
        _recover(restarted)
    assert restarted.list_observable_task_rows() == before


def test_concurrent_recovery_services_do_not_clone_or_reopen_twice(tmp_path: Path) -> None:
    service, owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="failed")["success"]
    peers = [TaskRuntimeService(str(tmp_path)), TaskRuntimeService(str(tmp_path))]

    def recover(peer: TaskRuntimeService) -> int | None:
        try:
            return int(_recover(peer)["id"])
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(recover, peers))
    assert results.count(int(owner["id"])) == 1
    assert results.count(None) == 1
    assert len(TaskRuntimeService(str(tmp_path)).list_observable_task_rows()) == 1


def test_recovery_cas_rejects_claim_between_observation_and_reopen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="failed")["success"]
    native_reopen = service._reopen_with_execution_event
    peer = TaskRuntimeService(str(tmp_path))
    observed: dict[str, Any] = {}

    def raced_reopen(*args: Any, **kwargs: Any) -> Any:
        assert peer.reopen_task_row(owner["id"], reason="separately admitted recovery") is not None
        newer = peer.claim_execution(
            owner["id"], worker_id="other-owner", role_id="director", run_id="director-other", external_task_id="WORK-A"
        )
        assert newer["success"]
        observed["rows"] = peer.list_observable_task_rows()
        return native_reopen(*args, **kwargs)

    monkeypatch.setattr(service, "_reopen_with_execution_event", raced_reopen)
    with pytest.raises(RuntimeError, match="compare_and_set_failed"):
        _recover(service)
    assert TaskRuntimeService(str(tmp_path)).list_observable_task_rows() == observed["rows"]


@pytest.mark.parametrize("race", ["contract_drift", "new_sibling"])
def test_recovery_rechecks_durable_binding_and_namespace_after_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, race: str
) -> None:
    service, owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="failed")["success"]
    native_reopen = service._reopen_with_execution_event
    peer = TaskRuntimeService(str(tmp_path))
    observed: dict[str, Any] = {}

    def raced_reopen(*args: Any, **kwargs: Any) -> Any:
        if race == "contract_drift":
            projection = dict(owner["metadata"]["task_completion_projection"])
            projection["project_contract_hash"] = "f" * 64
            peer.update_task_row(owner["id"], metadata={"task_completion_projection": projection})
        else:
            peer.create_task_row(subject="Concurrent logical sibling", metadata=dict(owner["metadata"]))
        observed["rows"] = peer.list_observable_task_rows()
        return native_reopen(*args, **kwargs)

    monkeypatch.setattr(service, "_reopen_with_execution_event", raced_reopen)
    with pytest.raises(RuntimeError):
        _recover(service)
    assert TaskRuntimeService(str(tmp_path)).list_observable_task_rows() == observed["rows"]


def test_two_processes_recover_one_physical_owner(tmp_path: Path) -> None:
    service, owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="failed")["success"]
    code = """
import json, sys
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.cells.runtime.task_runtime.tests.test_factory_logical_task_recovery import _recover
service = TaskRuntimeService(sys.argv[1])
print('READY', flush=True)
sys.stdin.readline()
try:
    result = {'id': _recover(service)['id']}
except RuntimeError as exc:
    result = {'refused': str(exc)}
print(json.dumps(result), flush=True)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            assert child.stdout is not None and child.stdout.readline().strip() == "READY"
        for child in children:
            assert child.stdin is not None
            child.stdin.write("GO\n")
            child.stdin.flush()
        results = []
        for child in children:
            output, errors = child.communicate(timeout=20)
            assert child.returncode == 0, errors
            results.append(json.loads(output.strip()))
        assert sum(result.get("id") == owner["id"] for result in results) == 1
        assert sum("refused" in result for result in results) == 1
        assert len(TaskRuntimeService(str(tmp_path)).list_observable_task_rows()) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)


@pytest.mark.skipif(os.name != "posix", reason="KernelOne guarded recovery is POSIX-qualified")
def test_forked_ticket_cannot_unlock_parent_namespace(tmp_path: Path) -> None:
    service, _owner, _claim = _bound_owner(tmp_path)
    code = """
import sys
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
service = TaskRuntimeService(sys.argv[1])
print('READY', flush=True)
with service._board.transaction(), service._board._mutation_guard():
    print('ACQUIRED', flush=True)
"""
    contender = None
    try:
        with service._board.transaction(), service._board._mutation_guard() as guard:
            child_pid = os.fork()
            if child_pid == 0:
                try:
                    try:
                        guard.validate(str(service._board.tasks_dir))
                    except RuntimeError:
                        pass
                    else:
                        os._exit(2)
                    guard.ticket.close()
                    os._exit(0)
                except (AssertionError, OSError, RuntimeError, ValueError):
                    os._exit(3)
            _waited, status = os.waitpid(child_pid, 0)
            assert os.waitstatus_to_exitcode(status) == 0
            guard.validate(str(service._board.tasks_dir))
            contender = subprocess.Popen(
                [sys.executable, "-c", code, str(tmp_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            assert contender.stdout is not None and contender.stdout.readline().strip() == "READY"
            readable, _, _ = select.select([contender.stdout], [], [], 0.15)
            assert not readable, "child ticket release unlocked the parent's namespace"
        output, errors = contender.communicate(timeout=20)
        assert contender.returncode == 0, errors
        assert output.strip() == "ACQUIRED"
    finally:
        if contender is not None:
            if contender.poll() is None:
                contender.kill()
            contender.communicate(timeout=10)


def test_recovery_notifies_ready_listeners_after_releasing_mutation_guards(tmp_path: Path) -> None:
    service, _owner, claim = _bound_owner(tmp_path)
    assert _settle_claimed_execution_attempt(service, claim, outcome="failed", summary="failed")["success"]
    created: list[int] = []

    def ready_listener() -> None:
        remove_listener()
        row = service.create_task_row(subject="Listener-created follow-up")
        created.append(int(row["id"]))

    remove_listener = service._board.add_ready_listener(ready_listener)
    _recover(service)
    assert len(created) == 1, "ready callback could not write while recovery retained the namespace lock"
    assert len(TaskRuntimeService(str(tmp_path)).list_observable_task_rows()) == 2
