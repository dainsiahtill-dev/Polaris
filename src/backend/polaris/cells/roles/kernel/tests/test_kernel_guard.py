from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.kernel_guard import KernelGuard, KernelGuardError
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger


class TestKernelGuard:
    def test_assert_single_decision_accepts_one(self) -> None:
        KernelGuard.assert_single_decision("turn_ok", 1)

    def test_assert_single_decision_rejects_multiple(self) -> None:
        with pytest.raises(KernelGuardError, match="single_decision"):
            KernelGuard.assert_single_decision("turn_bad", 2)

    def test_assert_single_tool_batch_rejects_second_batch(self) -> None:
        with pytest.raises(KernelGuardError, match="single_tool_batch"):
            KernelGuard.assert_single_tool_batch("turn_batch", 2)

    def test_assert_no_hidden_continuation_detects_second_decision_request(self) -> None:
        with pytest.raises(KernelGuardError, match="no_hidden_continuation"):
            KernelGuard.assert_no_hidden_continuation(
                "turn_loop",
                ["CONTEXT_BUILT", "DECISION_REQUESTED", "TOOL_BATCH_EXECUTED", "DECISION_REQUESTED", "COMPLETED"],
            )

    def test_assert_no_finalization_tool_calls_soft_warns_and_does_not_raise(self, caplog) -> None:
        # Guard softened: hallucinated tool calls are dropped by decoder rather
        # than causing a hard panic. Verify warning is logged and no exception.
        KernelGuard.assert_no_finalization_tool_calls("turn_final", [{"id": "call_1"}])
        assert "finalization_tool_calls_soft_guard" in caplog.text

    def test_assert_no_finalization_tool_calls_records_anomaly_flag(self) -> None:
        ledger = TurnLedger(turn_id="turn_1")
        tool_calls = [{"name": "read_file"}, {"name": "write_file"}]
        KernelGuard.assert_no_finalization_tool_calls("turn_1", tool_calls, ledger=ledger)
        assert len(ledger.anomaly_flags) == 1
        flag = ledger.anomaly_flags[0]
        assert flag["type"] == "finalize_tool_call_hallucination"
        assert flag["turn_id"] == "turn_1"
        assert flag["tool_count"] == 2
        assert flag["tool_names"] == ["read_file", "write_file"]

    def test_assert_no_finalization_tool_calls_no_ledger_no_crash(self) -> None:
        # Ensure passing ledger=None does not crash
        KernelGuard.assert_no_finalization_tool_calls("turn_2", [{"name": "search"}], ledger=None)

    def test_assert_no_finalization_tool_calls_empty_calls(self) -> None:
        ledger = TurnLedger(turn_id="turn_3")
        KernelGuard.assert_no_finalization_tool_calls("turn_3", [], ledger=ledger)
        assert len(ledger.anomaly_flags) == 0

    def test_assert_no_finalization_tool_calls_none_calls(self) -> None:
        ledger = TurnLedger(turn_id="turn_4")
        KernelGuard.assert_no_finalization_tool_calls("turn_4", None, ledger=ledger)
        assert len(ledger.anomaly_flags) == 0
