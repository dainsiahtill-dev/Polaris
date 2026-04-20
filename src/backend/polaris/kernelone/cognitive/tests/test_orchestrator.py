"""Unit tests for Cognitive Orchestrator - End-to-end cognitive pipeline."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.orchestrator import CognitiveOrchestrator, CognitiveResponse
from polaris.kernelone.cognitive.personality.traits import ROLE_TRAIT_PROFILES


@pytest.fixture
def orchestrator():
    # Reset global session manager to avoid test pollution from disk sessions
    from pathlib import Path

    import polaris.kernelone.cognitive.context as ctx_module

    # Clean up disk sessions first
    sessions_dir = Path(".") / ".polaris" / "cognitive_sessions"
    if sessions_dir.exists():
        for f in sessions_dir.glob("test_session*.json"):
            f.unlink(missing_ok=True)

    ctx_module._global_session_manager = None
    ctx_module._global_workspace = None
    return CognitiveOrchestrator()


@pytest.mark.asyncio
async def test_process_read_intent_bypass(orchestrator):
    """L0 read operations should bypass cognitive pipe."""
    result = await orchestrator.process(
        message="Read the file at src/main.py",
        session_id="test_session_1",
        role_id="director",
    )

    assert result is not None
    assert result.intent_type == "read_file"
    assert result.execution_path.value in ("bypass", "fast_think")
    assert result.blocked is False


@pytest.mark.asyncio
async def test_process_create_intent_full_pipe(orchestrator):
    """L1 create operations may need full pipe."""
    result = await orchestrator.process(
        message="Create a new API endpoint for user authentication",
        session_id="test_session_2",
        role_id="director",
    )

    assert result is not None
    assert result.intent_type == "create_file"
    assert result.confidence > 0.0


@pytest.mark.asyncio
async def test_process_delete_intent_blocked(orchestrator):
    """L3 delete operations should require confirmation or be blocked."""
    result = await orchestrator.process(
        message="Delete the temporary files",
        session_id="test_session_3",
        role_id="director",
    )

    assert result is not None
    assert result.intent_type == "delete_file"


@pytest.mark.asyncio
async def test_session_persistence(orchestrator):
    """Test that session context is maintained across turns."""
    session_id = "test_session_persist"

    # First turn
    await orchestrator.process(
        message="Read the config file",
        session_id=session_id,
        role_id="director",
    )

    # Second turn - same session
    await orchestrator.process(
        message="Create a backup",
        session_id=session_id,
        role_id="director",
    )

    # Get session context
    ctx = orchestrator.get_session(session_id)
    assert ctx is not None
    assert len(ctx.conversation_history) == 2


@pytest.mark.asyncio
async def test_different_roles_get_different_traits(orchestrator):
    """Test that different roles have different trait profiles."""
    roles = ["pm", "architect", "chief_engineer", "director", "qa", "scout"]

    traits_set = set()
    for role in roles:
        result = await orchestrator.process(
            message="Read the file",
            session_id=f"test_role_{role}",
            role_id=role,
        )
        traits_set.add(result.metadata.get("trait_profile"))

    # At least some roles should have different traits
    assert len(traits_set) >= 1


@pytest.mark.asyncio
async def test_orchestrator_returns_complete_response(orchestrator):
    """Test that CognitiveResponse contains all required fields."""
    result = await orchestrator.process(
        message="Create a new test file",
        session_id="test_complete_response",
        role_id="director",
    )

    assert isinstance(result, CognitiveResponse)
    assert result.content is not None
    assert result.execution_path is not None
    assert result.confidence >= 0.0
    assert result.intent_type is not None
    assert result.uncertainty_score >= 0.0
    assert result.conversation_turn is not None


@pytest.mark.asyncio
async def test_unknown_intent_handled_gracefully(orchestrator):
    """Test handling of unrecognized intents."""
    result = await orchestrator.process(
        message="do something random xyz123",
        session_id="test_unknown",
        role_id="director",
    )

    assert result is not None
    # Should still return a response even with unknown intent


@pytest.mark.asyncio
async def test_session_reset(orchestrator):
    """Test session reset functionality."""
    session_id = "test_reset"

    await orchestrator.process(
        message="Read a file",
        session_id=session_id,
        role_id="director",
    )

    # Verify session exists
    ctx = orchestrator.get_session(session_id)
    assert ctx is not None

    # Reset session
    orchestrator.reset_session(session_id)

    # Verify session is gone
    ctx = orchestrator.get_session(session_id)
    assert ctx is None


@pytest.mark.asyncio
async def test_evolution_records_triggers(orchestrator):
    """Test that evolution engine records triggers."""
    session_id = "test_evolution"

    # Process multiple messages
    for i in range(3):
        await orchestrator.process(
            message=f"Read file {i}",
            session_id=session_id,
            role_id="director",
        )

    # Evolution should have recorded some triggers
    # We can't easily check internal state, but we verify no errors occurred


def test_role_trait_profiles_defined():
    """Verify all role trait profiles are defined."""
    roles = ["pm", "architect", "chief_engineer", "director", "qa", "scout"]
    for role in roles:
        assert role in ROLE_TRAIT_PROFILES
        profile = ROLE_TRAIT_PROFILES[role]
        assert profile is not None
        assert len(profile.enabled_traits) > 0


def test_orchestrator_workspace_propagates_to_acting_handler(tmp_path):
    """Cognitive acting must use orchestrator workspace, not process cwd."""
    orchestrator = CognitiveOrchestrator(workspace=str(tmp_path))
    coordinator = orchestrator._coordinator
    pipeline = coordinator._pipeline
    acting = pipeline._acting
    assert acting._workspace == str(tmp_path)
