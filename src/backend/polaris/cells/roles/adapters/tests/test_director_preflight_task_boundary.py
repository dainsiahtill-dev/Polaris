"""Existing-scope preflight must seal TaskBoundary even when requires_fresh is false."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.adapters.internal.director.execute_method import (
    _phases_materialization as preflight_module,
)
from polaris.cells.roles.adapters.internal.director.execute_method._helpers import (
    MaterializationState,
)


def test_existing_scope_preflight_seals_boundary_when_requires_fresh_is_false(
    monkeypatch: Any,
) -> None:
    """L2-12 TASK-3-foundation rematerialize: files already exist.

    ``_task_requires_fresh_materialization`` is false for that Chinese
    split row, so preflight used to finalize TaskRuntime and skip the
    receipt-bound TaskBoundary append. Factory then failed
    ``task_boundary_verdict_missing`` after every owner row completed.
    """

    appended: list[dict[str, Any]] = []
    receipt_evidence = {
        "schema_version": "polaris.current_task_project_artifact_receipt_evidence.v1",
        "authority": "runtime.execution_broker.project_artifact_receipt.v1",
        "ok": True,
        "required_artifact_count": 1,
        "receipt_count": 1,
        "receipt_paths": ["src/__init__.py"],
        "receipt_refs": ["execution-broker://project-verification/artifact/abc"],
    }
    existing_evidence = {
        "ok": True,
        "reason": "declared_scope_present",
        "existing_paths": ["src/__init__.py"],
        "project_artifact_receipt_evidence": receipt_evidence,
    }

    monkeypatch.setattr(
        preflight_module,
        "_build_existing_workspace_task_evidence",
        lambda **kwargs: existing_evidence,
    )
    monkeypatch.setattr(
        preflight_module,
        "_attach_current_task_project_receipt_evidence",
        lambda *args, **kwargs: (existing_evidence, True),
    )
    monkeypatch.setattr(
        preflight_module,
        "_collect_materialization_quality_errors",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        preflight_module,
        "_can_accept_existing_workspace_scope",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        preflight_module,
        "_director_existing_scope_preflight_enabled",
        lambda context: True,
    )
    monkeypatch.setattr(
        preflight_module,
        "_emit_director_adapter_cognitive_receipt",
        lambda *args, **kwargs: {"receipt_type": "director_adapter_existing_scope_preflight"},
    )
    monkeypatch.setattr(
        preflight_module,
        "_finalize_claimed_execution",
        lambda *args, **kwargs: {
            "success": True,
            "identity": {"external_task_id": "TASK-3-foundation"},
        },
    )
    monkeypatch.setattr(
        preflight_module,
        "_task_completion_projection_from_context",
        lambda *args, **kwargs: {
            "schema_version": "polaris.task_completion_projection.v1",
            "task_id": "TASK-3-foundation",
            "project_id": "L2-12",
        },
    )

    def _capture_append(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        appended.append(dict(kwargs))
        return {"status": "completed_verified", "ok": True}

    monkeypatch.setattr(
        preflight_module,
        "_append_receipt_bound_preflight_task_boundary",
        _capture_append,
    )
    adapter = SimpleNamespace(workspace="/tmp/unused", _update_task_progress=lambda *a, **k: None)
    result = preflight_module._phase_existing_scope_preflight(
        adapter,
        board_claim_applied=True,
        task_execution_attempt_authority=None,
        context={},
        decision_signals=[],
        requires_fresh_materialization=False,
        run_id="director-preflight-1",
        target_task_id="135",
        task={"metadata": {"pm_task_id": "TASK-3-foundation", "target_files": ["src/__init__.py"]}},
        task_claim_session_id="session-1",
        workspace_name="unused",
        state=MaterializationState.from_locals({}, [], [], [], []),
    )

    assert result is not None
    assert result["success"] is True
    assert result["materialization_mode"] == "preflight_verified_existing_workspace_scope"
    assert len(appended) == 1
    assert appended[0]["target_task_id"] == "135"
    assert appended[0]["run_id"] == "director-preflight-1"


def test_existing_scope_preflight_seals_declared_paths_when_ce_owned_artifacts_empty(
    monkeypatch: Any,
) -> None:
    """L2-12 TASK-3-foundation: CE owned_artifacts=[] so receipts stay ok=False."""

    appended: list[dict[str, Any]] = []
    existing_evidence = {
        "ok": True,
        "reason": "declared_scope_present",
        "existing_paths": ["requirements.txt"],
        "project_artifact_receipt_evidence": {
            "schema_version": "polaris.current_task_project_artifact_receipt_evidence.v1",
            "authority": "runtime.execution_broker.project_artifact_receipt.v1",
            "ok": False,
            "required_artifact_count": 0,
            "receipt_count": 0,
            "receipt_paths": [],
            "receipt_refs": [],
        },
    }

    monkeypatch.setattr(
        preflight_module,
        "_build_existing_workspace_task_evidence",
        lambda **kwargs: existing_evidence,
    )
    monkeypatch.setattr(
        preflight_module,
        "_attach_current_task_project_receipt_evidence",
        lambda *args, **kwargs: (existing_evidence, False),
    )
    monkeypatch.setattr(
        preflight_module,
        "_collect_materialization_quality_errors",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        preflight_module,
        "_can_accept_existing_workspace_scope",
        lambda **kwargs: True,
    )
    monkeypatch.setattr(
        preflight_module,
        "_director_existing_scope_preflight_enabled",
        lambda context: True,
    )
    monkeypatch.setattr(
        preflight_module,
        "_emit_director_adapter_cognitive_receipt",
        lambda *args, **kwargs: {"receipt_type": "director_adapter_existing_scope_preflight"},
    )
    monkeypatch.setattr(
        preflight_module,
        "_finalize_claimed_execution",
        lambda *args, **kwargs: {
            "success": True,
            "identity": {"external_task_id": "TASK-3-foundation"},
        },
    )
    monkeypatch.setattr(
        preflight_module,
        "_task_completion_projection_from_context",
        lambda *args, **kwargs: {
            "schema_version": "polaris.task_completion_projection.v1",
            "task_id": "TASK-3-foundation",
            "project_id": "L2-12",
        },
    )
    monkeypatch.setattr(
        preflight_module,
        "_append_receipt_bound_preflight_task_boundary",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("receipt-bound path must not run")),
    )

    def _capture_declared(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args
        appended.append(dict(kwargs))
        return {"status": "completed_verified", "ok": True}

    monkeypatch.setattr(
        preflight_module,
        "_append_declared_scope_preflight_task_boundary",
        _capture_declared,
    )
    adapter = SimpleNamespace(workspace="/tmp/unused", _update_task_progress=lambda *a, **k: None)
    result = preflight_module._phase_existing_scope_preflight(
        adapter,
        board_claim_applied=True,
        task_execution_attempt_authority=None,
        context={},
        decision_signals=[],
        requires_fresh_materialization=False,
        run_id="director-preflight-foundation",
        target_task_id="144",
        task={"metadata": {"pm_task_id": "TASK-3-foundation", "target_files": ["requirements.txt"]}},
        task_claim_session_id="session-1",
        workspace_name="unused",
        state=MaterializationState.from_locals({}, [], [], [], []),
    )

    assert result is not None
    assert result["success"] is True
    assert len(appended) == 1
    assert appended[0]["existing_paths"] == ["requirements.txt"]
    assert appended[0]["target_task_id"] == "144"


def test_declared_scope_preflight_binds_content_hash_evidence_refs(
    tmp_path: Any,
    monkeypatch: Any,
) -> None:
    from polaris.cells.roles.adapters.internal.director.execute_method import _claim as claim_module

    requirements = tmp_path / "requirements.txt"
    requirements.write_text("radio==1.0\n", encoding="utf-8")
    captured: dict[str, Any] = {}

    class _Verdict:
        ok = True
        status = "completed_verified"

        def to_dict(self) -> dict[str, Any]:
            return {"status": "completed_verified", "ok": True, "evidence_refs": captured["evidence_refs"]}

    def _capture_verdict(**kwargs: Any) -> _Verdict:
        captured.update(kwargs)
        return _Verdict()

    appended: list[Any] = []
    monkeypatch.setattr(claim_module, "evaluate_task_boundary_verdict", _capture_verdict)
    monkeypatch.setattr(claim_module, "append_run_ledger_event", lambda command: appended.append(command))
    monkeypatch.setattr(
        claim_module,
        "_task_completion_projection_from_context",
        lambda *args, **kwargs: {"task_id": "TASK-3-foundation", "project_id": "L2-12"},
    )

    payload = claim_module._append_declared_scope_preflight_task_boundary(
        SimpleNamespace(workspace=str(tmp_path)),
        context={},
        target_task_id="144",
        run_id="director-preflight-foundation",
        finalize_result={"identity": {"external_task_id": "TASK-3-foundation"}},
        existing_paths=["requirements.txt"],
    )

    refs = captured["evidence_refs"]
    assert len(refs) == 1
    assert str(refs[0]).startswith("workspace-file-sha256:")
    assert str(refs[0]).endswith(":requirements.txt")
    assert payload["evidence_refs"] == refs
    assert appended
