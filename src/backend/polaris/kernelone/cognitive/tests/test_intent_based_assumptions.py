"""Test intent-based assumptions for CriticalThinkingEngine P1-3 enhancement.

# -*- coding: utf-8 -*-
UTF-8 encoding verified: All text uses UTF-8

Test cases for P1-3 CriticalThinkingEngine _extract_assumptions() enhancement:
- Verify modify_file type conclusion produces relevant assumptions
- Verify assumption source is marked as "intent_type"
"""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.perception.models import IntentChain, IntentNode
from polaris.kernelone.cognitive.reasoning.engine import CriticalThinkingEngine


class TestIntentBasedAssumptions:
    """Test that intent-based assumptions are correctly extracted."""

    @pytest.fixture
    def engine(self) -> CriticalThinkingEngine:
        """Create a CriticalThinkingEngine instance."""
        return CriticalThinkingEngine()

    @pytest.fixture
    def modify_file_intent_chain(self) -> IntentChain:
        """Create an IntentChain with modify_file intent_type."""
        surface_intent = IntentNode(
            node_id="intent_1",
            intent_type="modify_file",
            content="Modify the config file to add new settings",
            confidence=0.9,
            source_event_id="event_1",
        )
        return IntentChain(
            chain_id="chain_1",
            surface_intent=surface_intent,
            deep_intent=None,
            uncertainty=0.1,
            confidence_level="high",
            unstated_needs=(),
        )

    @pytest.fixture
    def create_file_intent_chain(self) -> IntentChain:
        """Create an IntentChain with create_file intent_type."""
        surface_intent = IntentNode(
            node_id="intent_2",
            intent_type="create_file",
            content="Create a new module for handling requests",
            confidence=0.8,
            source_event_id="event_2",
        )
        return IntentChain(
            chain_id="chain_2",
            surface_intent=surface_intent,
            deep_intent=None,
            uncertainty=0.2,
            confidence_level="medium",
            unstated_needs=(),
        )

    @pytest.fixture
    def delete_file_intent_chain(self) -> IntentChain:
        """Create an IntentChain with delete_file intent_type."""
        surface_intent = IntentNode(
            node_id="intent_3",
            intent_type="delete_file",
            content="Delete the deprecated utility module",
            confidence=0.7,
            source_event_id="event_3",
        )
        return IntentChain(
            chain_id="chain_3",
            surface_intent=surface_intent,
            deep_intent=None,
            uncertainty=0.3,
            confidence_level="medium",
            unstated_needs=(),
        )

    @pytest.fixture
    def read_file_intent_chain(self) -> IntentChain:
        """Create an IntentChain with read_file intent_type."""
        surface_intent = IntentNode(
            node_id="intent_4",
            intent_type="read_file",
            content="Read the current configuration",
            confidence=0.95,
            source_event_id="event_4",
        )
        return IntentChain(
            chain_id="chain_4",
            surface_intent=surface_intent,
            deep_intent=None,
            uncertainty=0.05,
            confidence_level="high",
            unstated_needs=(),
        )

    @pytest.fixture
    def execute_tool_intent_chain(self) -> IntentChain:
        """Create an IntentChain with execute_tool intent_type."""
        surface_intent = IntentNode(
            node_id="intent_5",
            intent_type="execute_tool",
            content="Execute the build command",
            confidence=0.85,
            source_event_id="event_5",
        )
        return IntentChain(
            chain_id="chain_5",
            surface_intent=surface_intent,
            deep_intent=None,
            uncertainty=0.15,
            confidence_level="high",
            unstated_needs=(),
        )

    @pytest.mark.asyncio
    async def test_modify_file_produces_intent_based_assumption(
        self, engine: CriticalThinkingEngine, modify_file_intent_chain: IntentChain
    ) -> None:
        """Test that modify_file intent_type produces a relevant assumption."""
        conclusion = "We should modify the config file"
        context = "The config needs new settings"

        result = await engine.analyze(conclusion, modify_file_intent_chain, context)

        # Find the intent-based assumption
        intent_assumptions = [a for a in result.six_questions.assumptions if a.source == "intent_type"]
        assert len(intent_assumptions) == 1

        assumption = intent_assumptions[0]
        assert assumption.text == "修改操作可能引入语法错误或逻辑错误"
        assert assumption.confidence == 0.7
        assert assumption.id == "intent_assumpt_modify"
        assert "语法错误" in assumption.conditions_for_failure
        assert "逻辑错误" in assumption.conditions_for_failure

    @pytest.mark.asyncio
    async def test_create_file_produces_intent_based_assumption(
        self, engine: CriticalThinkingEngine, create_file_intent_chain: IntentChain
    ) -> None:
        """Test that create_file intent_type produces a relevant assumption."""
        conclusion = "We should create a new module"
        context = "Need to handle new request type"

        result = await engine.analyze(conclusion, create_file_intent_chain, context)

        intent_assumptions = [a for a in result.six_questions.assumptions if a.source == "intent_type"]
        assert len(intent_assumptions) == 1

        assumption = intent_assumptions[0]
        assert assumption.text == "新文件可能与现有架构规范不一致"
        assert assumption.confidence == 0.5
        assert assumption.id == "intent_assumpt_create"

    @pytest.mark.asyncio
    async def test_delete_file_produces_intent_based_assumption(
        self, engine: CriticalThinkingEngine, delete_file_intent_chain: IntentChain
    ) -> None:
        """Test that delete_file intent_type produces a relevant assumption with high confidence."""
        conclusion = "We should delete the old module"
        context = "Module is deprecated"

        result = await engine.analyze(conclusion, delete_file_intent_chain, context)

        intent_assumptions = [a for a in result.six_questions.assumptions if a.source == "intent_type"]
        assert len(intent_assumptions) == 1

        assumption = intent_assumptions[0]
        assert assumption.text == "删除操作可能影响其他模块依赖"
        assert assumption.confidence == 0.8  # High confidence for delete

    @pytest.mark.asyncio
    async def test_read_file_produces_low_confidence_assumption(
        self, engine: CriticalThinkingEngine, read_file_intent_chain: IntentChain
    ) -> None:
        """Test that read_file intent_type produces low confidence assumption."""
        conclusion = "We should read the config file"
        context = "Need to check current settings"

        result = await engine.analyze(conclusion, read_file_intent_chain, context)

        intent_assumptions = [a for a in result.six_questions.assumptions if a.source == "intent_type"]
        assert len(intent_assumptions) == 1

        assumption = intent_assumptions[0]
        assert assumption.text == "读取的内容可能已被外部修改"
        assert assumption.confidence == 0.3  # Low confidence for read

    @pytest.mark.asyncio
    async def test_execute_tool_produces_intent_based_assumption(
        self, engine: CriticalThinkingEngine, execute_tool_intent_chain: IntentChain
    ) -> None:
        """Test that execute_tool intent_type produces relevant assumption."""
        conclusion = "We should execute the build command"
        context = "Need to build the project"

        result = await engine.analyze(conclusion, execute_tool_intent_chain, context)

        intent_assumptions = [a for a in result.six_questions.assumptions if a.source == "intent_type"]
        assert len(intent_assumptions) == 1

        assumption = intent_assumptions[0]
        assert assumption.text == "工具执行可能产生非预期的副作用"
        assert assumption.confidence == 0.6

    @pytest.mark.asyncio
    async def test_unknown_intent_type_no_intent_assumption(self, engine: CriticalThinkingEngine) -> None:
        """Test that unknown intent_type does not produce intent-based assumption."""
        # Create intent chain with unknown intent_type
        surface_intent = IntentNode(
            node_id="intent_unknown",
            intent_type="unknown_action",
            content="Perform unknown action",
            confidence=0.5,
            source_event_id="event_unknown",
        )
        intent_chain = IntentChain(
            chain_id="chain_unknown",
            surface_intent=surface_intent,
            deep_intent=None,
            uncertainty=0.5,
            confidence_level="medium",
            unstated_needs=(),
        )

        conclusion = "We should do something"
        context = "Something needs to be done"

        result = await engine.analyze(conclusion, intent_chain, context)

        # No intent-based assumptions should be present
        intent_assumptions = [a for a in result.six_questions.assumptions if a.source == "intent_type"]
        assert len(intent_assumptions) == 0

    @pytest.mark.asyncio
    async def test_keyword_based_assumptions_have_keyword_source(self, engine: CriticalThinkingEngine) -> None:
        """Test that keyword-based assumptions have source='keyword'."""
        conclusion = "This should work because it will be correct"
        context = ""

        # Pass None intent_chain to only get keyword-based assumptions
        result = await engine.analyze(conclusion, None, context)

        # All assumptions should have source='keyword' when no intent_type match
        keyword_assumptions = [a for a in result.six_questions.assumptions if a.source == "keyword"]
        # Should have assumptions from "should" and "because" and "will"
        assert len(keyword_assumptions) >= 3

    @pytest.mark.asyncio
    async def test_intent_chain_with_no_surface_intent(self, engine: CriticalThinkingEngine) -> None:
        """Test that IntentChain with None surface_intent doesn't produce intent assumptions."""
        intent_chain = IntentChain(
            chain_id="chain_empty",
            surface_intent=None,
            deep_intent=None,
            uncertainty=1.0,
            confidence_level="unknown",
            unstated_needs=(),
        )

        conclusion = "This should work"
        context = ""

        result = await engine.analyze(conclusion, intent_chain, context)

        # No intent-based assumptions should be present
        intent_assumptions = [a for a in result.six_questions.assumptions if a.source == "intent_type"]
        assert len(intent_assumptions) == 0

    @pytest.mark.asyncio
    async def test_intent_assumption_preserves_is_hidden_flag(
        self, engine: CriticalThinkingEngine, modify_file_intent_chain: IntentChain
    ) -> None:
        """Test that intent-based assumptions have is_hidden=True."""
        conclusion = "We should modify the config"
        context = ""

        result = await engine.analyze(conclusion, modify_file_intent_chain, context)

        intent_assumptions = [a for a in result.six_questions.assumptions if a.source == "intent_type"]
        assert len(intent_assumptions) == 1
        assert intent_assumptions[0].is_hidden is True
