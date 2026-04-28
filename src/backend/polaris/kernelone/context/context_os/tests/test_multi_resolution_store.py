"""Tests for Multi-Resolution Store (ContextOS 3.0 Phase 1)."""

from polaris.kernelone.context.context_os.content_store import ContentStore, RefTracker
from polaris.kernelone.context.context_os.multi_resolution_store import (
    MultiResolutionContent,
    MultiResolutionStore,
    ResolutionEntry,
    ResolutionLevel,
    create_extractive_content,
    create_structured_content,
    create_stub_content,
)


class TestResolutionLevel:
    """Test ResolutionLevel enum."""

    def test_enum_values(self) -> None:
        assert ResolutionLevel.L0_FULL.value == "full"
        assert ResolutionLevel.L1_EXTRACTIVE.value == "extractive"
        assert ResolutionLevel.L2_STRUCTURED.value == "structured"
        assert ResolutionLevel.L3_STUB.value == "stub"


class TestResolutionEntry:
    """Test ResolutionEntry dataclass."""

    def test_create_entry(self) -> None:
        from polaris.kernelone.context.context_os.content_store import ContentRef

        ref = ContentRef(hash="abc123", size=100, mime="text/plain")
        entry = ResolutionEntry(
            level=ResolutionLevel.L0_FULL,
            content_ref=ref,
            token_count=50,
        )
        assert entry.level == ResolutionLevel.L0_FULL
        assert entry.token_count == 50
        assert entry.lossiness == 0.0

    def test_to_dict(self) -> None:
        from polaris.kernelone.context.context_os.content_store import ContentRef

        ref = ContentRef(hash="abc123", size=100, mime="text/plain")
        entry = ResolutionEntry(
            level=ResolutionLevel.L1_EXTRACTIVE,
            content_ref=ref,
            token_count=30,
            derived_from="parent_hash",
            compression_policy="extractive",
            lossiness=0.3,
        )
        result = entry.to_dict()
        assert result["level"] == "extractive"
        assert result["lossiness"] == 0.3
        assert result["derived_from"] == "parent_hash"


class TestMultiResolutionContent:
    """Test MultiResolutionContent dataclass."""

    def test_create_content(self) -> None:
        content = MultiResolutionContent(content_id="abc123")
        assert content.content_id == "abc123"
        assert len(content.resolutions) == 0

    def test_get_resolution(self) -> None:
        from polaris.kernelone.context.context_os.content_store import ContentRef

        ref = ContentRef(hash="abc123", size=100, mime="text/plain")
        entry = ResolutionEntry(
            level=ResolutionLevel.L0_FULL,
            content_ref=ref,
            token_count=50,
        )
        content = MultiResolutionContent(
            content_id="abc123",
            resolutions={ResolutionLevel.L0_FULL: entry},
        )
        assert content.get_resolution(ResolutionLevel.L0_FULL) == entry
        assert content.get_resolution(ResolutionLevel.L3_STUB) is None

    def test_has_resolution(self) -> None:
        from polaris.kernelone.context.context_os.content_store import ContentRef

        ref = ContentRef(hash="abc123", size=100, mime="text/plain")
        entry = ResolutionEntry(
            level=ResolutionLevel.L0_FULL,
            content_ref=ref,
            token_count=50,
        )
        content = MultiResolutionContent(
            content_id="abc123",
            resolutions={ResolutionLevel.L0_FULL: entry},
        )
        assert content.has_resolution(ResolutionLevel.L0_FULL) is True
        assert content.has_resolution(ResolutionLevel.L3_STUB) is False

    def test_get_best_available_resolution(self) -> None:
        from polaris.kernelone.context.context_os.content_store import ContentRef

        ref = ContentRef(hash="abc123", size=100, mime="text/plain")
        entry_full = ResolutionEntry(
            level=ResolutionLevel.L0_FULL,
            content_ref=ref,
            token_count=50,
        )
        entry_stub = ResolutionEntry(
            level=ResolutionLevel.L3_STUB,
            content_ref=ref,
            token_count=10,
        )
        content = MultiResolutionContent(
            content_id="abc123",
            resolutions={
                ResolutionLevel.L0_FULL: entry_full,
                ResolutionLevel.L3_STUB: entry_stub,
            },
        )

        # Should get full when preferred
        assert content.get_best_available_resolution(ResolutionLevel.L0_FULL) == entry_full

        # Should get stub when preferred (full not preferred)
        assert content.get_best_available_resolution(ResolutionLevel.L3_STUB) == entry_stub

        # Should fall back to stub when preferred level not available
        assert content.get_best_available_resolution(ResolutionLevel.L1_EXTRACTIVE) == entry_stub

    def test_to_dict(self) -> None:
        content = MultiResolutionContent(content_id="abc123")
        result = content.to_dict()
        assert result["content_id"] == "abc123"
        assert "resolutions" in result


class TestMultiResolutionStore:
    """Test MultiResolutionStore class."""

    def test_create_store(self) -> None:
        content_store = ContentStore()
        store = MultiResolutionStore(content_store)
        assert store.stats["total_contents"] == 0

    def test_intern_with_resolutions(self) -> None:
        content_store = ContentStore()
        ref_tracker = RefTracker(content_store)
        store = MultiResolutionStore(content_store, ref_tracker)

        full_content = "This is a long content block that needs multiple resolutions."
        extractive = "This is a long content block..."
        structured = "[Structured summary]"
        stub = "Long content block"

        result = store.intern_with_resolutions(
            content=full_content,
            extractive_content=extractive,
            structured_content=structured,
            stub_content=stub,
        )

        assert result.has_resolution(ResolutionLevel.L0_FULL)
        assert result.has_resolution(ResolutionLevel.L1_EXTRACTIVE)
        assert result.has_resolution(ResolutionLevel.L2_STRUCTURED)
        assert result.has_resolution(ResolutionLevel.L3_STUB)

    def test_get_content(self) -> None:
        content_store = ContentStore()
        store = MultiResolutionStore(content_store)

        full_content = "Original full content"
        store.intern_with_resolutions(content=full_content)

        # Get full content
        result = store.get_content(
            content_id=next(iter(store._multi_resolution_map.keys())),
            preferred_level=ResolutionLevel.L0_FULL,
        )
        assert result == full_content

    def test_get_content_fallback(self) -> None:
        content_store = ContentStore()
        store = MultiResolutionStore(content_store)

        full_content = "Original full content"
        store.intern_with_resolutions(content=full_content)

        # Get with fallback (only full available)
        content_id = next(iter(store._multi_resolution_map.keys()))
        result = store.get_content(
            content_id=content_id,
            preferred_level=ResolutionLevel.L3_STUB,
        )
        # Should fall back to full
        assert result == full_content

    def test_has_content(self) -> None:
        content_store = ContentStore()
        store = MultiResolutionStore(content_store)

        store.intern_with_resolutions(content="test content")
        content_id = next(iter(store._multi_resolution_map.keys()))

        assert store.has_content(content_id) is True
        assert store.has_content("nonexistent") is False

    def test_stats(self) -> None:
        content_store = ContentStore()
        store = MultiResolutionStore(content_store)

        store.intern_with_resolutions(
            content="content 1",
            stub_content="stub 1",
        )
        store.intern_with_resolutions(
            content="content 2",
            extractive_content="extractive 2",
        )

        stats = store.stats
        assert stats["total_contents"] == 2
        assert stats["resolution_counts"]["full"] == 2
        assert stats["resolution_counts"]["stub"] == 1
        assert stats["resolution_counts"]["extractive"] == 1


class TestHelperFunctions:
    """Test helper functions."""

    def test_create_stub_content(self) -> None:
        content = "First line\nSecond line\nThird line"
        stub = create_stub_content(content, max_chars=20)
        assert len(stub) <= 23  # max_chars + "..."
        assert "First line" in stub

    def test_create_extractive_content(self) -> None:
        content = "A" * 1000
        extractive = create_extractive_content(content, max_ratio=0.3)
        assert len(extractive) < len(content)
        assert len(extractive) > 0

    def test_create_structured_content(self) -> None:
        content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
        structured = create_structured_content(content, max_ratio=0.5)
        assert "[5 lines total]" in structured
        assert "Line 1" in structured
        assert "Line 5" in structured

    def test_create_stub_content_short(self) -> None:
        content = "Short"
        stub = create_stub_content(content, max_chars=100)
        assert stub == content

    def test_create_extractive_content_short(self) -> None:
        content = "Short"
        extractive = create_extractive_content(content, max_ratio=0.3)
        # For very short content, extractive should be truncated
        assert len(extractive) <= len(content)

    def test_create_structured_content_short(self) -> None:
        content = "Short"
        structured = create_structured_content(content, max_ratio=0.5)
        # For single-line content, should be truncated
        assert len(structured) <= len(content)
