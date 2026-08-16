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
            "factory_run_deadline_epoch_seconds": now + 900.0,
            "factory_run_timeout_seconds": 1800.0,
        },
        retry_stage="quality_gate",
    )

    assert result is None


def test_same_run_retry_caps_deadline_extensions() -> None:
    now = 2_000_000.0
    result = extend_factory_run_deadline_for_same_run_retry(
        now_epoch=now,
        metadata={
            "factory_run_deadline_epoch_seconds": now - 10.0,
            "factory_run_timeout_seconds": 1800.0,
            "factory_run_deadline_extension_count": 8,
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
