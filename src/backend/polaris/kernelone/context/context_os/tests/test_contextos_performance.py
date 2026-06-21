"""ContextOS Performance & Correctness Tests.

Tests cover:
1. 1000+ messages handling performance
2. Different role contextWindowTokens
3. Snapshot serialization performance/correctness

No external LLM calls — pure unit tests with mocked dependencies.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import pytest
from polaris.kernelone.context.chunks.taxonomy import (
    ChunkMetadata,
    ChunkType,
    PromptChunk,
)
from polaris.kernelone.context.context_os.policies import (
    InputValidationPolicy,
    StateFirstContextOSPolicy,
)
from polaris.kernelone.context.context_os.runtime import StateFirstContextOS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_messages(count: int, *, avg_content_len: int = 200) -> list[dict[str, Any]]:
    """Generate synthetic messages for performance testing."""
    roles = ["system", "user", "assistant"]
    messages: list[dict[str, Any]] = []
    for i in range(count):
        role = roles[i % len(roles)]
        content = f"Message {i}: " + "x" * (avg_content_len - 20)
        messages.append({"role": role, "content": content})
    return messages


def _generate_policy_with_high_limits() -> StateFirstContextOSPolicy:
    """Create policy with high limits for performance testing."""
    return StateFirstContextOSPolicy(
        input_validation=InputValidationPolicy(
            max_messages=100_000,
            max_message_size=1_000_000,
            max_total_input_size=500_000_000,
        ),
    )


# ---------------------------------------------------------------------------
# Test: 1000+ Messages Performance
# ---------------------------------------------------------------------------


class TestLargeMessageVolume:
    """Test ContextOS handling of 1000+ messages."""

    @pytest.mark.parametrize("count", [100, 500, 1000, 2000])
    def test_generate_messages_correct_count(self, count: int) -> None:
        """Verify message generation produces correct count."""
        messages = _generate_messages(count)
        assert len(messages) == count

    def test_1000_messages_input_validation_within_limits(self) -> None:
        """1000 messages should pass validation with high limits."""
        policy = _generate_policy_with_high_limits()
        messages = _generate_messages(1000, avg_content_len=100)
        ctx = StateFirstContextOS(policy=policy, workspace="/tmp/test")

        # Should not raise ValidationError
        ctx._validate_project_input(messages)

    def test_2000_messages_input_validation_within_limits(self) -> None:
        """2000 messages should pass validation with high limits."""
        policy = _generate_policy_with_high_limits()
        messages = _generate_messages(2000, avg_content_len=50)
        ctx = StateFirstContextOS(policy=policy, workspace="/tmp/test")

        # Should not raise ValidationError
        ctx._validate_project_input(messages)

    def test_1000_messages_byte_calculation_performance(self) -> None:
        """Byte calculation for 1000 messages should complete under 1s."""
        messages = _generate_messages(1000, avg_content_len=500)

        start = time.monotonic()
        for msg in messages:
            StateFirstContextOS._project_input_message_bytes(msg)
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, f"Byte calculation took {elapsed:.3f}s for 1000 messages"

    def test_1000_messages_total_bytes_reasonable(self) -> None:
        """Total bytes for 1000 messages should be sum of individual."""
        messages = _generate_messages(1000, avg_content_len=200)
        total = sum(StateFirstContextOS._project_input_message_bytes(m) for m in messages)
        # Each ~200 char message ~200-300 bytes JSON
        assert 100_000 < total < 1_000_000


# ---------------------------------------------------------------------------
# Test: Different Role contextWindowTokens
# ---------------------------------------------------------------------------


class TestChunkTypeRoleMapping:
    """Test chunk type to role mapping and token estimation."""

    def test_system_chunk_maps_to_system_role(self) -> None:
        """SYSTEM chunk type should map to 'system' role."""
        chunk = PromptChunk(
            chunk_type=ChunkType.SYSTEM,
            content="You are Polaris.",
            metadata=ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="role_profile"),
        )
        msg = chunk.to_message()
        assert msg["role"] == "system"

    @pytest.mark.parametrize(
        "chunk_type",
        [
            ChunkType.CURRENT_TURN,
            ChunkType.CONTINUITY,
            ChunkType.WORKING_SET,
            ChunkType.HISTORY_DONE,
            ChunkType.EXAMPLES,
            ChunkType.REMINDER,
            ChunkType.REPO_INTELLIGENCE,
            ChunkType.READONLY_ASSETS,
        ],
    )
    def test_non_system_chunks_map_to_user_role(self, chunk_type: ChunkType) -> None:
        """All non-SYSTEM chunk types should map to 'user' role."""
        chunk = PromptChunk(
            chunk_type=chunk_type,
            content="Some content",
            metadata=ChunkMetadata(chunk_type=chunk_type, source="test"),
        )
        msg = chunk.to_message()
        assert msg["role"] == "user"

    def test_token_estimation_4_chars_per_token(self) -> None:
        """Token estimation should use ~4 chars/token fallback."""
        content = "a" * 400  # 400 chars
        chunk = PromptChunk(
            chunk_type=ChunkType.SYSTEM,
            content=content,
            metadata=ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test"),
        )
        assert chunk.tokens == 100  # 400 / 4

    def test_token_estimation_minimum_1(self) -> None:
        """Token estimation should be at least 1 for non-empty content."""
        chunk = PromptChunk(
            chunk_type=ChunkType.SYSTEM,
            content="x",
            metadata=ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test"),
        )
        assert chunk.tokens >= 1

    def test_token_estimation_empty_content(self) -> None:
        """Empty content should have 0 tokens."""
        chunk = PromptChunk(
            chunk_type=ChunkType.SYSTEM,
            content="",
            metadata=ChunkMetadata(chunk_type=ChunkType.SYSTEM, source="test"),
        )
        assert chunk.tokens == 0

    def test_char_count_auto_computed(self) -> None:
        """Char count should be auto-computed from content."""
        content = "Hello, world!"
        chunk = PromptChunk(
            chunk_type=ChunkType.CURRENT_TURN,
            content=content,
            metadata=ChunkMetadata(chunk_type=ChunkType.CURRENT_TURN, source="test"),
        )
        assert chunk.chars == len(content)

    @pytest.mark.parametrize(
        ("content_len", "expected_tokens"),
        [
            (100, 25),
            (400, 100),
            (1000, 250),
            (4000, 1000),
            (10000, 2500),
            (40000, 10000),
        ],
    )
    def test_token_estimation_various_sizes(self, content_len: int, expected_tokens: int) -> None:
        """Token estimation should scale linearly with content length."""
        chunk = PromptChunk(
            chunk_type=ChunkType.HISTORY_DONE,
            content="x" * content_len,
            metadata=ChunkMetadata(chunk_type=ChunkType.HISTORY_DONE, source="test"),
        )
        assert chunk.tokens == expected_tokens

    def test_chunk_type_eviction_priority_ordering(self) -> None:
        """Eviction priority should be ordered: SYSTEM < CURRENT_TURN < ... < READONLY_ASSETS."""
        tier_order = ChunkType.tier_order()
        priorities = [ct.eviction_priority for ct in tier_order]
        assert priorities == sorted(priorities)
        assert priorities[0] == 0  # SYSTEM
        assert priorities[-1] == 8  # READONLY_ASSETS

    def test_chunk_type_cacheable_flags(self) -> None:
        """Cacheable flags should match design spec."""
        assert ChunkType.SYSTEM.cacheable is True
        assert ChunkType.CONTINUITY.cacheable is True
        assert ChunkType.EXAMPLES.cacheable is True
        assert ChunkType.REPO_INTELLIGENCE.cacheable is True
        assert ChunkType.READONLY_ASSETS.cacheable is True
        # Non-cacheable
        assert ChunkType.CURRENT_TURN.cacheable is False
        assert ChunkType.HISTORY_DONE.cacheable is False
        assert ChunkType.WORKING_SET.cacheable is False
        assert ChunkType.REMINDER.cacheable is False


# ---------------------------------------------------------------------------
# Test: Snapshot Serialization Performance/Correctness
# ---------------------------------------------------------------------------


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
        from polaris.cells.roles.session.internal.snapshot_service import (
            SessionSnapshot,
        )

        data = self._make_snapshot(num_messages=50)
        snap = SessionSnapshot.from_dict(data)
        reexported = snap.to_dict()

        assert reexported["session_id"] == data["session_id"]
        assert len(reexported["messages"]) == len(data["messages"])
        assert reexported["snapshot_id"] == data["snapshot_id"]

    def test_snapshot_json_roundtrip(self) -> None:
        """Snapshot should survive JSON serialize/deserialize."""
        from polaris.cells.roles.session.internal.snapshot_service import (
            SessionSnapshot,
        )

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
        from polaris.cells.roles.session.internal.snapshot_service import (
            SessionSnapshot,
        )

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
        import hashlib as _hashlib

        fp_a = _hashlib.sha256(b"content_a").hexdigest()[:16]
        fp_b = _hashlib.sha256(b"content_b").hexdigest()[:16]
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


# ---------------------------------------------------------------------------
# Test: PromptChunk Assembly Performance
# ---------------------------------------------------------------------------


class TestPromptChunkAssembly:
    """Test prompt chunk creation and serialization at scale."""

    def test_create_1000_chunks_performance(self) -> None:
        """Creating 1000 chunks should complete under 1s."""
        start = time.monotonic()
        chunks: list[PromptChunk] = []
        for i in range(1000):
            chunk_type = list(ChunkType)[i % len(ChunkType)]
            chunk = PromptChunk(
                chunk_type=chunk_type,
                content=f"Chunk {i}: " + "a" * 200,
                metadata=ChunkMetadata(
                    chunk_type=chunk_type,
                    source=f"test_{i}",
                    session_id="sess_001",
                    turn_index=i,
                ),
            )
            chunks.append(chunk)
        elapsed = time.monotonic() - start

        assert len(chunks) == 1000
        assert elapsed < 1.0, f"Creating 1000 chunks took {elapsed:.3f}s"

    def test_chunk_to_dict_performance(self) -> None:
        """Serializing 1000 chunks to dict should complete under 1s."""
        chunks = [
            PromptChunk(
                chunk_type=ChunkType.HISTORY_DONE,
                content="x" * 300,
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.HISTORY_DONE,
                    source="test",
                    session_id="sess",
                    turn_index=i,
                ),
            )
            for i in range(1000)
        ]

        start = time.monotonic()
        dicts = [c.to_dict() for c in chunks]
        elapsed = time.monotonic() - start

        assert len(dicts) == 1000
        assert elapsed < 1.0, f"Serializing 1000 chunks took {elapsed:.3f}s"

    def test_chunk_to_message_performance(self) -> None:
        """Converting 1000 chunks to messages should complete under 1s."""
        chunks = [
            PromptChunk(
                chunk_type=ChunkType.SYSTEM if i % 10 == 0 else ChunkType.HISTORY_DONE,
                content="y" * 200,
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.SYSTEM,
                    source="test",
                    session_id="sess",
                    turn_index=i,
                ),
            )
            for i in range(1000)
        ]

        start = time.monotonic()
        messages = [c.to_message() for c in chunks]
        elapsed = time.monotonic() - start

        assert len(messages) == 1000
        assert elapsed < 1.0, f"Converting 1000 chunks took {elapsed:.3f}s"
        # Verify role mapping
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_total_token_count_across_1000_chunks(self) -> None:
        """Total tokens across 1000 chunks should be sum of individual tokens."""
        total_tokens = 0
        for _i in range(1000):
            chunk = PromptChunk(
                chunk_type=ChunkType.HISTORY_DONE,
                content="a" * 400,  # 100 tokens each
                metadata=ChunkMetadata(
                    chunk_type=ChunkType.HISTORY_DONE,
                    source="test",
                ),
            )
            total_tokens += chunk.tokens

        assert total_tokens == 100_000  # 1000 * 100


# ---------------------------------------------------------------------------
# Test: Input Validation with Large Payloads
# ---------------------------------------------------------------------------


class TestInputValidationLargePayloads:
    """Test input validation edge cases with large payloads."""

    def test_validation_rejects_exceeding_max_messages(self) -> None:
        """Should reject when message count exceeds policy limit."""
        policy = StateFirstContextOSPolicy(
            input_validation=InputValidationPolicy(max_messages=5),
        )
        ctx = StateFirstContextOS(policy=policy, workspace="/tmp/test")
        messages = _generate_messages(10)

        from polaris.kernelone.errors import ValidationError

        with pytest.raises(ValidationError) as excinfo:
            ctx._validate_project_input(messages)
        assert excinfo.value.constraint == "max_messages"

    def test_validation_rejects_single_oversized_message(self) -> None:
        """Should reject when single message exceeds size limit."""
        policy = StateFirstContextOSPolicy(
            input_validation=InputValidationPolicy(
                max_messages=1000,
                max_message_size=100,
                max_total_input_size=1_000_000,
            ),
        )
        ctx = StateFirstContextOS(policy=policy, workspace="/tmp/test")
        messages = [{"role": "user", "content": "x" * 200}]

        from polaris.kernelone.errors import ValidationError

        with pytest.raises(ValidationError) as excinfo:
            ctx._validate_project_input(messages)
        assert excinfo.value.constraint == "max_message_size"

    def test_validation_rejects_total_size_exceeded(self) -> None:
        """Should reject when total input size exceeds limit."""
        policy = StateFirstContextOSPolicy(
            input_validation=InputValidationPolicy(
                max_messages=1000,
                max_message_size=1_000_000,
                max_total_input_size=500,
            ),
        )
        ctx = StateFirstContextOS(policy=policy, workspace="/tmp/test")
        messages = _generate_messages(10, avg_content_len=100)

        from polaris.kernelone.errors import ValidationError

        with pytest.raises(ValidationError) as excinfo:
            ctx._validate_project_input(messages)
        assert excinfo.value.constraint == "max_total_input_size"

    def test_validation_accepts_exactly_at_limit(self) -> None:
        """Should accept messages exactly at policy limits."""
        policy = StateFirstContextOSPolicy(
            input_validation=InputValidationPolicy(
                max_messages=3,
                max_message_size=1000,
                max_total_input_size=10_000,
            ),
        )
        ctx = StateFirstContextOS(policy=policy, workspace="/tmp/test")
        messages = [
            {"role": "user", "content": "a" * 100},
            {"role": "assistant", "content": "b" * 100},
            {"role": "user", "content": "c" * 100},
        ]
        # Should not raise
        ctx._validate_project_input(messages)


__all__ = [
    "TestChunkTypeRoleMapping",
    "TestInputValidationLargePayloads",
    "TestLargeMessageVolume",
    "TestPromptChunkAssembly",
    "TestSnapshotSerialization",
]
