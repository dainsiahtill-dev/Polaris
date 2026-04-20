"""Unit tests for Neural Syndicate router module."""

from __future__ import annotations

import pytest
from polaris.kernelone.multi_agent.neural_syndicate.protocol import (
    AgentCapability,
    AgentMessage,
    Intent,
    Performative,
    RoutingStrategy,
)
from polaris.kernelone.multi_agent.neural_syndicate.router import (
    MessageRouter,
    RouteRule,
    create_broadcast_rule,
    create_critic_rule,
)


class TestRouteRule:
    """Tests for RouteRule dataclass."""

    def test_create_route_rule(self) -> None:
        """RouteRule should be created correctly."""
        rule = RouteRule(
            name="test_rule",
            priority=5,
            intent=Intent.CODE_REVIEW,
            strategy=RoutingStrategy.CAPABILITY_MATCH,
            target_agents=("critic-1", "critic-2"),
        )
        assert rule.name == "test_rule"
        assert rule.priority == 5
        assert rule.intent == Intent.CODE_REVIEW
        assert rule.strategy == RoutingStrategy.CAPABILITY_MATCH
        assert rule.target_agents == ("critic-1", "critic-2")

    def test_matches_performative(self) -> None:
        """matches should check performative."""
        rule = RouteRule(name="test", performative=Performative.REQUEST)
        msg_request = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        msg_inform = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
        )
        assert rule.matches(msg_request) is True
        assert rule.matches(msg_inform) is False

    def test_matches_intent(self) -> None:
        """matches should check intent."""
        rule = RouteRule(name="test", intent=Intent.CODE_REVIEW)
        msg_matching = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.CODE_REVIEW,
        )
        msg_other = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        assert rule.matches(msg_matching) is True
        assert rule.matches(msg_other) is False

    def test_matches_receiver_pattern(self) -> None:
        """matches should check receiver pattern with regex."""
        rule = RouteRule(name="test", receiver_pattern=r"worker-\d+")
        msg_matching = AgentMessage(
            sender="a",
            receiver="worker-42",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        msg_no_match = AgentMessage(
            sender="a",
            receiver="orchestrator",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        assert rule.matches(msg_matching) is True
        assert rule.matches(msg_no_match) is False

    def test_matches_sender_pattern(self) -> None:
        """matches should check sender pattern with regex."""
        rule = RouteRule(name="test", sender_pattern=r"orchestrator.*")
        msg_matching = AgentMessage(
            sender="orchestrator-1",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        msg_no_match = AgentMessage(
            sender="worker-1",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        assert rule.matches(msg_matching) is True
        assert rule.matches(msg_no_match) is False

    def test_matches_all_fields(self) -> None:
        """matches should require ALL non-None fields to match."""
        rule = RouteRule(
            name="test",
            performative=Performative.REQUEST,
            intent=Intent.CODE_REVIEW,
            receiver_pattern=r"critic-\d+",
        )
        msg_matching = AgentMessage(
            sender="orchestrator",
            receiver="critic-1",
            performative=Performative.REQUEST,
            intent=Intent.CODE_REVIEW,
        )
        # Wrong performative
        msg_bad_perf = AgentMessage(
            sender="orchestrator",
            receiver="critic-1",
            performative=Performative.INFORM,
            intent=Intent.CODE_REVIEW,
        )
        # Wrong intent
        msg_bad_intent = AgentMessage(
            sender="orchestrator",
            receiver="critic-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        assert rule.matches(msg_matching) is True
        assert rule.matches(msg_bad_perf) is False
        assert rule.matches(msg_bad_intent) is False

    def test_matches_with_none_fields(self) -> None:
        """matches should return True when only some fields are set."""
        rule = RouteRule(name="test", intent=Intent.CODE_REVIEW)
        msg = AgentMessage(
            sender="any",
            receiver="any",
            performative=Performative.INFORM,  # Not checked
            intent=Intent.CODE_REVIEW,
        )
        assert rule.matches(msg) is True

    @pytest.mark.asyncio
    async def test_matches_async_with_predicate(self) -> None:
        """matches_async should evaluate async predicate."""
        rule = RouteRule(
            name="test",
            intent=Intent.EXECUTE_TASK,
            predicate=lambda msg: len(msg.payload) > 0,
        )
        msg_with_payload = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
            payload={"key": "value"},
        )
        msg_empty_payload = AgentMessage(
            sender="a",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        assert rule.matches(msg_with_payload) is True
        assert await rule.matches_async(msg_with_payload) is True
        assert await rule.matches_async(msg_empty_payload) is False

    @pytest.mark.asyncio
    async def test_matches_async_with_async_predicate(self) -> None:
        """matches_async should handle coroutine predicates."""

        async def async_predicate(msg: AgentMessage) -> bool:
            return msg.sender == "allowed"

        rule = RouteRule(name="test", predicate=async_predicate)
        msg_allowed = AgentMessage(
            sender="allowed",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        msg_denied = AgentMessage(
            sender="denied",
            receiver="b",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        assert await rule.matches_async(msg_allowed) is True
        assert await rule.matches_async(msg_denied) is False


class TestMessageRouter:
    """Tests for MessageRouter."""

    @pytest.fixture
    def router(self) -> MessageRouter:
        """Create a fresh router for each test."""
        return MessageRouter(hop_limit=10)

    @pytest.mark.asyncio
    async def test_register_and_unregister_agent(self, router: MessageRouter) -> None:
        """register_agent and unregister_agent should work."""
        caps = [
            AgentCapability(name="code_gen", intents=[Intent.CODE_GENERATION]),
            AgentCapability(name="code_review", intents=[Intent.CODE_REVIEW]),
        ]
        await router.register_agent("worker-1", caps)

        agents = router.get_registered_agents()
        assert "worker-1" in agents
        assert len(agents["worker-1"]) == 2

        await router.unregister_agent("worker-1")
        agents = router.get_registered_agents()
        assert "worker-1" not in agents

    @pytest.mark.asyncio
    async def test_intent_index(self, router: MessageRouter) -> None:
        """Agent should be indexed by intent."""
        caps = [AgentCapability(name="review", intents=[Intent.CODE_REVIEW, Intent.VALIDATE])]
        await router.register_agent("critic-1", caps)

        critics_for_review = router.get_agents_for_intent(Intent.CODE_REVIEW)
        assert "critic-1" in critics_for_review

        critics_for_validate = router.get_agents_for_intent(Intent.VALIDATE)
        assert "critic-1" in critics_for_validate

        critics_for_code_gen = router.get_agents_for_intent(Intent.CODE_GENERATION)
        assert "critic-1" not in critics_for_code_gen

    @pytest.mark.asyncio
    async def test_route_direct(self, router: MessageRouter) -> None:
        """Route to specific receiver should be DIRECT."""
        msg = AgentMessage(
            sender="orchestrator",
            receiver="worker-1",
            performative=Performative.REQUEST,
            intent=Intent.EXECUTE_TASK,
        )
        decision = await router.route(msg)
        assert decision.strategy == RoutingStrategy.DIRECT
        assert decision.receivers == ("worker-1",)

    @pytest.mark.asyncio
    async def test_route_broadcast(self, router: MessageRouter) -> None:
        """Broadcast message should be BROADCAST when no rules or agents match."""
        msg = AgentMessage(
            sender="orchestrator",
            receiver="",
            performative=Performative.INFORM,
            intent=Intent.EXECUTE_TASK,
        )
        decision = await router.route(msg)
        assert decision.strategy == RoutingStrategy.BROADCAST
        assert decision.receivers == ()

    @pytest.mark.asyncio
    async def test_route_capability_match(self, router: MessageRouter) -> None:
        """Broadcast to intent should route to capable agents."""
        caps = [AgentCapability(name="code_review", intents=[Intent.CODE_REVIEW])]
        await router.register_agent("critic-1", caps)

        msg = AgentMessage(
            sender="orchestrator",
            receiver="",
            performative=Performative.REQUEST,
            intent=Intent.CODE_REVIEW,
        )
        decision = await router.route(msg)
        assert decision.strategy == RoutingStrategy.CAPABILITY_MATCH
        assert "critic-1" in decision.receivers

    @pytest.mark.asyncio
    async def test_route_rule_priority(self, router: MessageRouter) -> None:
        """Higher priority rules should be evaluated first."""
        rule_low = RouteRule(
            name="low_priority",
            priority=1,
            intent=Intent.CODE_REVIEW,
            strategy=RoutingStrategy.BROADCAST,
        )
        rule_high = RouteRule(
            name="high_priority",
            priority=10,
            intent=Intent.CODE_REVIEW,
            strategy=RoutingStrategy.CAPABILITY_MATCH,
            target_agents=("specific-critic",),
        )
        router.add_rule(rule_low)
        router.add_rule(rule_high)

        # Register the specific critic so rule matches
        caps = [AgentCapability(name="specific", intents=[Intent.CODE_REVIEW])]
        await router.register_agent("specific-critic", caps)

        msg = AgentMessage(
            sender="orchestrator",
            receiver="",
            performative=Performative.REQUEST,
            intent=Intent.CODE_REVIEW,
        )
        decision = await router.route(msg)
        # High priority rule should match first
        assert decision.strategy == RoutingStrategy.CAPABILITY_MATCH

    def test_add_and_remove_rule(self, router: MessageRouter) -> None:
        """add_rule and remove_rule should work."""
        rule = RouteRule(name="test_rule", intent=Intent.EXECUTE_TASK)
        router.add_rule(rule)

        stats = router.get_stats()
        assert stats["rules_count"] == 1

        removed = router.remove_rule("test_rule")
        assert removed is True

        stats = router.get_stats()
        assert stats["rules_count"] == 0

        # Removing non-existent should return False
        assert router.remove_rule("nonexistent") is False

    def test_clear_rules(self, router: MessageRouter) -> None:
        """clear_rules should remove all rules."""
        router.add_rule(RouteRule(name="a", intent=Intent.EXECUTE_TASK))
        router.add_rule(RouteRule(name="b", intent=Intent.CODE_REVIEW))
        router.clear_rules()

        stats = router.get_stats()
        assert stats["rules_count"] == 0

    def test_get_stats(self, router: MessageRouter) -> None:
        """get_stats should return router statistics."""
        stats = router.get_stats()
        assert "registered_agents" in stats
        assert "intent_index_size" in stats
        assert "rules_count" in stats
        assert "hop_limit" in stats


class TestCreateBroadcastRule:
    """Tests for create_broadcast_rule helper."""

    def test_create_broadcast_rule(self) -> None:
        """create_broadcast_rule should create correct rule."""
        rule = create_broadcast_rule(Intent.CODE_REVIEW, name="review_broadcast", priority=5)
        assert rule.name == "review_broadcast"
        assert rule.intent == Intent.CODE_REVIEW
        assert rule.strategy == RoutingStrategy.BROADCAST
        assert rule.priority == 5
        assert rule.target_agents == ()  # Empty for broadcast

    def test_create_broadcast_rule_auto_name(self) -> None:
        """create_broadcast_rule should auto-generate name if not provided."""
        rule = create_broadcast_rule(Intent.EXECUTE_TASK)
        assert rule.name == "broadcast_execute_task"


class TestCreateCriticRule:
    """Tests for create_critic_rule helper."""

    def test_create_critic_rule(self) -> None:
        """create_critic_rule should create correct rule."""
        rule = create_critic_rule(
            intent=Intent.CODE_REVIEW,
            critic_agents=["critic-1", "critic-2"],
            name="code_review_critics",
            priority=15,
        )
        assert rule.name == "code_review_critics"
        assert rule.intent == Intent.CODE_REVIEW
        assert rule.strategy == RoutingStrategy.CONSENSUS
        assert rule.target_agents == ("critic-1", "critic-2")
        assert rule.priority == 15

    def test_create_critic_rule_auto_name(self) -> None:
        """create_critic_rule should auto-generate name if not provided."""
        rule = create_critic_rule(Intent.VALIDATE, critic_agents=["c1"])
        assert rule.name == "critic_validate"
