"""Tests for LLM caller tool helper parsing."""

from __future__ import annotations

import json

from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import extract_native_tool_calls


class TestExtractNativeToolCalls:
    """Tests for native and text fallback tool extraction."""

    def test_gemma_inline_tool_call_preserves_outer_tool_name(self) -> None:
        """Regression: Gemma inline protocol should use the outer call name."""
        response_text = (
            '<|tool_call>call:write_file{content:<|"|>{\n'
            '  "name": "bootstrap-project",\n'
            '  "version": "1.0.0"\n'
            '}<|"|>,file:<|"|>package.json<|"|>}<tool_call|>'
        )

        calls, provider = extract_native_tool_calls(
            {},
            provider_id="openai_compat-1780683130410",
            model="gemma-4-12B-it-Q8_0",
            response_text=response_text,
        )

        assert provider == "text_fallback"
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "write_file"
        arguments = json.loads(calls[0]["function"]["arguments"])
        assert arguments["file"] == "package.json"
        assert '"name": "bootstrap-project"' in arguments["content"]

    def test_plain_package_json_text_is_not_native_tool_call(self) -> None:
        """Regression: plain package.json content must stay data, not control."""
        calls, provider = extract_native_tool_calls(
            {},
            provider_id="openai_compat-1780683130410",
            model="gemma-4-12B-it-Q8_0",
            response_text='{"name": "polaris-project", "version": "1.0.0"}',
        )

        assert calls == []
        assert provider == "openai"
