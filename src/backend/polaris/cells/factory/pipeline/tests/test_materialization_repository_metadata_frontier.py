"""Covered metadata cannot bypass real CE-bound verifier receipts or current inputs."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture
from polaris.cells.runtime.execution_broker.public.project_verification import ProjectVerificationReceiptV1


@pytest.mark.parametrize("failing", [False, True])
def test_metadata_reaches_real_owner_verifiers_and_current_input_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: bool
) -> None:
    executor, run, _ = _fixture(
        tmp_path,
        monkeypatch,
        stdlib_verifier=True,
        failing=failing,
        additional_authored_files={".gitignore": "node_modules/\ndist/\n*.log\n"},
        additional_semantic_role="config",
    )
    path = Path(executor.workspace) / ".gitignore"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert not observation.blockers, observation.to_dict()
    assert "unsupported_source:.gitignore" not in observation.static_evidence["incomplete_reasons"]
    assert len(observation.verifier_results) == 2
    receipts = materialization._current_materialization_receipts(executor, run, observation.verifier_results)
    assert len(receipts) == 2
    assert all(type(receipt) is ProjectVerificationReceiptV1 for receipt in receipts)
    assert [receipt.succeeded for receipt in receipts] == [True, not failing]
    assert all(receipt.receipt_ref and receipt.receipt_hash and receipt.input_artifact_hash for receipt in receipts)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    metadata = [
        issue
        for issue in observation.static_evidence["issues"]
        if issue["code"] == "non_executable_repository_metadata"
    ]
    assert len(metadata) == 1
    assert metadata[0]["metadata"]["execution_qualified"] is False
    assert bool(observation) is failing
    if failing:
        assert observation.verifier_results[-1]["exit_code"] == 1
    path.write_text("changed-input/\n", encoding="utf-8")
    with pytest.raises(ValueError, match="materialization_baseline_verifier_receipt_not_current"):
        materialization._current_materialization_receipts(executor, run, observation.verifier_results)


def test_unknown_required_artifact_still_blocks_before_verifier_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, _ = _fixture(
        tmp_path,
        monkeypatch,
        stdlib_verifier=True,
        additional_authored_files={"worker.custom": "unclassified code\n"},
    )
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert any("unsupported_source:worker.custom" in reason for reason in observation.blockers)
    assert not observation.verifier_results
    assert not observation._owner_receipts
    assert not (Path(executor.workspace) / "src/module.pyc").exists()
