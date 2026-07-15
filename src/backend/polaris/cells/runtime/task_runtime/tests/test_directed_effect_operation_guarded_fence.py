from __future__ import annotations

import ast
import inspect
from textwrap import dedent

from polaris.cells.runtime.task_runtime.internal import (
    directed_effect_operation as deo_internal,
    service as runtime_service_internal,
)
from polaris.cells.runtime.task_runtime.public import service as runtime_public_service


def _method_tree(name: str) -> ast.Module:
    method = getattr(deo_internal.DirectedEffectOperationRepository, name)
    return ast.parse(dedent(inspect.getsource(method)))


def _called_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.append(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            names.append(node.func.attr)
    return names


def _service_method_tree(name: str) -> ast.Module:
    method = getattr(runtime_service_internal.TaskRuntimeService, name)
    return ast.parse(dedent(inspect.getsource(method)))


def test_child_mutation_has_one_guarded_path_and_no_direct_append() -> None:
    calls = _called_names(_method_tree("_mutate"))

    assert "append_if_guarded_snapshot" in calls
    assert "append_fact_event" not in calls
    assert "_reconcile_operation_append" in calls
    assert calls.count("_guarded_attempt_failure") >= 4


def test_reprepare_policy_is_bounded_to_normative_drift_codes() -> None:
    assert deo_internal._MAX_GUARDED_ATTEMPTS == 3
    assert len(deo_internal._GUARDED_REPREPARE_DRIFT_CODES) == 2
    assert "target_snapshot_drift" in deo_internal._GUARDED_REPREPARE_DRIFT_CODES
    assert "guard_snapshot_drift" in deo_internal._GUARDED_REPREPARE_DRIFT_CODES
    constants = {
        node.value
        for node in ast.walk(_method_tree("_mutate"))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    required_evidence = {
        "guarded_reprepare_exhausted",
        "attempts_total",
        "reprepare_count",
        "drift_codes",
        "target_head_seq",
        "guard_head_seq",
        "operation_identity",
        "parent_binding_id",
    }
    assert constants >= required_evidence


def test_repository_has_no_disk_snapshot_side_effect_api() -> None:
    repository = deo_internal.DirectedEffectOperationRepository

    assert not hasattr(repository, "_persist_snapshot")
    assert not hasattr(repository, "_snapshot_path")


def test_parent_admission_has_one_service_owned_writer_and_no_recursive_validation() -> None:
    module_tree = ast.parse(inspect.getsource(deo_internal))
    append_owners: set[str] = set()
    for node in ast.walk(module_tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "append_fact_event" in _called_names(node):
            append_owners.add(node.name)

    repository_calls = _called_names(_method_tree("admit_parent_with_validated_authority"))
    public_calls = _called_names(
        ast.parse(dedent(inspect.getsource(runtime_public_service.admit_directed_effect_parent)))
    )
    service_calls = _called_names(_service_method_tree("admit_directed_effect_parent"))
    assert append_owners == {"admit_parent_with_validated_authority"}
    assert "validate_attempt" not in repository_calls
    assert "validate_execution_attempt" not in repository_calls
    assert "_get_session_lock" not in repository_calls
    assert "TaskRuntimeService" in public_calls
    assert "admit_directed_effect_parent" in public_calls
    assert "_get_session_lock" in service_calls
    assert "_session_file_lock_path" in service_calls


def test_all_active_to_inactive_writers_reach_the_common_pre_barrier() -> None:
    direct_methods = {
        "_settle_execution_attempt_locked",
        "fence_expired_factory_run_sessions",
        "_suspend_active_session_for_run_locked",
        "cancel_task_row_for_deduplication",
        "fail_task_row_from_role_adapter",
        "_reopen_with_execution_event",
    }
    for method_name in direct_methods:
        calls = _called_names(_service_method_tree(method_name))
        assert "_directed_effect_inactive_pre_barrier_locked" in calls

    rework_calls = _called_names(_service_method_tree("fail_task_row_after_rework_exhausted"))
    reopen_calls = _called_names(_service_method_tree("reopen_task_row"))
    assert "_reopen_with_execution_event" in rework_calls
    assert "_reopen_with_execution_event" in reopen_calls


def test_guarded_success_validates_all_public_receipt_fields_via_exact_replay() -> None:
    confirmation_tree = _method_tree("_confirmed_mutation_result")
    confirmation_constants = {
        node.value
        for node in ast.walk(confirmation_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert confirmation_constants >= {
        "event_id",
        "workspace",
        "stream",
        "storage_path",
        "appended_at",
        "appended_seq",
        "semantic_digest",
    }
    assert "append_if_guarded_snapshot" in _called_names(_method_tree("_confirm_guarded_append"))
    assert "append_if_guarded_snapshot" in _called_names(_method_tree("_reconcile_operation_append"))
