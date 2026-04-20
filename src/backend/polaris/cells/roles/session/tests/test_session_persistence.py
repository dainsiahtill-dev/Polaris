"""Unit tests for Session Persistence Service, TTL Cleanup, and Event Publishing.

Tests the M3 completion features:
- Session persistence to KernelOne Storage
- TTL automatic cleanup
- Lifecycle event publishing
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.session.internal.session_persistence import (
    _DEFAULT_SESSION_TTL_DAYS,
    SessionEventPublisher,
    SessionPersistenceService,
    SessionTTLCleanupService,
)

if TYPE_CHECKING:
    from collections.abc import Generator


class TestSessionPersistenceService:
    """Tests for SessionPersistenceService."""

    @pytest.fixture
    def temp_workspace(self) -> Generator[str, None, None]:
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def mock_session(self) -> MagicMock:
        """Create a mock session object."""
        session = MagicMock()
        session.id = "test-session-123"
        session.title = "Test Session"
        session.role = "pm"
        session.workspace = "/test/workspace"
        session.host_kind = "electron_workbench"
        session.session_type = "workbench"
        session.attachment_mode = "isolated"
        session.attached_run_id = None
        session.attached_task_id = None
        session.capability_profile = '{"streaming": true}'
        session.state = "active"
        session.context_config = '{"task": "T-001"}'
        session.message_count = 5
        session.created_at = datetime.now(timezone.utc)
        session.updated_at = datetime.now(timezone.utc)
        return session

    def test_init_default_values(self, temp_workspace: str) -> None:
        """Test service initialization with default values."""
        service = SessionPersistenceService(temp_workspace)
        assert service.workspace == temp_workspace
        assert service._storage_prefix == "runtime/sessions"
        assert service._ttl_days == _DEFAULT_SESSION_TTL_DAYS

    def test_init_custom_values(self, temp_workspace: str) -> None:
        """Test service initialization with custom values."""
        service = SessionPersistenceService(
            temp_workspace,
            storage_prefix="custom/sessions",
            ttl_days=60,
        )
        assert service._storage_prefix == "custom/sessions"
        assert service._ttl_days == 60

    def test_get_snapshot_path(self, temp_workspace: str) -> None:
        """Test snapshot path generation."""
        service = SessionPersistenceService(temp_workspace)
        path = service._get_snapshot_path("session-abc")
        assert path == "runtime/sessions/session_snapshot_session-abc.json"

    def test_persist_session_success(
        self,
        temp_workspace: str,
        mock_session: MagicMock,
    ) -> None:
        """Test successful session persistence."""
        service = SessionPersistenceService(temp_workspace)

        mock_fs = MagicMock()
        service._fs = mock_fs
        result = service.persist_session(mock_session)

        assert result is True
        mock_fs.write_json.assert_called_once()

        # Verify the call arguments
        call_args = mock_fs.write_json.call_args
        assert "runtime/sessions/session_snapshot_test-session-123.json" in str(call_args)

    def test_persist_session_failure(
        self,
        temp_workspace: str,
        mock_session: MagicMock,
    ) -> None:
        """Test session persistence failure handling."""
        service = SessionPersistenceService(temp_workspace)

        mock_fs = MagicMock()
        mock_fs.write_json.side_effect = RuntimeError("Disk full")
        service._fs = mock_fs

        result = service.persist_session(mock_session)

        assert result is False

    def test_load_session_snapshot_success(
        self,
        temp_workspace: str,
    ) -> None:
        """Test successful session snapshot loading."""
        service = SessionPersistenceService(temp_workspace)

        snapshot_data = {
            "version": 1,
            "session_id": "test-session-123",
            "title": "Test Session",
            "role": "pm",
        }

        mock_fs = MagicMock()
        mock_fs.read_json.return_value = snapshot_data
        service._fs = mock_fs

        result = service.load_session_snapshot("test-session-123")

        assert result == snapshot_data
        mock_fs.read_json.assert_called_once()

    def test_load_session_snapshot_not_found(
        self,
        temp_workspace: str,
    ) -> None:
        """Test loading non-existent snapshot."""
        service = SessionPersistenceService(temp_workspace)

        mock_fs = MagicMock()
        mock_fs.read_json.side_effect = FileNotFoundError()
        service._fs = mock_fs

        result = service.load_session_snapshot("nonexistent")

        assert result is None

    def test_delete_session_snapshot_success(
        self,
        temp_workspace: str,
    ) -> None:
        """Test successful snapshot deletion."""
        service = SessionPersistenceService(temp_workspace)

        mock_fs = MagicMock()
        service._fs = mock_fs
        result = service.delete_session_snapshot("test-session-123")

        assert result is True
        mock_fs.remove.assert_called_once()

    def test_delete_session_snapshot_not_found(
        self,
        temp_workspace: str,
    ) -> None:
        """Test deleting non-existent snapshot returns True (idempotent)."""
        service = SessionPersistenceService(temp_workspace)

        mock_fs = MagicMock()
        mock_fs.remove.side_effect = FileNotFoundError()
        service._fs = mock_fs

        result = service.delete_session_snapshot("nonexistent")

        assert result is True


class TestSessionTTLCleanupService:
    """Tests for SessionTTLCleanupService."""

    @pytest.fixture
    def temp_workspace(self) -> Generator[str, None, None]:
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def mock_persistence(self) -> MagicMock:
        """Create a mock persistence service."""
        return MagicMock(spec=SessionPersistenceService)

    def test_init_default_ttl(self, mock_persistence: MagicMock) -> None:
        """Test initialization with default TTL."""
        service = SessionTTLCleanupService(mock_persistence)
        assert service.ttl_days == _DEFAULT_SESSION_TTL_DAYS

    def test_init_custom_ttl(self, mock_persistence: MagicMock) -> None:
        """Test initialization with custom TTL."""
        service = SessionTTLCleanupService(mock_persistence, ttl_days=45)
        assert service.ttl_days == 45

    def test_cleanup_expired_removes_old_sessions(
        self,
        mock_persistence: MagicMock,
    ) -> None:
        """Test that expired sessions are cleaned up."""
        # Create snapshots with different ages
        now = datetime.now(timezone.utc)
        old_snapshot = {
            "session_id": "old-session",
            "persisted_at": (now - timedelta(days=60)).isoformat(),
        }
        recent_snapshot = {
            "session_id": "recent-session",
            "persisted_at": (now - timedelta(days=5)).isoformat(),
        }

        mock_persistence.list_session_snapshots.return_value = [
            old_snapshot,
            recent_snapshot,
        ]
        mock_persistence.delete_session_snapshot.return_value = True

        service = SessionTTLCleanupService(mock_persistence, ttl_days=30)
        cleaned = service.cleanup_expired()

        assert "old-session" in cleaned
        assert "recent-session" not in cleaned
        mock_persistence.delete_session_snapshot.assert_called_once_with("old-session")

    def test_cleanup_expired_handles_invalid_timestamps(
        self,
        mock_persistence: MagicMock,
    ) -> None:
        """Test that invalid timestamps are handled gracefully."""
        snapshots = [
            {
                "session_id": "bad-session",
                "persisted_at": "invalid-timestamp",
            },
            {
                "session_id": "good-session",
                "persisted_at": datetime.now(timezone.utc).isoformat(),
            },
        ]

        mock_persistence.list_session_snapshots.return_value = snapshots
        mock_persistence.delete_session_snapshot.return_value = True

        service = SessionTTLCleanupService(mock_persistence, ttl_days=30)
        cleaned = service.cleanup_expired()

        # Bad session should be skipped (not in cleaned list)
        assert len(cleaned) == 0
        # Good session should not be deleted (still within TTL)
        mock_persistence.delete_session_snapshot.assert_not_called()

    def test_cleanup_session(
        self,
        mock_persistence: MagicMock,
    ) -> None:
        """Test cleaning up a single session."""
        mock_persistence.delete_session_snapshot.return_value = True

        service = SessionTTLCleanupService(mock_persistence)
        result = service.cleanup_session("test-session")

        assert result is True
        mock_persistence.delete_session_snapshot.assert_called_once_with("test-session")


class TestSessionEventPublisher:
    """Tests for SessionEventPublisher."""

    @pytest.fixture
    def publisher(self) -> SessionEventPublisher:
        """Create an event publisher instance."""
        return SessionEventPublisher(workspace="/test/workspace")

    def test_init_default_values(self) -> None:
        """Test initialization with default values."""
        publisher = SessionEventPublisher()
        assert publisher._event_path == "runtime/sessions/events"
        assert publisher._workspace is None

    def test_init_custom_values(self) -> None:
        """Test initialization with custom values."""
        publisher = SessionEventPublisher(
            event_path="custom/events",
            workspace="/custom/workspace",
        )
        assert publisher._event_path == "custom/events"
        assert publisher._workspace == "/custom/workspace"

    def test_publish_session_created(self, publisher: SessionEventPublisher) -> None:
        """Test publishing session created event."""
        with patch("polaris.kernelone.events.session_events.emit_event") as mock_emit:
            publisher.publish_session_created(
                session_id="test-session-123",
                role="pm",
                host_kind="electron_workbench",
                workspace="/test/workspace",
                metadata={"custom_key": "custom_value"},
            )

            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs

            assert call_kwargs["name"] == "session_created"
            assert call_kwargs["kind"] == "action"
            assert call_kwargs["actor"] == "System"
            assert call_kwargs["refs"]["session_id"] == "test-session-123"
            assert call_kwargs["input"]["custom_key"] == "custom_value"

    def test_publish_session_updated(self, publisher: SessionEventPublisher) -> None:
        """Test publishing session updated event."""
        with patch("polaris.kernelone.events.session_events.emit_event") as mock_emit:
            changes = {"title": {"old": "Old", "new": "New"}}
            publisher.publish_session_updated(
                session_id="test-session-123",
                changes=changes,
            )

            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs

            assert call_kwargs["name"] == "session_updated"
            assert call_kwargs["refs"]["session_id"] == "test-session-123"
            assert call_kwargs["input"]["changes"] == changes

    def test_publish_session_ended(self, publisher: SessionEventPublisher) -> None:
        """Test publishing session ended event."""
        with patch("polaris.kernelone.events.session_events.emit_event") as mock_emit:
            publisher.publish_session_ended(
                session_id="test-session-123",
                reason="normal",
            )

            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs

            assert call_kwargs["name"] == "session_ended"
            assert call_kwargs["refs"]["session_id"] == "test-session-123"
            assert call_kwargs["input"]["reason"] == "normal"

    def test_publish_session_expired(self, publisher: SessionEventPublisher) -> None:
        """Test publishing session expired event."""
        with patch("polaris.kernelone.events.session_events.emit_event") as mock_emit:
            publisher.publish_session_expired(
                session_id="test-session-123",
                ttl_days=30,
            )

            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs

            assert call_kwargs["name"] == "session_expired"
            assert call_kwargs["refs"]["session_id"] == "test-session-123"
            assert call_kwargs["input"]["ttl_days"] == 30

    def test_publish_message_added(self, publisher: SessionEventPublisher) -> None:
        """Test publishing message added event."""
        with patch("polaris.kernelone.events.session_events.emit_event") as mock_emit:
            publisher.publish_session_message_added(
                session_id="test-session-123",
                message_role="user",
                message_count=5,
            )

            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs

            assert call_kwargs["name"] == "session_message_added"
            assert call_kwargs["refs"]["session_id"] == "test-session-123"
            assert call_kwargs["input"]["message_role"] == "user"
            assert call_kwargs["input"]["message_count"] == 5

    def test_event_publish_failure_is_non_blocking(
        self,
        publisher: SessionEventPublisher,
    ) -> None:
        """Test that event publish failures don't raise exceptions."""
        with patch(
            "polaris.kernelone.events.session_events.emit_event",
            side_effect=RuntimeError("Event system unavailable"),
        ):
            # Should not raise
            publisher.publish_session_created(
                session_id="test-session-123",
                role="pm",
                host_kind="electron_workbench",
            )


class TestRoleSessionServiceIntegration:
    """Integration tests for RoleSessionService with persistence and events."""

    @pytest.fixture
    def temp_workspace(self) -> Generator[str, None, None]:
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_create_session_persists_and_publishes_event(
        self,
        temp_workspace: str,
    ) -> None:
        """Test that creating a session triggers persistence and event publishing."""
        # Create a real in-memory test
        from polaris.cells.roles.session.internal.conversation import Base
        from polaris.cells.roles.session.internal.role_session_service import (
            RoleSessionService,
        )
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        # Setup in-memory database
        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine)

        # Mock the persistence and event publisher
        mock_persistence = MagicMock(spec=SessionPersistenceService)
        mock_persistence.persist_session.return_value = True

        with (
            patch(
                "polaris.cells.roles.session.internal.role_session_service._get_persistence_service",
                return_value=mock_persistence,
            ),
            patch(
                "polaris.cells.roles.session.internal.role_session_service._get_event_publisher"
            ) as mock_get_event_pub,
        ):
            mock_event_pub = MagicMock(spec=SessionEventPublisher)
            mock_get_event_pub.return_value = mock_event_pub

            with RoleSessionService(
                db=SessionFactory(),
                workspace=temp_workspace,
                enable_persistence=True,
                enable_events=True,
            ) as service:
                session = service.create_session(
                    role="pm",
                    workspace=temp_workspace,
                )

                # Verify persistence was called
                mock_persistence.persist_session.assert_called_once()

                # Verify event was published
                mock_event_pub.publish_session_created.assert_called_once()
                call_kwargs = mock_event_pub.publish_session_created.call_args.kwargs
                assert call_kwargs["session_id"] == session.id
                assert call_kwargs["role"] == "pm"

        engine.dispose()

    def test_update_session_persists_and_publishes_event(
        self,
        temp_workspace: str,
    ) -> None:
        """Test that updating a session triggers persistence and event publishing."""
        from polaris.cells.roles.session.internal.conversation import Base
        from polaris.cells.roles.session.internal.role_session_service import (
            RoleSessionService,
        )
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        # Setup in-memory database
        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine)

        mock_persistence = MagicMock(spec=SessionPersistenceService)
        mock_persistence.persist_session.return_value = True

        with (
            patch(
                "polaris.cells.roles.session.internal.role_session_service._get_persistence_service",
                return_value=mock_persistence,
            ),
            patch(
                "polaris.cells.roles.session.internal.role_session_service._get_event_publisher"
            ) as mock_get_event_pub,
        ):
            mock_event_pub = MagicMock(spec=SessionEventPublisher)
            mock_get_event_pub.return_value = mock_event_pub

            with RoleSessionService(
                db=SessionFactory(),
                workspace=temp_workspace,
            ) as service:
                # Create a session first
                session = service.create_session(role="pm")

                # Reset mocks after creation
                mock_persistence.persist_session.reset_mock()
                mock_event_pub.publish_session_updated.reset_mock()

                # Update the session
                service.update_session(session.id, title="New Title")

                # Verify persistence was called for update
                mock_persistence.persist_session.assert_called_once()

                # Verify update event was published
                mock_event_pub.publish_session_updated.assert_called_once()

        engine.dispose()

    def test_delete_session_publishes_ended_event(
        self,
        temp_workspace: str,
    ) -> None:
        """Test that deleting a session publishes the ended event."""
        from polaris.cells.roles.session.internal.conversation import Base
        from polaris.cells.roles.session.internal.role_session_service import (
            RoleSessionService,
        )
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        # Setup in-memory database
        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine)

        mock_persistence = MagicMock(spec=SessionPersistenceService)
        mock_persistence.persist_session.return_value = True

        with (
            patch(
                "polaris.cells.roles.session.internal.role_session_service._get_persistence_service",
                return_value=mock_persistence,
            ),
            patch(
                "polaris.cells.roles.session.internal.role_session_service._get_event_publisher"
            ) as mock_get_event_pub,
        ):
            mock_event_pub = MagicMock(spec=SessionEventPublisher)
            mock_get_event_pub.return_value = mock_event_pub

            with RoleSessionService(
                db=SessionFactory(),
                workspace=temp_workspace,
            ) as service:
                # Create a session first
                session = service.create_session(role="pm")

                # Reset mocks after creation
                mock_event_pub.publish_session_ended.reset_mock()

                # Delete the session
                service.delete_session(session.id)

                # Verify ended event was published
                mock_event_pub.publish_session_ended.assert_called_once()
                call_kwargs = mock_event_pub.publish_session_ended.call_args.kwargs
                assert call_kwargs["reason"] == "deleted"

        engine.dispose()

    def test_add_message_publishes_message_added_event(
        self,
        temp_workspace: str,
    ) -> None:
        """Test that adding a message publishes the message added event."""
        from polaris.cells.roles.session.internal.conversation import Base
        from polaris.cells.roles.session.internal.role_session_service import (
            RoleSessionService,
        )
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        # Setup in-memory database
        engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine)

        with patch(
            "polaris.cells.roles.session.internal.role_session_service._get_event_publisher"
        ) as mock_get_event_pub:
            mock_event_pub = MagicMock(spec=SessionEventPublisher)
            mock_get_event_pub.return_value = mock_event_pub

            with RoleSessionService(
                db=SessionFactory(),
                workspace=temp_workspace,
            ) as service:
                # Create a session first
                session = service.create_session(role="pm")

                # Reset mock after creation
                mock_event_pub.publish_session_message_added.reset_mock()

                # Add a message
                service.add_message(
                    session.id,
                    role="user",
                    content="Hello, world!",
                )

                # Verify message added event was published
                mock_event_pub.publish_session_message_added.assert_called_once()
                call_kwargs = mock_event_pub.publish_session_message_added.call_args.kwargs
                assert call_kwargs["session_id"] == session.id
                assert call_kwargs["message_role"] == "user"
                assert call_kwargs["message_count"] == 1

        engine.dispose()


class TestPersistenceServiceWithRealStorage:
    """Integration tests using real file system storage."""

    @pytest.fixture
    def temp_workspace(self) -> Generator[str, None, None]:
        """Create a temporary workspace directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_persistence_roundtrip(
        self,
        temp_workspace: str,
    ) -> None:
        """Test writing and reading a session snapshot."""

        service = SessionPersistenceService(
            temp_workspace,
            storage_prefix="runtime/sessions",
        )

        # Create a mock session
        mock_session = MagicMock()
        mock_session.id = "roundtrip-test-123"
        mock_session.title = "Roundtrip Test"
        mock_session.role = "architect"
        mock_session.workspace = temp_workspace
        mock_session.host_kind = "cli"
        mock_session.session_type = "standalone"
        mock_session.attachment_mode = "isolated"
        mock_session.attached_run_id = None
        mock_session.attached_task_id = None
        mock_session.capability_profile = None
        mock_session.state = "active"
        mock_session.context_config = None
        mock_session.message_count = 0
        mock_session.created_at = datetime.now(timezone.utc)
        mock_session.updated_at = datetime.now(timezone.utc)

        # Persist
        result = service.persist_session(mock_session)
        assert result is True

        # Load
        snapshot = service.load_session_snapshot("roundtrip-test-123")
        assert snapshot is not None
        assert snapshot["session_id"] == "roundtrip-test-123"
        assert snapshot["title"] == "Roundtrip Test"
        assert snapshot["role"] == "architect"
        assert snapshot["version"] == 1

        # Delete
        delete_result = service.delete_session_snapshot("roundtrip-test-123")
        assert delete_result is True

        # Verify deleted
        deleted_snapshot = service.load_session_snapshot("roundtrip-test-123")
        assert deleted_snapshot is None
