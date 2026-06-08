"""Runtime-owned tests for turn history persistence parity."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1


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

        call_marker = "self._persist_session_turn_state("
        pos = 0
        call_sites = []
        while True:
            idx = source.find(call_marker, pos)
            if idx == -1:
                break
            window = source[idx : idx + 600]
            call_sites.append(window)
            pos = idx + len(call_marker)

        assert len(call_sites) >= 4, f"Expected at least 4 call sites, found {len(call_sites)}"

        for i, window in enumerate(call_sites):
            assert "turn_history=" in window, (
                f"Call site {i} missing turn_history= within 600 chars of call. Call starts: {window[:100]}"
            )

    def test_no_legacy_fallback_else_branch(self) -> None:
        """_persist_session_turn_state must not have 'else' fallback for turn_history."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        source = inspect.getsource(RoleRuntimeService._persist_session_turn_state)

        assert "if turn_history:" not in source, (
            "Found 'if turn_history:' conditional in _persist_session_turn_state - "
            "turn_history is now required, no conditional check needed"
        )
        assert "else:" not in source or "# legacy" in source, (
            "Found 'else:' branch - this is the legacy fallback that must be removed"
        )


class TestPersistSessionTurnStateSemantics:
    """Phase 1: _persist_session_turn_state behavior with various inputs."""

    def _mock_command(self) -> ExecuteRoleSessionCommandV1:
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

    def test_empty_turn_history_calls_add_message_zero_times(self) -> None:
        """Empty turn_history should call add_message 0 times."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        mock_command = self._mock_command()
        with patch.object(RoleRuntimeService, "_persist_session_turn_state", new=MagicMock()) as mock_method:
            mock_method(
                command=mock_command,
                assistant_text="partial response",
                thinking=None,
                tool_calls=(),
                usage={},
                turn_history=[],
            )
            call_kwargs = mock_method.call_args.kwargs
            assert call_kwargs["turn_history"] == []

    def test_turn_history_source_has_no_conditional_check(self) -> None:
        """After Phase 1-2, source should NOT have 'if turn_history:' check."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        source = inspect.getsource(RoleRuntimeService._persist_session_turn_state)
        lines = source.split("\n")
        has_conditional_iteration = False
        failure_line_index = 0
        for index, line in enumerate(lines):
            if "if turn_history:" in line:
                has_conditional_iteration = True
                failure_line_index = index
                break

        assert not has_conditional_iteration, (
            f"Found 'if turn_history:' conditional at line {failure_line_index}. "
            "After Phase 1-2, turn_history is required - no conditional check needed."
        )


class TestContextOSDirectPersistenceIntegration:
    """ContextOSProjection is built directly from turn_history, not reconstructed."""

    def test_persist_uses_turn_history_not_command_history(self) -> None:
        """persist_session_turn_state must pass turn_history directly."""
        from polaris.cells.roles.runtime.public.persistence import persist_session_turn_state

        source = inspect.getsource(persist_session_turn_state)

        assert "_build_post_turn_history" not in source, (
            "Phase 3: persist_session_turn_state should use turn_history directly, not _build_post_turn_history"
        )
        sig = inspect.signature(persist_session_turn_state)
        assert "turn_history" in sig.parameters, (
            "Phase 3: persist_session_turn_state must accept turn_history parameter directly"
        )

    def test_no_build_post_turn_history_method(self) -> None:
        """_build_post_turn_history must be removed."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        assert not hasattr(RoleRuntimeService, "_build_post_turn_history"), (
            "_build_post_turn_history should be removed (Phase 4 cleanup)"
        )


class TestPhase4LegacyCleanup:
    """Removed dead code and cleaned up signatures."""

    def test_persist_session_turn_state_only_has_two_params(self) -> None:
        """_persist_session_turn_state signature must have only command + turn_history."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        sig = inspect.signature(RoleRuntimeService._persist_session_turn_state)
        params = list(sig.parameters.keys())

        assert "command" in params
        assert "turn_history" in params
        assert "turn_events_metadata" in params
        assert "assistant_text" not in params
        assert "thinking" not in params
        assert "tool_calls" not in params
        assert "usage" not in params
        assert len(params) == 3, f"Expected only 3 params, got: {params}"

    def test_persist_call_sites_match_new_signature(self) -> None:
        """All call sites of _persist_session_turn_state must use new signature."""
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        source = inspect.getsource(RoleRuntimeService)

        call_marker = "self._persist_session_turn_state("
        pos = 0
        violations = []
        while True:
            idx = source.find(call_marker, pos)
            if idx == -1:
                break
            window = source[idx : idx + 400]
            pos = idx + len(call_marker)
            for old_param in ["assistant_text=", "thinking=", "tool_calls=", "usage="]:
                if old_param in window:
                    violations.append(f"{old_param} found in call")
        assert len(violations) == 0, f"Old params found in calls: {violations}"
