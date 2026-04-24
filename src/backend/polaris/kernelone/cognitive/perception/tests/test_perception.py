"""Unit tests for Perception Layer."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.perception.engine import PerceptionLayer
from polaris.kernelone.cognitive.types import ClarityLevel as CognitiveClarityLevel


@pytest.fixture
def perception():
    return PerceptionLayer()


@pytest.mark.asyncio
async def test_semantic_parser_recognizes_create(perception):
    graph, _uncertainty = await perception.process("Create a new API endpoint for user authentication")
    assert graph.nodes[0].intent_type == "create_file"
    assert graph.nodes[0].confidence >= 0.7


@pytest.mark.asyncio
async def test_semantic_parser_recognizes_modify(perception):
    graph, _uncertainty = await perception.process("Update the user authentication module")
    assert graph.nodes[0].intent_type == "modify_file"


@pytest.mark.asyncio
async def test_semantic_parser_recognizes_read(perception):
    graph, _uncertainty = await perception.process("Read the file at src/main.py")
    assert graph.nodes[0].intent_type == "read_file"


@pytest.mark.asyncio
async def test_semantic_parser_recognizes_delete(perception):
    graph, _uncertainty = await perception.process("Delete the temporary files")
    assert graph.nodes[0].intent_type == "delete_file"


@pytest.mark.asyncio
async def test_uncertainty_drives_path_selection(perception):
    # High confidence message -> bypass or fast_think
    _graph_high, uncertainty_high = await perception.process("Read the file at src/main.py")
    assert uncertainty_high.recommended_action in ("bypass", "fast_think")

    # Ambiguous message -> full pipe
    _graph_low, uncertainty_low = await perception.process("Help me with something maybe")
    assert uncertainty_low.uncertainty_score > uncertainty_high.uncertainty_score


@pytest.mark.asyncio
async def test_unstated_needs_detection(perception):
    graph, _uncertainty = await perception.process("Create a new test file")
    # Should detect implicit need for documentation, backup, etc.
    unstated_types = [n.intent_type for n in graph.nodes]
    assert "unstated" in unstated_types


def test_clarity_level_enum():
    assert CognitiveClarityLevel.FUZZY == 1
    assert CognitiveClarityLevel.FULL_TRANSPARENT == 5


@pytest.mark.asyncio
async def test_intent_graph_contains_surface_intent(perception):
    graph, _uncertainty = await perception.process("Fix the bug in login")
    assert len(graph.nodes) >= 1
    assert graph.chains is not None
    assert len(graph.chains) >= 1
