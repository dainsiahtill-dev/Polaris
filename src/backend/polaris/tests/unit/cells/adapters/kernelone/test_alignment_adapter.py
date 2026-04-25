"""Unit tests for AlignmentServiceAdapter."""

from __future__ import annotations

import pytest
from polaris.cells.adapters.kernelone.alignment_adapter import AlignmentServiceAdapter
from polaris.cells.values.alignment_service import ValueAlignmentResult
from polaris.kernelone.ports.alignment import IAlignmentService


class TestAlignmentServiceAdapter:
    """Tests for the AlignmentServiceAdapter bridging KernelOne and Cells."""

    @pytest.fixture
    def adapter(self) -> AlignmentServiceAdapter:
        return AlignmentServiceAdapter()

    def test_is_instance_of_ialignment_service(self, adapter: AlignmentServiceAdapter) -> None:
        assert isinstance(adapter, IAlignmentService)

    @pytest.mark.asyncio
    async def test_evaluate_returns_result(self, adapter: AlignmentServiceAdapter) -> None:
        result = await adapter.evaluate(action="read file", context="test", user_intent="explore")
        assert isinstance(result, ValueAlignmentResult)
        assert 0.0 <= result.overall_score <= 1.0
        assert result.final_verdict in ("APPROVED", "CONDITIONAL", "REJECTED")

    @pytest.mark.asyncio
    async def test_evaluate_dangerous_command_rejected(self, adapter: AlignmentServiceAdapter) -> None:
        result = await adapter.evaluate(action="rm -rf /", context="", user_intent="cleanup")
        assert result.final_verdict == "REJECTED"
        assert result.overall_score < 0.7

    @pytest.mark.asyncio
    async def test_is_action_aligned_true(self, adapter: AlignmentServiceAdapter) -> None:
        aligned = await adapter.is_action_aligned("read file", context="test", user_intent="explore")
        assert aligned is True

    @pytest.mark.asyncio
    async def test_is_action_aligned_false(self, adapter: AlignmentServiceAdapter) -> None:
        aligned = await adapter.is_action_aligned("rm -rf /")
        assert aligned is False

    @pytest.mark.asyncio
    async def test_get_alignment_score_range(self, adapter: AlignmentServiceAdapter) -> None:
        score = await adapter.get_alignment_score("read file")
        assert 0.0 <= score <= 1.0

    @pytest.mark.asyncio
    async def test_explain_misalignment_approved(self, adapter: AlignmentServiceAdapter) -> None:
        explanation = await adapter.explain_misalignment("read file")
        assert "fully aligned" in explanation

    @pytest.mark.asyncio
    async def test_explain_misalignment_rejected(self, adapter: AlignmentServiceAdapter) -> None:
        explanation = await adapter.explain_misalignment("rm -rf /")
        assert explanation != ""
        assert "fully aligned" not in explanation
