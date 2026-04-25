"""Unit tests for polaris.cells.archive.run_archive.internal.stream_archiver."""

from __future__ import annotations

import gzip
import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.archive.run_archive.internal.stream_archiver import (
    StreamArchiver,
    StreamArchiverError,
    create_stream_archiver,
)


class TestStreamArchiverError:
    """Tests for StreamArchiverError."""

    def test_basic_error(self) -> None:
        exc = StreamArchiverError("something failed")
        assert str(exc) == "something failed"
        assert exc.archive_id == ""

    def test_error_with_archive_id(self) -> None:
        exc = StreamArchiverError("failed", archive_id="abc123")
        assert exc.archive_id == "abc123"


class TestStreamArchiverArchiveTurn:
    """Tests for StreamArchiver.archive_turn."""

    @pytest.fixture
    def mock_archiver(self) -> MagicMock:
        archiver = MagicMock()
        archiver.history_root = Path("/tmp/history")
        archiver._kernel_fs = MagicMock()
        archiver._kernel_fs.to_workspace_relative_path = lambda p: Path(p).name
        archiver._kernel_fs.workspace_write_bytes = MagicMock()
        archiver._kernel_fs.workspace_write_text = MagicMock()
        return archiver

    @pytest.mark.asyncio
    async def test_archive_turn_success(self, mock_archiver: MagicMock) -> None:
        stream_archiver = StreamArchiver(mock_archiver)
        events = [{"type": "chunk", "data": "hello"}, {"type": "chunk", "data": "world"}]

        with patch.object(Path, "mkdir") as mock_mkdir:
            archive_id = await stream_archiver.archive_turn(
                session_id="s1",
                turn_id="t1",
                events=events,
            )

        assert archive_id == "t1"
        mock_mkdir.assert_called_once_with(parents=True, exist_ok=True)

    @pytest.mark.asyncio
    async def test_archive_turn_empty_events(self, mock_archiver: MagicMock) -> None:
        stream_archiver = StreamArchiver(mock_archiver)

        with patch.object(Path, "mkdir"):
            archive_id = await stream_archiver.archive_turn(
                session_id="s1",
                turn_id="t1",
                events=[],
            )

        assert archive_id == "t1"

    @pytest.mark.asyncio
    async def test_archive_turn_os_error(self, mock_archiver: MagicMock) -> None:
        stream_archiver = StreamArchiver(mock_archiver)

        with (
            patch.object(Path, "mkdir", side_effect=OSError("disk full")),
            pytest.raises(StreamArchiverError, match="disk full"),
        ):
            await stream_archiver.archive_turn(
                session_id="s1",
                turn_id="t1",
                events=[{"type": "chunk"}],
            )


class TestStreamArchiverGetArchive:
    """Tests for StreamArchiver.get_archive."""

    @pytest.fixture
    def mock_archiver(self) -> MagicMock:
        archiver = MagicMock()
        archiver.history_root = Path("/tmp/history")
        archiver._kernel_fs = MagicMock()
        return archiver

    @pytest.mark.asyncio
    async def test_get_archive_not_found(self, mock_archiver: MagicMock) -> None:
        stream_archiver = StreamArchiver(mock_archiver)

        with patch.object(Path, "exists", return_value=False):
            result = await stream_archiver.get_archive("nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_archive_corrupt_meta(self, mock_archiver: MagicMock, tmp_path: Path) -> None:
        stream_archiver = StreamArchiver(mock_archiver)
        history_root = tmp_path / "history"
        target_dir = history_root / "runs" / "t1"
        target_dir.mkdir(parents=True)

        # Create a gzipped JSONL file
        header = {"type": "header", "session_id": "s1", "turn_id": "t1", "event_count": 1}
        event = {"type": "event", "seq": 0, "event": {"data": "hello"}}
        lines = json.dumps(header) + "\n" + json.dumps(event)
        compressed = io.BytesIO()
        with gzip.GzipFile(fileobj=compressed, mode="wb") as gz:
            gz.write(lines.encode("utf-8"))

        events_file = target_dir / "stream_events.jsonl.gz"
        events_file.write_bytes(compressed.getvalue())

        # Create meta with mismatched hash
        meta = {"content_hash": "wrong_hash"}
        meta_file = target_dir / "stream_meta.json"
        meta_file.write_text(json.dumps(meta))

        mock_archiver.history_root = history_root
        result = await stream_archiver.get_archive("t1")
        assert result is None


class TestCreateStreamArchiver:
    """Tests for create_stream_archiver factory."""

    def test_factory(self) -> None:
        with patch("polaris.cells.archive.run_archive.internal.stream_archiver.HistoryArchiveService") as mock_cls:
            mock_cls.return_value = MagicMock()
            archiver = create_stream_archiver("/tmp/ws")
            assert isinstance(archiver, StreamArchiver)
            mock_cls.assert_called_once_with("/tmp/ws")
