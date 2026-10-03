"""Historical source provenance is reusable, never current completion authority."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as owner
from polaris.cells.runtime.execution_broker.public.project_verification import (
    ProjectArtifactReceiptV1,
    QueryProjectArtifactSourceBaselineV1,
    query_project_artifact_receipt,
    query_project_artifact_source_baseline,
    record_project_artifact,
)
from polaris.cells.runtime.execution_broker.tests.test_project_verification_receipts import (
    _artifact_command,
    _artifact_query,
    _AuthorityPort,
)


class _EpochPort(_AuthorityPort):
    revision = "e" * 64

    def resolve_project_artifact_authority(self, query):
        return replace(super().resolve_project_artifact_authority(query), authority_revision=self.revision)


@pytest.fixture
def artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "src/main.py"
    source.parent.mkdir()
    source.write_text("print('authored')\n", encoding="utf-8")
    port = _EpochPort()
    monkeypatch.setattr(owner, "_EXECUTION_AUTHORITY_PORT", port)
    receipt = record_project_artifact(_artifact_command(tmp_path))
    return source, port, receipt


def _query(workspace: Path) -> QueryProjectArtifactSourceBaselineV1:
    query = _artifact_query(workspace)
    return QueryProjectArtifactSourceBaselineV1(
        workspace=query.workspace,
        project_id=query.project_id,
        run_id=query.run_id,
        completion_contract_hash=query.completion_contract_hash,
        obligation_id=query.obligation_id,
        owner_task_id=query.owner_task_id,
        path=query.path,
    )


def test_epoch_change_preserves_readonly_source_but_not_current_receipt(artifact, tmp_path: Path) -> None:
    source, port, prior = artifact
    port.revision = "9" * 64
    assert query_project_artifact_receipt(_artifact_query(tmp_path)) is None
    database = owner._db_path(str(tmp_path))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    baseline = query_project_artifact_source_baseline(_query(tmp_path))
    assert baseline is not None
    assert not isinstance(baseline, ProjectArtifactReceiptV1)
    assert baseline.artifact_hash == hashlib.sha256(source.read_bytes()).hexdigest()
    assert baseline.source_receipt_hash == prior.receipt_hash
    assert baseline.source_authority_revision == "e" * 64
    assert baseline.writes_allowed is False
    assert baseline.completion_eligible is False
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert query_project_artifact_receipt(_artifact_query(tmp_path)) is None
    with pytest.raises(ValueError):
        replace(baseline, run_id="forged-run")


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "different"),
        ("completion_contract_hash", "b" * 64),
        ("owner_task_id", "foreign"),
        ("path", "src/foreign.py"),
        ("obligation_id", "foreign"),
    ],
)
def test_baseline_never_retags_foreign_identity(artifact, tmp_path: Path, field: str, value: str) -> None:
    del artifact
    assert query_project_artifact_source_baseline(replace(_query(tmp_path), **{field: value})) is None


def test_changed_source_cannot_reuse_historical_receipt(artifact, tmp_path: Path) -> None:
    source, _, _ = artifact
    source.write_text("changed\n", encoding="utf-8")
    assert query_project_artifact_source_baseline(_query(tmp_path)) is None


def test_authenticated_chain_tamper_remains_rejected(artifact, tmp_path: Path) -> None:
    del artifact
    with sqlite3.connect(owner._db_path(str(tmp_path))) as connection:
        connection.execute("UPDATE project_verification_receipt_events SET auth_hash=? WHERE sequence=1", ("0" * 64,))
    with pytest.raises(ValueError, match="provenance chain"):
        query_project_artifact_source_baseline(_query(tmp_path))


def test_absent_baseline_query_does_not_create_runtime_or_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("input\n", encoding="utf-8")
    monkeypatch.setattr(owner, "_EXECUTION_AUTHORITY_PORT", _EpochPort())
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert query_project_artifact_source_baseline(_query(tmp_path)) is None
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before


def test_absent_baseline_does_not_enter_cold_owner_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real cold CE adapter may initialize roots before reporting no contract."""

    class ColdOwner(_EpochPort):
        def resolve_project_artifact_authority(self, query):
            del query
            raise AssertionError("cold_owner_must_not_initialize_storage")

    monkeypatch.setattr(owner, "_EXECUTION_AUTHORITY_PORT", ColdOwner())
    assert query_project_artifact_source_baseline(_query(tmp_path)) is None


def test_real_cold_owner_does_not_initialize_absent_source_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the real CE adapter's former side-effect path, not a fake port."""
    from polaris.bootstrap.project_completion_diagnostics_owner import ProjectCompletionOwnerObservationAdapter

    workspace = tmp_path / "project"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src/main.py").write_text("input\n", encoding="utf-8")
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "platform"))
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(workspace / ".polaris/runtime"))
    monkeypatch.setenv("KERNELONE_RUNTIME_CACHE_ROOT", str(tmp_path / "cache"))
    monkeypatch.setenv("KERNELONE_RAMDISK_ENABLED", "0")
    monkeypatch.setattr(owner, "_EXECUTION_AUTHORITY_PORT", ProjectCompletionOwnerObservationAdapter())
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert query_project_artifact_source_baseline(_query(workspace)) is None
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == before
