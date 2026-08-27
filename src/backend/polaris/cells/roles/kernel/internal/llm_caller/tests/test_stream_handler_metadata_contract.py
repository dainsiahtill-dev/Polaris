from __future__ import annotations

from polaris.cells.roles.kernel.internal.llm_caller.stream_handler import normalize_stream_chunk
from polaris.kernelone.llm.engine.contracts import AIStreamEvent


def test_normalize_stream_chunk_preserves_meta_from_serialized_ai_stream_event() -> None:
    """Serialized KernelOne events use ``meta``; roles must not drop that evidence."""
    argument_audit = {
        "provider": "anthropic_compat",
        "tool_name": "write_file",
        "call_id": "call-audit-1",
        "raw_arguments_sha256": "a" * 64,
        "decoded_arguments_sha256": "b" * 64,
    }
    serialized = AIStreamEvent.tool_call_event(
        {
            "id": "call-audit-1",
            "type": "function",
            "function": {
                "name": "write_file",
                "arguments": {"path": "src/main.cpp", "content": "int main() { return 0; }"},
            },
        },
        meta={"tool_call_argument_audit": argument_audit},
    ).to_dict()

    normalized = normalize_stream_chunk(
        serialized,
        native_tool_mode="native",
        tool_protocol="openai",
    )

    assert normalized.event_type == "tool_call"
    assert normalized.metadata["tool_call_argument_audit"] == argument_audit
