"""Shared B3.3 prepared-context factory for stream engine tests.

Extracted from test_llm_caller_components_sa.py so sibling split modules
(test_llm_caller_componentsb.py) stop borrowing it without an import.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock


def make_b33_propagating_prepared(*, native_response_format: bool = False) -> SimpleNamespace:
    request = SimpleNamespace(provider_id="provider-a", model="model-a", context={}, options={})
    return SimpleNamespace(
        ai_request=request,
        messages=[],
        request_options={},
        context_summary="summary",
        context_os_audit={},
        context_result=SimpleNamespace(
            token_estimate=1,
            compression_applied=False,
            compression_strategy=None,
        ),
        factory_semantic_request=object(),
        factory_dispatch_port=Mock(),
        native_response_format=native_response_format,
        native_tool_mode="disabled",
        native_tool_schemas=[],
        response_format_mode="plain_text",
        __post_init__=Mock(),
    )
