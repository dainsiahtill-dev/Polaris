"""Readonly staging inputs consume real CE persistence and owner-sealed receipts."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerPortfolioTaskV1,
    build_chief_engineer_blueprint_portfolio,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
    _library_completion_requirements,
    _portfolio_command_authority,
)
from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.cells.control_plane.run_ledger.public.directed_effect_receipt_validation import (
    directed_effect_receipt_payload_hash,
    directed_effect_receipt_v2_errors,
)
from polaris.cells.runtime.execution_broker.public import (
    ProjectArtifactExecutionAuthorityV1,
    RecordProjectArtifactCommandV1,
    record_project_artifact,
)
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter
from polaris.tests.integration import test_deo_2c_production_receipt_wiring as production

MODULE = "polaris.cells.factory.pipeline.internal.native_validation_inputs"


def _module() -> Any:
    assert importlib.util.find_spec(MODULE) is not None, "authoritative readonly input builder is missing"
    return importlib.import_module(MODULE)


@pytest.fixture
def authored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, KernelFileSystem]:
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="DEO-LIFECYCLE",
            objective="Author library and tests",
            target_files=("src/a.py", "src/authored.js", "tests/test_a.py"),
        ),
    )
    portfolio = build_chief_engineer_blueprint_portfolio(
        BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run",
            tasks=tasks,
            **_portfolio_command_authority(tasks=tasks, project_kind="library", workspace=tmp_path, run_id="run"),
            llm_blueprint={
                "construction_plan": {"project_interface_contract": {}},
                "project_completion_contract": _library_completion_requirements(
                    "src/a.py",
                    "src/authored.js",
                    owner_task_ids=("DEO-LIFECYCLE", "DEO-LIFECYCLE"),
                    test_path="tests/test_a.py",
                    test_owner_task_id="DEO-LIFECYCLE",
                ),
                "risk_flags": [],
            },
        )
    )
    contract = portfolio.project_completion_contract
    assert contract is not None

    class ArtifactOwnerPort:
        """Test composition only; receipts themselves are issued by the real broker."""

        def resolve_project_verification_authority(self, _query: Any) -> Any:
            raise ValueError("fixture does not grant process execution")

        def consume_project_verification_execution_capability(self, _command: Any) -> Any:
            raise ValueError("fixture does not mint execution capabilities")

        def resolve_project_artifact_authority(self, query: Any) -> ProjectArtifactExecutionAuthorityV1:
            artifact = next(
                item for item in contract.obligations.artifacts if item.obligation_id == query.obligation_id
            )
            return ProjectArtifactExecutionAuthorityV1(
                workspace=query.workspace,
                project_id=contract.project_id,
                run_id=contract.run_id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=artifact.owner_task_id,
                path=artifact.path,
                job_token_id="fixture-artifact-owner",
                job_token_set_hash="c" * 64,
                execution_policy_hash="d" * 64,
                authority_revision="e" * 64,
            )

    # Bind the existing singleton once, substituting only its fixture authority
    # read. No receipt constructor/seal/SQL or production reader is replaced.
    from polaris.bootstrap.project_completion_diagnostics_owner import (
        PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER,
        configure_project_completion_diagnostics_owner,
    )

    configure_project_completion_diagnostics_owner()
    monkeypatch.setattr(
        PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER,
        "resolve_project_artifact_authority",
        ArtifactOwnerPort().resolve_project_artifact_authority,
    )
    fs = KernelFileSystem(str(tmp_path), get_default_adapter())
    for artifact in contract.obligations.artifacts:
        fs.workspace_write_text(artifact.path, "before\n", encoding="utf-8")
        record_project_artifact(
            RecordProjectArtifactCommandV1(
                workspace=str(tmp_path),
                project_id=contract.project_id,
                run_id=contract.run_id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=artifact.owner_task_id,
                path=artifact.path,
            )
        )
    return portfolio, contract, fs


def _freeze(root: Path, contract: Any) -> Any:
    return _module().freeze_native_validation_baseline(
        workspace=str(root),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )


def test_closed_baseline_includes_authored_js_but_never_unknown_compiler_outputs(
    tmp_path: Path, authored: tuple[Any, Any, KernelFileSystem]
) -> None:
    _portfolio, contract, fs = authored
    fs.workspace_write_text("tests/test_a.js", "unknown generated output\n", encoding="utf-8")
    before = {a.path: fs.workspace_read_bytes(a.path) for a in contract.obligations.artifacts}

    baseline = _freeze(tmp_path, contract)

    assert baseline.input_hashes == {path: hashlib.sha256(content).hexdigest() for path, content in before.items()}
    assert baseline.input_bytes == before
    assert "src/authored.js" in baseline.input_hashes
    assert "tests/test_a.js" not in baseline.input_hashes
    assert {path: fs.workspace_read_bytes(path) for path in before} == before


@pytest.mark.parametrize("change", ["bytes", "symlink", "missing"])
def test_baseline_rejects_unproven_current_source(
    tmp_path: Path, authored: tuple[Any, Any, KernelFileSystem], change: str
) -> None:
    _portfolio, contract, fs = authored
    if change == "bytes":
        fs.workspace_write_text("src/a.py", "unreceipted\n", encoding="utf-8")
    elif change == "missing":
        (tmp_path / "src/a.py").unlink()
    else:
        outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
        outside.write_text("before\n", encoding="utf-8")
        (tmp_path / "src/a.py").unlink()
        (tmp_path / "src/a.py").symlink_to(outside)
    module = _module()
    with pytest.raises(module.NativeValidationInputError):
        _freeze(tmp_path, contract)


def test_unknown_ce_identity_never_falls_back_to_disk_inventory(
    tmp_path: Path, authored: tuple[Any, Any, KernelFileSystem]
) -> None:
    _portfolio, contract, _fs = authored
    module = _module()
    with pytest.raises(module.NativeValidationInputError):
        module.freeze_native_validation_baseline(
            workspace=str(tmp_path),
            project_id=contract.project_id,
            run_id="foreign-run",
            completion_contract_hash=contract.contract_hash,
        )


def test_epoch_change_uses_real_readonly_history_without_promoting_current_receipt(
    tmp_path: Path, authored: tuple[Any, Any, KernelFileSystem], monkeypatch: pytest.MonkeyPatch
) -> None:
    from polaris.bootstrap.project_completion_diagnostics_owner import PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER
    from polaris.cells.runtime.execution_broker.public import (
        ProjectArtifactSourceBaselineV1,
        QueryProjectArtifactReceiptV1,
        query_project_artifact_receipt,
    )

    _portfolio, contract, fs = authored
    prior = _freeze(tmp_path, contract)
    owner = PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.resolve_project_artifact_authority
    monkeypatch.setattr(
        PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER,
        "resolve_project_artifact_authority",
        lambda query: replace(owner(query), authority_revision="f" * 64),
    )
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    baseline = _freeze(tmp_path, contract)

    assert baseline.input_bytes == prior.input_bytes
    for item in baseline.files:
        assert type(item.source_evidence) is ProjectArtifactSourceBaselineV1
        assert item.source_evidence.writes_allowed is False
        assert item.source_evidence.completion_eligible is False
        assert (
            query_project_artifact_receipt(
                QueryProjectArtifactReceiptV1(
                    workspace=str(tmp_path),
                    project_id=contract.project_id,
                    run_id=contract.run_id,
                    completion_contract_hash=contract.contract_hash,
                    obligation_id=item.obligation_id,
                    owner_task_id=item.owner_task_id,
                    path=item.path,
                )
            )
            is None
        )
        assert fs.workspace_read_bytes(item.path) == prior.input_bytes[item.path]
    assert {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("path", [".", "../escape", "./src/a.py", "/absolute", ".polaris/runtime/key", "src\\a.py"])
def test_noncanonical_source_paths_fail_closed(path: str) -> None:
    module = _module()
    with pytest.raises(module.NativeValidationInputError):
        module._source_path(path)


@pytest.mark.parametrize("attack", ["missing_closure", "foreign_path", "foreign_owner"])
def test_rehashed_caller_baseline_cannot_change_owner_sealed_closure(
    tmp_path: Path, authored: tuple[Any, Any, KernelFileSystem], attack: str
) -> None:
    portfolio, contract, _fs = authored
    module = _module()
    baseline = _freeze(tmp_path, contract)
    files = baseline.files
    if attack == "missing_closure":
        files = files[:-1]
    else:
        item = replace(
            files[0], **({"path": "foreign.py"} if attack == "foreign_path" else {"owner_task_id": "FOREIGN"})
        )
        files = (item, *files[1:])
    forged = replace(baseline, files=files, baseline_hash=module._baseline_hash(baseline.workspace, contract, files))
    attempt = production._setup_attempt(str(tmp_path))
    with pytest.raises(module.NativeValidationInputError, match="native_validation_baseline"):
        module.overlay_native_validation_candidate(
            forged,
            pending={"task_id": "DEO-LIFECYCLE", "task_row_id": attempt.task_id, "execution_attempt": attempt},
            task_completion_projection=_projection(portfolio, contract),
            round_repair_results=[],
        )


def _projection(portfolio: Any, contract: Any) -> dict[str, Any]:
    task_id = "DEO-LIFECYCLE"
    verification = [item for item in contract.obligations.verification if item.owner_task_id == task_id]
    authorities = [item for item in contract.verification_command_authority if item.task_id == task_id]
    by_hash = {item.authority_hash: item for item in authorities}
    seed = {
        "schema_version": "polaris.task_completion_projection.v1",
        "project_contract_id": contract.contract_id,
        "project_contract_ref": portfolio.project_completion_contract_ref,
        "project_contract_hash": contract.contract_hash,
        "project_id": contract.project_id,
        "run_id": contract.run_id,
        "task_id": task_id,
        "project_kind": contract.project_kind,
        "completion_predicate_version": contract.completion_predicate_version,
        "verifier_policy_hash": contract.verifier_policy_hash,
        "verifier_policy_snapshot_hash": contract.verifier_policy_snapshot_hash,
        "owned_artifacts": [item.to_dict() for item in contract.obligations.artifacts if item.owner_task_id == task_id],
        "dependency_artifacts": [],
        "owned_entrypoints": [
            item.to_dict() for item in contract.obligations.entrypoints if item.owner_task_id == task_id
        ],
        "owned_verification": [item.to_dict() for item in verification],
        "verification_command_authority": [item.to_dict() for item in authorities],
        "verification_execution_authority": [
            {
                "obligation_id": item.obligation_id,
                "owner_task_id": item.owner_task_id,
                "modality": item.modality,
                "command": item.command,
                "argv": list(by_hash[item.command_authority_hash].argv),
                "cwd": by_hash[item.command_authority_hash].cwd,
                "command_authority_hash": item.command_authority_hash,
                "covers_obligation_ids": list(item.covers_obligation_ids),
            }
            for item in verification
            if item.applicability != "not_applicable"
        ],
    }
    return {**seed, "projection_hash": stable_hash(seed)}


async def _real_effect(root: Path, *, attempt: Any = None) -> tuple[Any, list[dict[str, Any]]]:
    """Existing production fixture issues real claim/fence/commit and writes via owner port."""
    if attempt is None:
        attempt = production._setup_attempt(str(root))
    before = hashlib.sha256((root / "src/a.py").read_bytes()).hexdigest()
    candidate = production._candidate(
        attempt,
        ordinal=0,
        target_exists=True,
        target_before_content_hash=before,
        turn_id="turn-input-fixture",
        batch_id="batch-input-fixture",
    )
    policy = production._WorkspaceObservingPolicyPort(root)
    authority = production._authority(attempt)
    lifecycle = production.DirectedEffectLifecycleService(policy_snapshot_port=policy).prepare_batch(
        execution_attempt=attempt,
        execution_attempt_authority=authority,
        turn_id="turn-input-fixture",
        batch_id="batch-input-fixture",
        candidates=(candidate,),
    )
    assert lifecycle.status == "ready", lifecycle
    prepared = lifecycle.prepared_batch
    assert prepared is not None
    member = prepared.prepared_members[0].member
    fence = production.create_directed_effect_fence_ports()
    mutation = production.create_director_directed_effect_mutation_port(
        workspace=str(root),
        policy_snapshot_port=policy,
        fence_consume_port=fence.consume,
    )
    runtime = production.ToolBatchRuntime(
        executor=production._reject_non_mutation_execution,
        directed_effect_runtime=production.DirectedEffectRuntimeDependenciesV1(
            policy_snapshot_port=policy,
            fence_admin_port=fence.admin,
            mutation_port=mutation,
        ),
        directed_effect_required=True,
        directed_effect_execution_attempt=prepared.execution_attempt,
        directed_effect_execution_attempt_authority=authority,
        prepared_directed_effect_batch=prepared,
        directed_effect_restrictions_by_call_id=((member.tool_call_id, production._job_restriction_evidence()),),
        directed_effect_dispatch_call_ids=(member.tool_call_id,),
    )
    invocation = production.ToolInvocation(
        call_id=production.ToolCallId(member.tool_call_id),
        tool_name=member.normalized_tool_name,
        arguments={"content": "after\n", "path": "src/a.py"},
    )
    batches = await runtime.execute_batch(
        production.ToolBatch(
            batch_id=production.BatchId(prepared.parent_binding.correlation.batch_id),
            invocations=[invocation],
            serial_writes=[invocation],
        ),
        production.TurnId("turn-input-fixture"),
    )
    assert batches[0].success_count == 1
    return prepared.execution_attempt, list(batches[0].raw_results)


@pytest.mark.asyncio
async def test_current_real_committed_effect_overlays_frozen_baseline_without_receipt_requery(
    tmp_path: Path, authored: tuple[Any, Any, KernelFileSystem], monkeypatch: pytest.MonkeyPatch
) -> None:
    portfolio, contract, fs = authored
    baseline = _freeze(tmp_path, contract)
    attempt, rows = await _real_effect(tmp_path)
    module = _module()
    assert callable(getattr(module, "overlay_native_validation_candidate", None)), "verified overlay builder is missing"
    monkeypatch.setattr(
        module, "query_project_artifact_receipt", lambda _query: pytest.fail("must not requery after claim")
    )
    pending = {"task_id": "DEO-LIFECYCLE", "task_row_id": attempt.task_id, "execution_attempt": attempt}
    inputs = module.overlay_native_validation_candidate(
        baseline,
        pending=pending,
        task_completion_projection=_projection(portfolio, contract),
        round_repair_results=rows,
    )
    assert inputs.input_bytes["src/a.py"] == b"after\n"
    assert inputs.input_hashes["src/a.py"] == hashlib.sha256(b"after\n").hexdigest()
    assert baseline.input_bytes["src/a.py"] == b"before\n"
    assert inputs.input_bytes["src/authored.js"] == b"before\n"
    assert fs.workspace_read_bytes("src/a.py") == b"after\n"


@pytest.mark.parametrize(
    "attack", ["legacy", "body", "before", "after", "foreign_attempt", "projection_owner", "projection_hash", "noop"]
)
@pytest.mark.asyncio
async def test_overlay_rejects_unknown_or_forged_proof(
    tmp_path: Path, authored: tuple[Any, Any, KernelFileSystem], attack: str
) -> None:
    portfolio, contract, _fs = authored
    baseline = _freeze(tmp_path, contract)
    attempt, rows = await _real_effect(tmp_path)
    module = _module()
    assert callable(getattr(module, "overlay_native_validation_candidate", None)), "verified overlay builder is missing"
    rows = deepcopy(rows)
    projection = _projection(portfolio, contract)
    pending = {"task_id": "DEO-LIFECYCLE", "task_row_id": attempt.task_id, "execution_attempt": attempt}
    if attack == "legacy":
        rows[0]["effect_receipt"]["schema_version"] = "legacy"
    elif attack in {"body", "before", "after", "noop"}:
        key = {"body": "bytes_written", "before": "before_sha256", "after": "after_sha256", "noop": "after_sha256"}[
            attack
        ]
        rows[0]["result"][key] = "0" * 64
    elif attack == "foreign_attempt":
        pending["task_id"] = "FOREIGN"
    elif attack == "projection_owner":
        projection["owned_artifacts"][0]["owner_task_id"] = "FOREIGN"
        projection["projection_hash"] = stable_hash({k: v for k, v in projection.items() if k != "projection_hash"})
    else:
        projection["projection_hash"] = "0" * 64
    with pytest.raises(module.NativeValidationInputError):
        module.overlay_native_validation_candidate(
            baseline,
            pending=pending,
            task_completion_projection=projection,
            round_repair_results=rows,
        )


@pytest.mark.asyncio
async def test_selfconsistent_v2_receipt_does_not_replace_persisted_commit(
    tmp_path: Path, authored: tuple[Any, Any, KernelFileSystem]
) -> None:
    portfolio, contract, _fs = authored
    baseline = _freeze(tmp_path, contract)
    attempt, original_rows = await _real_effect(tmp_path)
    rows = deepcopy(original_rows)
    receipt = rows[0]["effect_receipt"]
    commit = rows[0]["effect_receipt_commit"]
    receipt["policy_evidence_hash"] = "0" * 64
    receipt["receipt_hash"] = directed_effect_receipt_payload_hash(receipt)
    receipt["receipt_id"] = f"director-physical-effect-{receipt['receipt_hash'][:24]}"
    commit["receipt_hash"] = receipt["receipt_hash"]
    commit["receipt_ref"] = receipt["receipt_id"]
    rows[0]["result"]["effect_receipt"] = receipt
    rows[0]["result"]["effect_receipt_commit"] = commit
    assert directed_effect_receipt_v2_errors(receipt, commit, prefix="test") == ()
    module = _module()
    with pytest.raises(module.NativeValidationInputError, match="persisted_receipt_binding_mismatch"):
        module.overlay_native_validation_candidate(
            baseline,
            pending={"task_id": "DEO-LIFECYCLE", "task_row_id": attempt.task_id, "execution_attempt": attempt},
            task_completion_projection=_projection(portfolio, contract),
            round_repair_results=rows,
        )


@pytest.mark.parametrize("drift", ["before", "after", "sibling", "symlink"])
@pytest.mark.asyncio
async def test_real_committed_effect_cannot_cover_source_or_candidate_drift(
    tmp_path: Path, authored: tuple[Any, Any, KernelFileSystem], drift: str
) -> None:
    portfolio, contract, fs = authored
    baseline = _freeze(tmp_path, contract)
    if drift == "before":
        fs.workspace_write_text("src/a.py", "interloper\n", encoding="utf-8")
    attempt, rows = await _real_effect(tmp_path)
    if drift == "after":
        fs.workspace_write_text("src/a.py", "late unreceipted write\n", encoding="utf-8")
    elif drift == "sibling":
        fs.workspace_write_text("src/authored.js", "late unreceipted write\n", encoding="utf-8")
    elif drift == "symlink":
        (tmp_path / "src/a.py").unlink()
        (tmp_path / "src/a.py").symlink_to(tmp_path / "src/authored.js")
    module = _module()
    with pytest.raises(module.NativeValidationInputError):
        module.overlay_native_validation_candidate(
            baseline,
            pending={"task_id": "DEO-LIFECYCLE", "task_row_id": attempt.task_id, "execution_attempt": attempt},
            task_completion_projection=_projection(portfolio, contract),
            round_repair_results=rows,
        )
