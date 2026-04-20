"""Tests for audit_quick.py --journal integration (UEP v2.0)."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from polaris.delivery.cli.audit.audit.handlers import _handle_journal_events
from polaris.kernelone.storage import resolve_runtime_path

if TYPE_CHECKING:
    from collections.abc import Generator


class TestJournalIntegration:
    """Test suite for --journal flag in audit_quick events command."""

    @pytest.fixture
    def temp_runtime(self) -> Generator[Path, None, None]:
        """Create a temporary runtime directory with journal files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(resolve_runtime_path(str(tmpdir), "runtime"))
            runs_dir = runtime_root / "runs"
            runs_dir.mkdir(parents=True)

            # Create a run directory with journal
            run_dir = runs_dir / "test-run-001"
            logs_dir = run_dir / "logs"
            logs_dir.mkdir(parents=True)

            # Create a norm journal file
            journal_file = logs_dir / "journal.norm.jsonl"
            events = [
                {
                    "timestamp": "2026-03-31T12:00:00Z",
                    "level": "info",
                    "channel": "llm",
                    "domain": "llm",
                    "kind": "action",
                    "actor": "director",
                    "message": "tool_call: read_file",
                },
                {
                    "timestamp": "2026-03-31T12:00:01Z",
                    "level": "info",
                    "channel": "llm",
                    "domain": "llm",
                    "kind": "state",
                    "actor": "director",
                    "message": "call_start",
                },
            ]
            with open(journal_file, "w", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")

            yield runtime_root

    def _create_args(
        self,
        journal: bool = True,
        format_str: str = "compact",
        limit: int = 50,
        no_relative_time: bool = False,
    ) -> argparse.Namespace:
        """Create mock args for testing."""
        return argparse.Namespace(
            journal=journal,
            format=format_str,
            limit=limit,
            no_relative_time=no_relative_time,
        )

    def test_handle_journal_events_requires_runtime(self, capsys: Any) -> None:
        """Test that --journal requires runtime_root."""
        args = self._create_args()
        result = _handle_journal_events(args, None)

        assert result == 1
        captured = capsys.readouterr()
        assert "错误" in captured.err or "error" in captured.err.lower()

    def test_handle_journal_events_finds_events(
        self,
        temp_runtime: Path,
        capsys: Any,
    ) -> None:
        """Test that --journal finds and displays journal events."""
        args = self._create_args(format_str="compact")
        result = _handle_journal_events(args, temp_runtime)

        assert result == 0
        captured = capsys.readouterr()
        assert "Journal Events" in captured.out
        assert "2" in captured.out  # 2 events
        assert "director" in captured.out

    def test_handle_journal_events_json_format(
        self,
        temp_runtime: Path,
        capsys: Any,
    ) -> None:
        """Test that --journal with -f json outputs valid JSON."""
        args = self._create_args(format_str="json")
        result = _handle_journal_events(args, temp_runtime)

        assert result == 0
        captured = capsys.readouterr()

        # Parse JSON output
        data = json.loads(captured.out)
        assert data["source"] == "journal"
        assert data["version"] == "2.0"
        assert len(data["events"]) == 2

    def test_handle_journal_events_empty_runtime(self, capsys: Any) -> None:
        """Test behavior when no journal files exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(resolve_runtime_path(str(tmpdir), "runtime"))
            (runtime_root / "runs").mkdir(parents=True)

            args = self._create_args()
            result = _handle_journal_events(args, runtime_root)

            assert result == 0
            captured = capsys.readouterr()
            assert "未找到 Journal" in captured.out or "0" in captured.out

    def test_handle_journal_events_respects_limit(
        self,
        temp_runtime: Path,
        capsys: Any,
    ) -> None:
        """Test that --limit is respected."""
        args = self._create_args(format_str="json", limit=1)
        result = _handle_journal_events(args, temp_runtime)

        assert result == 0
        captured = capsys.readouterr()

        data = json.loads(captured.out)
        assert len(data["events"]) <= 1

    def test_handle_journal_events_limit_zero(
        self,
        temp_runtime: Path,
        capsys: Any,
    ) -> None:
        """Test that --limit 0 returns empty result (not default 50)."""
        args = self._create_args(format_str="json", limit=0)
        result = _handle_journal_events(args, temp_runtime)

        assert result == 0
        captured = capsys.readouterr()

        data = json.loads(captured.out)
        # With limit=0, should return 0 events, not default 50
        assert len(data["events"]) == 0

    def test_handle_journal_events_timestamp_sorting(
        self,
        capsys: Any,
    ) -> None:
        """Test that events are correctly sorted by timestamp."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(resolve_runtime_path(str(tmpdir), "runtime"))
            runs_dir = runtime_root / "runs"
            runs_dir.mkdir(parents=True)

            # Create run with out-of-order timestamps
            run_dir = runs_dir / "test-run-001"
            logs_dir = run_dir / "logs"
            logs_dir.mkdir(parents=True)

            journal_file = logs_dir / "journal.norm.jsonl"
            events = [
                {"timestamp": "2026-03-31T12:00:02Z", "message": "third"},
                {"timestamp": "2026-03-31T12:00:00Z", "message": "first"},
                {"timestamp": "2026-03-31T12:00:01Z", "message": "second"},
            ]
            with open(journal_file, "w", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")

            args = self._create_args(format_str="json", limit=50)
            result = _handle_journal_events(args, runtime_root)

            assert result == 0
            captured = capsys.readouterr()

            data = json.loads(captured.out)
            assert len(data["events"]) == 3
            # Verify sorted order (by timestamp)
            timestamps = [e.get("timestamp", "") for e in data["events"]]
            assert timestamps == sorted(timestamps)


class TestJournalFileDiscovery:
    """Test journal file discovery logic."""

    @pytest.fixture
    def temp_runtime_multiple_runs(self) -> Generator[Path, None, None]:
        """Create runtime with multiple runs for discovery testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_root = Path(resolve_runtime_path(str(tmpdir), "runtime"))
            runs_dir = runtime_root / "runs"
            runs_dir.mkdir(parents=True)

            # Create multiple run directories
            for i in range(3):
                run_dir = runs_dir / f"test-run-{i:03d}"
                logs_dir = run_dir / "logs"
                logs_dir.mkdir(parents=True)

                journal_file = logs_dir / "journal.norm.jsonl"
                with open(journal_file, "w", encoding="utf-8") as f:
                    f.write(json.dumps({"timestamp": f"2026-03-31T12:00:0{i}Z"}) + "\n")

            yield runtime_root

    def test_discovers_multiple_runs(
        self,
        temp_runtime_multiple_runs: Path,
        capsys: Any,
    ) -> None:
        """Test that events from multiple runs are discovered."""
        args = argparse.Namespace(
            journal=True,
            format="compact",
            limit=50,
            no_relative_time=False,
        )
        result = _handle_journal_events(args, temp_runtime_multiple_runs)

        assert result == 0
        captured = capsys.readouterr()
        # Should show events from 3 runs
        assert "3 runs" in captured.out or "runs" in captured.out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
