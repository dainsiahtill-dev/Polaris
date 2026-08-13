"""Receipt-bound dependency artifact evidence for Director final requests."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director import dependency_artifact_evidence as evidence_module
from polaris.cells.roles.adapters.internal.director.dependency_artifact_evidence import (
    DirectorDependencyArtifactEvidenceError,
    TrustedDirectorDependencyArtifactSnapshotV2,
    build_current_task_project_artifact_receipt_evidence,
    build_director_dependency_artifact_snapshot,
    project_director_dependency_artifact_snapshot,
)


def _effect_receipt(path: str, *, suffix: str = "1") -> dict[str, Any]:
    receipt_hash = suffix * 64
    return {
        "status": "success",
        "result": {"file": path},
        "effect_receipt": {
            "schema_version": "roles.adapters.director_physical_effect_receipt.v2",
            "receipt_id": f"director-physical-effect-{suffix * 24}",
            "receipt_hash": receipt_hash,
            "receipt_binding_hash": "b" * 64,
            "physical_result_hash": "c" * 64,
            "target_state_hash": "d" * 64,
            "receipt_outcome": "succeeded",
            "authoritative": True,
            "durable": True,
        },
        "effect_receipt_commit": {
            "state": "RECEIPT_COMMITTED",
            "receipt_ref": f"director-physical-effect-{suffix * 24}",
            "receipt_hash": receipt_hash,
        },
    }


def _parent_row(
    *,
    task_id: int = 1,
    external_task_id: str = "TASK-1",
    paths: tuple[str, ...] = ("src/models/flavor.rs",),
) -> dict[str, Any]:
    return {
        "id": task_id,
        "status": "completed",
        "metadata": {
            "external_task_id": external_task_id,
            "adapter_result": {
                "new_files": list(paths),
                "modified_files": [],
                "write_tool_evidence": True,
                "primary_llm": {
                    "metadata": {
                        "batch_receipt": {
                            "raw_results": [
                                _effect_receipt(path, suffix=str(index + 1)) for index, path in enumerate(paths)
                            ]
                        }
                    }
                },
            },
        },
    }


def _child_task(dependencies: list[int | str]) -> dict[str, Any]:
    return {
        "id": 2,
        "metadata": {
            "external_task_id": "TASK-2",
            "resolved_depends_on_task_ids": dependencies,
        },
    }


def _resolver(rows: dict[str, dict[str, Any]]):
    def resolve(task_id: str) -> dict[str, Any] | None:
        return rows.get(str(task_id))

    return resolve


def test_snapshot_is_bound_to_parent_task_effect_receipt_and_exact_body(tmp_path: Path) -> None:
    source = tmp_path / "src" / "models" / "flavor.rs"
    source.parent.mkdir(parents=True)
    body = "pub enum FlavorProfile { Sweet, Sour }\n"
    source.write_text(body, encoding="utf-8")

    snapshot = build_director_dependency_artifact_snapshot(
        workspace=str(tmp_path),
        child_task=_child_task([1]),
        get_task=_resolver({"1": _parent_row()}),
    )

    assert type(snapshot) is TrustedDirectorDependencyArtifactSnapshotV2
    payload = snapshot.payload()
    assert payload["schema_version"] == "polaris.actual_sibling_exports.evidence.v2"
    assert payload["dependency_task_ids"] == ["1"]
    assert payload["covered_parent_task_ids"] == ["1"]
    assert payload["module_count"] == 1
    module = payload["modules"][0]
    assert module["parent_task_id"] == "1"
    assert module["parent_external_task_id"] == "TASK-1"
    assert module["path"] == "src/models/flavor.rs"
    assert module["body"] == body
    assert module["effect_receipt_id"].startswith("director-physical-effect-")
    assert len(module["effect_receipt_hash"]) == 64
    assert len(module["source_fact_hash"]) == 64
    assert len(module["sha256"]) == 64
    rendered = "\n".join(snapshot.message_lines())
    assert payload["snapshot_sha256"] in rendered
    assert body in rendered


def test_completion_adapter_result_tool_results_feed_sibling_snapshot(tmp_path: Path) -> None:
    """R129: completed parent adapter_result must carry receipt-bound tool_results.

    Live L1-01 TASK-2 failed with missing_required_refs=actual_sibling_exports
    because completion metadata only stored new_files/write_tool_evidence.
    """
    from polaris.cells.roles.adapters.internal.director.execute_method import (
        _attach_dependency_artifact_receipt_evidence,
        _project_dependency_artifact_tool_results,
    )

    path = "src/models/Firefly.ts"
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    body = "export class Firefly {}\n"
    source.write_text(body, encoding="utf-8")

    raw_tool_results = [
        {
            "tool_name": "write_file",
            "success": True,
            "status": "success",
            "result": {"file": path, "content": body},
            "effect_receipt": _effect_receipt(path, suffix="9")["effect_receipt"],
            "effect_receipt_commit": _effect_receipt(path, suffix="9")["effect_receipt_commit"],
        }
    ]
    projected = _project_dependency_artifact_tool_results(raw_tool_results)
    assert len(projected) == 1
    assert projected[0]["result"]["file"] == path
    assert "content" not in projected[0]["result"]

    adapter_result: dict[str, Any] = {
        "new_files": [path],
        "modified_files": [],
        "write_tool_evidence": True,
    }
    _attach_dependency_artifact_receipt_evidence(adapter_result, tool_results=raw_tool_results)
    assert adapter_result["tool_results"][0]["effect_receipt"]["authoritative"] is True

    parent = {
        "id": 1,
        "status": "completed",
        "metadata": {
            "external_task_id": "TASK-1",
            "adapter_result": adapter_result,
        },
    }
    snapshot = build_director_dependency_artifact_snapshot(
        workspace=str(tmp_path),
        child_task=_child_task([1]),
        get_task=_resolver({"1": parent}),
    )
    assert type(snapshot) is TrustedDirectorDependencyArtifactSnapshotV2
    assert snapshot.payload()["modules"][0]["body"] == body


def test_existing_scope_retry_uses_current_project_artifact_receipts(tmp_path: Path) -> None:
    """R48: retry preflight must reuse project receipts when no new tool write occurs."""

    path = "src/engine/rules.js"
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    body = "export const rules = ['dream', 'alchemy'];\n"
    source.write_text(body, encoding="utf-8")
    contract_hash = "a" * 64
    parent = {
        "id": 9,
        "status": "completed",
        "metadata": {
            "external_task_id": "TASK-1",
            "adapter_result": {
                "materialization_mode": "preflight_verified_existing_workspace_scope",
                "existing_contract_evidence": {
                    "ok": True,
                    "existing_paths": [path],
                },
                "new_files": [],
                "modified_files": [],
            },
            "task_completion_projection": {
                "schema_version": "polaris.task_completion_projection.v1",
                "task_id": "TASK-1",
                "project_id": "L1-02",
                "run_id": "factory-r48",
                "project_contract_hash": contract_hash,
                "owned_artifacts": [
                    {
                        "applicability": "required",
                        "obligation_id": "artifact-rules",
                        "owner_task_id": "TASK-1",
                        "path": path,
                    }
                ],
            },
        },
    }
    observed_queries: list[dict[str, str]] = []

    def lookup(query: Any) -> dict[str, str]:
        payload = dict(query)
        observed_queries.append(payload)
        return {
            **payload,
            "artifact_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "authority_revision": "b" * 64,
            "receipt_hash": "c" * 64,
            "receipt_ref": "execution-broker://project-verification/artifact/" + "c" * 64,
        }

    snapshot = build_director_dependency_artifact_snapshot(
        workspace=str(tmp_path),
        child_task=_child_task(["TASK-1"]),
        get_task=_resolver({"TASK-1": parent}),
        get_project_artifact_receipt=lookup,
    )

    assert type(snapshot) is TrustedDirectorDependencyArtifactSnapshotV2
    assert observed_queries == [
        {
            "workspace": str(tmp_path),
            "project_id": "L1-02",
            "run_id": "factory-r48",
            "completion_contract_hash": contract_hash,
            "obligation_id": "artifact-rules",
            "owner_task_id": "TASK-1",
            "path": path,
        }
    ]
    module = snapshot.payload()["modules"][0]
    assert module["body"] == body
    assert module["receipt_authority_source"] == "runtime.execution_broker.project_artifact_receipt.v1"
    assert module["effect_receipt_hash"] == "c" * 64
    assert module["physical_result_hash"] == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_current_task_retry_accepts_only_complete_byte_current_project_receipts(tmp_path: Path) -> None:
    paths = ("tests/product.test.js", "README.md")
    bodies = {
        "tests/product.test.js": "import test from 'node:test';\ntest('ok', () => {});\n",
        "README.md": "# Verified project\n",
    }
    for path, body in bodies.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    task = {
        "id": 13,
        "metadata": {"external_task_id": "TASK-2"},
        "task_completion_projection": {
            "task_id": "TASK-2",
            "project_id": "L1-02",
            "run_id": "factory-r48",
            "project_contract_hash": "a" * 64,
            "owned_artifacts": [
                {
                    "applicability": "required",
                    "obligation_id": f"artifact-{index}",
                    "owner_task_id": "TASK-2",
                    "path": path,
                }
                for index, path in enumerate(paths)
            ],
        },
    }

    def lookup(query: Any) -> dict[str, str]:
        payload = dict(query)
        receipt_hash = ("c" if payload["path"] == paths[0] else "d") * 64
        return {
            **payload,
            "artifact_hash": hashlib.sha256(bodies[payload["path"]].encode("utf-8")).hexdigest(),
            "authority_revision": "b" * 64,
            "receipt_hash": receipt_hash,
            "receipt_ref": "execution-broker://project-verification/artifact/" + receipt_hash,
        }

    evidence = build_current_task_project_artifact_receipt_evidence(
        task=task,
        task_id="13",
        workspace=str(tmp_path),
        lookup=lookup,
    )
    assert evidence["ok"] is True
    assert evidence["receipt_paths"] == sorted(paths)
    assert evidence["receipt_refs"] == [
        "execution-broker://project-verification/artifact/" + "c" * 64,
        "execution-broker://project-verification/artifact/" + "d" * 64,
    ]
    assert evidence["missing_or_stale_paths"] == []

    (tmp_path / "README.md").write_text("# stale bytes\n", encoding="utf-8")
    stale = build_current_task_project_artifact_receipt_evidence(
        task=task,
        task_id="13",
        workspace=str(tmp_path),
        lookup=lookup,
    )
    assert stale["ok"] is False
    assert stale["missing_or_stale_paths"] == ["README.md"]


def test_multiple_receipts_for_same_path_use_last_successful_write(tmp_path: Path) -> None:
    """R132: materialize then quality-repair rewrite must not fail sibling projection.

    Live r131 failed build_director_dependency_artifact_snapshot with
    dependency_artifact_receipt_conflict on package.json when TASK-1 wrote
    the same path twice with distinct effect receipts.
    """
    path = "package.json"
    source = tmp_path / path
    source.write_text('{"name":"final"}\n', encoding="utf-8")

    first = _effect_receipt(path, suffix="1")
    second = _effect_receipt(path, suffix="2")
    parent = {
        "id": 1,
        "status": "completed",
        "metadata": {
            "external_task_id": "TASK-1",
            "adapter_result": {
                "new_files": [path],
                "modified_files": [],
                "write_tool_evidence": True,
                "tool_results": [first, second],
            },
        },
    }
    snapshot = build_director_dependency_artifact_snapshot(
        workspace=str(tmp_path),
        child_task=_child_task([1]),
        get_task=_resolver({"1": parent}),
    )
    assert type(snapshot) is TrustedDirectorDependencyArtifactSnapshotV2
    module = snapshot.payload()["modules"][0]
    assert module["path"] == path
    # Last receipt (suffix 2) is authoritative for dependency consumers.
    assert module["effect_receipt_id"] == second["effect_receipt"]["receipt_id"]
    assert module["effect_receipt_hash"] == second["effect_receipt"]["receipt_hash"]
    assert module["body"] == '{"name":"final"}\n'


def test_partial_receipt_coverage_keeps_authoritative_siblings_and_marks_gap(tmp_path: Path) -> None:
    """One unreceipted modified file must not erase other committed sibling bodies."""

    trusted_path = "src/engine/simulation.ts"
    uncovered_path = "src/web.ts"
    trusted_body = "export const simulate = () => 1;\n"
    (tmp_path / "src" / "engine").mkdir(parents=True)
    (tmp_path / trusted_path).write_text(trusted_body, encoding="utf-8")
    (tmp_path / uncovered_path).write_text("export const web = true;\n", encoding="utf-8")
    parent = _parent_row(paths=(trusted_path, uncovered_path))
    parent["metadata"]["adapter_result"]["primary_llm"]["metadata"]["batch_receipt"]["raw_results"] = [
        _effect_receipt(trusted_path)
    ]

    snapshot = build_director_dependency_artifact_snapshot(
        workspace=str(tmp_path),
        child_task=_child_task([1]),
        get_task=_resolver({"1": parent}),
    )

    assert type(snapshot) is TrustedDirectorDependencyArtifactSnapshotV2
    payload = snapshot.payload()
    assert payload["receipt_coverage_complete"] is False
    assert [module["path"] for module in payload["modules"]] == [trusted_path]
    assert payload["uncovered_artifacts"] == [
        {
            "parent_task_id": "1",
            "path": uncovered_path,
            "reason": "committed_effect_receipt_missing",
        }
    ]
    rendered = "\n".join(snapshot.message_lines())
    assert trusted_body in rendered
    assert f"1:{uncovered_path}" in rendered
    assert "export const web = true" not in rendered


def test_snapshot_reads_only_receipt_listed_parent_files(tmp_path: Path) -> None:
    parent = tmp_path / "src" / "parent.py"
    parent.parent.mkdir()
    parent.write_text("PARENT = 1\n", encoding="utf-8")
    unrelated = tmp_path / "src" / "unrelated.py"
    unrelated.write_text("SECRET = 2\n", encoding="utf-8")

    snapshot = build_director_dependency_artifact_snapshot(
        workspace=str(tmp_path),
        child_task=_child_task([1]),
        get_task=_resolver({"1": _parent_row(paths=("src/parent.py",))}),
    )

    assert snapshot is not None
    payload = snapshot.payload()
    assert [module["path"] for module in payload["modules"]] == ["src/parent.py"]
    assert "unrelated.py" not in "\n".join(snapshot.message_lines())


def test_snapshot_uses_one_guarded_read_for_payload_and_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "src" / "parent.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    calls: list[str] = []
    original = evidence_module.read_guarded_regular_file_snapshot

    def counted(root: str, relative_path: str, max_bytes: int):
        calls.append(relative_path)
        return original(root, relative_path, max_bytes)

    monkeypatch.setattr(evidence_module, "read_guarded_regular_file_snapshot", counted)

    snapshot = build_director_dependency_artifact_snapshot(
        workspace=str(tmp_path),
        child_task=_child_task([1]),
        get_task=_resolver({"1": _parent_row(paths=("src/parent.py",))}),
    )

    assert snapshot is not None
    assert calls == ["src/parent.py"]
    assert "VALUE = 1" in "\n".join(snapshot.message_lines())


def test_projection_rejects_caller_preset_and_uses_only_trusted_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "src" / "parent.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    snapshot = build_director_dependency_artifact_snapshot(
        workspace=str(tmp_path),
        child_task=_child_task([1]),
        get_task=_resolver({"1": _parent_row(paths=("src/parent.py",))}),
    )
    context: dict[str, Any] = {
        "actual_sibling_exports": {"schema_version": "forged"},
        "metadata": {"actual_sibling_exports": {"schema_version": "forged"}},
    }

    project_director_dependency_artifact_snapshot(context, snapshot)

    assert snapshot is not None
    assert context["actual_sibling_exports"] == snapshot.payload()
    assert context["metadata"]["actual_sibling_exports"] == snapshot.payload()

    project_director_dependency_artifact_snapshot(context, None)
    assert "actual_sibling_exports" not in context
    assert "actual_sibling_exports" not in context["metadata"]


def test_projecting_none_wipes_sibling_exports_and_rebind_restores_them(tmp_path: Path) -> None:
    """L1-01 r122: quality-repair dialogue projected None and cleared sibling exports.

    Rebind must rebuild the trusted token from the child task + parent receipts so
    final-request coverage keeps actual_sibling_exports present.
    """
    from polaris.cells.roles.adapters.internal.director.adapter import DirectorAdapter
    from polaris.cells.roles.adapters.internal.director.dependency_artifact_evidence import (
        DIRECTOR_DEPENDENCY_ARTIFACT_SNAPSHOT_CONTEXT_KEY,
    )

    source = tmp_path / "src" / "models" / "types.ts"
    source.parent.mkdir(parents=True)
    source.write_text("export type GardenState = { tick: number };\n", encoding="utf-8")
    parent = _parent_row(paths=("src/models/types.ts",))
    child = _child_task([1])
    adapter = DirectorAdapter(str(tmp_path))
    adapter._get_task = lambda task_id: (
        parent
        if str(task_id) in {"1", "TASK-1"}
        # type: ignore[method-assign]
        else (child if str(task_id) in {"2", "TASK-2"} else None)
    )

    context: dict[str, Any] = {
        "task": child,
        "task_id": "2",
        "actual_sibling_exports": {"schema_version": "forged-stale"},
        "metadata": {"actual_sibling_exports": {"schema_version": "forged-stale"}},
    }
    # Simulate dialogue path without trusted token: project None wipes payload.
    project_director_dependency_artifact_snapshot(context, None)
    assert "actual_sibling_exports" not in context

    rebound = adapter._rebind_director_dependency_artifact_for_dialogue(context)

    assert type(rebound) is TrustedDirectorDependencyArtifactSnapshotV2
    assert type(context.get(DIRECTOR_DEPENDENCY_ARTIFACT_SNAPSHOT_CONTEXT_KEY)) is (
        TrustedDirectorDependencyArtifactSnapshotV2
    )
    payload = context["actual_sibling_exports"]
    assert payload["schema_version"] == "polaris.actual_sibling_exports.evidence.v2"
    assert payload["module_count"] == 1
    assert payload["modules"][0]["path"] == "src/models/types.ts"
    assert "GardenState" in payload["modules"][0]["body"]
    # Second rebind is a no-op when trusted token already present.
    again = adapter._rebind_director_dependency_artifact_for_dialogue(context)
    assert again is rebound


def test_rebind_restores_drained_parent_from_strict_ce_projection_and_project_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA-only recovery must reuse settled sibling facts after TaskRuntime drain.

    The bridge may not fabricate a completed parent row or scan the workspace.
    It resolves the same-run CE completion projection, then the dependency
    snapshot accepts only an exact execution-broker receipt whose hash matches
    the guarded file bytes.
    """
    from polaris.cells.roles.adapters.internal.director.adapter import (
        DirectorAdapter,
        _core as adapter_core,
    )

    path = "src/engine/forecast.py"
    body = "def mood_from_weather(code: str) -> str:\n    return 'bright' if code == 'sun' else 'calm'\n"
    source = tmp_path / path
    source.parent.mkdir(parents=True)
    source.write_text(body, encoding="utf-8")
    artifact_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    run_id = "factory-r49"
    contract_hash = "a" * 64
    projection = {
        "schema_version": "polaris.task_completion_projection.v1",
        "task_id": "TASK-2",
        "project_id": "L1-03",
        "run_id": run_id,
        "project_contract_hash": contract_hash,
        "owned_artifacts": [
            {
                "applicability": "required",
                "owner_task_id": "TASK-2",
                "obligation_id": "artifact-task-2-forecast",
                "path": path,
            }
        ],
    }
    child = {
        "id": "TASK-3",
        "metadata": {
            "external_task_id": "TASK-3",
            "factory_run_id": run_id,
            "depends_on": ["TASK-2"],
        },
    }
    adapter = DirectorAdapter(str(tmp_path))
    adapter._get_task = lambda _task_id: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        adapter_core,
        "get_blueprint_status",
        lambda query: SimpleNamespace(ok=True, blueprint_id="ce_TASK-2_r49"),
    )
    monkeypatch.setattr(
        adapter_core,
        "validate_director_handoff_from_payload",
        lambda workspace, payload, require_strict: {
            "allowed": True,
            "task_completion_projection": projection,
        },
    )

    def receipt_lookup(query: Mapping[str, str]) -> Mapping[str, Any] | None:
        return {
            **dict(query),
            "artifact_hash": artifact_hash,
            "authority_revision": "b" * 64,
            "receipt_hash": "c" * 64,
            "receipt_ref": "project-artifact-receipt-r49",
        }

    monkeypatch.setattr(adapter_core, "query_project_artifact_receipt_payload", receipt_lookup)
    context: dict[str, Any] = {"task": child, "task_id": "TASK-3"}

    rebound = adapter._rebind_director_dependency_artifact_for_dialogue(context)

    assert type(rebound) is TrustedDirectorDependencyArtifactSnapshotV2
    payload = context["actual_sibling_exports"]
    assert payload["dependency_task_ids"] == ["TASK-2"]
    assert payload["covered_parent_task_ids"] == ["TASK-2"]
    assert payload["modules"][0]["path"] == path
    assert payload["modules"][0]["body"] == body
    assert payload["modules"][0]["receipt_authority_source"] == ("runtime.execution_broker.project_artifact_receipt.v1")


@pytest.mark.parametrize(
    ("workspace", "expected_code"),
    [
        ("", "dependency_artifact_workspace_invalid"),
        ("relative/workspace", "dependency_artifact_workspace_invalid"),
    ],
)
def test_snapshot_rejects_empty_or_relative_workspace(workspace: str, expected_code: str) -> None:
    with pytest.raises(DirectorDependencyArtifactEvidenceError) as exc_info:
        build_director_dependency_artifact_snapshot(
            workspace=workspace,
            child_task=_child_task([1]),
            get_task=_resolver({"1": _parent_row()}),
        )
    assert exc_info.value.code == expected_code


def test_snapshot_rejects_symlink_parent_artifact(tmp_path: Path) -> None:
    real = tmp_path / "real.py"
    real.write_text("VALUE = 1\n", encoding="utf-8")
    source = tmp_path / "src" / "parent.py"
    source.parent.mkdir()
    source.symlink_to(real)

    with pytest.raises(DirectorDependencyArtifactEvidenceError) as exc_info:
        build_director_dependency_artifact_snapshot(
            workspace=str(tmp_path),
            child_task=_child_task([1]),
            get_task=_resolver({"1": _parent_row(paths=("src/parent.py",))}),
        )

    assert exc_info.value.code == "dependency_artifact_guarded_read_failed"


def test_snapshot_rejects_oversized_parent_artifact(tmp_path: Path) -> None:
    source = tmp_path / "src" / "parent.py"
    source.parent.mkdir()
    source.write_text("x" * (64 * 1024 + 1), encoding="utf-8")

    with pytest.raises(DirectorDependencyArtifactEvidenceError) as exc_info:
        build_director_dependency_artifact_snapshot(
            workspace=str(tmp_path),
            child_task=_child_task([1]),
            get_task=_resolver({"1": _parent_row(paths=("src/parent.py",))}),
        )

    assert exc_info.value.code == "dependency_artifact_guarded_read_failed"


def test_snapshot_rejects_parent_without_committed_receipt(tmp_path: Path) -> None:
    source = tmp_path / "src" / "parent.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    parent = _parent_row(paths=("src/parent.py",))
    del parent["metadata"]["adapter_result"]["primary_llm"]["metadata"]["batch_receipt"]["raw_results"][0][
        "effect_receipt_commit"
    ]

    with pytest.raises(DirectorDependencyArtifactEvidenceError) as exc_info:
        build_director_dependency_artifact_snapshot(
            workspace=str(tmp_path),
            child_task=_child_task([1]),
            get_task=_resolver({"1": parent}),
        )

    assert exc_info.value.code == "dependency_artifact_receipt_missing"


def test_snapshot_rejects_resolver_returning_wrong_parent(tmp_path: Path) -> None:
    source = tmp_path / "src" / "parent.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(DirectorDependencyArtifactEvidenceError) as exc_info:
        build_director_dependency_artifact_snapshot(
            workspace=str(tmp_path),
            child_task=_child_task([2]),
            get_task=_resolver({"2": _parent_row(task_id=1, paths=("src/parent.py",))}),
        )

    assert exc_info.value.code == "dependency_artifact_parent_identity_mismatch"
