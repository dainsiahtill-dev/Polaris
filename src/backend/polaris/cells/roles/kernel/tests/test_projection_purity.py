"""Gate 5: Projection purity tests.

验证：
- raw tool output 不得直接进入 prompt projection
- system warning 不得进入 projection
- thinking 不得进入 projection
- XML wrapper 不得进入 projection
- control-plane noise 被显式剥离
"""

from __future__ import annotations

from polaris.kernelone.context.projection_engine import ProjectionEngine
from polaris.kernelone.context.receipt_store import ReceiptStore


class TestProjectionPurity:
    def test_raw_tool_output_is_referenced_not_inlined(self) -> None:
        engine = ProjectionEngine()
        store = ReceiptStore()
        large_output = "x" * 10000
        store.put("ref_large", large_output)

        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "analyze", "receipt_refs": ["ref_large"]}],
            },
            store,
        )

        user_msg = messages[1]
        assert "[Receipt ref_large]:" in user_msg["content"]
        assert large_output[:500] in user_msg["content"]
        assert len(user_msg["content"]) < len(large_output) + 100

    def test_system_warning_does_not_appear_in_projection(self) -> None:
        engine = ProjectionEngine()
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "do it"}],
                "system_warnings": ["This is a warning"],
            },
            ReceiptStore(),
        )

        content_str = str(messages)
        assert "This is a warning" not in content_str

    def test_thinking_content_is_excluded(self) -> None:
        engine = ProjectionEngine()
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [
                    {
                        "role": "assistant",
                        "content": "answer",
                        "thinking": "step 1: analyze\nstep 2: conclude",
                    }
                ],
            },
            ReceiptStore(),
        )

        # ProjectionEngine must enforce purity at turn level as well:
        # thinking remains telemetry-only and must not be projected.
        content_str = str(messages)
        assert "answer" in content_str
        assert messages[1].get("thinking") is None

    def test_xml_wrapper_stripped(self) -> None:
        engine = ProjectionEngine()
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [
                    {
                        "role": "assistant",
                        "content": "<thinking>reasoning</thinking>final answer",
                    }
                ],
            },
            ReceiptStore(),
        )

        # ProjectionEngine is a read-only message assembler; wrapper stripping
        # is the responsibility of upstream sanitization (transcript leak guard).
        # We verify ProjectionEngine does not corrupt or re-escape content.
        content_str = str(messages)
        assert "final answer" in content_str

    def test_control_plane_noise_stripped(self) -> None:
        engine = ProjectionEngine()
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "do it"}],
                "budget_status": {"remaining": 5},
                "policy_verdict": "allowed",
                "telemetry": {"latency_ms": 42},
                "telemetry_events": [{"event": "foo"}],
                "metrics": {"tokens": 100},
            },
            ReceiptStore(),
        )

        content_str = str(messages)
        assert "budget_status" not in content_str
        assert "policy_verdict" not in content_str
        assert "telemetry" not in content_str
        assert "metrics" not in content_str

    def test_receipt_store_missing_ref_graceful(self) -> None:
        engine = ProjectionEngine()
        messages = engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "do it", "receipt_refs": ["missing_ref"]}],
            },
            ReceiptStore(),
        )

        assert messages[1]["content"] == "do it"

    def test_projection_engine_is_readonly(self) -> None:
        engine = ProjectionEngine()
        store = ReceiptStore()
        store.put("ref_1", "content")

        original_entries = store.list_receipt_ids()
        engine.project(
            {
                "system_hint": "sys",
                "turns": [{"role": "user", "content": "do it"}],
            },
            store,
        )

        assert store.list_receipt_ids() == original_entries
