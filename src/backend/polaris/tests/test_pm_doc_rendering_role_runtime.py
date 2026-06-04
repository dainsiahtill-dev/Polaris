from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.delivery.cli.pm.orchestration import doc_rendering


def _quality_markdown() -> str:
    return """# Requirements

## Product Goal
- Deliver a playable collaborative card experience with explicit table state, turn order, and player identity.
- Keep every user-facing action measurable through command output, saved artifacts, or deterministic UI state.

## Functional Scope
- Create a lobby flow that allows two local players to join, leave, and resume the same table.
- Implement card movement rules with validation for draw, discard, reveal, and score updates.
- Persist match snapshots so interrupted sessions can be restored without corrupting player hands.

## Engineering Constraints
- Use typed boundaries between state, rendering, and persistence modules.
- Keep domain logic testable without launching a browser or server.
- Reject invalid actions with structured errors that include the player id and attempted action.

## Verification
- Unit tests cover state transitions, invalid actions, and persistence restore behavior.
- Integration tests create a table, perform a full turn, reload state, and verify scores.
- Evidence is stored under runtime verification artifacts for PM and QA review.
"""


def test_render_llm_authored_docs_uses_architect_role_runtime(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    class FakeRoleRuntimeService:
        async def execute_role_session(self, command):
            captured["command"] = command
            return SimpleNamespace(
                ok=True,
                output=_quality_markdown(),
                usage={},
                metadata={"provider_type": "role_runtime", "model": "test-model"},
            )

    monkeypatch.setattr(doc_rendering, "_role_llm_docs_enabled", lambda: True)
    monkeypatch.setattr(doc_rendering, "_create_role_runtime_service", lambda: FakeRoleRuntimeService())

    rendered, stats = doc_rendering._render_llm_authored_docs(
        workspace_full=str(tmp_path),
        docs_map={"docs/10_requirements.md": "# Draft\n"},
        fields={"goal": "Build a multiplayer card game"},
        qa_commands=["pytest -q"],
        fallback_model="unused-model",
    )

    assert stats["accepted"] == 1
    assert rendered["docs/10_requirements.md"].startswith("# Requirements")
    command = captured["command"]
    assert command.role == "architect"
    assert command.workspace == str(tmp_path)
    assert command.domain == "document"
    assert command.stream is False
    assert command.host_kind == "pm_doc_rendering"
    assert command.metadata["role_runtime_required"] is True
    assert command.metadata["cognitive_runtime_required"] is True
    assert command.metadata["context_os_expected"] is True
    assert command.metadata["runtime_fallback_used"] is False
    assert command.metadata["fallback_policy"] == "fail_closed"
    assert "legacy_fallback_used" not in command.metadata
    assert command.context["doc_path"] == "docs/10_requirements.md"
