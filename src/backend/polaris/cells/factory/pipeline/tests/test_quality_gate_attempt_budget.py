"""Real coordinator state, typed lifecycle receipts are private producer fixtures."""

import hashlib
from dataclasses import replace

import pytest
from polaris.cells.factory.pipeline.internal.factory_physical_attempt_coordinator import (
    FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptCoordinator,
)
from polaris.cells.factory.pipeline.tests.test_factory_physical_attempt_control import (
    _begin,
    _command,
    _commit,
    _cutoff_view,
    _grant_view,
    _start_receipt,
    _terminal_receipt,
)
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA,
    SettleFactoryPhysicalAttemptV1,
)


def _complete(c, reservation, index):
    permit = c.begin_start(_begin(reservation))
    receipt = replace(
        _start_receipt(permit),
        logical_sequence=index * 2 + 1,
        event_hash=hashlib.sha256(f"start{index}".encode()).hexdigest(),
    )
    lease = c.commit_started(_commit(permit, receipt=receipt))
    terminal = replace(
        _terminal_receipt(lease),
        logical_sequence=index * 2 + 2,
        event_hash=hashlib.sha256(f"terminal{index}".encode()).hexdigest(),
    )
    c.settle(
        SettleFactoryPhysicalAttemptV1(
            schema_version=SETTLE_FACTORY_PHYSICAL_ATTEMPT_SCHEMA, lease=lease, terminal_receipt=terminal
        )
    )


def _reserve(c, grant, index):
    freeze = f"freeze-{index}"
    c.register_cutoff(_cutoff_view(grant, request_freeze_id=freeze, run_id=grant.execution_authority_hash))
    return c.reserve(
        _command(
            execution_authority_hash=grant.execution_authority_hash,
            request_freeze_id=freeze,
            role=grant.role,
            run_id=grant.execution_authority_hash,
        )
    )


def test_quality_qa_budget_aggregates_retired_children_and_counts_history():
    c = FactoryPhysicalAttemptCoordinator(factory_run_id="factory-run-1")
    first = _grant_view(role="qa", stage="quality_gate")
    second = replace(first, execution_authority_hash="b" * 64)
    c.register_grant(first)
    for i in range(16):
        _complete(c, _reserve(c, first, i), i)
    c.register_successor_grant(second, (first.execution_authority_hash,))
    assert c.budget_state(first.execution_authority_hash).closed
    for i in range(16, 32):
        _complete(c, _reserve(c, second, i), i)
    c.register_cutoff(_cutoff_view(second, request_freeze_id="freeze-last", run_id=second.execution_authority_hash))
    with pytest.raises(FactoryPhysicalAttemptControlError, match="role_budget_exhausted"):
        c.reserve(
            _command(
                execution_authority_hash=second.execution_authority_hash,
                request_freeze_id="freeze-last",
                role="qa",
                run_id=second.execution_authority_hash,
            )
        )
    assert sum(c.budget_state(g.execution_authority_hash).consumed_attempts for g in (first, second)) == 32


def test_successor_unsettled_guard_is_atomic_and_preserves_original():
    c = FactoryPhysicalAttemptCoordinator(factory_run_id="factory-run-1")
    first = _grant_view(role="qa", stage="quality_gate")
    second = replace(first, execution_authority_hash="b" * 64)
    c.register_grant(first)
    _reserve(c, first, 0)
    before = c.budget_state(first.execution_authority_hash)
    with pytest.raises(FactoryPhysicalAttemptControlError, match="previous_child_unsettled"):
        c.register_successor_grant(second, (first.execution_authority_hash,))
    assert c.budget_state(first.execution_authority_hash) == before
    assert len(c._grants) == 1


@pytest.mark.parametrize(
    "field,value", [("stage_claim_nonce", "foreign"), ("workspace_fencing_token", 8), ("role", "director")]
)
def test_successor_foreign_pool_does_not_fence_old(field, value):
    c = FactoryPhysicalAttemptCoordinator(factory_run_id="factory-run-1")
    first = _grant_view(role="qa", stage="quality_gate")
    c.register_grant(first)
    second = replace(first, execution_authority_hash="b" * 64, **{field: value})
    before = c.budget_state(first.execution_authority_hash)
    with pytest.raises(FactoryPhysicalAttemptControlError, match="successor_scope_mismatch"):
        c.register_successor_grant(second, (first.execution_authority_hash,))
    assert c.budget_state(first.execution_authority_hash) == before
    assert len(c._grants) == 1


def test_quality_budget_separates_roles_and_exact_stage_claims():
    c = FactoryPhysicalAttemptCoordinator(factory_run_id="factory-run-1")
    qa = _grant_view(role="qa", stage="quality_gate")
    c.register_grant(qa)
    for i in range(32):
        _reserve(c, qa, i)
    for i, overrides in enumerate(
        ({"role": "director"}, {"stage_claim_nonce": "next"}, {"stage": "director_dispatch"})
    ):
        grant = replace(qa, execution_authority_hash=f"{i + 100:064x}", **overrides)
        c.register_grant(grant)
        assert _reserve(c, grant, 40 + i).authority_attempt_ordinal == 1


def test_successor_cannot_omit_an_existing_qa_child():
    c = FactoryPhysicalAttemptCoordinator(factory_run_id="factory-run-1")
    first = _grant_view(role="qa", stage="quality_gate")
    second = replace(first, execution_authority_hash="b" * 64)
    third = replace(first, execution_authority_hash="c" * 64)
    c.register_grant(first)
    c.register_grant(second)
    before = c.drain_snapshot()
    with pytest.raises(FactoryPhysicalAttemptControlError, match="successor_scope_mismatch"):
        c.register_successor_grant(third, (first.execution_authority_hash,))
    assert c.drain_snapshot() == before


def test_concurrent_qa_children_share_atomic_32_reservations():
    from concurrent.futures import ThreadPoolExecutor

    c = FactoryPhysicalAttemptCoordinator(factory_run_id="factory-run-1")
    first = _grant_view(role="qa", stage="quality_gate")
    second = replace(first, execution_authority_hash="b" * 64)
    for g in (first, second):
        c.register_grant(g)

    def reserve(index):
        try:
            return _reserve(c, (first, second)[index % 2], index)
        except FactoryPhysicalAttemptControlError as exc:
            assert exc.code == "factory_physical_attempt_role_budget_exhausted"
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(reserve, range(64)))
    assert sum(r is not None for r in results) == 32
    assert sum(c.budget_state(g.execution_authority_hash).reserved_count for g in (first, second)) == 32
