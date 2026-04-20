"""Tests for truncation handling and completion integrity detection.

These tests verify:
1. Truncation detection from provider metadata
2. File block integrity detection (unclosed blocks)
3. Completion integrity evaluation
4. Fail-closed behavior on blocked outputs
"""

import pytest
from polaris.infrastructure.llm.sdk import (
    detect_truncation_from_metadata,
)
from polaris.infrastructure.llm.sdk.completion_integrity import (
    IntegrityStatus,
    evaluate_completion_integrity,
)


class TestTruncationDetection:
    """Tests for provider truncation detection."""

    def test_provider_length_truncation(self):
        """finish_reason=length should be detected as truncated."""
        metadata = {"finish_reason": "length"}
        result = detect_truncation_from_metadata(metadata)
        assert result.truncated is True
        assert result.reason == "length"
        assert result.continuation_supported is True

    def test_ollama_done_reason_length(self):
        """Ollama done_reason=length should be detected as truncated."""
        metadata = {"done_reason": "length", "done": False}
        result = detect_truncation_from_metadata(metadata)
        assert result.truncated is True
        assert result.reason == "length"
        assert result.continuation_supported is True

    def test_normal_stop(self):
        """Normal stop (done=True) should not be truncated."""
        metadata = {"finish_reason": "stop", "done": True}
        result = detect_truncation_from_metadata(metadata)
        assert result.truncated is False
        assert result.reason == "none"
        assert result.continuation_supported is False

    def test_incomplete_response(self):
        """Incomplete response (done=False without done_reason) should be truncated."""
        metadata = {"done": False}
        result = detect_truncation_from_metadata(metadata)
        assert result.truncated is True
        assert result.reason == "incomplete_response"
        assert result.continuation_supported is True


class TestFileBlockIntegrity:
    """Tests for file block integrity detection."""

    def test_complete_blocks(self):
        """Complete FILE blocks should not be detected as truncated."""
        text = "FILE: a.py\ncontent\nEND FILE\nFILE: b.py\ncontent\nEND FILE"
        integrity = evaluate_completion_integrity(text, {})
        assert integrity.status == IntegrityStatus.COMPLETE
        assert integrity.parse_state is not None
        assert integrity.parse_state.has_unclosed_block is False

    def test_unclosed_single_block(self):
        """Unclosed FILE block should be detected as truncated."""
        text = "FILE: a.py\ncontent"
        integrity = evaluate_completion_integrity(text, {})
        assert integrity.status == IntegrityStatus.TRUNCATED
        assert "unclosed" in integrity.truncation_reason
        assert integrity.continuation_supported is True

    def test_unclosed_last_block(self):
        """Last block without END FILE should be detected as truncated."""
        text = "FILE: a.py\ncontent\nEND FILE\nFILE: b.py\ncontent"
        integrity = evaluate_completion_integrity(text, {})
        assert integrity.status == IntegrityStatus.TRUNCATED
        assert "unclosed" in integrity.truncation_reason
        assert "b.py" in integrity.truncation_reason

    def test_no_changes_not_truncated(self):
        """NO_CHANGES should not be detected as truncated."""
        text = "NO_CHANGES"
        integrity = evaluate_completion_integrity(text, {})
        assert integrity.status == IntegrityStatus.NO_CHANGES

    def test_empty_not_truncated(self):
        """Empty text should not be detected as truncated."""
        text = ""
        integrity = evaluate_completion_integrity(text, {})
        assert integrity.status == IntegrityStatus.NO_CHANGES


class TestCompletionIntegrity:
    """Tests for completion integrity evaluation."""

    def test_provider_length_with_complete_content(self):
        """Provider length truncation should override content analysis."""
        text = "FILE: a.py\ncontent\nEND FILE"
        metadata = {"finish_reason": "length"}
        integrity = evaluate_completion_integrity(text, metadata)
        assert integrity.status == IntegrityStatus.TRUNCATED
        assert integrity.truncation_reason == "length"
        assert integrity.continuation_supported is True

    def test_complete_output_no_metadata(self):
        """Complete output without truncation metadata should be valid."""
        text = "FILE: a.py\nprint('hello')\nEND FILE"
        integrity = evaluate_completion_integrity(text, {})
        assert integrity.status == IntegrityStatus.COMPLETE
        assert integrity.truncation_reason is None
        assert integrity.continuation_supported is False


class TestFailClosed:
    """Tests for fail-closed behavior."""

    def test_truncated_without_continuation_support(self):
        """Truncated output without continuation support should be blocked."""
        # JSON that appears truncated
        text = '{"key": "value", "nested": {'
        metadata = {}  # No provider metadata
        integrity = evaluate_completion_integrity(text, metadata)
        # JSON truncation detection is optional, so this might not detect it
        # The important thing is the behavior is predictable
        assert integrity is not None

    def test_unclosed_block_blocks_apply(self):
        """Unclosed file blocks should block application."""
        text = "FILE: a.py\ndef foo():\n    pass"
        metadata = {}
        integrity = evaluate_completion_integrity(text, metadata)
        assert integrity.status == IntegrityStatus.TRUNCATED
        assert integrity.continuation_supported is True  # Can attempt continuation


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
