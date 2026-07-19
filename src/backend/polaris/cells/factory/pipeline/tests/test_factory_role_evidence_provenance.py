"""A009B2a2 strict PM/CE artifact provenance and immutable snapshot tests."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import polaris.kernelone.fs.guarded_regular_file_snapshot as snapshot_module
import pytest
from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.factory.pipeline.internal import factory_stage_artifact_bindings as bindings_module
from polaris.cells.factory.pipeline.internal.factory_stage_artifact_bindings import (
    FactoryStageArtifactBindingError,
    FactoryStageArtifactBindingsV1,
    build_chief_engineer_stage_artifact_bindings,
    build_pm_stage_artifact_bindings,
    revalidate_chief_engineer_stage_artifact_binding,
    revalidate_pm_stage_artifact_binding,
)
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.internal.factory_store import (
    FactoryArtifactSnapshotError,
    FactoryStore,
)
from polaris.kernelone.storage import resolve_storage_roots


def _pm_task(task_id: str = "TASK-1", target_files: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Implement {task_id}",
        "goal": "Ship one bounded implementation task",
        "scope": "Factory provenance regression",
        "steps": ["Implement", "Verify"],
        "acceptance": ["Declared targets pass their checks."],
        "depends_on": [],
        "target_files": list(target_files or ["src/main.py"]),
        "metadata": {"priority": "high"},
    }


def _pm_document(tasks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "pm.plan_artifact.v1",
        "generated_at": "2026-07-18T00:00:00+00:00",
        "source": "pm_adapter_v2",
        "directive": "[PM planning directive redacted]",
        "quality_gate": {
            "score": 100,
            "critical_issue_count": 0,
            "summary": "clear",
            "signals": [],
        },
        "tasks": deepcopy(tasks if tasks is not None else [_pm_task()]),
    }


def _write_bytes(root: Path, logical_path: str, raw: bytes) -> Path:
    target = root / logical_path.removeprefix("runtime/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return target


def _write_json(root: Path, logical_path: str, payload: dict[str, Any], *, indent: int | None = 2) -> bytes:
    raw = (json.dumps(payload, ensure_ascii=False, indent=indent) + "\n").encode("utf-8")
    _write_bytes(root, logical_path, raw)
    return raw


def _pm_event(run_id: str, binding: FactoryStageArtifactBindingsV1) -> dict[str, Any]:
    return {
        "type": "stage_completed",
        "stage": "pm_planning",
        "run_id": run_id,
        "event_id": "evt-pm-complete",
        "result": {"stage": "pm_planning", "status": "success"},
        "chain_schema_version": "factory.event_chain.v1",
        "chain_sequence": 2,
        "chain_previous_hash": "0" * 64,
        "chain_event_hash": "a" * 64,
        "stage_artifact_bindings": binding.to_record(),
    }


def _blueprint(run_id: str, task: dict[str, Any], blueprint_id: str = "bp-TASK-1") -> dict[str, Any]:
    task_id = str(task["id"])
    payload: dict[str, Any] = {
        "schema_version": "chief_engineer.blueprint.v1",
        "role": "chief_engineer",
        "blueprint_id": blueprint_id,
        "task_id": task_id,
        "run_id": run_id,
        "title": "Blueprint",
        "objective": "Implement exact PM task",
        "summary": "Bounded blueprint",
        "status": "generated",
        "source": "chief_engineer.blueprint",
        "target_files": list(task["target_files"]),
        "pm_task": deepcopy(task),
        "pm_contract_hash": stable_hash(task),
    }
    payload["blueprint_hash"] = stable_hash(payload)
    return payload


def _review_row(task: dict[str, Any], blueprint_id: str = "bp-TASK-1") -> dict[str, Any]:
    return {
        "task_id": task["id"],
        "status": "generated",
        "blueprint_id": blueprint_id,
        "blueprint_path": f"runtime/blueprints/{blueprint_id}.json",
        "summary": "Bounded blueprint",
        "recommendations": [],
        "risks": [],
        "handoff_ready": True,
        "handoff_decision": {},
        "llm_evidence": {},
        "llm_blueprint_consumed": True,
        "llm_blueprint_keys": [],
        "portfolio_reference": {},
    }


def _review_document(run_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "factory.chief_engineer_review.v2",
        "generated_at": "2026-07-18T00:00:01+00:00",
        "source": "factory_stage_executor.chief_engineer_portfolio_review",
        "factory_run_id": run_id,
        "task_plan": "tasks/plan.json",
        "total_tasks": len(rows),
        "generated_blueprints": len(rows),
        "llm_call_count": len(rows),
        "portfolio": {},
        "project_interface_contract": {},
        "blueprints": deepcopy(rows),
        "signals": [],
    }


def test_factory_store_snapshot_is_content_addressed_reusable_and_strict(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    store = FactoryStore(runtime_root)
    raw = b'{"schema_version":"pm.plan_artifact.v1"}\n'
    raw_sha256 = hashlib.sha256(raw).hexdigest()

    first = store.persist_stage_artifact_snapshot("run-1", raw_sha256, raw)
    second = store.persist_stage_artifact_snapshot("run-1", raw_sha256, raw)

    assert first == second
    assert first.content == raw
    assert first.logical_ref == (f"runtime/run-1/artifacts/stage-bindings/sha256/{raw_sha256[:2]}/{raw_sha256}.json")
    assert store.read_stage_artifact_snapshot("run-1", first.logical_ref, raw_sha256, len(raw)) == first

    with pytest.raises(FactoryArtifactSnapshotError, match="factory_artifact_snapshot_run_id_invalid"):
        store.persist_stage_artifact_snapshot("../run-1", raw_sha256, raw)
    with pytest.raises(FactoryArtifactSnapshotError, match="factory_artifact_snapshot_hash_invalid"):
        store.persist_stage_artifact_snapshot("run-1", "A" * 64, raw)
    with pytest.raises(FactoryArtifactSnapshotError, match="factory_artifact_snapshot_hash_mismatch"):
        store.persist_stage_artifact_snapshot("run-1", "b" * 64, raw)
    with pytest.raises(FactoryArtifactSnapshotError, match="factory_artifact_snapshot_ref_mismatch"):
        store.read_stage_artifact_snapshot("run-2", first.logical_ref, raw_sha256, len(raw))


def test_factory_store_snapshot_collision_never_overwrites(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    store = FactoryStore(runtime_root)
    raw = b'{"value":"expected"}\n'
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    logical_ref = store.stage_artifact_snapshot_ref("run-1", raw_sha256)
    target = runtime_root / logical_ref.removeprefix("runtime/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'{"value":"collision"}\n')

    with pytest.raises(FactoryArtifactSnapshotError) as exc_info:
        store.persist_stage_artifact_snapshot("run-1", raw_sha256, raw)

    assert exc_info.value.code == "factory_artifact_snapshot_hash_collision"
    assert target.read_bytes() == b'{"value":"collision"}\n'


def test_pm_binding_hashes_exact_bytes_but_canonicalizes_json(tmp_path: Path) -> None:
    document = _pm_document()
    roots = [tmp_path / "runtime-a", tmp_path / "runtime-b"]
    raw_a = _write_json(roots[0], "tasks/plan.json", document, indent=2)
    raw_b = _write_json(roots[1], "tasks/plan.json", dict(reversed(tuple(document.items()))), indent=None)

    binding_a = build_pm_stage_artifact_bindings(
        factory_store=FactoryStore(roots[0]), source_root=roots[0], factory_run_id="run-1"
    )
    binding_b = build_pm_stage_artifact_bindings(
        factory_store=FactoryStore(roots[1]), source_root=roots[1], factory_run_id="run-1"
    )
    item_a = binding_a.items[0]
    item_b = binding_b.items[0]

    assert raw_a != raw_b
    assert item_a.raw_sha256 != item_b.raw_sha256
    assert item_a.canonical_json_sha256 == item_b.canonical_json_sha256
    assert item_a.task_id_vector_sha256 == item_b.task_id_vector_sha256
    assert item_a.target_files_projection_sha256 == item_b.target_files_projection_sha256
    assert binding_a.binding_vector_sha256 != binding_b.binding_vector_sha256


def test_pm_binding_semantic_change_updates_only_affected_frozen_hashes(tmp_path: Path) -> None:
    roots = [tmp_path / "runtime-a", tmp_path / "runtime-b"]
    _write_json(roots[0], "tasks/plan.json", _pm_document([_pm_task(target_files=["src/a.py"])]))
    _write_json(roots[1], "tasks/plan.json", _pm_document([_pm_task(target_files=["src/b.py"])]))

    first = build_pm_stage_artifact_bindings(
        factory_store=FactoryStore(roots[0]), source_root=roots[0], factory_run_id="run-1"
    ).items[0]
    second = build_pm_stage_artifact_bindings(
        factory_store=FactoryStore(roots[1]), source_root=roots[1], factory_run_id="run-1"
    ).items[0]

    assert first.task_id_vector_sha256 == second.task_id_vector_sha256
    assert first.canonical_json_sha256 != second.canonical_json_sha256
    assert first.target_files_projection_sha256 != second.target_files_projection_sha256


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"\xff", "factory_stage_artifact_invalid_utf8"),
        (b'{"schema_version":"pm.plan_artifact.v1","schema_version":"x"}', "factory_stage_artifact_duplicate_key"),
        (b'{"value":NaN}', "factory_stage_artifact_invalid_json_constant"),
        (b"[]", "factory_stage_artifact_root_not_object"),
        (
            json.dumps({**_pm_document(), "extra": True}).encode("utf-8"),
            "factory_stage_artifact_pm_document_fields_invalid",
        ),
        (
            json.dumps({**_pm_document(), "tasks": [{"task_id": "TASK-1", "target_files": ["src/a.py"]}]}).encode(
                "utf-8"
            ),
            "factory_stage_artifact_pm_task_id_invalid",
        ),
        (
            json.dumps(
                _pm_document([{**_pm_task(), "task_id": "TASK-ALIAS"}]),
                ensure_ascii=False,
            ).encode("utf-8"),
            "factory_stage_artifact_pm_task_id_invalid",
        ),
        (
            json.dumps(_pm_document([_pm_task(target_files=["../escape.py"])]), ensure_ascii=False).encode("utf-8"),
            "factory_stage_artifact_logical_path_invalid",
        ),
    ],
)
def test_pm_binding_rejects_strict_json_and_contract_attacks(tmp_path: Path, raw: bytes, code: str) -> None:
    runtime_root = tmp_path / "runtime"
    _write_bytes(runtime_root, "tasks/plan.json", raw)

    with pytest.raises(FactoryStageArtifactBindingError) as exc_info:
        build_pm_stage_artifact_bindings(
            factory_store=FactoryStore(runtime_root),
            source_root=runtime_root,
            factory_run_id="run-1",
        )

    assert exc_info.value.code == code


@pytest.mark.parametrize(
    "document",
    [
        _pm_document([_pm_task(f"TASK-{index}") for index in range(513)]),
        _pm_document([_pm_task(target_files=[f"src/{index}.py" for index in range(513)])]),
        _pm_document([_pm_task("T" * 257)]),
        _pm_document([_pm_task(target_files=["a" * 1025])]),
    ],
)
def test_pm_binding_rejects_frozen_count_and_utf8_bounds(tmp_path: Path, document: dict[str, Any]) -> None:
    runtime_root = tmp_path / "runtime"
    _write_json(runtime_root, "tasks/plan.json", document)

    with pytest.raises(FactoryStageArtifactBindingError):
        build_pm_stage_artifact_bindings(
            factory_store=FactoryStore(runtime_root), source_root=runtime_root, factory_run_id="run-1"
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX source-object semantics")
@pytest.mark.parametrize("source_kind", ["symlink", "hardlink", "fifo"])
def test_pm_binding_rejects_unsafe_source_objects(tmp_path: Path, source_kind: str) -> None:
    runtime_root = tmp_path / "runtime"
    tasks_dir = runtime_root / "tasks"
    tasks_dir.mkdir(parents=True)
    target = tasks_dir / "plan.json"
    source = tasks_dir / "source.json"
    source.write_text(json.dumps(_pm_document()), encoding="utf-8")
    if source_kind == "symlink":
        target.symlink_to(source)
    elif source_kind == "hardlink":
        os.link(source, target)
    else:
        os.mkfifo(target)

    with pytest.raises(FactoryStageArtifactBindingError) as exc_info:
        build_pm_stage_artifact_bindings(
            factory_store=FactoryStore(runtime_root), source_root=runtime_root, factory_run_id="run-1"
        )

    assert exc_info.value.code == "factory_stage_artifact_source_unsafe"


def test_pm_binding_rejects_atomic_source_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime_root = tmp_path / "runtime"
    target = _write_bytes(runtime_root, "tasks/plan.json", json.dumps(_pm_document()).encode("utf-8"))
    replacement = target.with_name("replacement.json")
    replacement.write_bytes(json.dumps(_pm_document()).encode("utf-8"))
    real_read = snapshot_module.os.read
    replaced = False

    def replace_after_first_read(fd: int, amount: int) -> bytes:
        nonlocal replaced
        chunk = real_read(fd, amount)
        if chunk and not replaced:
            replaced = True
            os.replace(replacement, target)
        return chunk

    monkeypatch.setattr(snapshot_module.os, "read", replace_after_first_read)

    with pytest.raises(FactoryStageArtifactBindingError) as exc_info:
        build_pm_stage_artifact_bindings(
            factory_store=FactoryStore(runtime_root), source_root=runtime_root, factory_run_id="run-1"
        )

    assert exc_info.value.code == "factory_stage_artifact_source_unsafe"


def test_ce_binding_uses_exact_pm_event_public_query_and_manifest_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    run_id = "run-1"
    tasks = [_pm_task("TASK-2", ["src/b.py"]), _pm_task("TASK-1", ["src/a.py"])]
    _write_json(runtime_root, "tasks/plan.json", _pm_document(tasks))
    store = FactoryStore(runtime_root)
    pm_binding = build_pm_stage_artifact_bindings(factory_store=store, source_root=runtime_root, factory_run_id=run_id)
    rows = [_review_row(tasks[1], "bp-1"), _review_row(tasks[0], "bp-2")]
    _write_json(runtime_root, f"runtime/state/blueprints/{run_id}.review.json", _review_document(run_id, rows))
    _write_json(runtime_root, "runtime/blueprints/bp-1.json", _blueprint(run_id, tasks[1], "bp-1"))
    _write_json(runtime_root, "runtime/blueprints/bp-2.json", _blueprint(run_id, tasks[0], "bp-2"))
    calls: list[Any] = []
    real_query = bindings_module.query_blueprint_provenance

    def recording_query(query: Any) -> Any:
        calls.append(query)
        return real_query(query)

    monkeypatch.setattr(bindings_module, "query_blueprint_provenance", recording_query)
    ce_binding = build_chief_engineer_stage_artifact_bindings(
        factory_store=store,
        source_root=runtime_root,
        factory_run_id=run_id,
        pm_stage_event=_pm_event(run_id, pm_binding),
    )

    assert [item.kind for item in ce_binding.items] == [
        "pm_stage_event",
        "ce_review_manifest",
        "ce_blueprint",
        "ce_blueprint",
    ]
    assert [item.task_id for item in ce_binding.items[2:]] == ["TASK-1", "TASK-2"]
    assert [item.ordinal for item in ce_binding.items[2:]] == [0, 1]
    assert len(calls) == 2
    assert dict(calls[0].expected_pm_task) == tasks[1]
    assert dict(calls[1].expected_pm_task) == tasks[0]
    assert ce_binding.items[0].pm_raw_sha256 == pm_binding.items[0].raw_sha256
    assert FactoryStageArtifactBindingsV1.from_record(ce_binding.to_record()) == ce_binding


def test_ce_binding_requires_manifest_task_set_to_equal_committed_pm_tasks(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    run_id = "run-1"
    tasks = [_pm_task("TASK-1", ["src/a.py"]), _pm_task("TASK-2", ["src/b.py"])]
    _write_json(runtime_root, "tasks/plan.json", _pm_document(tasks))
    store = FactoryStore(runtime_root)
    pm_binding = build_pm_stage_artifact_bindings(
        factory_store=store,
        source_root=runtime_root,
        factory_run_id=run_id,
    )
    row = _review_row(tasks[0], "bp-1")
    _write_json(
        runtime_root,
        f"runtime/state/blueprints/{run_id}.review.json",
        _review_document(run_id, [row]),
    )
    _write_json(runtime_root, "runtime/blueprints/bp-1.json", _blueprint(run_id, tasks[0], "bp-1"))

    with pytest.raises(FactoryStageArtifactBindingError, match="task_set"):
        build_chief_engineer_stage_artifact_bindings(
            factory_store=store,
            source_root=runtime_root,
            factory_run_id=run_id,
            pm_stage_event=_pm_event(run_id, pm_binding),
        )


@pytest.mark.parametrize(("status", "handoff_ready"), [("failed", True), ("generated", False)])
def test_ce_binding_requires_generated_handoff_ready_rows(
    tmp_path: Path,
    status: str,
    handoff_ready: bool,
) -> None:
    runtime_root = tmp_path / "runtime"
    run_id = "run-1"
    task = _pm_task()
    _write_json(runtime_root, "tasks/plan.json", _pm_document([task]))
    store = FactoryStore(runtime_root)
    pm_binding = build_pm_stage_artifact_bindings(
        factory_store=store,
        source_root=runtime_root,
        factory_run_id=run_id,
    )
    row = _review_row(task)
    row["status"] = status
    row["handoff_ready"] = handoff_ready
    _write_json(
        runtime_root,
        f"runtime/state/blueprints/{run_id}.review.json",
        _review_document(run_id, [row]),
    )
    _write_json(runtime_root, "runtime/blueprints/bp-TASK-1.json", _blueprint(run_id, task))

    with pytest.raises(FactoryStageArtifactBindingError, match="review_row"):
        build_chief_engineer_stage_artifact_bindings(
            factory_store=store,
            source_root=runtime_root,
            factory_run_id=run_id,
            pm_stage_event=_pm_event(run_id, pm_binding),
        )


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "extra", "cross_run", "alias"])
def test_ce_binding_rejects_manifest_and_identity_attacks(tmp_path: Path, mutation: str) -> None:
    runtime_root = tmp_path / "runtime"
    run_id = "run-1"
    task = _pm_task()
    _write_json(runtime_root, "tasks/plan.json", _pm_document([task]))
    store = FactoryStore(runtime_root)
    pm_binding = build_pm_stage_artifact_bindings(factory_store=store, source_root=runtime_root, factory_run_id=run_id)
    row = _review_row(task)
    review = _review_document(run_id, [row])
    blueprint = _blueprint(run_id, task)
    if mutation == "duplicate":
        review["blueprints"] = [row, deepcopy(row)]
        review["generated_blueprints"] = 2
        review["total_tasks"] = 2
    elif mutation == "missing":
        del review["signals"]
    elif mutation == "extra":
        review["unexpected"] = True
    elif mutation == "cross_run":
        blueprint["run_id"] = "run-2"
        blueprint["blueprint_hash"] = stable_hash(
            {key: value for key, value in blueprint.items() if key != "blueprint_hash"}
        )
    else:
        review["blueprints"][0]["task_id"] = "task-1"
    _write_json(runtime_root, f"runtime/state/blueprints/{run_id}.review.json", review)
    _write_json(runtime_root, "runtime/blueprints/bp-TASK-1.json", blueprint)

    with pytest.raises(FactoryStageArtifactBindingError):
        build_chief_engineer_stage_artifact_bindings(
            factory_store=store,
            source_root=runtime_root,
            factory_run_id=run_id,
            pm_stage_event=_pm_event(run_id, pm_binding),
        )


def test_orphan_snapshot_has_no_authority_when_later_ce_binding_fails(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    run_id = "run-1"
    task = _pm_task()
    _write_json(runtime_root, "tasks/plan.json", _pm_document([task]))
    store = FactoryStore(runtime_root)
    pm_binding = build_pm_stage_artifact_bindings(factory_store=store, source_root=runtime_root, factory_run_id=run_id)
    review = _review_document(run_id, [_review_row(task)])
    _write_json(runtime_root, f"runtime/state/blueprints/{run_id}.review.json", review)
    _write_bytes(runtime_root, "runtime/blueprints/bp-TASK-1.json", b'{"schema_version":"broken"}')

    with pytest.raises(FactoryStageArtifactBindingError):
        build_chief_engineer_stage_artifact_bindings(
            factory_store=store,
            source_root=runtime_root,
            factory_run_id=run_id,
            pm_stage_event=_pm_event(run_id, pm_binding),
        )

    snapshot_files = list((runtime_root / run_id / "artifacts" / "stage-bindings").rglob("*.json"))
    assert len(snapshot_files) >= 2
    assert list((runtime_root / run_id).glob("events/*.jsonl")) == []


def test_binding_records_and_stage_context_do_not_alias_mutable_inputs(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    document = _pm_document()
    original = deepcopy(document)
    _write_json(runtime_root, "tasks/plan.json", document)
    binding = build_pm_stage_artifact_bindings(
        factory_store=FactoryStore(runtime_root), source_root=runtime_root, factory_run_id="run-1"
    )
    record = binding.to_record()
    reconstructed = FactoryStageArtifactBindingsV1.from_record(record)
    record["items"][0]["raw_sha256"] = "f" * 64
    document["tasks"][0]["target_files"].append("src/changed.py")

    assert reconstructed == binding
    assert original["tasks"][0]["target_files"] == ["src/main.py"]

    executor = OrchestrationStageExecutor(tmp_path / "workspace")
    task = _pm_task()
    context = executor._task_blueprint_context(task, run_id="run-1", index=1)
    context["pm_task_contract"]["target_files"].append("src/context-only.py")
    assert task["target_files"] == ["src/main.py"]


@pytest.mark.parametrize("mutation", ["extra", "cross_run_ref", "vector_hash"])
def test_binding_record_rejects_unknown_cross_run_and_hash_tampering(
    tmp_path: Path,
    mutation: str,
) -> None:
    runtime_root = tmp_path / "runtime"
    _write_json(runtime_root, "tasks/plan.json", _pm_document())
    record = build_pm_stage_artifact_bindings(
        factory_store=FactoryStore(runtime_root), source_root=runtime_root, factory_run_id="run-1"
    ).to_record()
    if mutation == "extra":
        record["items"][0]["unexpected"] = True
    elif mutation == "cross_run_ref":
        record["items"][0]["immutable_snapshot_ref"] = str(record["items"][0]["immutable_snapshot_ref"]).replace(
            "runtime/run-1/", "runtime/run-2/"
        )
    else:
        record["binding_vector_sha256"] = "f" * 64

    with pytest.raises(FactoryStageArtifactBindingError):
        FactoryStageArtifactBindingsV1.from_record(record)


def test_binding_create_reuses_strict_invariants_without_recursive_roundtrip(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    _write_json(runtime_root, "tasks/plan.json", _pm_document())
    binding = build_pm_stage_artifact_bindings(
        factory_store=FactoryStore(runtime_root),
        source_root=runtime_root,
        factory_run_id="run-1",
    )

    assert FactoryStageArtifactBindingsV1.from_record(binding.to_record()) == binding
    with pytest.raises(FactoryStageArtifactBindingError):
        FactoryStageArtifactBindingsV1.create(
            factory_run_id="run-1",
            stage="pm_planning",
            items=(),
        )
    with pytest.raises(FactoryStageArtifactBindingError):
        FactoryStageArtifactBindingsV1.create(
            factory_run_id="run-1",
            stage="pm_planning",
            items=(replace(binding.items[0], logical_source_path="tasks/alias.json"),),
        )


@pytest.mark.parametrize("category_c_path", ["src/control-\x01.py", "src/c1-\x85.py", "src/format-\u200b.py"])
def test_pm_binding_rejects_all_unicode_category_c_paths(tmp_path: Path, category_c_path: str) -> None:
    runtime_root = tmp_path / "runtime"
    _write_json(
        runtime_root,
        "tasks/plan.json",
        _pm_document([_pm_task(target_files=[category_c_path])]),
    )

    with pytest.raises(FactoryStageArtifactBindingError, match="logical_path_invalid"):
        build_pm_stage_artifact_bindings(
            factory_store=FactoryStore(runtime_root),
            source_root=runtime_root,
            factory_run_id="run-1",
        )


def test_pm_validation_normalization_is_persisted_and_idempotent(tmp_path: Path) -> None:
    executor = OrchestrationStageExecutor(tmp_path / "workspace")
    tasks = [
        {
            **_pm_task("TASK-1", ["src/main.py"]),
            "acceptance": ["Run pytest", "Build succeeds"],
        },
        {
            **_pm_task("TASK-2", ["tests/test_main.py"]),
            "depends_on": ["TASK-1"],
        },
    ]
    document = _pm_document(tasks)
    executor._write_json_artifact("tasks/plan.json", document)

    first = executor._persist_normalized_pm_plan_validation_contracts("tasks/plan.json")
    first_bytes = executor._artifact_path("tasks/plan.json").read_bytes()
    second = executor._persist_normalized_pm_plan_validation_contracts("tasks/plan.json")
    second_bytes = executor._artifact_path("tasks/plan.json").read_bytes()
    persisted = json.loads(second_bytes.decode("utf-8"))

    assert first["changed"] is True
    assert second["changed"] is False
    assert first_bytes == second_bytes
    assert persisted["tasks"] == executor._load_pm_plan_tasks("tasks/plan.json", include_mirrors=False)
    assert "pytest" not in " ".join(persisted["tasks"][0]["acceptance"]).lower()


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"pm.plan_artifact.v1","schema_version":"duplicate"}',
        b'{"value":NaN}',
        b"[]",
    ],
)
def test_pm_validation_normalization_strict_parse_failure_never_mutates_bytes(
    tmp_path: Path,
    raw: bytes,
) -> None:
    executor = OrchestrationStageExecutor(tmp_path / "workspace")
    runtime_root = Path(resolve_storage_roots(str(executor.workspace)).runtime_root)
    plan_path = _write_bytes(runtime_root, "tasks/plan.json", raw)

    with pytest.raises(FactoryStageArtifactBindingError):
        executor._persist_normalized_pm_plan_validation_contracts("tasks/plan.json")

    assert plan_path.read_bytes() == raw


@pytest.mark.skipif(os.name == "nt", reason="POSIX source-object semantics")
@pytest.mark.parametrize("source_kind", ["same_root_symlink", "outside_symlink", "hardlink", "fifo"])
def test_pm_validation_normalization_rejects_unsafe_leaf_without_mutating_targets(
    tmp_path: Path,
    source_kind: str,
) -> None:
    executor = OrchestrationStageExecutor(tmp_path / "workspace")
    runtime_root = Path(resolve_storage_roots(str(executor.workspace)).runtime_root)
    tasks_dir = runtime_root / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    plan_path = tasks_dir / "plan.json"
    same_root_source = tasks_dir / "source.json"
    outside_source = tmp_path / "outside.json"
    original = (json.dumps(_pm_document(), ensure_ascii=False) + "\n").encode("utf-8")
    same_root_source.write_bytes(original)
    outside_source.write_bytes(original)
    if source_kind == "same_root_symlink":
        plan_path.symlink_to(same_root_source)
    elif source_kind == "outside_symlink":
        plan_path.symlink_to(outside_source)
    elif source_kind == "hardlink":
        os.link(same_root_source, plan_path)
    else:
        os.mkfifo(plan_path)

    with pytest.raises(snapshot_module.GuardedRegularFileSnapshotError):
        executor._persist_normalized_pm_plan_validation_contracts("tasks/plan.json")

    assert same_root_source.read_bytes() == original
    assert outside_source.read_bytes() == original
    if source_kind != "fifo":
        assert os.path.lexists(plan_path)


def test_pm_validation_normalization_detects_read_write_identity_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = OrchestrationStageExecutor(tmp_path / "workspace")
    runtime_root = Path(resolve_storage_roots(str(executor.workspace)).runtime_root)
    tasks = [
        {**_pm_task("TASK-1", ["src/main.py"]), "acceptance": ["Run pytest", "Build succeeds"]},
        {**_pm_task("TASK-2", ["tests/test_main.py"]), "depends_on": ["TASK-1"]},
    ]
    plan_path = _write_bytes(
        runtime_root,
        "tasks/plan.json",
        (json.dumps(_pm_document(tasks), ensure_ascii=False) + "\n").encode("utf-8"),
    )
    external = plan_path.with_name("external.json")
    external_raw = (json.dumps(_pm_document([_pm_task("EXTERNAL")]), ensure_ascii=False) + "\n").encode("utf-8")
    external.write_bytes(external_raw)

    def replace_before_final_revalidation(parent_fd: int, leaf_name: str) -> None:
        del parent_fd, leaf_name
        os.replace(external, plan_path)

    monkeypatch.setattr(
        snapshot_module,
        "_before_guarded_replace_revalidation",
        replace_before_final_revalidation,
    )

    with pytest.raises(snapshot_module.GuardedRegularFileSnapshotError, match="changed"):
        executor._persist_normalized_pm_plan_validation_contracts("tasks/plan.json")

    assert plan_path.read_bytes() == external_raw
    assert list(plan_path.parent.glob(".plan.json.*.tmp")) == []


def test_pm_binding_revalidation_is_strict_and_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    run_id = "run-1"
    document = _pm_document([_pm_task("TASK-1", ["src/main.py"])])
    _write_json(runtime_root, "tasks/plan.json", document)
    store = FactoryStore(runtime_root)
    binding = build_pm_stage_artifact_bindings(
        factory_store=store,
        source_root=runtime_root,
        factory_run_id=run_id,
    )
    event = _pm_event(run_id, binding)

    def persistence_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("read-only revalidation must not persist snapshots")

    monkeypatch.setattr(store, "persist_stage_artifact_snapshot", persistence_forbidden)

    revalidated = revalidate_pm_stage_artifact_binding(
        factory_store=store,
        factory_run_id=run_id,
        stage_event=event,
    )

    assert revalidated.binding == binding
    assert revalidated.item == binding.items[0]
    assert revalidated.document == document
    assert revalidated.task_ids == ("TASK-1",)


def test_ce_binding_revalidation_is_ordered_one_to_one_and_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    run_id = "run-1"
    task = _pm_task("TASK-1", ["src/main.py"])
    _write_json(runtime_root, "tasks/plan.json", _pm_document([task]))
    store = FactoryStore(runtime_root)
    pm_binding = build_pm_stage_artifact_bindings(
        factory_store=store,
        source_root=runtime_root,
        factory_run_id=run_id,
    )
    pm_event = _pm_event(run_id, pm_binding)
    row = _review_row(task)
    review = _review_document(run_id, [row])
    blueprint = _blueprint(run_id, task)
    _write_json(runtime_root, f"runtime/state/blueprints/{run_id}.review.json", review)
    _write_json(runtime_root, "runtime/blueprints/bp-TASK-1.json", blueprint)
    ce_binding = build_chief_engineer_stage_artifact_bindings(
        factory_store=store,
        source_root=runtime_root,
        factory_run_id=run_id,
        pm_stage_event=pm_event,
    )
    ce_event = {
        "type": "stage_completed",
        "stage": "chief_engineer_review",
        "run_id": run_id,
        "event_id": "evt-ce-complete",
        "result": {"stage": "chief_engineer_review", "status": "success"},
        "chain_schema_version": "factory.event_chain.v1",
        "chain_sequence": 3,
        "chain_previous_hash": "a" * 64,
        "chain_event_hash": "b" * 64,
        "stage_artifact_bindings": ce_binding.to_record(),
    }

    def persistence_forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("read-only revalidation must not persist snapshots")

    monkeypatch.setattr(store, "persist_stage_artifact_snapshot", persistence_forbidden)

    revalidated = revalidate_chief_engineer_stage_artifact_binding(
        factory_store=store,
        factory_run_id=run_id,
        stage_event=ce_event,
        pm_stage_event=pm_event,
    )

    assert revalidated.binding == ce_binding
    assert revalidated.review_document == review
    assert revalidated.blueprint_documents == (blueprint,)
    assert tuple(item.task_id for item in revalidated.blueprint_items) == ("TASK-1",)
