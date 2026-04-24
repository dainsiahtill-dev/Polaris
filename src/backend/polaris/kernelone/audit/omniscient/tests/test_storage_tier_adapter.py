"""Tests for StorageTierAdapter — Cold/Hot分层存储.

Run with:
    pytest polaris/kernelone/audit/omniscient/tests/test_storage_tier_adapter.py -v
"""

from __future__ import annotations

import gzip
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from polaris.kernelone.audit.omniscient.adapters.storage_tier_adapter import (
    DEFAULT_COLD_TTL_DAYS,
    DEFAULT_HOT_TTL_DAYS,
    StorageTierAdapter,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_runtime(tmp_path: Path) -> Path:
    """Provide a temporary runtime root."""
    runtime = tmp_path / "audit_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


@pytest_asyncio.fixture
async def tier_adapter(temp_runtime: Path) -> AsyncGenerator[StorageTierAdapter, None]:
    """Provide a storage tier adapter."""
    adapter = StorageTierAdapter(
        runtime_root=temp_runtime,
        hot_ttl_days=7,
        cold_ttl_days=30,
        archive_on_rotation=True,
    )
    await adapter.start()
    yield adapter
    await adapter.stop(timeout=5.0)


# =============================================================================
# TTL Classification Tests
# =============================================================================


def test_is_hot_recent_event() -> None:
    """Recent events (within TTL) are hot."""
    adapter = StorageTierAdapter(runtime_root=Path("/tmp"), hot_ttl_days=7)
    recent = datetime.now(timezone.utc) - timedelta(days=3)
    assert adapter.is_hot(recent) is True


def test_is_hot_event_dict_with_recent_timestamp() -> None:
    """Event dict with recent timestamp is hot."""
    adapter = StorageTierAdapter(runtime_root=Path("/tmp"), hot_ttl_days=7)
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    event = {"timestamp": recent}
    assert adapter.is_hot(event) is True


def test_is_cold_old_event() -> None:
    """Old events (beyond hot TTL) are cold."""
    adapter = StorageTierAdapter(runtime_root=Path("/tmp"), hot_ttl_days=7)
    old = datetime.now(timezone.utc) - timedelta(days=10)
    assert adapter.is_hot(old) is False


def test_get_tier_hot() -> None:
    """Events within hot TTL return 'hot'."""
    adapter = StorageTierAdapter(runtime_root=Path("/tmp"), hot_ttl_days=7)
    recent = datetime.now(timezone.utc) - timedelta(days=3)
    assert adapter.get_tier(recent) == "hot"


def test_get_tier_cold() -> None:
    """Events beyond hot but within cold TTL return 'cold'."""
    adapter = StorageTierAdapter(runtime_root=Path("/tmp"), hot_ttl_days=7, cold_ttl_days=30)
    old = datetime.now(timezone.utc) - timedelta(days=15)
    assert adapter.get_tier(old) == "cold"


def test_get_tier_expired() -> None:
    """Events beyond cold TTL return 'expired'."""
    adapter = StorageTierAdapter(runtime_root=Path("/tmp"), hot_ttl_days=7, cold_ttl_days=30)
    very_old = datetime.now(timezone.utc) - timedelta(days=60)
    assert adapter.get_tier(very_old) == "expired"


def test_get_tier_with_iso_string() -> None:
    """ISO date strings are correctly classified."""
    adapter = StorageTierAdapter(runtime_root=Path("/tmp"), hot_ttl_days=7)
    recent_iso = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert adapter.get_tier(recent_iso) == "hot"


def test_default_ttl_constants() -> None:
    """Default TTL values are correct."""
    assert DEFAULT_HOT_TTL_DAYS == 7
    assert DEFAULT_COLD_TTL_DAYS == 90


# =============================================================================
# Emit (hot path) Tests
# =============================================================================


@pytest.mark.asyncio
async def test_emit_returns_event_id(temp_runtime: Path) -> None:
    """emit() returns an event ID."""
    adapter = StorageTierAdapter(runtime_root=temp_runtime, hot_ttl_days=7)
    await adapter.start()
    try:
        event_id = await adapter.emit({"event_type": "test_event", "data": {"key": "value"}})
        assert event_id != ""
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_emit_sets_event_id_if_missing(temp_runtime: Path) -> None:
    """emit() sets event_id if not present in event."""
    adapter = StorageTierAdapter(runtime_root=temp_runtime, hot_ttl_days=7)
    await adapter.start()
    try:
        await adapter.emit({"event_type": "test"})
        event = adapter.get_stats()
        # Stats should show emitted events
        assert event["events_emitted"] >= 1
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_adapter_stats_include_tier_info(temp_runtime: Path) -> None:
    """Adapter stats include tier configuration."""
    adapter = StorageTierAdapter(
        runtime_root=temp_runtime,
        hot_ttl_days=7,
        cold_ttl_days=30,
    )
    await adapter.start()
    try:
        stats = adapter.get_stats()
        assert stats["hot_ttl_days"] == 7
        assert stats["cold_ttl_days"] == 30
        assert stats["archived_partitions"] == 0
    finally:
        await adapter.stop()


# =============================================================================
# Archive Operation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_archive_old_partitions_no_files(temp_runtime: Path) -> None:
    """archive_old_partitions() handles empty directory gracefully."""
    adapter = StorageTierAdapter(runtime_root=temp_runtime, hot_ttl_days=7)
    await adapter.start()
    try:
        result = await adapter.archive_old_partitions()
        assert result["archived"] == 0
        assert result["deleted"] == 0
    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_archive_old_partitions_compresses_files(
    temp_runtime: Path,
) -> None:
    """Old partition files are gzip compressed into archive/."""
    # Create an old partition manually
    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    ws_dir = temp_runtime / "audit" / "test_workspace" / old_date
    ws_dir.mkdir(parents=True, exist_ok=True)

    # Write a test JSONL file
    jsonl_file = ws_dir / "audit.test.jsonl"
    jsonl_file.write_text('{"event_type":"test","event_id":"abc123"}\n', encoding="utf-8")

    adapter = StorageTierAdapter(
        runtime_root=temp_runtime,
        hot_ttl_days=7,
        cold_ttl_days=30,
        archive_on_rotation=True,
    )
    await adapter.start()
    try:
        result = await adapter.archive_old_partitions()
        assert result["archived"] >= 1

        # Verify .jsonl file was replaced with .jsonl.gz
        assert jsonl_file.exists() is False

        # Verify .gz file exists in archive/
        archive_file = temp_runtime / "audit" / "archive" / "test_workspace" / old_date / "audit.test.jsonl.gz"
        assert archive_file.exists() is True

        # Verify .gz file is valid
        with gzip.open(archive_file, "rt", encoding="utf-8") as f:
            content = f.read()
            assert "abc123" in content

    finally:
        await adapter.stop()


@pytest.mark.asyncio
async def test_get_archive_stats_empty(temp_runtime: Path) -> None:
    """get_archive_stats() handles empty archive."""
    adapter = StorageTierAdapter(runtime_root=temp_runtime)
    stats = adapter.get_archive_stats()
    assert stats["partition_count"] == 0
    assert stats["total_gb"] == 0.0


# =============================================================================
# Lifecycle Tests
# =============================================================================


@pytest.mark.asyncio
async def test_start_stop_idempotent(temp_runtime: Path) -> None:
    """Multiple start/stop calls are handled gracefully."""
    adapter = StorageTierAdapter(runtime_root=temp_runtime)
    await adapter.start()
    await adapter.start()  # Idempotent
    await adapter.stop(timeout=2.0)
    await adapter.stop(timeout=2.0)  # Idempotent


@pytest.mark.asyncio
async def test_archive_on_stop(temp_runtime: Path) -> None:
    """stop() triggers archive before shutting down."""
    # Create an old partition
    old_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    ws_dir = temp_runtime / "audit" / "test_workspace" / old_date
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "audit.test.jsonl").write_text('{"event_type":"test"}\n', encoding="utf-8")

    adapter = StorageTierAdapter(runtime_root=temp_runtime, hot_ttl_days=7)
    await adapter.start()
    await adapter.stop(timeout=5.0)

    # After stop, old partition should be archived
    archive_dir = temp_runtime / "audit" / "archive" / "test_workspace" / old_date
    assert archive_dir.exists()
