from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_inflight import (
    ProviderAttemptDrainError,
    ProviderAttemptInFlightCoordinator,
)
from polaris.kernelone.llm.engine.contracts import (
    FrozenFinalProviderAttemptV1,
    ProviderAttemptDrainResultV1,
    ProviderAttemptTerminalFailureV1,
)


def _attempt(
    *,
    verification_scope: str,
    scope_id: str,
    provider_request_id: str = "provider-request-1",
    attempt_number: int = 1,
) -> FrozenFinalProviderAttemptV1:
    return FrozenFinalProviderAttemptV1(
        provider_request_id=provider_request_id,
        request_freeze_id="freeze-1",
        factory_run_id=scope_id if verification_scope == "factory" else "",
        scope_id=scope_id,
        run_id="run-1",
        turn_id="turn-1",
        call_id="call-1",
        role="director",
        provider="openai",
        model="model-1",
        attempt_number=attempt_number,
        verification_scope=verification_scope,
        semantic_request_hash="a" * 64,
        physical_wire_hash="b" * 64,
        composite_request_hash="c" * 64,
        dispatch_view={},
        durable_view={},
    )


@pytest.mark.asyncio
async def test_wait_settled_is_per_scope_and_rejects_scope_mismatch() -> None:
    first = ProviderAttemptInFlightCoordinator.for_factory_run("factory-run-1")
    second = ProviderAttemptInFlightCoordinator.for_factory_run("factory-run-2")
    attempt = _attempt(verification_scope="factory", scope_id="factory-run-1")
    first.register(attempt)

    second_result = await second.wait_settled(
        verification_scope="factory",
        scope_id="factory-run-2",
        timeout_seconds=0.1,
    )
    assert second_result.settled is True
    assert second_result.inflight_request_ids == ()

    with pytest.raises(ProviderAttemptDrainError, match="scope mismatch"):
        await first.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-2",
            timeout_seconds=0.1,
        )

    with pytest.raises(ProviderAttemptDrainError) as timeout:
        await first.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.01,
        )
    assert timeout.value.code == "provider_attempt_drain_timeout"
    assert timeout.value.result.inflight_request_ids == (attempt.provider_request_id,)

    first.terminal_acked(attempt.provider_request_id)
    result = await first.wait_settled(
        verification_scope="factory",
        scope_id="factory-run-1",
        timeout_seconds=0.1,
    )
    assert result.settled is True


@pytest.mark.asyncio
async def test_terminal_failure_stays_inflight_and_fails_drain_with_typed_diagnostic() -> None:
    coordinator = ProviderAttemptInFlightCoordinator.for_factory_run("factory-run-1")
    attempt = _attempt(verification_scope="factory", scope_id="factory-run-1")
    coordinator.register(attempt)
    coordinator.terminal_failed(attempt.provider_request_id, OSError("terminal fsync failed"))

    with pytest.raises(ProviderAttemptDrainError) as failed:
        await coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.1,
        )
    assert failed.value.code == "provider_attempt_terminal_persistence_failed"
    assert failed.value.result.settled is False
    assert failed.value.result.inflight_request_ids == (attempt.provider_request_id,)
    assert failed.value.result.terminal_failures[0].provider_request_id == attempt.provider_request_id
    assert failed.value.result.terminal_failures[0].error_type == "OSError"
    assert "terminal fsync failed" in failed.value.result.terminal_failures[0].error


@pytest.mark.asyncio
async def test_terminal_failure_with_empty_message_preserves_error_type_diagnostic() -> None:
    coordinator = ProviderAttemptInFlightCoordinator.for_factory_run("factory-run-1")
    attempt = _attempt(verification_scope="factory", scope_id="factory-run-1")
    coordinator.register(attempt)
    coordinator.terminal_failed(attempt.provider_request_id, RuntimeError())

    with pytest.raises(ProviderAttemptDrainError) as failed:
        await coordinator.wait_settled(
            verification_scope="factory",
            scope_id="factory-run-1",
            timeout_seconds=0.1,
        )
    terminal_failure = failed.value.result.terminal_failures[0]
    assert terminal_failure.error_type == "RuntimeError"
    assert terminal_failure.error == "RuntimeError"


def test_duplicate_registration_late_settle_and_scope_confusion_fail_closed() -> None:
    factory = ProviderAttemptInFlightCoordinator.for_factory_run("factory-run-1")
    role_session = ProviderAttemptInFlightCoordinator.for_role_session("role-session-1")
    factory_attempt = _attempt(verification_scope="factory", scope_id="factory-run-1")
    factory.register(factory_attempt)

    with pytest.raises(RuntimeError, match="duplicate"):
        factory.register(factory_attempt)
    with pytest.raises(RuntimeError, match="scope"):
        role_session.register(factory_attempt)

    factory.terminal_acked(factory_attempt.provider_request_id)
    with pytest.raises(RuntimeError, match=r"late|unknown"):
        factory.terminal_acked(factory_attempt.provider_request_id)
    with pytest.raises(RuntimeError, match=r"late|unknown"):
        factory.terminal_failed(factory_attempt.provider_request_id, OSError("late failure"))


def test_concurrent_attempt_identity_minting_is_unique_and_monotonic() -> None:
    coordinator = ProviderAttemptInFlightCoordinator.for_role_session("role-session-1")

    with ThreadPoolExecutor(max_workers=16) as pool:
        identities = tuple(pool.map(lambda _index: coordinator.mint_attempt_identity(), range(200)))

    attempt_numbers = sorted(number for number, _request_id in identities)
    request_ids = tuple(request_id for _number, request_id in identities)
    assert attempt_numbers == list(range(1, 201))
    assert len(request_ids) == len(set(request_ids)) == 200


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("provider_request_id", ""),
        ("role", ""),
        ("provider", ""),
        ("model", ""),
        ("attempt_number", 0),
        ("semantic_request_hash", "A" * 64),
        ("physical_wire_hash", "b" * 63),
        ("composite_request_hash", "not-a-hash"),
    ),
)
def test_frozen_attempt_authority_fields_validate_at_runtime(field_name: str, invalid_value: object) -> None:
    attempt = _attempt(verification_scope="factory", scope_id="factory-run-1")
    with pytest.raises(ValueError):
        replace(attempt, **{field_name: invalid_value})

    with pytest.raises(ValueError, match="Factory"):
        replace(attempt, scope_id="another-run")
    with pytest.raises(ValueError, match="role-session"):
        replace(attempt, verification_scope="role_session")


@pytest.mark.parametrize("field_name", ("dispatch_view", "durable_view"))
def test_frozen_attempt_rejects_non_mapping_views(field_name: str) -> None:
    attempt = _attempt(verification_scope="factory", scope_id="factory-run-1")

    with pytest.raises(TypeError, match="mapping"):
        replace(attempt, **{field_name: []})


def test_drain_result_and_failure_contracts_reject_contradictory_or_untyped_state() -> None:
    failure = ProviderAttemptTerminalFailureV1(
        provider_request_id="request-1",
        error_type="OSError",
        error="fsync failed",
    )
    with pytest.raises(ValueError, match="settled flag"):
        ProviderAttemptDrainResultV1(
            verification_scope="factory",
            scope_id="factory-run-1",
            settled=True,
            inflight_request_ids=("request-1",),
            terminal_failures=(),
        )
    with pytest.raises(ValueError, match="remain registered"):
        ProviderAttemptDrainResultV1(
            verification_scope="factory",
            scope_id="factory-run-1",
            settled=False,
            inflight_request_ids=("request-2",),
            terminal_failures=(failure,),
        )
    with pytest.raises(ValueError, match="error"):
        ProviderAttemptTerminalFailureV1(provider_request_id="request-1", error_type="OSError", error="")
