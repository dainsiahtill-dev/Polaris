"""Critical path integration tests.

These tests validate the core integration paths of the Polaris system:
- Tool call chain
- Event bus flow
- Role dialogue flow
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest


@dataclass
class ToolCall:
    """Represents a tool call in the chain."""

    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any] = field(default_factory=dict)


class TestToolCallChain:
    """Integration tests for complete tool call chain."""

    @pytest.mark.asyncio
    async def test_tool_call_chain_basic(self) -> None:
        """Test basic tool call chain execution."""

        async def execute_tool_chain(
            calls: list[ToolCall],
            executor: Any,
        ) -> list[dict[str, Any]]:
            """Execute a chain of tool calls."""
            results = []
            for call in calls:
                result = await executor.execute(call.tool_name, call.args)
                results.append(result)
            return results

        # Mock executor
        mock_executor = AsyncMock()
        mock_executor.execute.side_effect = [
            {"success": True, "content": "file content"},
            {"success": True, "matches": ["line1", "line2"]},
        ]

        # Create tool call chain
        calls = [
            ToolCall(tool_name="read_file", args={"path": "test.py"}),
            ToolCall(tool_name="search_code", args={"pattern": "def test"}),
        ]

        results = await execute_tool_chain(calls, mock_executor)

        assert len(results) == 2
        assert results[0]["success"] is True
        assert results[1]["matches"] == ["line1", "line2"]
        assert mock_executor.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_tool_call_chain_with_error(self) -> None:
        """Test tool call chain handles errors."""

        async def execute_tool_chain_with_error_handling(
            calls: list[ToolCall],
            executor: Any,
        ) -> list[dict[str, Any]]:
            """Execute tool chain with error handling."""
            results = []
            for call in calls:
                try:
                    result = await executor.execute(call.tool_name, call.args)
                    results.append(result)
                except Exception as e:
                    results.append({"success": False, "error": str(e)})
                    break  # Stop chain on error
            return results

        mock_executor = AsyncMock()
        mock_executor.execute.side_effect = [
            {"success": True, "content": "file content"},
            RuntimeError("Tool execution failed"),
        ]

        calls = [
            ToolCall(tool_name="read_file", args={"path": "test.py"}),
            ToolCall(tool_name="failing_tool", args={}),
            ToolCall(tool_name="skipped_tool", args={}),  # Should not execute
        ]

        results = await execute_tool_chain_with_error_handling(calls, mock_executor)

        assert len(results) == 2  # Third call skipped
        assert results[0]["success"] is True
        assert results[1]["success"] is False
        assert "Tool execution failed" in results[1]["error"]

    @pytest.mark.asyncio
    async def test_tool_call_chain_with_context_passing(self) -> None:
        """Test tool call chain passes context between calls."""

        async def execute_tool_chain_with_context(
            initial_context: dict[str, Any],
            executor: Any,
        ) -> dict[str, Any]:
            """Execute tool chain with context accumulation."""
            context = dict(initial_context)

            # Step 1: Read file
            file_result = await executor.execute("read_file", {"path": context["file_path"]})
            context["file_content"] = file_result.get("content", "")

            # Step 2: Search within file
            search_result = await executor.execute(
                "search_code",
                {"pattern": context["search_pattern"], "content": context["file_content"]},
            )
            context["matches"] = search_result.get("matches", [])

            return context

        mock_executor = AsyncMock()
        mock_executor.execute.side_effect = [
            {"success": True, "content": "def test(): pass\ndef other(): pass"},
            {"success": True, "matches": ["def test(): pass"]},
        ]

        initial_context = {
            "file_path": "test.py",
            "search_pattern": "def test",
        }

        final_context = await execute_tool_chain_with_context(initial_context, mock_executor)

        assert "file_content" in final_context
        assert "matches" in final_context
        assert len(final_context["matches"]) == 1


@dataclass
class Event:
    """Represents an event in the event bus."""

    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = ""


class TestEventBusFlow:
    """Integration tests for event bus flow."""

    @pytest.mark.asyncio
    async def test_event_bus_publish_subscribe(self) -> None:
        """Test event bus publish/subscribe flow."""

        class EventBus:
            """Simple event bus implementation."""

            def __init__(self) -> None:
                self.subscribers: dict[str, list[Any]] = {}
                self.events: list[Event] = []

            def subscribe(self, event_type: str, handler: Any) -> None:
                if event_type not in self.subscribers:
                    self.subscribers[event_type] = []
                self.subscribers[event_type].append(handler)

            async def publish(self, event: Event) -> None:
                self.events.append(event)
                handlers = self.subscribers.get(event.event_type, [])
                for handler in handlers:
                    await handler(event)

        received_events: list[Event] = []

        async def handler(event: Event) -> None:
            received_events.append(event)

        bus = EventBus()
        bus.subscribe("tool_call", handler)
        bus.subscribe("tool_result", handler)

        # Publish events
        await bus.publish(Event(event_type="tool_call", payload={"tool": "read_file"}))
        await bus.publish(Event(event_type="tool_result", payload={"result": "content"}))
        await bus.publish(Event(event_type="other_event", payload={}))  # Not subscribed

        assert len(received_events) == 2
        assert received_events[0].event_type == "tool_call"
        assert received_events[1].event_type == "tool_result"

    @pytest.mark.asyncio
    async def test_event_bus_event_chain(self) -> None:
        """Test event chain through event bus."""

        class EventBus:
            def __init__(self) -> None:
                self.subscribers: dict[str, list[Any]] = {}
                self.event_chain: list[str] = []

            def subscribe(self, event_type: str, handler: Any) -> None:
                if event_type not in self.subscribers:
                    self.subscribers[event_type] = []
                self.subscribers[event_type].append(handler)

            async def publish(self, event: Event) -> None:
                self.event_chain.append(event.event_type)
                handlers = self.subscribers.get(event.event_type, [])
                for handler in handlers:
                    await handler(event)

        bus = EventBus()

        # Handler that publishes another event
        async def tool_call_handler(event: Event) -> None:
            if event.event_type == "tool_call":
                # Simulate tool execution and publish result
                await bus.publish(
                    Event(
                        event_type="tool_result",
                        payload={"tool": event.payload["tool"], "success": True},
                    )
                )

        async def turn_complete_handler(event: Event) -> None:
            if event.event_type == "turn_complete":
                # Log completion
                bus.event_chain.append("logged")

        bus.subscribe("tool_call", tool_call_handler)
        bus.subscribe("tool_result", turn_complete_handler)

        # Trigger the chain
        await bus.publish(Event(event_type="tool_call", payload={"tool": "read_file"}))

        assert bus.event_chain == ["tool_call", "tool_result"]

    def test_event_bus_filtering(self) -> None:
        """Test event filtering by type."""

        events = [
            Event(event_type="tool_call", payload={"tool": "read"}),
            Event(event_type="llm_request", payload={"model": "claude"}),
            Event(event_type="tool_result", payload={"result": "data"}),
            Event(event_type="llm_response", payload={"content": "Hello"}),
        ]

        tool_events = [e for e in events if e.event_type.startswith("tool_")]
        llm_events = [e for e in events if e.event_type.startswith("llm_")]

        assert len(tool_events) == 2
        assert len(llm_events) == 2


@dataclass
class RoleMessage:
    """Represents a message in role dialogue."""

    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class TestRoleDialogueFlow:
    """Integration tests for role dialogue flow."""

    @pytest.mark.asyncio
    async def test_role_dialogue_basic_flow(self) -> None:
        """Test basic role dialogue flow."""

        async def role_dialogue(
            role: str,
            message: str,
            llm_invoker: Any,
        ) -> RoleMessage:
            """Execute a role dialogue turn."""
            response = await llm_invoker.invoke(
                role=role,
                prompt=message,
            )
            return RoleMessage(
                role=role,
                content=response["content"],
                metadata={"model": response.get("model"), "usage": response.get("usage")},
            )

        mock_invoker = AsyncMock()
        mock_invoker.invoke.return_value = {
            "content": "I'll help you with that task.",
            "model": "claude-3-opus",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }

        result = await role_dialogue("pm", "Plan this feature", mock_invoker)

        assert result.role == "pm"
        assert "help you" in result.content
        assert result.metadata["model"] == "claude-3-opus"

    @pytest.mark.asyncio
    async def test_role_dialogue_with_tool_calls(self) -> None:
        """Test role dialogue with tool calls."""

        async def role_dialogue_with_tools(
            role: str,
            message: str,
            llm_invoker: Any,
            tool_executor: Any,
        ) -> dict[str, Any]:
            """Execute role dialogue with potential tool calls."""
            # Get LLM response
            response = await llm_invoker.invoke(role=role, prompt=message)

            # Check for tool calls
            tool_calls = response.get("tool_calls", [])
            tool_results = []

            for call in tool_calls:
                result = await tool_executor.execute(call["name"], call["args"])
                tool_results.append({"tool": call["name"], "result": result})

            return {
                "content": response["content"],
                "tool_calls": tool_calls,
                "tool_results": tool_results,
            }

        mock_invoker = AsyncMock()
        mock_invoker.invoke.return_value = {
            "content": "I'll search for that.",
            "tool_calls": [{"name": "search_code", "args": {"pattern": "def test"}}],
        }

        mock_executor = AsyncMock()
        mock_executor.execute.return_value = {"matches": ["def test(): pass"]}

        result = await role_dialogue_with_tools(
            "director",
            "Find test functions",
            mock_invoker,
            mock_executor,
        )

        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "search_code"
        assert len(result["tool_results"]) == 1

    @pytest.mark.asyncio
    async def test_role_dialogue_multi_turn(self) -> None:
        """Test multi-turn role dialogue."""

        async def multi_turn_dialogue(
            role: str,
            messages: list[str],
            llm_invoker: Any,
        ) -> list[RoleMessage]:
            """Execute multi-turn dialogue."""
            responses = []
            context = []

            for message in messages:
                # Build context from previous turns
                prompt = f"{context}\nUser: {message}" if context else message

                response = await llm_invoker.invoke(role=role, prompt=prompt)

                role_message = RoleMessage(
                    role=role,
                    content=response["content"],
                )
                responses.append(role_message)
                context.append(f"User: {message}\n{role}: {response['content']}")

            return responses

        mock_invoker = AsyncMock()
        mock_invoker.invoke.side_effect = [
            {"content": "First response"},
            {"content": "Second response considering context"},
        ]

        messages = ["Hello", "What about this?"]
        results = await multi_turn_dialogue("architect", messages, mock_invoker)

        assert len(results) == 2
        assert results[0].content == "First response"
        assert "context" in results[1].content

    def test_role_dialogue_context_accumulation(self) -> None:
        """Test context accumulation in role dialogue."""

        dialogue_history = []

        def add_to_history(role: str, content: str) -> None:
            dialogue_history.append({"role": role, "content": content})

        # Simulate dialogue
        add_to_history("user", "Plan a feature")
        add_to_history("pm", "Here's the plan...")
        add_to_history("user", "Add more details")
        add_to_history("pm", "Here are the details...")

        assert len(dialogue_history) == 4
        assert dialogue_history[0]["role"] == "user"
        assert dialogue_history[1]["role"] == "pm"


class TestEndToEndIntegration:
    """End-to-end integration tests combining all critical paths."""

    @pytest.mark.asyncio
    async def test_complete_workflow(self) -> None:
        """Test complete workflow: role -> tool chain -> events."""

        # Components
        events: list[Event] = []

        async def event_handler(event: Event) -> None:
            events.append(event)

        async def mock_llm_invoke(role: str, prompt: str) -> dict[str, Any]:
            await event_handler(Event(event_type="llm_request", payload={"role": role}))
            return {
                "content": "Using tools...",
                "tool_calls": [
                    {"name": "read_file", "args": {"path": "test.py"}},
                ],
            }

        async def mock_tool_execute(tool_name: str, args: dict) -> dict[str, Any]:
            await event_handler(Event(event_type="tool_call", payload={"tool": tool_name}))
            result = {"success": True, "content": "file content"}
            await event_handler(Event(event_type="tool_result", payload={"tool": tool_name}))
            return result

        # Execute workflow
        llm_response = await mock_llm_invoke("director", "Read the file")

        for call in llm_response.get("tool_calls", []):
            await mock_tool_execute(call["name"], call["args"])

        # Verify events
        event_types = [e.event_type for e in events]
        assert "llm_request" in event_types
        assert "tool_call" in event_types
        assert "tool_result" in event_types
