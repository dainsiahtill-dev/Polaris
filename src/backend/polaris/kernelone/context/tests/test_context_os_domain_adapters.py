from __future__ import annotations

from polaris.kernelone.context.context_os import (
    CodeContextDomainAdapter,
    GenericContextDomainAdapter,
    RoutingClass,
)
from polaris.kernelone.context.context_os.models import StateFirstContextOSPolicy, TranscriptEvent


def _event(
    *,
    role: str,
    kind: str,
    content: str,
    sequence: int = 1,
    _metadata: dict[str, str] | None = None,
) -> TranscriptEvent:
    return TranscriptEvent(
        event_id=f"evt_{sequence}",
        sequence=sequence,
        role=role,
        kind=kind,
        route="",
        content=content,
        source_turns=(f"t{sequence}",),
        created_at="2026-03-26T00:00:00Z",
        _metadata=dict(_metadata or {}),
    )


def test_generic_adapter_stays_domain_agnostic() -> None:
    adapter = GenericContextDomainAdapter()
    hints = adapter.extract_state_hints(
        _event(
            role="user",
            kind="user_message",
            content="Please write the rollout plan and keep it concise.",
        )
    )

    assert hints.goals
    assert hints.style
    assert hints.entities == ()


def test_code_adapter_extracts_code_entities() -> None:
    adapter = CodeContextDomainAdapter()
    hints = adapter.extract_state_hints(
        _event(
            role="user",
            kind="user_message",
            content="Fix polaris/kernelone/context/session_continuity.py and inspect SessionContinuityEngine().",
        )
    )

    assert "polaris/kernelone/context/session_continuity.py" in hints.entities
    assert any("SessionContinuityEngine()" in item for item in hints.entities)


def test_code_adapter_archives_code_blocks() -> None:
    adapter = CodeContextDomainAdapter()
    event = _event(
        role="tool",
        kind="tool_result",
        content="```python\nprint('hello')\n```",
        sequence=4,
    )
    decision = adapter.classify_event(event, policy=StateFirstContextOSPolicy())
    artifact = adapter.build_artifact(event, artifact_id="art_test", policy=StateFirstContextOSPolicy())

    assert decision.route == RoutingClass.ARCHIVE
    assert decision.confidence >= 0.9
    assert "code_artifact" in decision.reasons or "tool_result" in decision.reasons
    assert artifact is not None
    assert artifact.artifact_type in {"code_block", "code_tool_result"}


def test_generic_adapter_exposes_routing_confidence() -> None:
    adapter = GenericContextDomainAdapter()
    decision = adapter.classify_event(
        _event(
            role="assistant",
            kind="assistant_message",
            content="I will draft the rollout plan and keep the open loops visible.",
            sequence=5,
        ),
        policy=StateFirstContextOSPolicy(),
    )

    assert decision.route == RoutingClass.PATCH
    assert 0.0 <= decision.confidence <= 1.0
    assert decision.reasons


def test_generic_adapter_lifts_followup_confirmation_into_goal_and_open_loop() -> None:
    adapter = GenericContextDomainAdapter()
    hints = adapter.extract_state_hints(
        _event(
            role="user",
            kind="user_message",
            content="需要",
            sequence=6,
        )
    )
    assert hints.goals == ()
    assert hints.open_loops == ()

    hints = adapter.extract_state_hints(
        _event(
            role="user",
            kind="user_message",
            content="需要",
            sequence=7,
            _metadata={
                "followup_action": "深入查看特定代码段或进行代码修复",
                "followup_confirmed": "true",
            },
        )
    )
    assert hints.goals == ("深入查看特定代码段或进行代码修复",)
    assert hints.open_loops == ("深入查看特定代码段或进行代码修复",)


def test_generic_adapter_ignores_control_plane_tool_noise() -> None:
    adapter = GenericContextDomainAdapter()
    hints = adapter.extract_state_hints(
        _event(
            role="tool",
            kind="tool_result",
            content='Tool result: read_file\n```json\n{"path": "src/app.ts"}\n```',
            sequence=8,
        )
    )

    assert hints.goals == ()
    assert hints.open_loops == ()
    assert hints.decisions == ()


def test_code_adapter_ignores_control_plane_system_noise() -> None:
    adapter = CodeContextDomainAdapter()
    hints = adapter.extract_state_hints(
        _event(
            role="system",
            kind="system_message",
            content="[SYSTEM WARNING] 8 consecutive READ-ONLY operations with ZERO write/edit output.",
            sequence=9,
        )
    )

    assert hints.goals == ()
    assert hints.open_loops == ()
    assert hints.entities == ()
