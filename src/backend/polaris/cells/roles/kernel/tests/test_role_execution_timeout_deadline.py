"""Session budgets reuse the canonical physical execution deadline clamp."""

import time
from typing import Any

import pytest
from polaris.cells.roles.kernel.public import llm_caller_contracts


def test_public_execution_timeout_clamp_keeps_budget_without_deadline() -> None:
    assert llm_caller_contracts.clamp_role_execution_timeout_to_deadline(timeout_seconds=12, context={}) == 12


def test_public_execution_timeout_clamp_respects_absolute_deadline() -> None:
    limit = llm_caller_contracts.clamp_role_execution_timeout_to_deadline(
        timeout_seconds=30,
        context={"factory_run_deadline_epoch_seconds": time.time() + 3},
    )
    assert 1 <= limit <= 3


def test_public_execution_timeout_clamp_rejects_expired_deadline() -> None:
    with pytest.raises(RuntimeError, match="execution_deadline_exhausted"):
        llm_caller_contracts.clamp_role_execution_timeout_to_deadline(
            timeout_seconds=30,
            context={"factory_run_deadline_epoch_seconds": time.time() - 1},
        )


@pytest.mark.parametrize(
    ("timeout", "context", "expected"),
    [
        (12.75, None, 12.75),
        (12.75, {}, 12.75),
        (0.0, None, 0.1),
        (-4.0, {}, 0.1),
        (0.05, {}, 0.1),
        (9.5, {"factory_run_deadline_epoch_seconds": 100.25}, 0.25),
        (0.125, {"factory_run_deadline_epoch_seconds": 100.25}, 0.125),
        (9.5, {"factory_run_deadline_epoch_seconds": 100.05}, 0.04999999999999716),
        (9.5, {"metadata": {"factory_run_deadline_epoch_seconds": 100.25}}, 0.25),
        (9.5, {"factory_run_deadline_epoch_seconds": " 100.25 "}, 0.25),
        (
            20.0,
            {"factory_run_deadline_epoch_seconds": 110.0, "metadata": {"factory_run_deadline_epoch_seconds": 100.25}},
            10.0,
        ),
        (
            9.5,
            {"factory_run_deadline_epoch_seconds": None, "metadata": {"factory_run_deadline_epoch_seconds": 100.25}},
            0.25,
        ),
        (
            9.5,
            {"factory_run_deadline_epoch_seconds": 102.5, "factory_director_execution_deadline_epoch_seconds": 99.0},
            2.5,
        ),
        (
            9.5,
            {"factory_run_deadline_epoch_seconds": 99.0, "factory_director_execution_deadline_epoch_seconds": 100.75},
            0.75,
        ),
        (
            9.5,
            {"factory_run_deadline_epoch_seconds": 101.0, "factory_director_execution_deadline_epoch_seconds": 100.25},
            0.25,
        ),
        (9.5, {"metadata": [100.25]}, 9.5),
        (True, {}, 1.0),
        (float("nan"), {}, 0.1),
        (float("inf"), {}, float("inf")),
        (float("inf"), {"factory_run_deadline_epoch_seconds": 100.25}, 0.25),
        ("12.75", {}, 12.75),
    ],
)
def test_public_float_deadline_matches_frozen_adapter_goldens(
    timeout: Any, context: Any, expected: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "time", lambda: 100.0)
    result = llm_caller_contracts.clamp_role_execution_timeout_seconds_to_deadline(
        timeout_seconds=timeout, context=context
    )
    assert type(result) is float
    assert result.hex() == expected.hex()


@pytest.mark.parametrize(
    "key", ["factory_run_deadline_epoch_seconds", "factory_director_execution_deadline_epoch_seconds"]
)
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize(
    "invalid", [True, False, 0, -1, float("nan"), float("inf"), float("-inf"), "NaN", "Infinity", "nonsense", ""]
)
def test_public_float_deadline_rejects_invalid_run_and_wave_values(
    key: str, nested: bool, invalid: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "time", lambda: 100.0)
    context = {"metadata": {key: invalid}} if nested else {key: invalid}
    with pytest.raises(RuntimeError, match=r"^factory_director_execution_deadline_invalid$"):
        llm_caller_contracts.clamp_role_execution_timeout_seconds_to_deadline(timeout_seconds=9.5, context=context)


@pytest.mark.parametrize(
    "context",
    [
        {"factory_run_deadline_epoch_seconds": 100.0},
        {"factory_run_deadline_epoch_seconds": 99.0},
        {"factory_run_deadline_epoch_seconds": 102.5, "factory_director_execution_deadline_epoch_seconds": 100.0},
    ],
)
def test_public_float_deadline_preserves_exhausted_and_exact_now_selection(
    context: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "time", lambda: 100.0)
    with pytest.raises(RuntimeError, match=r"^factory_director_execution_deadline_exhausted$"):
        llm_caller_contracts.clamp_role_execution_timeout_seconds_to_deadline(timeout_seconds=9.5, context=context)


def test_public_float_deadline_rejects_invalid_wave_even_with_viable_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "time", lambda: 100.0)
    with pytest.raises(RuntimeError, match=r"^factory_director_execution_deadline_invalid$"):
        llm_caller_contracts.clamp_role_execution_timeout_seconds_to_deadline(
            timeout_seconds=9.5,
            context={
                "factory_run_deadline_epoch_seconds": 110.0,
                "factory_director_execution_deadline_epoch_seconds": True,
            },
        )


def test_existing_integer_deadline_contract_keeps_floor_and_subsecond_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "time", lambda: 100.0)
    with pytest.raises(RuntimeError, match=r"^factory_director_execution_deadline_exhausted$"):
        llm_caller_contracts.clamp_role_execution_timeout_to_deadline(
            timeout_seconds=30, context={"factory_run_deadline_epoch_seconds": 100.5}
        )
    result = llm_caller_contracts.clamp_role_execution_timeout_to_deadline(
        timeout_seconds=30, context={"factory_run_deadline_epoch_seconds": 102.75}
    )
    assert type(result) is int
    assert result == 2
    unchanged = llm_caller_contracts.clamp_role_execution_timeout_to_deadline(timeout_seconds=12, context={})
    assert type(unchanged) is int
    assert unchanged == 12
