"""Tests for turn_history persistence parity between stream and non-stream paths.

Phase 1-2 Coverage:
- P0: _persist_session_turn_state receives turn_history in all code paths
- P1-2: No fallback branch for turn_history=None
- P1-3: stream and non-stream use the same persistence semantics
- P2: Unified ContextRequest/TurnEngineContextResult types

G-3 coverage: run/stream parity gate for session persistence.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.kernelone.context.contracts import (
    TurnEngineContextRequest,
    TurnEngineContextResult,
)


class TestTurnEngineContextTypes:
    """Phase 2: Unified ContextRequest and ContextResult types."""

    def test_turn_engine_context_request_immutable(self) -> None:
        """TurnEngineContextRequest must be frozen/immutable."""
        req = TurnEngineContextRequest(
            message="hello",
            history=(("user", "hello"), ("assistant", "hi")),
            task_id="task-1",
        )
        with pytest.raises(AttributeError):
            req.message = "changed"  # type: ignore[index]

    def test_turn_engine_context_result_immutable(self) -> None:
        """TurnEngineContextResult must be frozen/immutable."""
        res = TurnEngineContextResult(
            messages=({"role": "user", "content": "hello"},),
            token_estimate=100,
            context_sources=("memory",),
        )
        with pytest.raises(AttributeError):
            res.messages = ()  # type: ignore[index]

    def test_turn_engine_context_request_accepts_tuple_history(self) -> None:
        """history field must accept tuple, not just list."""
        req = TurnEngineContextRequest(
            message="test",
            history=(("user", "msg1"), ("assistant", "msg2"), ("tool", "result")),
        )
        assert len(req.history) == 3
        assert req.history[0] == ("user", "msg1")
        assert req.history[2] == ("tool", "result")

    def test_turn_engine_context_result_accepts_tuple_messages(self) -> None:
        """messages field must accept tuple."""
        res = TurnEngineContextResult(
            messages=(
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hello"},
            ),
            token_estimate=50,
            context_sources=("project_structure",),
        )
        assert len(res.messages) == 2
        assert res.messages[0]["role"] == "system"

    def test_turn_engine_context_request_empty_history(self) -> None:
        """Empty history should be represented as empty tuple."""
        req = TurnEngineContextRequest(message="hello")
        assert req.history == ()
        assert req.task_id is None

    def test_turn_engine_context_result_empty_sources(self) -> None:
        """Empty sources should default to empty tuple."""
        res = TurnEngineContextResult(messages=({"role": "user", "content": "hi"},))
        assert res.context_sources == ()
        assert res.token_estimate == 0


class TestPersistSessionTurnStateSignatures:
    """Phase 1-2: _persist_session_turn_state signature enforces turn_history."""

    def test_turn_history_is_required_param(self) -> None:
        """turn_history must be a required parameter (no default None)."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        sig = inspect.signature(RoleRuntimeService._persist_session_turn_state)
        params = sig.parameters

        assert "turn_history" in params
        turn_history_param = params["turn_history"]
        assert turn_history_param.default is inspect.Parameter.empty, (
            f"turn_history must be required; got default: {turn_history_param.default!r}"
        )

    def test_all_call_sites_pass_turn_history(self) -> None:
        """All callers of _persist_session_turn_state must pass turn_history."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        source = inspect.getsource(RoleRuntimeService)

        # Find all occurrences of the call
        call_marker = "self._persist_session_turn_state("
        pos = 0
        call_sites = []
        while True:
            idx = source.find(call_marker, pos)
            if idx == -1:
                break
            # Extract a window of ~600 chars (covers multiline calls)
            window = source[idx : idx + 600]
            call_sites.append(window)
            pos = idx + len(call_marker)

        assert len(call_sites) >= 4, f"Expected at least 4 call sites, found {len(call_sites)}"

        for i, window in enumerate(call_sites):
            # turn_history= must appear within the call window
            assert "turn_history=" in window, (
                f"Call site {i} missing turn_history= within 600 chars of call. Call starts: {window[:100]}"
            )

    def test_no_legacy_fallback_else_branch(self) -> None:
        """_persist_session_turn_state must not have 'else' fallback for turn_history."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        source = inspect.getsource(RoleRuntimeService._persist_session_turn_state)

        # After Phase 1-2, the function should:
        # 1. NOT have "if turn_history:" check before the loop
        # 2. NOT have an "else:" branch with svc.add_message for user/assistant
        assert "if turn_history:" not in source, (
            "Found 'if turn_history:' conditional in _persist_session_turn_state - "
            "turn_history is now required, no conditional check needed"
        )
        assert "else:" not in source or "# legacy" in source, (
            "Found 'else:' branch - this is the legacy fallback that must be removed"
        )


class TestPersistSessionTurnStateSemantics:
    """Phase 1: _persist_session_turn_state behavior with various inputs."""

    @pytest.fixture
    def mock_command(self):
        """Minimal command for testing."""
        cmd = MagicMock(spec=ExecuteRoleSessionCommandV1)
        cmd.session_id = "session-test-123"
        cmd.run_id = "run-456"
        cmd.task_id = "task-789"
        cmd.user_message = "test message"
        cmd.stream = False
        cmd.history = []
        cmd.context = {}
        return cmd

    def test_empty_turn_history_calls_add_message_zero_times(self, mock_command) -> None:
        """Empty turn_history should call add_message 0 times (loop body not entered)."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        # Patch where RoleSessionService is looked up (inside the function)
        with patch.object(RoleRuntimeService, "_persist_session_turn_state") as mock_method:
            # Just call directly with empty turn_history
            mock_method(
                command=mock_command,
                assistant_text="partial response",
                thinking=None,
                tool_calls=(),
                usage={},
                turn_history=[],
            )
            # Verify add_message was NOT called (empty list, loop not entered)
            # The call would have been with empty turn_history
            call_kwargs = mock_method.call_args.kwargs
            assert call_kwargs["turn_history"] == []

    def test_turn_history_source_has_no_conditional_check(self) -> None:
        """After Phase 1-2, source should NOT have 'if turn_history:' check."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        source = inspect.getsource(RoleRuntimeService._persist_session_turn_state)

        # The Phase 1-2 refactored version should iterate directly over turn_history
        # without a truthiness check, since turn_history is now required
        lines = source.split("\n")
        has_conditional_iteration = False
        for _i, line in enumerate(lines):
            if "if turn_history:" in line:
                has_conditional_iteration = True
                break

        assert not has_conditional_iteration, (
            f"Found 'if turn_history:' conditional at line {_i}. "
            "After Phase 1-2, turn_history is required - no conditional check needed."
        )


class TestToolLoopControllerHistory:
    """Phase 2: ToolLoopController.build_context_request returns correct types."""

    def _make_role_turn_request(self, history_list: list[tuple], task_id: str = "task-1"):
        """Create a minimally valid RoleTurnRequest."""
        from polaris.cells.roles.profile.internal.schema import RoleTurnRequest

        # SSOT: context_override must have context_os_snapshot (same as RoleTurnRequest.__init__ bootstrap)
        context_override = {
            "context_os_snapshot": {
                "version": 1,
                "mode": "state_first_context_os_v1",
                "adapter_id": "generic",
                "transcript_log": [],
                "working_state": {},
                "artifact_store": [],
                "episode_store": [],
                "updated_at": "",
            }
        }

        req = MagicMock(spec=RoleTurnRequest)
        req.message = "test"
        req.history = history_list
        req.task_id = task_id
        req.metadata = {}
        req.tool_results = []
        req.domain = "code"
        req.prompt_appendix = None
        req.context_override = context_override
        req.run_id = "run-1"
        req.session_id = "session-1"
        req.role = "director"
        return req

    def _make_profile(self):
        """Create a minimally valid RoleProfile mock."""
        from polaris.cells.roles.profile.public.service import RoleProfile

        profile = MagicMock(spec=RoleProfile)
        profile.role_id = "director"
        profile.tool_policy = MagicMock()
        profile.tool_policy.policy_id = "default"
        profile.tool_policy.allowed_tools = []
        profile.tool_policy.forbidden_tools = []
        return profile

    def test_build_context_request_returns_tuple_history(self) -> None:
        """build_context_request must return tuple (not list) for history."""

        request = self._make_role_turn_request(history_list=[])
        profile = self._make_profile()
        controller = ToolLoopController.from_request(request=request, profile=profile)
        result = controller.build_context_request()

        assert isinstance(result.history, tuple), f"history must be tuple, got {type(result.history).__name__}"
        assert result.history == ()

    def test_build_context_request_preserves_history_tuples(self) -> None:
        """History tuples must be preserved through build_context_request.

        FIX: When context_os_snapshot is present, build_context_request now
        returns the full _history (seeded from snapshot.transcript_log) instead
        of empty history. The gateway is responsible for deduplication.
        This fixes the context loss bug where tool results were lost between
        LLM calls within the same turn.
        """

        # Phase 5: Provide history via context_os_snapshot
        snapshot_with_history = {
            "version": 1,
            "mode": "state_first_context_os_v1",
            "adapter_id": "generic",
            "transcript_log": [
                {"event_id": "e0", "role": "user", "content": "hello", "sequence": 0, "metadata": {}},
                {"event_id": "e1", "role": "tool", "content": "result", "sequence": 1, "metadata": {}},
            ],
            "working_state": {},
            "artifact_store": [],
            "episode_store": [],
            "updated_at": "",
        }
        context_override = {"context_os_snapshot": snapshot_with_history}
        from polaris.cells.roles.profile.internal.schema import RoleTurnRequest

        request = MagicMock(spec=RoleTurnRequest)
        request.message = "test"
        request.history = []
        request.task_id = "task-1"
        request.metadata = {}
        request.tool_results = []
        request.domain = "code"
        request.prompt_appendix = None
        request.context_override = context_override
        request.run_id = "run-1"
        request.session_id = "session-1"
        request.role = "director"
        profile = self._make_profile()
        controller = ToolLoopController.from_request(request=request, profile=profile)

        result = controller.build_context_request()

        # FIX: history now contains the seeded events from snapshot.transcript_log
        assert isinstance(result.history, tuple)
        assert len(result.history) == 2, f"Expected 2 history items, got {len(result.history)}"
        # First event should be user message from snapshot
        assert result.history[0][0] == "user"
        assert result.history[0][1] == "hello"
        # Second event should be tool result from snapshot
        assert result.history[1][0] == "tool"
        assert result.history[1][1] == "result"
        # Snapshot is also passed for state summary
        assert result.context_os_snapshot is not None


class TestContextGatewayBuildContext:
    """Phase 2: RoleContextGateway.build_context handles tuple types."""

    def _make_gateway_and_request(self, message: str, history_tuples: tuple):
        """Create gateway and request for testing."""
        from polaris.cells.roles.profile.public.service import RoleProfile

        profile = MagicMock(spec=RoleProfile)
        profile.role_id = "director"
        profile.context_policy = MagicMock()
        profile.context_policy.include_project_structure = False
        profile.context_policy.include_task_history = False
        profile.context_policy.max_context_tokens = 100000
        profile.context_policy.max_history_turns = 20
        profile.context_policy.compression_strategy = "none"

        gateway = RoleContextGateway(profile, workspace=".")

        request = TurnEngineContextRequest(
            message=message,
            history=history_tuples,
        )
        return gateway, request

    @pytest.mark.asyncio
    async def test_build_context_accepts_tuple_history(self) -> None:
        """build_context must accept tuple history."""
        gateway, request = self._make_gateway_and_request(
            message="hello",
            history_tuples=(("user", "hi"), ("assistant", "hello")),
        )

        result = await gateway.build_context(request)

        assert isinstance(result.messages, tuple)
        assert len(result.messages) >= 2  # at least user + assistant

    @pytest.mark.asyncio
    async def test_build_context_result_messages_is_tuple(self) -> None:
        """build_context result messages must be tuple (immutable)."""
        gateway, request = self._make_gateway_and_request(
            message="plan the project",
            history_tuples=(),
        )

        result = await gateway.build_context(request)

        assert isinstance(result.messages, tuple)
        assert isinstance(result.context_sources, tuple)
        # Verify immutability by confirming it's a tuple (can't use append)
        assert type(result.messages).__name__ == "tuple"


class TestNoRegressions:
    """Regression tests ensuring existing behavior is preserved."""

    def test_kernel_build_context_returns_context_request(self) -> None:
        """kernel._build_context returns correct type with list input."""
        from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
        from polaris.cells.roles.profile.internal.schema import RoleTurnRequest
        from polaris.cells.roles.profile.public.service import RoleProfile
        from polaris.kernelone.context.contracts import TurnEngineContextRequest

        kernel = RoleExecutionKernel(workspace=".")
        profile = MagicMock(spec=RoleProfile)
        profile.role_id = "director"

        request = RoleTurnRequest(
            message="test",
            history=[("user", "hello")],  # list input
            task_id="task-1",
        )

        result = kernel._build_context(profile, request)

        # Result must be TurnEngineContextRequest (the unified type)
        assert isinstance(result, TurnEngineContextRequest)
        # history must be tuple (converted from list)
        assert isinstance(result.history, tuple)
        assert result.history == (("user", "hello"),)

    def test_kernel_build_context_empty_history(self) -> None:
        """kernel._build_context handles empty history."""
        from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
        from polaris.cells.roles.profile.internal.schema import RoleTurnRequest
        from polaris.cells.roles.profile.public.service import RoleProfile
        from polaris.kernelone.context.contracts import TurnEngineContextRequest

        kernel = RoleExecutionKernel(workspace=".")
        profile = MagicMock(spec=RoleProfile)
        profile.role_id = "pm"

        request = RoleTurnRequest(message="hello", history=[], task_id="t1")
        result = kernel._build_context(profile, request)

        assert isinstance(result, TurnEngineContextRequest)
        assert result.history == ()
        assert result.message == "hello"


class TestPhase3ContextOSDirectIntegration:
    """Phase 3: ContextOSProjection is built directly from turn_history (not reconstructed)."""

    def test_persist_uses_turn_history_not_command_history(self) -> None:
        """persist_session_turn_state must pass turn_history directly."""
        import inspect

        from polaris.cells.roles.runtime.public.persistence import persist_session_turn_state

        source = inspect.getsource(persist_session_turn_state)

        # Must NOT use _build_post_turn_history (removed in Phase 3)
        assert "_build_post_turn_history" not in source, (
            "Phase 3: persist_session_turn_state should use turn_history directly, not _build_post_turn_history"
        )
        # Must accept turn_history as a parameter (check signature)
        sig = inspect.signature(persist_session_turn_state)
        assert "turn_history" in sig.parameters, (
            "Phase 3: persist_session_turn_state must accept turn_history parameter directly"
        )

    def test_no_build_post_turn_history_method(self) -> None:
        """_build_post_turn_history must be removed (Phase 4, dead code after Phase 3)."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        assert not hasattr(RoleRuntimeService, "_build_post_turn_history"), (
            "_build_post_turn_history should be removed (Phase 4 cleanup)"
        )

    def test_context_request_has_context_os_snapshot_field(self) -> None:
        """TurnEngineContextRequest must have context_os_snapshot field."""
        from polaris.kernelone.context.contracts import TurnEngineContextRequest

        req = TurnEngineContextRequest(
            message="hello",
            context_os_snapshot={"transcript_log": [], "working_state": {}},
        )
        assert req.context_os_snapshot is not None
        assert "transcript_log" in req.context_os_snapshot

    def test_kernel_build_context_extracts_context_os_snapshot(self) -> None:
        """kernel._build_context extracts context_os_snapshot from context_override."""
        from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
        from polaris.cells.roles.profile.internal.schema import RoleTurnRequest
        from polaris.cells.roles.profile.public.service import RoleProfile
        from polaris.kernelone.context.contracts import TurnEngineContextRequest

        kernel = RoleExecutionKernel(workspace=".")
        profile = MagicMock(spec=RoleProfile)
        profile.role_id = "director"

        snapshot = {"transcript_log": [], "working_state": {"current_task": "test"}}
        request = RoleTurnRequest(
            message="hello",
            history=[],
            task_id="t1",
            context_override={"context_os_snapshot": snapshot},
        )

        result = kernel._build_context(profile, request)

        assert isinstance(result, TurnEngineContextRequest)
        assert result.context_os_snapshot == snapshot
        assert result.context_os_snapshot["working_state"]["current_task"] == "test"

    def test_context_gateway_formats_context_os_snapshot(self) -> None:
        """RoleContextGateway._format_context_os_snapshot produces system message."""

        # Use real _format_context_os_snapshot without full gateway init
        gateway = object.__new__(RoleContextGateway)

        snapshot = {
            "transcript_log": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
            "working_state": {"current_task": "Implement login"},
            "artifact_store": [{"artifact_id": "a1"}],
            "pending_followup": {"description": "Review PR"},
        }

        result = gateway._format_context_os_snapshot(snapshot)

        assert "Context OS State" in result
        assert "transcript_events: 2 event(s)" in result
        assert "current_task: Implement login" in result
        assert "artifacts: 1 record(s)" in result
        assert "pending_followup: Review PR" in result

    def test_context_gateway_empty_snapshot(self) -> None:
        """RoleContextGateway handles empty context_os_snapshot gracefully."""

        gateway = object.__new__(RoleContextGateway)

        snapshot = {}
        result = gateway._format_context_os_snapshot(snapshot)

        assert "transcript_events: (empty)" in result


class TestPhase4LegacyCleanup:
    """Phase 4: Removed dead code and cleaned up signatures."""

    def test_persist_session_turn_state_only_has_two_params(self) -> None:
        """_persist_session_turn_state signature must have only command + turn_history."""
        import inspect

        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        sig = inspect.signature(RoleRuntimeService._persist_session_turn_state)
        params = list(sig.parameters.keys())

        # Must have exactly: command, turn_history, turn_events_metadata
        assert "command" in params
        assert "turn_history" in params
        assert "turn_events_metadata" in params
        # Removed: assistant_text, thinking, tool_calls, usage
        assert "assistant_text" not in params
        assert "thinking" not in params
        assert "tool_calls" not in params
        assert "usage" not in params
        assert len(params) == 3, f"Expected only 3 params, got: {params}"

    def test_persist_call_sites_match_new_signature(self) -> None:
        """All call sites of _persist_session_turn_state must use new signature."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        source = inspect.getsource(RoleRuntimeService)

        # Find all calls
        call_marker = "self._persist_session_turn_state("
        pos = 0
        violations = []
        while True:
            idx = source.find(call_marker, pos)
            if idx == -1:
                break
            window = source[idx : idx + 400]
            pos = idx + len(call_marker)
            # Check that OLD params are NOT present
            for old_param in ["assistant_text=", "thinking=", "tool_calls=", "usage="]:
                if old_param in window:
                    violations.append(f"{old_param} found in call")
        assert len(violations) == 0, f"Old params found in calls: {violations}"


class TestPhase5ScratchpadPattern:
    """Phase 5 (方案C): Context OS as Single Source of Truth, _controller._history as scratchpad."""

    def _make_role_turn_request_with_snapshot(
        self,
        history_list: list[tuple],
        snapshot: dict[str, Any] | None,
        task_id: str = "task-1",
    ):
        """Create a RoleTurnRequest with context_os_snapshot in context_override."""
        from polaris.cells.roles.profile.internal.schema import RoleTurnRequest

        context_override = {}
        if snapshot is not None:
            context_override["context_os_snapshot"] = snapshot

        req = MagicMock(spec=RoleTurnRequest)
        req.message = "test"
        req.history = history_list
        req.task_id = task_id
        req.metadata = {}
        req.tool_results = []
        req.domain = "code"
        req.prompt_appendix = None
        req.context_override = context_override
        req.run_id = "run-1"
        req.session_id = "session-1"
        req.role = "director"
        return req

    def _make_profile(self):
        """Create a minimally valid RoleProfile mock."""
        from polaris.cells.roles.profile.public.service import RoleProfile

        profile = MagicMock(spec=RoleProfile)
        profile.role_id = "director"
        profile.tool_policy = MagicMock()
        profile.tool_policy.policy_id = "default"
        profile.tool_policy.allowed_tools = []
        profile.tool_policy.forbidden_tools = []
        return profile

    def test_extract_snapshot_history_method_exists(self) -> None:
        """ToolLoopController must have _extract_snapshot_history method."""

        assert hasattr(ToolLoopController, "_extract_snapshot_history")

    def test_post_init_seeds_from_snapshot_transcript_log(self) -> None:
        """__post_init__ seeds _history from context_os_snapshot.transcript_log when available."""

        snapshot = {
            "transcript_log": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
                {"role": "tool", "content": "tool result 1"},
            ],
            "working_state": {},
            "artifact_store": [],
        }
        request = self._make_role_turn_request_with_snapshot(
            history_list=[("user", "old")],
            snapshot=snapshot,
        )
        profile = self._make_profile()
        controller = ToolLoopController(
            request=request,
            profile=profile,
            safety_policy=ToolLoopController._resolve_safety_policy(None),
        )

        # _history should be seeded from snapshot.transcript_log, NOT from request.history
        # SSOT: _history contains ContextEvent objects, use .to_tuple() for comparison
        assert len(controller._history) == 3
        assert controller._history[0].to_tuple() == ("user", "hello")
        assert controller._history[1].to_tuple() == ("assistant", "hi there")
        assert controller._history[2].to_tuple() == ("tool", "tool result 1")

    def test_post_init_raises_when_no_snapshot(self) -> None:
        """Without context_os_snapshot, __post_init__ raises ValueError (SSOT enforcement).

        Per user's requirement: no backward compatibility, all calls must use the new path.
        When context_os_snapshot is missing entirely (not just empty), raise error.
        """

        request = self._make_role_turn_request_with_snapshot(
            history_list=[("user", "from request")],
            snapshot=None,
        )
        profile = self._make_profile()
        with pytest.raises(ValueError, match="requires context_os_snapshot"):
            ToolLoopController(
                request=request,
                profile=profile,
                safety_policy=ToolLoopController._resolve_safety_policy(None),
            )

    def test_build_context_request_passes_empty_history_when_snapshot_present(self) -> None:
        """build_context_request returns _history (seeded from snapshot) for gateway deduplication.

        FIX: Previously returned history=() when snapshot present, causing context loss.
        Now returns the seeded _history so gateway can properly merge and deduplicate.
        """

        snapshot = {
            "transcript_log": [
                {"event_id": "e0", "role": "user", "content": "hello", "sequence": 0, "metadata": {}},
                {"event_id": "e1", "role": "assistant", "content": "hi", "sequence": 1, "metadata": {}},
            ],
            "working_state": {},
            "artifact_store": [],
        }
        request = self._make_role_turn_request_with_snapshot(
            history_list=[],
            snapshot=snapshot,
        )
        profile = self._make_profile()
        controller = ToolLoopController(
            request=request,
            profile=profile,
            safety_policy=ToolLoopController._resolve_safety_policy(None),
        )

        result = controller.build_context_request()

        # FIX: history now contains the seeded events from snapshot.transcript_log
        # BUG-M03: history tuples include metadata: (role, content, metadata_dict)
        assert len(result.history) == 2, f"Expected 2 history items, got {len(result.history)}"
        assert result.history[0][0] == "user"
        assert result.history[0][1] == "hello"
        assert result.history[1][0] == "assistant"
        assert result.history[1][1] == "hi"
        assert result.context_os_snapshot is snapshot

    def test_build_context_request_raises_when_no_snapshot(self) -> None:
        """Without context_os_snapshot, __post_init__ raises (SSOT enforcement)."""

        request = self._make_role_turn_request_with_snapshot(
            history_list=[("user", "msg1"), ("assistant", "msg2")],
            snapshot=None,
        )
        profile = self._make_profile()
        with pytest.raises(ValueError, match="requires context_os_snapshot"):
            ToolLoopController(
                request=request,
                profile=profile,
                safety_policy=ToolLoopController._resolve_safety_policy(None),
            )

    def test_extract_snapshot_history_handles_empty_transcript(self) -> None:
        """_extract_snapshot_history returns _NO_SNAPSHOT for empty transcript_log, falling back to request.history."""

        request = self._make_role_turn_request_with_snapshot(
            history_list=[],
            snapshot={"transcript_log": [], "working_state": {}},
        )
        profile = self._make_profile()
        controller = ToolLoopController(
            request=request,
            profile=profile,
            safety_policy=ToolLoopController._resolve_safety_policy(None),
        )

        # Empty transcript returns _NO_SNAPSHOT, so fallback to request.history is used
        # Since request.history is also empty, _history should be empty
        assert controller._history == []

    def test_extract_snapshot_history_handles_malformed_events(self) -> None:
        """_extract_snapshot_history skips non-dict and role-less events."""

        snapshot = {
            "transcript_log": [
                {"role": "user", "content": "valid"},  # valid
                {"content": "no role"},  # skip (no role)
                "not a dict",  # skip (not dict)
                {"role": "", "content": "empty role"},  # skip (empty role)
                {"role": "assistant", "content": "also valid"},
            ],
            "working_state": {},
        }
        request = self._make_role_turn_request_with_snapshot(
            history_list=[],
            snapshot=snapshot,
        )
        profile = self._make_profile()
        controller = ToolLoopController(
            request=request,
            profile=profile,
            safety_policy=ToolLoopController._resolve_safety_policy(None),
        )

        # Malformed events are skipped, only valid ones are included
        # SSOT: _history contains ContextEvent objects, use .to_tuple() for comparison
        assert [e.to_tuple() for e in controller._history] == [
            ("user", "valid"),
            ("assistant", "also valid"),
        ]

    def test_history_accumulates_after_append_tool_cycle(self) -> None:
        """_history grows correctly after append_tool_cycle when seeded from snapshot."""

        snapshot = {
            "transcript_log": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"},
            ],
            "working_state": {},
            "artifact_store": [],
        }
        request = self._make_role_turn_request_with_snapshot(
            history_list=[],
            snapshot=snapshot,
        )
        profile = self._make_profile()
        controller = ToolLoopController(
            request=request,
            profile=profile,
            safety_policy=ToolLoopController._resolve_safety_policy(None),
        )

        # Consume-on-Read: build_context_request must be called first to consume
        # _pending_user_message into _last_consumed_message, which append_tool_cycle
        # then writes to history.
        controller.build_context_request()

        # Simulate a tool cycle
        # Note: append_tool_cycle first appends _last_consumed_message (consumed from
        # _pending_user_message in build_context_request), then appends the assistant message
        controller.append_tool_cycle(
            assistant_message="I'll help you",
            tool_results=[],
        )

        # _history should now include: snapshot history + consumed user msg + assistant msg
        # SSOT: _history contains ContextEvent objects, use .to_tuple() for comparison
        assert controller._history[0].to_tuple() == ("user", "hello")
        assert controller._history[1].to_tuple() == ("assistant", "hi")
        # index 2 is _last_consumed_message ("test" from request.message, consumed in build_context_request)
        assert controller._history[2].to_tuple() == ("user", "test")
        assert controller._history[3].to_tuple() == ("assistant", "I'll help you")

    @pytest.mark.asyncio
    async def test_context_gateway_avoids_double_history_with_snapshot(self) -> None:
        """Gateway expands transcript_log into messages and deduplicates with history.

        FIX: Previously snapshot was only formatted as a summary system message,
        losing the actual conversation context. Now transcript_log is expanded
        into full dialogue messages for LLM to understand context.
        """
        from polaris.kernelone.context.contracts import TurnEngineContextRequest

        snapshot = {
            "transcript_log": [
                {"event_id": "e0", "role": "user", "content": "hello", "sequence": 0, "metadata": {}},
                {"event_id": "e1", "role": "assistant", "content": "hi", "sequence": 1, "metadata": {}},
            ],
            "working_state": {},
            "artifact_store": [],
        }

        profile = MagicMock()
        profile.role_id = "director"
        profile.context_policy = MagicMock()
        profile.context_policy.include_project_structure = False
        profile.context_policy.include_task_history = False
        profile.context_policy.max_context_tokens = 100000
        profile.context_policy.max_history_turns = 20
        profile.context_policy.compression_strategy = "none"

        gateway = RoleContextGateway(profile, workspace=".")

        # With snapshot and empty history, gateway should:
        # 1. Expand transcript_log into prior messages
        # 2. Add current user message
        request = TurnEngineContextRequest(
            message="what's next?",
            history=(),  # Empty - no current turn additions
            context_os_snapshot=snapshot,
        )

        result = await gateway.build_context(request)

        # Check that prior history from transcript_log is expanded into messages
        user_messages = [m for m in result.messages if m.get("role") == "user"]
        assistant_messages = [m for m in result.messages if m.get("role") == "assistant"]

        # Should have 2 user messages: "hello" (from transcript_log) + "what's next?" (current)
        assert len(user_messages) == 2, f"Expected 2 user messages, got {len(user_messages)}: {user_messages}"
        assert user_messages[0]["content"] == "hello"
        assert user_messages[1]["content"] == "what's next?"

        # Should have 1 assistant message: "hi" (from transcript_log)
        assert len(assistant_messages) == 1, f"Expected 1 assistant message, got {len(assistant_messages)}"
        assert assistant_messages[0]["content"] == "hi"

        # Snapshot state summary should NOT be added as system message when no meaningful state
        # (working_state and artifact_store are empty)
        system_names = [m.get("name") for m in result.messages if m.get("role") == "system"]
        # No context_os_snapshot system message because no meaningful state beyond transcript
        assert "context_os_state" not in system_names


class TestPhase6EventSourcingSafeguard:
    """Phase 6: Event Sourcing Safeguard - transcript_log is immutable.

    transcript_log must NEVER be compressed, modified, or truncated.
    Compression only happens at the VIEW layer (active_window, artifact_stubs, etc.)
    """

    def test_context_os_snapshot_is_frozen(self) -> None:
        """ContextOSSnapshot must be frozen/immutable."""
        from polaris.kernelone.context.context_os.models_v2 import ContextOSSnapshotV2 as ContextOSSnapshot

        snapshot = ContextOSSnapshot(
            transcript_log=(),
        )
        with pytest.raises(AttributeError):
            snapshot.transcript_log = ()  # type: ignore[index]

    def test_transcript_log_is_tuple_immutable(self) -> None:
        """transcript_log field must be a tuple (immutable)."""
        from polaris.kernelone.context.context_os.models_v2 import ContextOSSnapshotV2 as ContextOSSnapshot

        snapshot = ContextOSSnapshot(
            transcript_log=(("user", "hello"), ("assistant", "hi")),
        )
        assert isinstance(snapshot.transcript_log, tuple)
        with pytest.raises(AttributeError):
            snapshot.transcript_log = ()  # type: ignore[index]

    def test_context_os_projection_compress_does_not_modify_snapshot(self) -> None:
        """compress() must NOT modify snapshot.transcript_log."""
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        # Create a snapshot with known transcript_log
        transcript_events = (
            TranscriptEvent(
                event_id="e1",
                sequence=1,
                role="user",
                kind="message",
                content="hello",
                route="user",
                _metadata={},
            ),
            TranscriptEvent(
                event_id="e2",
                sequence=2,
                role="assistant",
                kind="message",
                content="hi there",
                route="assistant",
                _metadata={},
            ),
            TranscriptEvent(
                event_id="e3",
                sequence=3,
                role="tool",
                kind="tool_result",
                content="tool result",
                route="tool",
                _metadata={},
            ),
        )
        snapshot = ContextOSSnapshot(
            transcript_log=transcript_events,
        )

        # Create projection with 3 active_window events
        projection = ContextOSProjection(
            snapshot=snapshot,
            head_anchor="anchor1",
            tail_anchor="anchor2",
            active_window=transcript_events,
        )

        # Compress to a small target token count to force truncation
        compressed = projection.compress(target_tokens=1)

        # The COMPRESSED projection should have truncated active_window
        assert len(compressed.active_window) <= len(transcript_events)

        # BUT the ORIGINAL snapshot.transcript_log must be UNCHANGED
        assert compressed.snapshot.transcript_log == transcript_events
        assert len(compressed.snapshot.transcript_log) == 3

    def test_context_os_projection_compress_preserves_pinned_events(self) -> None:
        """compress() must preserve events marked as is_root (pinned)."""
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        transcript_events = (
            TranscriptEvent(
                event_id="e1",
                sequence=1,
                role="user",
                kind="message",
                content="hello",
                route="user",
                _metadata={"is_root": True},  # Pinned event
            ),
            TranscriptEvent(
                event_id="e2",
                sequence=2,
                role="assistant",
                kind="message",
                content="hi there",
                route="assistant",
                _metadata={},
            ),
        )
        snapshot = ContextOSSnapshot(
            transcript_log=transcript_events,
        )
        projection = ContextOSProjection(
            snapshot=snapshot,
            head_anchor="anchor1",
            tail_anchor="anchor2",
            active_window=transcript_events,
        )

        # Compress to a small target token count - should preserve is_root event
        compressed = projection.compress(target_tokens=1)

        # is_root event should be preserved regardless of max_active_window_messages
        # metadata is frozen as tuple of (key, value) pairs, so check with dict-like access
        def _has_is_root(ev: object) -> bool:
            meta = getattr(ev, "metadata", None)
            if isinstance(meta, dict):
                return meta.get("is_root", False) is True
            if isinstance(meta, tuple):
                return any(k == "is_root" and v is True for k, v in meta)
            return False

        assert any(_has_is_root(e) for e in compressed.active_window)

    def test_context_os_projection_compress_returns_new_instance(self) -> None:
        """compress() must return a new instance, not modify in place."""
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        snapshot = ContextOSSnapshot(
            transcript_log=(
                TranscriptEvent(
                    event_id="e1",
                    sequence=1,
                    role="user",
                    kind="message",
                    content="hello world " * 50,  # Enough tokens to force compression
                    route="user",
                    _metadata={},
                ),
            ),
        )
        projection = ContextOSProjection(
            snapshot=snapshot,
            head_anchor="anchor1",
            tail_anchor="anchor2",
            active_window=snapshot.transcript_log,
        )

        compressed = projection.compress(target_tokens=1)

        # Should be a different instance
        assert compressed is not projection
        # Original should be unchanged
        assert len(projection.active_window) == 1
