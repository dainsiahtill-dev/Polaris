from __future__ import annotations

from dataclasses import replace

from polaris.kernelone.context.context_os import (
    CodeContextDomainAdapter,
    EpisodeCard,
    GenericContextDomainAdapter,
    RoutingClass,
    StateFirstContextOS,
    summarize_context_os_payload,
)
from polaris.kernelone.context.context_os.helpers import get_metadata_value


async def test_generic_context_os_projects_without_code_domain_entities() -> None:
    engine = StateFirstContextOS(domain_adapter=GenericContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "Please write a concise implementation plan and keep the response detailed.",
                "sequence": 1,
            },
            {
                "role": "assistant",
                "content": "I will draft the plan and keep the response detailed.",
                "sequence": 2,
            },
        ],
        recent_window_messages=2,
    )

    assert projection.snapshot.adapter_id == "generic"
    assert projection.snapshot.working_state.task_state.current_goal is not None
    assert projection.snapshot.working_state.active_entities == ()
    assert "Current goal:" in projection.head_anchor


async def test_code_context_os_enriches_entities_and_artifacts() -> None:
    engine = StateFirstContextOS(domain_adapter=CodeContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "Fix polaris/kernelone/context/session_continuity.py and add tests for SessionContinuityEngine.",
                "sequence": 3,
            },
            {
                "role": "tool",
                "content": "```python\nfrom polaris.kernelone.context.session_continuity import SessionContinuityEngine\n```",
                "sequence": 4,
            },
        ],
        recent_window_messages=1,
    )

    entities = {item.value for item in projection.snapshot.working_state.active_entities}
    assert projection.snapshot.adapter_id == "code"
    assert "polaris/kernelone/context/session_continuity.py" in entities
    assert projection.snapshot.artifact_store
    assert projection.artifact_stubs
    assert projection.artifact_stubs[0].artifact_type in {
        "code_block",
        "code_tool_result",
        "file_excerpt",
    }


async def test_context_os_keeps_goal_turn_alive_outside_recent_window() -> None:
    engine = StateFirstContextOS(domain_adapter=GenericContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "Implement the new memory runtime and preserve the existing facade.",
                "sequence": 10,
            },
            {
                "role": "assistant",
                "content": "I will start with the runtime contracts.",
                "sequence": 11,
            },
            {
                "role": "assistant",
                "content": "Status update one.",
                "sequence": 12,
            },
            {
                "role": "assistant",
                "content": "Status update two.",
                "sequence": 13,
            },
        ],
        recent_window_messages=1,
    )

    active_sequences = {item.sequence for item in projection.active_window}
    assert 10 in active_sequences
    assert 13 in active_sequences


async def test_context_os_builds_run_card_and_slice_plan() -> None:
    engine = StateFirstContextOS(domain_adapter=GenericContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "Do not replace context.engine. Implement the runtime blueprint and keep open loops visible.",
                "sequence": 20,
            },
            {
                "role": "assistant",
                "content": "I will keep the facade stable and implement the runtime blueprint.",
                "sequence": 21,
            },
        ],
        recent_window_messages=1,
    )

    assert projection.run_card is not None
    assert projection.context_slice_plan is not None
    assert projection.run_card.current_goal
    assert any("do not replace context.engine" in item.lower() for item in projection.run_card.hard_constraints)
    assert "latest_user_turn" in projection.context_slice_plan.roots
    assert projection.context_slice_plan.included


async def test_context_os_decision_log_tracks_kind_and_supersedes() -> None:
    engine = StateFirstContextOS(domain_adapter=GenericContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "The plan is Context OS first.",
                "sequence": 30,
            },
            {
                "role": "assistant",
                "content": "Decision: adopt the plan and keep SessionContinuityEngine as facade.",
                "sequence": 31,
            },
            {
                "role": "assistant",
                "content": "Decision: update the plan and keep Context OS first with compatibility preserved.",
                "sequence": 32,
            },
        ],
        recent_window_messages=2,
    )

    decisions = list(projection.snapshot.working_state.decision_log)
    assert decisions
    assert any(item.kind == "accepted_plan" for item in decisions)
    assert decisions[-1].to_dict()["value"]
    assert decisions[-1].supersedes == decisions[-2].decision_id


async def test_context_os_search_memory_returns_explainable_scores() -> None:
    engine = StateFirstContextOS(domain_adapter=CodeContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "Fix polaris/kernelone/context/session_continuity.py and preserve context.engine behavior.",
                "sequence": 40,
            },
            {
                "role": "tool",
                "content": "```python\nfrom polaris.kernelone.context.session_continuity import SessionContinuityEngine\n```",
                "sequence": 41,
            },
        ],
        recent_window_messages=2,
    )

    results = engine.search_memory(
        projection.snapshot,
        "session_continuity.py",
        limit=4,
    )

    assert results
    first = results[0]
    assert "score_breakdown" in first
    assert "why" in first
    assert any(reason in first["why"] for reason in ("lexical_match", "goal_overlap", "entity_match"))


async def test_context_os_payload_summary_uses_run_card_and_slice_plan() -> None:
    summary = summarize_context_os_payload(
        {
            "adapter_id": "code",
            "run_card": {
                "current_goal": "Fix session continuity runtime",
                "hard_constraints": ["Do not replace context.engine"],
                "open_loops": ["wire CLI observability"],
                "active_entities": ["polaris/kernelone/context/session_continuity.py"],
                "active_artifacts": ["art_001"],
                "next_action_hint": "Patch delivery.cli debug payload",
            },
            "context_slice_plan": {
                "plan_id": "plan-1",
                "budget_tokens": 2048,
                "roots": ["latest_user_turn"],
                "included": [{"type": "state", "ref": "run_card", "reason": "always pin"}],
                "excluded": [{"type": "episode", "ref": "ep_1", "reason": "closed"}],
                "pressure_level": "soft",
            },
            "episode_cards": [{"episode_id": "ep_1"}],
        }
    )

    assert summary["present"] is True
    assert summary["adapter_id"] == "code"
    assert summary["current_goal"] == "Fix session continuity runtime"
    assert summary["pressure_level"] == "soft"
    assert summary["hard_constraint_count"] == 1
    assert summary["open_loop_count"] == 1
    assert summary["active_entity_count"] == 1
    assert summary["active_artifact_count"] == 1
    assert summary["included_count"] == 1
    assert summary["excluded_count"] == 1
    assert summary["episode_count"] == 1


async def test_context_os_reclassify_event_preserves_route_history() -> None:
    engine = StateFirstContextOS(domain_adapter=CodeContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "tool",
                "content": "```python\nprint('hello')\n```",
                "sequence": 60,
            },
        ],
        recent_window_messages=1,
    )

    event = projection.snapshot.transcript_log[0]
    assert event.route == RoutingClass.ARCHIVE
    reclassified = await engine.reclassify_event(
        projection.snapshot,
        event_id=event.event_id,
        new_route=RoutingClass.SUMMARIZE,
        reason="manual override for narrative continuity",
    )

    updated_event = reclassified.snapshot.transcript_log[0]
    assert updated_event.route == RoutingClass.SUMMARIZE
    assert get_metadata_value(updated_event.metadata, "routing_status") == "reclassified"
    assert get_metadata_value(updated_event.metadata, "routing_confidence") == 1.0
    route_history = get_metadata_value(updated_event.metadata, "route_history")
    assert route_history[-1]["from"] == RoutingClass.ARCHIVE
    assert route_history[-1]["to"] == RoutingClass.SUMMARIZE
    assert reclassified.snapshot.artifact_store


async def test_context_os_reopen_episode_pins_closed_span() -> None:
    engine = StateFirstContextOS(domain_adapter=GenericContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "Implement the runtime blueprint and preserve context.engine.",
                "sequence": 70,
            },
            {
                "role": "assistant",
                "content": "I will start with the contracts and receipts.",
                "sequence": 71,
            },
            {
                "role": "assistant",
                "content": "Status update alpha.",
                "sequence": 72,
            },
            {
                "role": "assistant",
                "content": "Status update beta.",
                "sequence": 73,
            },
        ],
        recent_window_messages=1,
    )
    episode = EpisodeCard(
        episode_id="ep_reopen",
        from_sequence=70,
        to_sequence=72,
        intent="Implement the runtime blueprint",
        outcome="Drafted contracts and receipts",
        source_spans=("t70:t72",),
        digest_64="runtime blueprint",
        digest_256="runtime blueprint and receipts",
        digest_1k="runtime blueprint and receipts narrative",
        status="sealed",
    )
    reopened = await engine.reopen_episode(
        projection.snapshot.model_copy(update={"episode_store": (episode,)}),
        episode_id="ep_reopen",
        reason="open loop reactivated by follow-up turn",
        recent_window_messages=1,
    )

    reopened_episode = reopened.snapshot.episode_store[0]
    assert reopened_episode.status == "reopened"
    assert reopened_episode.reopen_reason == "open loop reactivated by follow-up turn"
    active_sequences = {item.sequence for item in reopened.active_window}
    assert episode.from_sequence in active_sequences
    assert episode.to_sequence in active_sequences
    assert get_metadata_value(reopened.active_window[0].metadata, "reopen_hold") == episode.episode_id


async def test_context_os_short_affirmation_keeps_assistant_followup_focus() -> None:
    engine = StateFirstContextOS(domain_adapter=GenericContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "总结这个项目代码",
                "sequence": 90,
            },
            {
                "role": "assistant",
                "content": "需要我深入查看特定代码段或进行代码修复吗？",
                "sequence": 91,
            },
            {
                "role": "user",
                "content": "需要",
                "sequence": 92,
            },
        ],
        recent_window_messages=1,
    )

    goal = projection.snapshot.working_state.task_state.current_goal
    assert goal is not None
    assert "深入查看特定代码段或进行代码修复" in goal.value
    assert projection.run_card is not None
    assert "深入查看特定代码段或进行代码修复" in projection.run_card.next_action_hint
    active_sequences = {item.sequence for item in projection.active_window}
    assert {90, 91, 92}.issubset(active_sequences)


async def test_context_os_pins_latest_three_messages_even_when_recent_window_is_one() -> None:
    engine = StateFirstContextOS(domain_adapter=GenericContextDomainAdapter())
    projection = await engine.project(
        messages=[
            {
                "role": "user",
                "content": "alpha",
                "sequence": 100,
            },
            {
                "role": "assistant",
                "content": "beta",
                "sequence": 101,
            },
            {
                "role": "user",
                "content": "好的",
                "sequence": 102,
            },
            {
                "role": "assistant",
                "content": "收到",
                "sequence": 103,
            },
        ],
        recent_window_messages=1,
    )

    active_sequences = {item.sequence for item in projection.active_window}
    assert {101, 102, 103}.issubset(active_sequences)
    excluded_refs = (
        {item.ref for item in projection.context_slice_plan.excluded} if projection.context_slice_plan else set()
    )
    for item in projection.active_window:
        assert item.event_id not in excluded_refs
