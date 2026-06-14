"""LLMInvoker wiring for provider-bound ContextOS receipts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from polaris.cells.factory.cognitive_runtime.public import RecordRuntimeReceiptCommandV1
from polaris.cells.roles.kernel.internal.llm_caller import invoker as invoker_module
from polaris.cells.roles.kernel.internal.llm_caller.invoker import LLMInvoker


def test_get_executor_injects_contextos_final_request_sink(tmp_path: Path) -> None:
    invoker = LLMInvoker(workspace=str(tmp_path))

    executor = invoker._get_executor()

    assert executor.workspace == str(tmp_path)
    assert callable(executor.final_request_receipt_sink)


def test_final_request_sink_records_cognitive_runtime_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    commands: list[RecordRuntimeReceiptCommandV1] = []

    class _Runtime:
        def record_runtime_receipt(self, command: RecordRuntimeReceiptCommandV1) -> Any:
            commands.append(command)
            return SimpleNamespace(ok=True, receipt=SimpleNamespace(receipt_id="receipt-1"))

    monkeypatch.setattr(
        invoker_module,
        "_get_cognitive_runtime_receipt_deps",
        lambda: (RecordRuntimeReceiptCommandV1, lambda: _Runtime()),
    )

    invoker = LLMInvoker(workspace=str(tmp_path))
    invoker._record_final_request_receipt(
        {
            "receipt_type": "contextos.final_request",
            "payload": {
                "trace_id": "trace-1",
                "run_id": "run-1",
                "session_id": "session-1",
                "provider_id": "provider-a",
                "model": "model-a",
            },
            "trace_refs": ("trace-1",),
        }
    )

    assert len(commands) == 1
    command = commands[0]
    assert command.workspace == str(tmp_path)
    assert command.receipt_type == "contextos.final_request"
    assert command.session_id == "session-1"
    assert command.run_id == "run-1"
    assert command.trace_refs == ("trace-1",)
    assert command.payload["provider_id"] == "provider-a"
    assert command.turn_envelope["source"] == "roles.kernel.llm_invoker"


def test_final_request_sink_runtime_shape_error_does_not_raise(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        invoker_module,
        "_get_cognitive_runtime_receipt_deps",
        lambda: (RecordRuntimeReceiptCommandV1, lambda: object()),
    )

    invoker = LLMInvoker(workspace=str(tmp_path))
    invoker._record_final_request_receipt(
        {
            "receipt_type": "contextos.final_request",
            "payload": {"trace_id": "trace-1", "run_id": "run-1"},
            "trace_refs": ("trace-1",),
        }
    )
