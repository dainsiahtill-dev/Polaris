"""Same-run retry must remint an expired factory_run_deadline.

Live L2-13: quality_gate retry after started_at+5400 left
quality_repair_deadline_insufficient and never started the owner Director
LLM. Isolated backend was still up; only the caller-supplied wall-clock
had expired. Same-run owner repair must receive a fresh run deadline.
"""

from __future__ import annotations

from polaris.cells.factory.pipeline.internal.factory_deadline_calculations import (
    extend_factory_run_deadline_for_same_run_retry,
)


def test_same_run_retry_extends_expired_factory_deadline() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 120.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_safety_seconds": 27.0,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_source"] == "same_run_retry_epoch"
    assert result["factory_run_deadline_extension_count"] == 1
    assert result["factory_run_original_deadline_epoch_seconds"] == now - 120.0
    remaining = float(result["factory_run_deadline_epoch_seconds"]) - now
    assert remaining >= 180.0
    assert remaining <= 1800.0
    director_remaining = float(result["factory_director_execution_deadline_epoch_seconds"]) - now
    assert director_remaining >= 180.0
    assert director_remaining <= remaining


def test_same_run_retry_keeps_still_viable_factory_deadline() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now + 2000.0,
            "factory_run_timeout_seconds": 1800.0,
        },
        retry_stage="director_dispatch",
    )

    assert result is None


def test_l217_implementation_remint_when_remaining_cannot_admit_director() -> None:
    """Live L2-17 remint-2: rem=259 skipped, then dispatch_deadline_blocker."""

    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now + 259.0,
            "factory_run_timeout_seconds": 1800.0,
        },
        retry_stage="implementation",
    )

    assert result is not None
    remaining = float(result["factory_run_deadline_epoch_seconds"]) - now
    assert remaining >= 1800.0 - 30.0


def test_same_run_retry_still_extends_after_twentieth_quality_remint() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 20,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 21


def test_same_run_retry_caps_deadline_extensions() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 32,
        },
        retry_stage="director_dispatch",
    )

    assert result is None


def test_same_run_retry_allows_third_quality_extension() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 3,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 4


def test_same_run_retry_allows_fifth_quality_extension() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 4,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 5


def test_same_run_retry_allows_sixth_quality_extension() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 5,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 6


def test_same_run_retry_allows_seventh_quality_extension() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 6,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 7


def test_same_run_retry_allows_eighth_quality_extension() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 7,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 8


def test_same_run_retry_allows_ninth_quality_extension() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 8,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 9


def test_same_run_retry_allows_tenth_quality_extension() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 9,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 10


def test_same_run_retry_allows_twelfth_quality_extension() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 11,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 12


def test_same_run_quality_retry_remints_shrunken_leftover_timeout() -> None:
    """A leftover 193s epoch is not a viable quality repair budget.

    Live L2-15 remint-9 died after ~19 min. The next retry saw rem=193
    (shrunken factory_run_timeout_seconds leftover) and skipped remint
    because 193 >= 180. Quality never got a full same-run epoch.
    """

    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now + 193.0,
            "factory_run_timeout_seconds": 223.0,
            "factory_run_deadline_safety_seconds": 30.0,
            "factory_run_deadline_extension_count": 8,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    remaining = float(result["factory_run_deadline_epoch_seconds"]) - now
    assert remaining >= 1700.0
    assert remaining <= 1800.0
    assert result["factory_run_deadline_extension_count"] == 9
    assert result["factory_run_timeout_seconds"] >= 1800.0


def test_same_run_retry_allows_thirteenth_quality_extension() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 12,
        },
        retry_stage="quality_gate",
    )

    assert result is not None
    assert result["factory_run_deadline_extension_count"] == 13


def test_same_run_retry_extends_missing_deadline_for_owner_repair() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={"factory_run_timeout_seconds": 5400.0},
        retry_stage="quality_gate",
    )

    assert result is not None
    remaining = float(result["factory_run_deadline_epoch_seconds"]) - now
    assert remaining >= 180.0
    assert result["factory_run_deadline_source"] == "same_run_retry_epoch"
