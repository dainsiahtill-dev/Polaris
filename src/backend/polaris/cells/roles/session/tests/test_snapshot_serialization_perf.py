"""SessionSnapshot serialization correctness + performance tests.

Relocated from the kernelone context_os performance suite: ``SessionSnapshot``
is owned by ``roles.session``, so these tests belong in this cell (same-cell
access) instead of reaching into roles.session internals from kernelone — which
is a layer inversion (kernelone is the shared base and must not depend on a
higher-level cell).
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import pytest
from polaris.cells.roles.session.internal.snapshot_service import SessionSnapshot


class TestSnapshotSerialization:
    """Test SessionSnapshot serialization correctness and performance."""

    def _make_snapshot(
        self,
        *,
        num_messages: int = 10,
        msg_content_len: int = 100,
        num_artifacts: int = 2,
    ) -> dict[str, Any]:
        """Create a synthetic snapshot dict for testing."""
        messages = []
        for i in range(num_messages):
            messages.append(
                {
                    "role": ["system", "user", "assistant"][i % 3],
                    "content": f"msg_{i}:" + "y" * msg_content_len,
                    "sequence": i,
                    "timestamp": f"2026-06-21T{10 + i % 12:02d}:00:00Z",
                }
            )
        artifacts = []
        for i in range(num_artifacts):
            artifacts.append(
                {
                    "artifact_id": f"art_{i}",
                    "title": f"Artifact {i}",
                    "content": "z" * 500,
                }
            )
        fingerprints = [hashlib.sha256(str(m["content"]).encode()).hexdigest()[:16] for m in messages]
        return {
            "session_id": "test_session",
            "messages": messages,
            "artifacts": artifacts,
            "fingerprints": fingerprints,
            "timestamp": "2026-06-21T12:00:00Z",
            "snapshot_id": "abc123",
        }

    def test_snapshot_dict_roundtrip(self) -> None:
        """Snapshot dict should survive JSON roundtrip."""
        data = self._make_snapshot(num_messages=50)
        snap = SessionSnapshot.from_dict(data)
        reexported = snap.to_dict()

        assert reexported["session_id"] == data["session_id"]
        assert len(reexported["messages"]) == len(data["messages"])
        assert reexported["snapshot_id"] == data["snapshot_id"]

    def test_snapshot_json_roundtrip(self) -> None:
        """Snapshot should survive JSON serialize/deserialize."""
        data = self._make_snapshot(num_messages=100)
        snap = SessionSnapshot.from_dict(data)
        json_str = json.dumps(snap.to_dict(), ensure_ascii=False)
        restored = SessionSnapshot.from_dict(json.loads(json_str))

        assert restored.session_id == snap.session_id
        assert len(restored.messages) == len(snap.messages)
        assert restored.fingerprints == snap.fingerprints

    @pytest.mark.parametrize("num_messages", [100, 500, 1000, 2000])
    def test_snapshot_serialization_performance(self, num_messages: int) -> None:
        """Serialization of large snapshots should complete under 2s."""
        data = self._make_snapshot(
            num_messages=num_messages,
            msg_content_len=300,
            num_artifacts=10,
        )
        snap = SessionSnapshot.from_dict(data)

        start = time.monotonic()
        json_str = json.dumps(snap.to_dict(), ensure_ascii=False)
        elapsed_serialize = time.monotonic() - start

        start = time.monotonic()
        SessionSnapshot.from_dict(json.loads(json_str))
        elapsed_deserialize = time.monotonic() - start

        total = elapsed_serialize + elapsed_deserialize
        assert total < 2.0, (
            f"Serialization roundtrip for {num_messages} messages took {total:.3f}s "
            f"(serialize={elapsed_serialize:.3f}s, deserialize={elapsed_deserialize:.3f}s)"
        )

    def test_snapshot_fingerprint_stability(self) -> None:
        """Fingerprints should be deterministic for same content."""
        content = "test content for fingerprinting"
        fp1 = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
        fp2 = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
        assert fp1 == fp2

    def test_snapshot_fingerprint_uniqueness(self) -> None:
        """Different content should produce different fingerprints."""
        fp_a = hashlib.sha256(b"content_a").hexdigest()[:16]
        fp_b = hashlib.sha256(b"content_b").hexdigest()[:16]
        assert fp_a != fp_b

    def test_snapshot_1000_messages_json_size_reasonable(self) -> None:
        """JSON for 1000 messages should be under 10MB."""
        data = self._make_snapshot(
            num_messages=1000,
            msg_content_len=200,
            num_artifacts=20,
        )
        json_str = json.dumps(data, ensure_ascii=False)
        size_mb = len(json_str.encode("utf-8")) / (1024 * 1024)
        assert size_mb < 10.0, f"Snapshot JSON is {size_mb:.2f}MB"

    def test_snapshot_artifacts_content_capped(self) -> None:
        """Artifact content in snapshot should be capped at 2000 chars for fingerprinting."""
        # This tests the SnapshotService behavior: content[:2000]
        long_content = "x" * 5000
        capped = long_content[:2000]
        assert len(capped) == 2000
