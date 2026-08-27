from __future__ import annotations

from polaris.cells.control_plane.run_ledger.public.tool_lifecycle._receipts import (
    build_native_tool_call_envelopes,
)


def test_native_tool_call_envelope_preserves_only_safe_provider_argument_audit() -> None:
    raw_calls = [
        {
            "id": "call-argument-audit",
            "type": "function",
            "function": {
                "name": "write_file",
                "arguments": '{"path":"src/main.cpp","content":"secret source"}',
            },
            "provider_argument_audit": {
                "provider": "anthropic_compat",
                "tool_name": "write_file",
                "call_id": "call-argument-audit",
                "target_path": "src/main.cpp",
                "raw_arguments_length": 52,
                "raw_arguments_sha256": "a" * 64,
                "decoded_arguments_sha256": "b" * 64,
                "content_length": 13,
                "content_sha256": "c" * 64,
                "content_angle_open_count": 2,
                "content_angle_close_count": 2,
                "content_xml_close_count": 0,
                "assembly": {
                    "delta_count": 7,
                    "fragment_count": 3,
                    "ignored": "must-not-survive",
                },
                "raw_arguments": "must-not-survive",
                "content": "must-not-survive",
                "secret": "must-not-survive",
            },
        }
    ]

    envelopes = build_native_tool_call_envelopes(
        raw_calls,
        provider="anthropic_compat",
    )

    assert len(envelopes) == 1
    audit = envelopes[0].metadata["provider_argument_audit"]
    assert audit == {
        "provider": "anthropic_compat",
        "tool_name": "write_file",
        "call_id": "call-argument-audit",
        "target_path": "src/main.cpp",
        "raw_arguments_length": 52,
        "raw_arguments_sha256": "a" * 64,
        "decoded_arguments_sha256": "b" * 64,
        "content_length": 13,
        "content_sha256": "c" * 64,
        "content_angle_open_count": 2,
        "content_angle_close_count": 2,
        "content_xml_close_count": 0,
        "assembly": {
            "delta_count": 7,
            "fragment_count": 3,
        },
    }
    assert "secret source" not in repr(envelopes[0])
    assert "must-not-survive" not in repr(envelopes[0])


def test_native_tool_call_envelope_drops_invalid_provider_argument_audit_fields() -> None:
    raw_calls = [
        {
            "id": "call-invalid-audit",
            "type": "function",
            "function": {"name": "write_file", "arguments": "{}"},
            "provider_argument_audit": {
                "raw_arguments_length": -1,
                "raw_arguments_sha256": "not-a-hash",
                "content_length": "13",
                "content_sha256": "D" * 64,
                "assembly": {"delta_count": -2},
            },
        }
    ]

    envelope = build_native_tool_call_envelopes(
        raw_calls,
        provider="anthropic_compat",
    )[0]

    assert "provider_argument_audit" not in envelope.metadata
