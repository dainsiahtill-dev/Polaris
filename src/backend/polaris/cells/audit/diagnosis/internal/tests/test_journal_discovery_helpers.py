"""Historical journal readers are verified by their audit diagnosis owner."""

from __future__ import annotations

import json
from pathlib import Path


class TestJournalDiscoveryFunctions:
    """Test journal discovery functions for audit_quick integration."""

    def test_discover_journal_run_dirs_empty(self, tmp_path: Path) -> None:
        """Test discovering journal run dirs when none exist."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            _discover_journal_run_dirs,
        )

        runtime_root = tmp_path / "runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)

        result = _discover_journal_run_dirs(runtime_root)
        assert result == []

    def test_discover_journal_run_dirs_with_runs(self, tmp_path: Path) -> None:
        """Test discovering journal run dirs when runs exist."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            _discover_journal_run_dirs,
        )

        runtime_root = tmp_path / "runtime"
        runs_root = runtime_root / "runs"
        runs_root.mkdir(parents=True, exist_ok=True)

        # Create run directories with logs
        for run_id in ["run-1", "run-2"]:
            run_dir = runs_root / run_id
            logs_dir = run_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            # Create journal file
            journal_file = logs_dir / "journal.norm.jsonl"
            journal_file.write_text("{}\n", encoding="utf-8")

        result = _discover_journal_run_dirs(runtime_root)
        assert len(result) == 2
        # Verify paths are sorted
        assert all(p.is_dir() for p in result)

    def test_resolve_journal_events_path(self, tmp_path: Path) -> None:
        """Test resolving journal events path with priority order."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            _resolve_journal_events_path,
        )

        runtime_root = tmp_path / "runtime"
        runs_root = runtime_root / "runs"
        run_dir = runs_root / "test-run"
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Create all three journal files
        (logs_dir / "journal.raw.jsonl").write_text("{}\n", encoding="utf-8")
        (logs_dir / "journal.norm.jsonl").write_text("{}\n", encoding="utf-8")
        (logs_dir / "journal.enriched.jsonl").write_text("{}\n", encoding="utf-8")

        result = _resolve_journal_events_path(run_dir)
        assert result is not None
        # Should return norm (highest priority)
        assert result.name == "journal.norm.jsonl"

    def test_load_journal_events(self, tmp_path: Path) -> None:
        """Test loading events from journal file."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            load_journal_events,
        )

        runtime_root = tmp_path / "runtime"
        runs_root = runtime_root / "runs"
        run_dir = runs_root / "test-run"
        logs_dir = run_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Create journal with events
        journal_file = logs_dir / "journal.norm.jsonl"
        events = [
            {"seq": 1, "type": "content_chunk", "content": "Hello"},
            {"seq": 2, "type": "tool_call", "tool": "read_file"},
            {"seq": 3, "type": "complete", "content": "Done"},
        ]
        lines = [json.dumps(e, ensure_ascii=False) for e in events]

        journal_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # load_journal_events takes run_dir, not runtime_root + run_id
        result = load_journal_events(run_dir)
        assert len(result) == 3
        assert result[0]["type"] == "content_chunk"
        assert result[1]["type"] == "tool_call"
        assert result[2]["type"] == "complete"

    def test_discover_strategy_receipts(self, tmp_path: Path) -> None:
        """Test discovering strategy receipt files."""
        from polaris.cells.audit.diagnosis.internal.toolkit.service import (
            discover_strategy_receipts,
        )

        runtime_root = tmp_path / "runtime"
        receipts_root = runtime_root / "strategy_runs"
        receipts_root.mkdir(parents=True, exist_ok=True)

        # Create receipt files
        for receipt_id in ["receipt-1", "receipt-2"]:
            receipt_file = receipts_root / f"{receipt_id}.json"
            receipt_file.write_text("{}\n", encoding="utf-8")

        result = discover_strategy_receipts(runtime_root)
        assert len(result) == 2
        assert all(p.suffix == ".json" for p in result)
