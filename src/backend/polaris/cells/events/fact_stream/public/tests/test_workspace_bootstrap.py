"""Regression tests for explicit workspace authority bootstrap and enrollment."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    EnrollFactStreamStreamsCommandV1,
    FactStreamError,
    FactStreamMaintenanceReceiptV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    enroll_fact_stream_streams,
    fact_stream_bootstrap_streams,
    query_fact_events,
    workspace_bootstrap as workspace_bootstrap_module,
)

_BOOTSTRAP_CONCURRENCY = 64
_THREAD_BARRIER_TIMEOUT_SECONDS = 30


def _bootstrap(
    workspace: Path,
    reason: str = "fact_stream_workspace_bootstrap_test",
    *,
    platform_lock_root: Path | None = None,
) -> FactStreamMaintenanceReceiptV1:
    return bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason=reason,
            platform_lock_root=str(platform_lock_root) if platform_lock_root is not None else None,
        )
    )


def _assert_valid_non_authoritative_bootstrap_receipt(
    receipt: FactStreamMaintenanceReceiptV1,
    *,
    workspace: Path,
) -> None:
    """Check durable maintenance evidence without treating the DTO as authority."""

    assert isinstance(receipt, FactStreamMaintenanceReceiptV1)
    assert receipt.operation == "bootstrap_workspace"
    assert receipt.streams == fact_stream_bootstrap_streams()
    assert receipt.workspace == str(workspace.resolve())
    assert receipt.storage_identity_token
    assert tuple(proof.operation for proof in receipt.proofs) == (
        "provision_authority",
        "enroll_stream_lock_keys",
    )
    assert all(proof.final_validation is True for proof in receipt.proofs)
    assert all(proof.format_revision for proof in receipt.proofs)
    assert all(proof.root_identity.inode > 0 for proof in receipt.proofs)
    assert all(proof.anchor_identity.inode > 0 for proof in receipt.proofs)
    assert all(proof.realm_identity.inode > 0 for proof in receipt.proofs)


def _assert_exact_bootstrap_concurrency_evidence(
    receipts: tuple[FactStreamMaintenanceReceiptV1, ...],
    *,
    workspace: Path,
) -> None:
    """Verify every concurrent call revalidated one durable authority topology."""

    assert len(receipts) == _BOOTSTRAP_CONCURRENCY
    for receipt in receipts:
        _assert_valid_non_authoritative_bootstrap_receipt(receipt, workspace=workspace)

    assert len({receipt.storage_identity_token for receipt in receipts}) == 1
    proofs_by_operation = {
        operation: tuple(
            next(proof for proof in receipt.proofs if proof.operation == operation) for receipt in receipts
        )
        for operation in ("provision_authority", "enroll_stream_lock_keys")
    }
    for operation, proofs in proofs_by_operation.items():
        verdicts = tuple(proof.verdict for proof in proofs)
        assert verdicts.count("created") == 1, operation
        assert verdicts.count("already_present") == _BOOTSTRAP_CONCURRENCY - 1, operation
        assert (
            len(
                {
                    (
                        proof.storage_identity_token,
                        proof.runtime_root,
                        proof.format_revision,
                        proof.root_identity.device,
                        proof.root_identity.inode,
                        proof.anchor_identity.device,
                        proof.anchor_identity.inode,
                        proof.realm_identity.device,
                        proof.realm_identity.inode,
                    )
                    for proof in proofs
                }
            )
            == 1
        )

    enrollment_proofs = proofs_by_operation["enroll_stream_lock_keys"]
    expected_key_proofs = tuple(
        (
            item.logical_path,
            item.lock_key,
            item.identity.device,
            item.identity.inode,
        )
        for item in enrollment_proofs[0].lock_keys
    )
    assert len(expected_key_proofs) == len(fact_stream_bootstrap_streams())
    for proof in enrollment_proofs:
        assert (
            tuple(
                (item.logical_path, item.lock_key, item.identity.device, item.identity.inode)
                for item in proof.lock_keys
            )
            == expected_key_proofs
        )
    for key_index, key_proof in enumerate(expected_key_proofs):
        key_verdicts = tuple(proof.lock_keys[key_index].verdict for proof in enrollment_proofs)
        assert key_verdicts.count("created") == 1, key_proof
        assert key_verdicts.count("already_present") == _BOOTSTRAP_CONCURRENCY - 1, key_proof


@pytest.mark.parametrize(
    "streams, expected_illegal",
    [
        (
            ("roles.kernel.provider_attempts.factory.run-one",),
            "roles.kernel.provider_attempts.factory.run-one",
        ),
        (
            ("task_runtime.execution", "factory.role_evidence_authority.run-one"),
            "factory.role_evidence_authority.run-one",
        ),
    ],
)
def test_bootstrap_rejects_segmented_namespace_before_any_maintenance_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    streams: tuple[str, ...],
    expected_illegal: str,
) -> None:
    maintenance_calls: list[str] = []

    def forbidden_provision(command: object) -> object:
        maintenance_calls.append(f"provision:{command!r}")
        raise AssertionError("bootstrap reached authority provisioning")

    def forbidden_enrollment(command: object) -> object:
        maintenance_calls.append(f"enroll:{command!r}")
        raise AssertionError("bootstrap reached stream enrollment")

    monkeypatch.setattr(workspace_bootstrap_module, "provision_fact_stream_lock_authority", forbidden_provision)
    monkeypatch.setattr(workspace_bootstrap_module, "enroll_fact_stream_streams", forbidden_enrollment)

    with pytest.raises(FactStreamError) as rejected:
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(tmp_path),
                streams=streams,
                maintenance_reason="segmented_namespace_bootstrap_preflight_test",
            )
        )

    assert rejected.value.code == "segmented_stream_api_required"
    assert rejected.value.details == {"stream": expected_illegal}
    assert maintenance_calls == []


def test_bootstrap_is_idempotent_across_exactly_64_threads(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    authority_root = tmp_path / "platform-authority"
    workspace.mkdir()
    release_barrier = Barrier(_BOOTSTRAP_CONCURRENCY + 1)

    def bootstrap_after_shared_release() -> FactStreamMaintenanceReceiptV1:
        release_barrier.wait(timeout=_THREAD_BARRIER_TIMEOUT_SECONDS)
        return _bootstrap(workspace, platform_lock_root=authority_root)

    with ThreadPoolExecutor(max_workers=_BOOTSTRAP_CONCURRENCY) as executor:
        futures = tuple(executor.submit(bootstrap_after_shared_release) for _ in range(_BOOTSTRAP_CONCURRENCY))
        release_barrier.wait(timeout=_THREAD_BARRIER_TIMEOUT_SECONDS)
        receipts = tuple(future.result() for future in futures)

    _assert_exact_bootstrap_concurrency_evidence(receipts, workspace=workspace)


def test_bootstrap_failure_does_not_publish_completion_state_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    authority_root = tmp_path / "platform-authority"
    workspace.mkdir()
    injected_calls = 0

    def fail_enrollment_once(command: EnrollFactStreamStreamsCommandV1) -> FactStreamMaintenanceReceiptV1:
        nonlocal injected_calls
        injected_calls += 1
        raise FactStreamError("injected enrollment failure", code="injected_bootstrap_failure")

    with monkeypatch.context() as injected_patch:
        injected_patch.setattr(workspace_bootstrap_module, "enroll_fact_stream_streams", fail_enrollment_once)
        with pytest.raises(FactStreamError) as failed:
            _bootstrap(workspace, platform_lock_root=authority_root)

    assert failed.value.code == "injected_bootstrap_failure"
    assert injected_calls == 1

    retry = _bootstrap(workspace, platform_lock_root=authority_root)
    _assert_valid_non_authoritative_bootstrap_receipt(retry, workspace=workspace)


def test_bootstrap_keeps_workspace_storage_identities_distinct(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_receipt = _bootstrap(first)
    second_receipt = _bootstrap(second)

    assert first_receipt.workspace == str(first.resolve())
    assert second_receipt.workspace == str(second.resolve())
    assert first_receipt.storage_identity_token != second_receipt.storage_identity_token


def test_dynamic_stream_requires_explicit_enrollment_after_bootstrap(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _bootstrap(workspace)

    query = QueryFactEventsV1(workspace=str(workspace), stream="deo_dynamic_stream", strict_integrity=True)
    with pytest.raises(FactStreamError) as missing:
        query_fact_events(query)
    assert missing.value.code == "stream_lock_missing"

    receipt = enroll_fact_stream_streams(
        EnrollFactStreamStreamsCommandV1(
            workspace=str(workspace),
            streams=("deo_dynamic_stream",),
            maintenance_reason="directed_effect_first_business_io",
        )
    )
    assert receipt.operation == "enroll_streams"
    assert len(receipt.proofs) == 1
    proof = receipt.proofs[0]
    assert proof.operation == "enroll_stream_lock_keys"
    assert proof.final_validation is True
    assert proof.lock_keys
    assert tuple(item.lock_key for item in proof.lock_keys) == tuple(sorted(item.lock_key for item in proof.lock_keys))
    assert all(item.verdict in {"created", "already_present"} for item in proof.lock_keys)
    assert query_fact_events(query).total == 0


def test_dynamic_enrollment_without_authority_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(FactStreamError) as ordinary_io:
        query_fact_events(
            QueryFactEventsV1(
                workspace=str(workspace),
                stream="deo_dynamic_stream",
                strict_integrity=True,
            )
        )
    assert ordinary_io.value.code == "lock_authority_missing"

    with pytest.raises(FactStreamError) as missing:
        enroll_fact_stream_streams(
            EnrollFactStreamStreamsCommandV1(
                workspace=str(workspace),
                streams=("deo_dynamic_stream",),
                maintenance_reason="directed_effect_first_business_io",
            )
        )

    assert missing.value.code == "lock_authority_missing"
