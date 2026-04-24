"""Unit tests for circuit breaker detection.

Tests the progressive circuit breaker system which provides:
1. Semantic-aware same-tool repetition detection (via ProgressiveCircuitBreaker)
2. Cross-tool loop pattern detection (ABAB, ABCABC) via legacy _recent_tool_names
3. State stagnation (read-only streak without write)
4. Progressive 3-level escalation (WARNING -> HARD -> BREAK)

The tests use the "quick_fix" scene profile which has lower thresholds:
- WARNING at count 2, HARD at count 3, BREAK at count 5
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.circuit_breaker import (
    SCENE_PROFILES,
    CircuitBreakerLevel,
    ProgressiveCircuitBreaker,
)
from polaris.cells.roles.kernel.internal.tool_loop_controller import (
    ToolLoopCircuitBreakerError,
    ToolLoopController,
)


def _make_controller(scene: str = "quick_fix") -> ToolLoopController:
    """Create a bare controller with given scene for configurable thresholds.

    Uses __new__ to bypass __init__ (which requires request/profile).
    Initializes all fields that _track_successful_call depends on.
    """
    controller = ToolLoopController.__new__(ToolLoopController)
    controller._recent_successful_calls = []
    controller._recent_successful_counts = {}
    controller._read_only_streak = 0
    controller._workspace_modified = False
    controller._recent_tool_names = []
    controller._history = []
    controller._circuit_breaker = ProgressiveCircuitBreaker(scene=scene)
    return controller


# ═══════════════════════════════════════════════════════════════════════════
# Same-Tool Repetition Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestSameToolCircuitBreaker:
    """Tests for same-tool repetition circuit breaker via ProgressiveCircuitBreaker."""

    def test_hard_level_injects_warning_but_does_not_raise(self):
        """HARD level injects warning but does NOT raise — LLM gets a chance to self-correct.

        The progressive breaker escalation for quick_fix scene:
        - Call 1: new info => OK
        - Call 2: same fingerprint => no-gain=1, same_sig=1 => OK
        - Call 3: no-gain=2, same_sig=2 => WARNING (injected, no raise)
        - Call 4: no-gain=3, same_sig=3, mult=1.5 => effective=4 => HARD (injected, no raise)
        - Call 5: effective=6 => BREAK => raises ToolLoopCircuitBreakerError
        """
        controller = _make_controller("quick_fix")
        result = {"success": True, "args": {"path": "/test/file.txt"}}

        # Calls 1-4: OK/WARNING/HARD — no raise
        controller._track_successful_call(result, "read_file")
        controller._track_successful_call(result, "read_file")
        controller._track_successful_call(result, "read_file")
        # Call 4: HARD level — should NOT raise
        controller._track_successful_call(result, "read_file")

        # Verify HARD warning was injected into history
        system_events = [e for e in controller._history if e.role == "system"]
        assert len(system_events) >= 1  # WARNING + HARD both inject

        # Call 5: BREAK level — raises
        with pytest.raises(ToolLoopCircuitBreakerError) as exc_info:
            controller._track_successful_call(result, "read_file")

        assert "read_file" in str(exc_info.value)

    def test_break_threshold_at_count_5(self):
        """Circuit breaker triggers at BREAK level for quick_fix scene.

        With dual detection (no-gain + semantic stagnation), escalation is
        faster than pure no-gain tracking. Both same_signature and no_gain
        counters contribute, with stagnation multiplier applied when both
        are elevated.
        """
        breaker = ProgressiveCircuitBreaker(scene="quick_fix")
        result = {"success": True, "args": {"path": "/test"}}
        args = {"path": "/test"}

        levels = []
        for _ in range(6):
            level, _count = breaker.evaluate("read_file", args, result)
            levels.append(level)

        # With dual detection: both no_gain and same_signature increment
        # together for identical calls. Stagnation multiplier kicks in
        # at same_sig>=3 && no_gain>=2 => mult=1.5, escalating faster.
        assert levels[0] == CircuitBreakerLevel.OK  # new info, same_sig=0
        assert levels[1] == CircuitBreakerLevel.OK  # no-gain=1, same_sig=1
        assert levels[2] == CircuitBreakerLevel.WARNING  # both=2, effective=2 (>= warning=2)
        assert levels[3] == CircuitBreakerLevel.HARD  # both=3, mult=1.5 => effective=4 (>= hard=3)
        assert levels[4] == CircuitBreakerLevel.BREAK  # both=4, mult=1.5 => effective=6 (>= break=5)
        assert levels[5] == CircuitBreakerLevel.BREAK  # escalating

    def test_resets_on_different_args(self):
        """Different args that produce new info don't accumulate no-gain streak."""
        controller = _make_controller("quick_fix")

        # Each result with unique content produces new info
        result1 = {"success": True, "args": {"path": "/test/file1.txt"}, "result": {"content": "AAA"}}
        result2 = {"success": True, "args": {"path": "/test/file2.txt"}, "result": {"content": "BBB"}}

        # These should all be OK because each produces new information
        controller._track_successful_call(result1, "read_file")
        controller._track_successful_call(result2, "read_file")
        controller._track_successful_call(result2, "read_file")
        # No raise expected: result1 has new info, result2 has new info,
        # then result2 again has same fingerprint but only 1 no-gain

    def test_resets_on_write_tool(self):
        """Write tool resets read-only streak and clears legacy tracking."""
        controller = _make_controller("quick_fix")

        read_result = {"success": True, "args": {"path": "/test/file.txt"}}
        write_result = {"success": True, "args": {"path": "/test/file.txt", "content": "data"}}

        # Multiple read_file calls
        controller._track_successful_call(read_result, "read_file")
        controller._track_successful_call(read_result, "read_file")
        controller._track_successful_call(read_result, "read_file")

        # Write tool resets
        controller._track_successful_call(write_result, "write_file")
        assert controller._read_only_streak == 0
        assert controller._workspace_modified is True

        # Legacy tracking is cleared, so fresh start
        controller._track_successful_call(read_result, "read_file")

    def test_failure_not_counted(self):
        """Failed tool calls are not tracked (early return)."""
        controller = _make_controller("quick_fix")

        success_result = {"success": True, "args": {"path": "/test/file.txt"}}
        fail_result = {"success": False, "args": {"path": "/test/file.txt"}}

        controller._track_successful_call(success_result, "read_file")
        controller._track_successful_call(fail_result, "read_file")  # Skipped
        controller._track_successful_call(fail_result, "read_file")  # Skipped
        controller._track_successful_call(success_result, "read_file")

        # No raise expected: only 2 successful calls tracked with no-gain=1


# ═══════════════════════════════════════════════════════════════════════════
# Cross-Tool Loop Detection Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossToolLoopDetection:
    """Tests for cross-tool loop detection via _detect_cross_tool_loop and tool_names tracking."""

    def test_cross_tool_names_tracked(self):
        """Tool names are tracked in _recent_tool_names for pattern detection."""
        controller = _make_controller("quick_fix")

        result = {"success": True, "args": {"path": "/test"}, "result": {"entries": []}}

        controller._track_successful_call(result, "repo_tree")
        controller._track_successful_call(result, "read_file")
        controller._track_successful_call(result, "repo_tree")

        assert controller._recent_tool_names == ["repo_tree", "read_file", "repo_tree"]

    def test_abab_cross_tool_pattern_detected(self):
        """_detect_cross_tool_loop detects ABAB pattern in tool names."""
        controller = _make_controller("quick_fix")

        result = {"success": True, "args": {}, "result": {"entries": []}}
        # Feed identical results so progressive breaker sees no-gain
        # but test cross-tool name tracking specifically
        controller._track_successful_call(result, "repo_tree")
        controller._track_successful_call(result, "read_file")
        controller._track_successful_call(result, "repo_tree")
        controller._track_successful_call(result, "read_file")

        # _detect_cross_tool_loop should detect ABAB
        assert controller._detect_cross_tool_loop() is True

    def test_abcabc_cross_tool_pattern_detected(self):
        """_detect_cross_tool_loop detects ABCABC pattern.

        Directly inject tool names into _recent_tool_names to test the
        pattern detection algorithm without triggering progressive breaker.
        """
        controller = _make_controller("quick_fix")

        # Directly set tool names to create ABCABC pattern
        controller._recent_tool_names = [
            "repo_tree",
            "read_file",
            "repo_rg",
            "repo_tree",
            "read_file",
            "repo_rg",
        ]

        assert controller._detect_cross_tool_loop() is True

    def test_no_false_positive_short_sequence(self):
        """Short sequences should not trigger cross-tool loop detection."""
        controller = _make_controller("quick_fix")

        result = {"success": True, "args": {}, "result": {"entries": []}}
        controller._track_successful_call(result, "repo_tree")
        controller._track_successful_call(result, "read_file")
        controller._track_successful_call(result, "repo_tree")

        # Only 3 calls - not enough for ABAB (needs 4) or ABCABC (needs 6)
        assert controller._detect_cross_tool_loop() is False

    def test_same_tool_cross_loop_not_detected(self):
        """AAAA pattern should NOT trigger cross-tool loop (same tool = not cross).

        Tests the _detect_cross_tool_loop() method directly. Note: the
        progressive circuit breaker WILL trigger for repeated same-tool
        same-target calls via semantic stagnation detection, but this test
        specifically validates the cross-tool ABAB/ABCABC logic.
        """
        controller = _make_controller("quick_fix")

        # Use different file paths to avoid semantic stagnation trigger
        # (different files => different semantic signatures => no stagnation)
        for i in range(4):
            controller._track_successful_call(
                {"success": True, "args": {"path": f"/test/file_{i}.txt"}, "result": {"content": f"unique_{i}"}},
                "read_file",
            )

        # AAAA: last4 = [read_file, read_file, read_file, read_file]
        # ABAB requires last4[0] == last4[2] AND last4[1] == last4[3] AND last4[0] != last4[1]
        # Since all are the same tool, last4[0] == last4[1], so NOT cross-tool
        assert controller._detect_cross_tool_loop() is False


# ═══════════════════════════════════════════════════════════════════════════
# State Stagnation Detection Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestStateStagnationDetection:
    """Tests for state stagnation (read-only streak without write)."""

    def test_stagnation_counter_increments_for_read_tools(self):
        """Read-only tools increment _read_only_streak."""
        controller = _make_controller("quick_fix")

        # Each with unique content to avoid progressive breaker raising
        results = [
            {"success": True, "args": {"path": f"/test/file{i}.txt"}, "result": {"content": f"content_{i}"}}
            for i in range(5)
        ]

        for i in range(4):
            controller._track_successful_call(results[i], "read_file")
        assert controller._read_only_streak == 4

        controller._track_successful_call(results[4], "read_file")
        assert controller._read_only_streak == 5

    def test_stagnation_resets_on_write_tool(self):
        """Write tool resets read-only streak to 0."""
        controller = _make_controller("quick_fix")

        results = [
            {"success": True, "args": {"path": f"/test/file{i}.txt"}, "result": {"content": f"c_{i}"}} for i in range(4)
        ]
        write_result = {"success": True, "args": {"path": "/test", "content": "data"}, "result": {}}

        for i in range(4):
            controller._track_successful_call(results[i], "read_file")
        assert controller._read_only_streak == 4

        controller._track_successful_call(write_result, "write_file")
        assert controller._read_only_streak == 0
        assert controller._workspace_modified is True

        # Can accumulate again after write
        for i in range(4):
            controller._track_successful_call(results[i], "read_file")
        assert controller._read_only_streak == 4

    def test_mixed_read_tools_count_toward_stagnation(self):
        """Different read-only tools all increment stagnation counter."""
        controller = _make_controller("quick_fix")

        # Each tool with unique result content (dict format) to avoid progressive breaker
        controller._track_successful_call(
            {"success": True, "args": {}, "result": {"entries": [{"name": "a"}]}}, "repo_tree"
        )
        controller._track_successful_call(
            {"success": True, "args": {}, "result": {"content": "file_data"}}, "read_file"
        )
        controller._track_successful_call({"success": True, "args": {}, "result": {"matches": []}}, "repo_rg")
        controller._track_successful_call(
            {"success": True, "args": {}, "result": {"entries": [{"name": "b"}]}}, "list_directory"
        )

        assert controller._read_only_streak == 4

        controller._track_successful_call({"success": True, "args": {}, "result": {"exists": False}}, "file_exists")
        assert controller._read_only_streak == 5

    def test_identical_read_results_trigger_breaker(self):
        """Identical results from same read tool trigger progressive breaker (BREAK level)."""
        controller = _make_controller("quick_fix")

        # Use identical results to force no-gain detection
        result = {"success": True, "args": {"path": "/test/file.txt"}, "result": {"content": "same"}}

        # Call 1: new info, Call 2: no-gain=1, Call 3: no-gain=2 => WARNING
        # Call 4: HARD (injects warning, no raise)
        controller._track_successful_call(result, "read_file")
        controller._track_successful_call(result, "read_file")
        controller._track_successful_call(result, "read_file")
        controller._track_successful_call(result, "read_file")  # HARD — no raise
        # Call 5: BREAK => raise
        with pytest.raises(ToolLoopCircuitBreakerError):
            controller._track_successful_call(result, "read_file")


# ═══════════════════════════════════════════════════════════════════════════
# Progressive Breaker Direct Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestProgressiveBreaker:
    """Tests for the ProgressiveCircuitBreaker directly."""

    def test_quick_fix_thresholds(self):
        """Quick fix scene has lower thresholds."""
        profile = SCENE_PROFILES["quick_fix"]
        assert profile.warning_threshold == 2
        assert profile.hard_threshold == 3
        assert profile.break_threshold == 5

    def test_normal_thresholds(self):
        """Normal scene has medium thresholds."""
        profile = SCENE_PROFILES["normal"]
        assert profile.warning_threshold == 3
        assert profile.hard_threshold == 5
        assert profile.break_threshold == 7

    def test_deep_analysis_thresholds(self):
        """Deep analysis scene has highest thresholds."""
        profile = SCENE_PROFILES["deep_analysis"]
        assert profile.warning_threshold == 5
        assert profile.hard_threshold == 8
        assert profile.break_threshold == 12

    def test_progressive_escalation_quick_fix(self):
        """Verify progressive escalation from OK -> WARNING -> HARD -> BREAK for quick_fix.

        With dual detection (no-gain + semantic stagnation), both counters
        increment for identical same-target calls. The stagnation multiplier
        accelerates escalation when both counters are elevated.
        """
        breaker = ProgressiveCircuitBreaker(scene="quick_fix")
        result = {"success": True, "result": {"content": "data"}}
        args = {"path": "/test"}

        levels = []
        for _ in range(7):
            level, _count = breaker.evaluate("read_file", args, result)
            levels.append(level)

        # Both no_gain and same_signature increment together for identical calls.
        # Stagnation multiplier kicks in at same_sig>=3 && no_gain>=2 => mult=1.5
        assert levels[0] == CircuitBreakerLevel.OK  # new info, same_sig=0
        assert levels[1] == CircuitBreakerLevel.OK  # no-gain=1, same_sig=1
        assert levels[2] == CircuitBreakerLevel.WARNING  # both=2, effective=2 (>= warning=2)
        assert levels[3] == CircuitBreakerLevel.HARD  # both=3, mult=1.5 => effective=4 (>= hard=3)
        assert levels[4] == CircuitBreakerLevel.BREAK  # both=4, mult=1.5 => effective=6 (>= break=5)
        assert levels[5] == CircuitBreakerLevel.BREAK  # escalating
        assert levels[6] == CircuitBreakerLevel.BREAK  # continuing to break

    def test_new_information_resets_streak(self):
        """When new information is detected, no-gain streak resets.

        Note: _consecutive_same_signature does NOT reset on new information
        if the semantic signature is the same (same target). This is the key
        behavioral change for the semantic stagnation detection.
        """
        breaker = ProgressiveCircuitBreaker(scene="quick_fix")

        # Same result twice => no-gain streak
        same_result = {"success": True, "result": {"content": "same"}}
        args = {"path": "/test"}

        breaker.evaluate("read_file", args, same_result)  # new info
        breaker.evaluate("read_file", args, same_result)  # no-gain=1
        assert breaker._consecutive_no_gain == 1

        # New result resets no-gain streak (but NOT same_signature)
        new_result = {"success": True, "result": {"content": "different"}}
        level, _count = breaker.evaluate("read_file", args, new_result)
        # same_sig=2 still contributes to effective count (>= warning=2)
        assert level == CircuitBreakerLevel.WARNING
        assert breaker._consecutive_no_gain == 0
        # same_signature continues to increment (same target "/test")
        assert breaker._consecutive_same_signature == 2

    def test_reset_clears_state(self):
        """reset() clears all breaker state."""
        breaker = ProgressiveCircuitBreaker(scene="quick_fix")

        result = {"success": True, "result": {}}
        args = {"path": "/test"}
        for _ in range(5):
            breaker.evaluate("read_file", args, result)

        assert breaker._consecutive_no_gain > 0

        breaker.reset()
        assert breaker._consecutive_no_gain == 0
        assert breaker._last_signature == ("", "")


# ═══════════════════════════════════════════════════════════════════════════
# Recovery Hint Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestRecoveryHint:
    """Tests that circuit breakers provide helpful recovery hints."""

    def test_break_level_recovery_hint(self):
        """BREAK level provides recovery guidance (HARD injects warning but doesn't raise)."""
        controller = _make_controller("quick_fix")

        result = {"success": True, "args": {"path": "/test"}, "result": {"content": "same"}}

        exc_caught = None
        for _ in range(20):
            try:
                controller._track_successful_call(result, "read_file")
            except ToolLoopCircuitBreakerError as e:
                exc_caught = e
                break

        assert exc_caught is not None
        assert hasattr(exc_caught, "recovery_hint")
        assert isinstance(exc_caught.recovery_hint, str)
        assert len(exc_caught.recovery_hint) > 0
        assert hasattr(exc_caught, "breaker_type")

    def test_breaker_error_contains_tool_name(self):
        """Error message includes the tool name that triggered the breaker."""
        controller = _make_controller("quick_fix")

        result = {"success": True, "args": {"path": "/test"}, "result": {"content": "data"}}

        exc_caught = None
        for _ in range(20):
            try:
                controller._track_successful_call(result, "read_file")
            except ToolLoopCircuitBreakerError as e:
                exc_caught = e
                break

        assert exc_caught is not None
        assert "read_file" in str(exc_caught)


# ═══════════════════════════════════════════════════════════════════════════
# Read-Only Stagnation Tests (Production Pattern Defense)
# ═══════════════════════════════════════════════════════════════════════════


class TestReadOnlyStagnation:
    """Tests for read-only stagnation detection across all targets.

    Validates the fix for the production pattern where the LLM reads N different
    files repeatedly without ever producing a write/edit operation. The previous
    circuit breaker only tracked per-target stagnation (same file), so rotating
    between files (A→B→C→D→A→B→...) evaded detection entirely.
    """

    def test_rotating_file_pattern_triggers_for_normal_scene(self):
        """Rotating read_file across different files triggers HARD at streak=8.

        This is the exact production pattern from fileserver-32fc198ee3e4:
        5x read_file → repo_tree → read_file → read_file (total=8 read-only).
        For 'normal' scene, read_stagnation_threshold=8.
        """
        breaker = ProgressiveCircuitBreaker(scene="normal")

        # Simulate production pattern: rotating different files
        files = [f"/src/module{i}.py" for i in range(5)]
        levels = []
        for i in range(9):
            args = {"path": files[i % len(files)]}
            result = {"success": True, "result": {"content": f"content_{i}"}}
            level, _count = breaker.evaluate("read_file", args, result, is_read_only=True)
            levels.append(level)

        # Normal scene: read_stagnation_threshold=8
        # Streak 1-7: OK (under threshold)
        assert levels[0] == CircuitBreakerLevel.OK
        assert levels[6] == CircuitBreakerLevel.OK
        # Streak 8: HARD (at threshold)
        assert levels[7] == CircuitBreakerLevel.HARD
        # Streak 9: HARD or BREAK (still elevated)
        assert levels[8] in (CircuitBreakerLevel.HARD, CircuitBreakerLevel.BREAK)

    def test_rotating_file_pattern_triggers_earlier_for_quick_fix(self):
        """Quick_fix scene triggers HARD at streak=6 (lower threshold)."""
        breaker = ProgressiveCircuitBreaker(scene="quick_fix")

        files = [f"/src/file{i}.py" for i in range(4)]
        levels = []
        for i in range(8):
            args = {"path": files[i % len(files)]}
            result = {"success": True, "result": {"content": f"unique_{i}"}}
            level, _count = breaker.evaluate("read_file", args, result, is_read_only=True)
            levels.append(level)

        # Quick fix: read_stagnation_threshold=6
        assert levels[4] == CircuitBreakerLevel.OK  # streak=5
        assert levels[5] == CircuitBreakerLevel.HARD  # streak=6 → threshold met

    def test_write_operation_resets_read_only_streak(self):
        """A write operation resets _read_only_streak to 0 in the breaker."""
        breaker = ProgressiveCircuitBreaker(scene="normal")

        # Accumulate 5 read-only streak
        for i in range(5):
            args = {"path": f"/test/file{i}.py"}
            result = {"success": True, "result": {"content": f"c_{i}"}}
            breaker.evaluate("read_file", args, result, is_read_only=True)

        assert breaker._read_only_streak == 5

        # Write operation resets streak
        args_w = {"path": "/test/file.py", "content": "fixed"}
        result_w = {"success": True, "result": {}}
        level, _ = breaker.evaluate("write_file", args_w, result_w, is_read_only=False)

        assert breaker._read_only_streak == 0
        assert level == CircuitBreakerLevel.OK

    def test_deep_analysis_allows_longer_streak(self):
        """Deep analysis scene allows 15 read-only ops before HARD."""
        breaker = ProgressiveCircuitBreaker(scene="deep_analysis")

        for i in range(16):
            args = {"path": f"/src/deep/file{i % 5}.py"}
            result = {"success": True, "result": {"content": f"analysis_{i}"}}
            level, _count = breaker.evaluate("read_file", args, result, is_read_only=True)

            if i < 14:
                assert level == CircuitBreakerLevel.OK, f"Should be OK at streak={i + 1}"
            elif i == 14:
                assert level == CircuitBreakerLevel.HARD, "Should be HARD at streak=15"
            else:
                assert level in (CircuitBreakerLevel.HARD, CircuitBreakerLevel.BREAK)

    def test_mixed_read_tools_contribute_to_streak(self):
        """Different read tools (repo_tree, read_file, repo_rg) all increment streak."""
        breaker = ProgressiveCircuitBreaker(scene="quick_fix")

        tools_and_results = [
            ("repo_tree", {"path": "/src"}, {"entries": [{"name": "a"}]}),
            ("read_file", {"path": "/src/main.py"}, {"content": "code"}),
            ("repo_rg", {"path": "/src", "pattern": "TODO"}, {"matches": []}),
            ("list_directory", {"path": "/src"}, {"entries": [{"name": "b"}]}),
            ("file_exists", {"path": "/src/test.py"}, {"exists": True}),
            ("repo_read_head", {"path": "/src/app.py"}, {"content": "head"}),
        ]

        levels = []
        for tool, args, result_data in tools_and_results:
            result = {"success": True, "result": result_data}
            level, _ = breaker.evaluate(tool, args, result, is_read_only=True)
            levels.append(level)

        # 6 read-only ops → quick_fix threshold=6 → HARD
        assert breaker._read_only_streak == 6
        assert levels[-1] == CircuitBreakerLevel.HARD

    def test_controller_does_not_raise_for_rotating_file_exploration(self):
        """Rotating read-only ops on different files with new info should NOT hard-break.

        Multi-file refactoring tasks legitimately require reading 8+ different
        files before writing anything. The circuit breaker may inject HARD-level
        warnings, but ToolLoopController should not raise an exception as long
        as the agent is reading different targets with new information.
        """
        controller = _make_controller("normal")
        controller.request = SimpleNamespace(domain="code", task_id="task-1")

        for idx in range(10):
            controller._track_successful_call(
                {
                    "success": True,
                    "args": {"path": f"/src/file{idx}.py"},
                    "result": {"content": f"content_{idx}"},
                },
                "read_file",
            )

    def test_controller_raises_for_same_file_read_only_stagnation(self):
        """Repeated reads of the SAME file without writes IS stagnation and should break.

        ProgressiveCircuitBreaker escalates to BREAK for same-target stagnation,
        so the exception is raised via the BREAK path, not the HARD path.
        """
        controller = _make_controller("normal")
        controller.request = SimpleNamespace(domain="code", task_id="task-1")

        with pytest.raises(ToolLoopCircuitBreakerError):
            for _ in range(10):
                controller._track_successful_call(
                    {
                        "success": True,
                        "args": {"path": "/src/same.py"},
                        "result": {"content": "same_content"},
                    },
                    "read_file",
                )
