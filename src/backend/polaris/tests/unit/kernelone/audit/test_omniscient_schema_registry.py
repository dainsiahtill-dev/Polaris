"""Unit tests for polaris.kernelone.audit.omniscient.schema_registry."""

from __future__ import annotations

import pytest
from polaris.kernelone.audit.omniscient.schema_registry import (
    SchemaRegistry,
    get_schema_registry,
)
from polaris.kernelone.audit.omniscient.schemas.base import AuditEvent, EventDomain
from polaris.kernelone.audit.omniscient.schemas.llm_event import LLMEvent
from polaris.kernelone.audit.omniscient.schemas.tool_event import ToolEvent


class TestSchemaRegistry:
    def test_domains(self) -> None:
        reg = SchemaRegistry()
        assert "llm" in reg.DOMAINS
        assert "tool" in reg.DOMAINS

    def test_register_audit_event_subclass(self) -> None:
        reg = SchemaRegistry()
        reg.register(LLMEvent)
        assert reg.get_schema("llm", "llm_call") is LLMEvent

    def test_register_with_override(self) -> None:
        reg = SchemaRegistry()
        reg.register(ToolEvent, domain="custom", event_type="my_tool")
        assert reg.get_schema("custom", "my_tool") is ToolEvent

    def test_register_invalid_raises(self) -> None:
        reg = SchemaRegistry()
        with pytest.raises(TypeError, match="AuditEvent subclass"):
            reg.register(str)  # type: ignore[arg-type]

    def test_validate_dict(self) -> None:
        reg = SchemaRegistry()
        reg.register(LLMEvent)
        data = {
            "event_id": "e1",
            "version": "3.0",
            "domain": "llm",
            "event_type": "llm_call",
            "timestamp": "2024-01-01T00:00:00Z",
            "trace_id": "abcd1234abcd1234",
            "run_id": "r1",
            "priority": "info",
            "workspace": "",
            "role": "",
            "model": "claude",
            "provider": "anthropic",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 0.0,
            "first_token_latency_ms": 0.0,
            "strategy": "primary",
            "fallback_model": "",
            "finish_reason": None,
            "error": "",
            "error_type": "",
            "prompt_preview": "",
            "completion_preview": "",
            "safety_flags": [],
            "thinking_enabled": False,
            "temperature": 0.0,
            "max_tokens": 0,
            "data": {},
            "correlation_context": {},
        }
        assert reg.validate(data) is True

    def test_validate_no_schema_returns_true(self) -> None:
        reg = SchemaRegistry()
        assert reg.validate({"domain": "unknown", "event_type": "x"}) is True

    def test_validate_instance_mismatch(self) -> None:
        reg = SchemaRegistry()
        reg.register(LLMEvent)
        event = AuditEvent(domain=EventDomain.LLM, event_type="llm_call")
        assert reg.validate(event) is False

    def test_get_versions(self) -> None:
        reg = SchemaRegistry()
        reg.register(AuditEvent)
        versions = reg.get_versions("system", "")
        assert "3.0" in versions

    def test_get_latest_version(self) -> None:
        reg = SchemaRegistry()
        reg.register(AuditEvent)
        assert reg.get_latest_version("system", "") == "3.0"

    def test_get_schema_uri(self) -> None:
        reg = SchemaRegistry()
        reg.register(AuditEvent)
        uri = reg.get_schema_uri("system", "")
        assert uri is not None
        assert "polaris.dev" in uri

    def test_list_registered(self) -> None:
        reg = SchemaRegistry()
        reg.register(AuditEvent)
        items = reg.list_registered()
        assert any(item["domain"] == "system" for item in items)

    def test_increment_event_count(self) -> None:
        reg = SchemaRegistry()
        reg.register(AuditEvent)
        reg.increment_event_count("system", "")
        versions = reg.get_versions("system", "")
        # Internal tracking only; just verify no exception
        assert versions

    def test_compare_versions(self) -> None:
        reg = SchemaRegistry()
        assert reg._compare_versions("1.0", "2.0") == -1
        assert reg._compare_versions("2.0", "1.0") == 1
        assert reg._compare_versions("1.0", "1.0") == 0

    def test_parse_version(self) -> None:
        reg = SchemaRegistry()
        assert reg._parse_version("1.2.3") == (1, 2, 3)
        assert reg._parse_version("2") == (2, 0, 0)


class TestGetSchemaRegistry:
    def test_singleton(self) -> None:
        r1 = get_schema_registry()
        r2 = get_schema_registry()
        assert r1 is r2
