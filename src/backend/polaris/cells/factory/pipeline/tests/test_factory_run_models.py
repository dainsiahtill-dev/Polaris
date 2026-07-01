"""Factory run persistence model guard tests."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_models import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.internal.factory_store import FactoryStore


def _factory_run_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "factory-run-1",
        "config": {
            "name": "Convergence smoke",
            "description": None,
            "stages": ["pm_planning"],
            "auto_dispatch": True,
            "checkpoint_interval": 300,
        },
        "status": "running",
        "created_at": "2026-07-01T00:00:00+00:00",
        "updated_at": None,
        "started_at": None,
        "completed_at": None,
        "stages_completed": [],
        "stages_failed": [],
        "recovery_point": None,
        "metadata": {"source": "test"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("status", list(FactoryRunStatus))
def test_factory_run_from_dict_accepts_persisted_status(status: FactoryRunStatus) -> None:
    run = FactoryRun.from_dict(_factory_run_payload(status=status.value))

    assert run.status is status


@pytest.mark.parametrize(
    ("override", "expected_message"),
    [
        ({"status": ""}, "must be a non-empty string"),
        ({"status": None}, "must be a non-empty string"),
        ({"status": "unknown"}, "must be one of"),
    ],
)
def test_factory_run_from_dict_rejects_invalid_status(
    override: dict[str, Any],
    expected_message: str,
) -> None:
    with pytest.raises(ValueError, match=expected_message):
        FactoryRun.from_dict(_factory_run_payload(**override))


def test_factory_run_from_dict_rejects_missing_status() -> None:
    payload = _factory_run_payload()
    payload.pop("status")

    with pytest.raises(ValueError, match="field 'status' is required"):
        FactoryRun.from_dict(payload)


def test_factory_run_to_dict_round_trips_status() -> None:
    run = FactoryRun(
        id="factory-run-2",
        config=FactoryConfig(name="Round trip", stages=["quality_gate"]),
        status=FactoryRunStatus.COMPLETED,
        created_at="2026-07-01T00:00:00+00:00",
        metadata={"source": "roundtrip"},
    )

    restored = FactoryRun.from_dict(run.to_dict())

    assert restored.status is FactoryRunStatus.COMPLETED
    assert restored.metadata == {"source": "roundtrip"}


@pytest.mark.asyncio
async def test_factory_store_skips_run_record_with_missing_status(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = FactoryStore(tmp_path / "factory-runs")
    run_dir = store.get_run_dir("factory-run-corrupt")
    run_dir.mkdir(parents=True)
    run_file = run_dir / "run.json"
    payload = _factory_run_payload(id="factory-run-corrupt")
    payload.pop("status")
    run_file.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    caplog.set_level(logging.WARNING)

    run = await store.get_run("factory-run-corrupt")

    assert run is None
    assert "invalid run record skipped" in caplog.text
    assert "field 'status' is required" in caplog.text
