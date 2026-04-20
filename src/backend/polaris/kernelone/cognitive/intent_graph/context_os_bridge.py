"""ContextOS Bridge - Adapter between ContextOS and IntentGraph.

Provides bidirectional conversion between ContextOS state representations
and IntentGraph cognitive structures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.cognitive.perception.models import (
        IntentChain,
        IntentEdge,
        IntentGraph,
        IntentNode,
    )
    from polaris.kernelone.context.context_os.models import (
        ContextOSSnapshot,
        TranscriptEvent,
    )


def transcript_event_to_intent_node(event: TranscriptEvent) -> IntentNode | None:
    """Convert a TranscriptEvent to an IntentNode.

    Args:
        event: The transcript event to convert.

    Returns:
        IntentNode if the event can be interpreted as an intent, None otherwise.
    """
    from polaris.kernelone.cognitive.perception.models import IntentNode

    # Only convert user messages with content
    if event.role != "user" or not event.content.strip():
        return None

    # Map route to intent type
    intent_type = "surface"
    if event.route == "PATCH":
        intent_type = "deep"
    elif event.route == "ARCHIVE":
        intent_type = "unstated"

    return IntentNode(
        node_id=f"intent_{event.event_id}",
        intent_type=intent_type,
        content=event.content,
        confidence=0.8 if event.route == "CLEAR" else 0.6,
        source_event_id=event.event_id,
        uncertainty_factors=(),
        metadata={
            "sequence": event.sequence,
            "route": event.route,
            "kind": event.kind,
        },
    )


def snapshot_to_intent_graph(
    snapshot: ContextOSSnapshot,
    graph_id: str | None = None,
) -> IntentGraph:
    """Convert a ContextOSSnapshot to an IntentGraph.

        Extracts intent information from transcript events and working state
    to build a cognitive intent graph.

        Args:
            snapshot: The ContextOS snapshot to convert.
            graph_id: Optional graph ID (defaults to snapshot-derived ID).

        Returns:
            IntentGraph representing the cognitive state.
    """
    from datetime import datetime, timezone

    from polaris.kernelone.cognitive.perception.models import (
        IntentChain,
        IntentEdge,
        IntentGraph,
        IntentNode,
    )

    nodes: list[IntentNode] = []
    edges: list[IntentEdge] = []
    chains: list[IntentChain] = []

    # Convert transcript events to intent nodes
    user_events: list[TranscriptEvent] = []
    for event in snapshot.transcript_log:
        node = transcript_event_to_intent_node(event)
        if node:
            nodes.append(node)
            user_events.append(event)

    # Create edges between consecutive user intents
    for i in range(len(nodes) - 1):
        edges.append(
            IntentEdge(
                from_node_id=nodes[i].node_id,
                to_node_id=nodes[i + 1].node_id,
                edge_type="leads_to",
                confidence=0.7,
                reasoning="Consecutive user messages in conversation",
            )
        )

    # Extract goal from working state as deep intent
    current_goal = snapshot.working_state.task_state.current_goal
    if current_goal:
        goal_node = IntentNode(
            node_id=f"goal_{snapshot.updated_at or 'unknown'}",
            intent_type="deep",
            content=current_goal.value,
            confidence=current_goal.confidence,
            source_event_id=current_goal.source_turns[0] if current_goal.source_turns else "",
            uncertainty_factors=(),
            metadata={"path": current_goal.path},
        )
        nodes.append(goal_node)

        # Link last user intent to goal
        if nodes:
            edges.append(
                IntentEdge(
                    from_node_id=nodes[-1].node_id,
                    to_node_id=goal_node.node_id,
                    edge_type="refines",
                    confidence=0.75,
                    reasoning="User intent refines to task goal",
                )
            )

    # Create intent chains from episode cards if available
    for episode in snapshot.episode_store:
        if episode.intent:
            chain_nodes = [n for n in nodes if any(n.source_event_id in span for span in episode.source_spans)]
            if chain_nodes:
                surface = chain_nodes[0] if chain_nodes else None
                deep = chain_nodes[-1] if len(chain_nodes) > 1 else None

                chains.append(
                    IntentChain(
                        chain_id=f"chain_{episode.episode_id}",
                        surface_intent=surface,
                        deep_intent=deep,
                        uncertainty=0.3 if episode.outcome else 0.7,
                        confidence_level="high" if episode.outcome else "medium",
                        unstated_needs=(),
                    )
                )

    # If no chains created but we have nodes, create a default chain
    if not chains and nodes:
        chains.append(
            IntentChain(
                chain_id=f"chain_default_{snapshot.updated_at or 'unknown'}",
                surface_intent=nodes[0] if nodes else None,
                deep_intent=nodes[-1] if len(nodes) > 1 else None,
                uncertainty=0.5,
                confidence_level="medium",
                unstated_needs=(),
            )
        )

    now = datetime.now(timezone.utc).isoformat()
    return IntentGraph(
        graph_id=graph_id or f"graph_{snapshot.updated_at or now}",
        session_id=snapshot.adapter_id,
        created_at=snapshot.updated_at or now,
        updated_at=now,
        nodes=tuple(nodes),
        edges=tuple(edges),
        chains=tuple(chains),
    )


def intent_graph_to_run_card_updates(graph: IntentGraph) -> dict[str, Any]:
    """Extract RunCard updates from an IntentGraph.

    Args:
        graph: The intent graph to analyze.

    Returns:
        Dictionary of RunCard field updates.
    """
    updates: dict[str, Any] = {}

    # Extract latest user intent
    surface_intents = [n for n in graph.nodes if n.intent_type == "surface"]
    if surface_intents:
        latest = max(surface_intents, key=lambda n: n.metadata.get("sequence", 0))
        updates["latest_user_intent"] = latest.content

    # Extract goal from deep intents
    deep_intents = [n for n in graph.nodes if n.intent_type == "deep"]
    if deep_intents:
        updates["current_goal"] = deep_intents[0].content

    # Extract open loops from chains
    open_loops: list[str] = []
    for chain in graph.chains:
        if chain.unstated_needs:
            for need in chain.unstated_needs:
                open_loops.append(need.content)
    if open_loops:
        updates["open_loops"] = tuple(open_loops)

    return updates


def merge_intent_graph_into_snapshot(
    snapshot: ContextOSSnapshot,
    graph: IntentGraph,
) -> ContextOSSnapshot:
    """Merge IntentGraph insights into a ContextOSSnapshot.

    Updates working state with intent-derived information.

    Args:
        snapshot: The original ContextOS snapshot.
        graph: The intent graph to merge.

    Returns:
        Updated ContextOSSnapshot with merged intent information.
    """
    from dataclasses import replace
    from datetime import datetime, timezone

    from polaris.kernelone.context.context_os.models import StateEntry

    updates = intent_graph_to_run_card_updates(graph)
    working_state = snapshot.working_state

    # Update task state goal if we have a deep intent
    if updates.get("current_goal"):
        new_goal = StateEntry(
            entry_id=f"goal_{datetime.now(timezone.utc).isoformat()}",
            path="task.current_goal",
            value=updates["current_goal"],
            source_turns=(),
            confidence=0.8,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        working_state = replace(
            working_state,
            task_state=replace(
                working_state.task_state,
                current_goal=new_goal,
            ),
        )

    # Create new snapshot with updated working state
    return replace(
        snapshot,
        working_state=working_state,
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
