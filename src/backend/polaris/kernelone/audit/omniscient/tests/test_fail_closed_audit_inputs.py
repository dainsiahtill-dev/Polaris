"""Optional masking configuration cannot block work; event time stays accurate."""

from datetime import datetime, timezone

import pytest
from polaris.kernelone.audit.omniscient.adapters.sanitization_hook import (
    SanitizationConfig as AuditSanitizationConfig,
    SanitizationHook as AuditSanitizationHook,
)
from polaris.kernelone.audit.omniscient.query_engine import AuditQueryEngine
from polaris.kernelone.audit.omniscient.redaction import SensitiveFieldRedactor
from polaris.kernelone.security.sanitization_hook import SanitizationConfig, SanitizationHook


@pytest.mark.parametrize("factory", [
    lambda pattern: SanitizationHook(SanitizationConfig(patterns=["password", pattern])),
    lambda pattern: AuditSanitizationHook(AuditSanitizationConfig(patterns=["password", pattern])),
    lambda pattern: SensitiveFieldRedactor(extra_sensitive_patterns=[pattern]),
])
def test_optional_invalid_masking_pattern_does_not_stop_usable_redaction(factory) -> None:
    hook = factory("private_example_secret[")
    redact = hook.redact if isinstance(hook, SensitiveFieldRedactor) else hook.sanitize
    assert redact({"password": "secret", "note": "kept"}) == {"password": "[REDACTED]", "note": "kept"}


@pytest.mark.parametrize("timestamp", ["2026-10-11Tinvalid", "2026-10-11 unexpected suffix"])
def test_malformed_full_timestamp_cannot_become_an_apparently_valid_date(timestamp: str) -> None:
    assert AuditQueryEngine._extract_timestamp({"timestamp": timestamp}) is None


@pytest.mark.parametrize("timestamp,want", [
    ("2026-10-11T08:00:00Z", datetime(2026, 10, 11, 8, tzinfo=timezone.utc)),
    ("2026-10-11T16:00:00+08:00", datetime(2026, 10, 11, 8, tzinfo=timezone.utc)),
    ("2026-10-11", datetime(2026, 10, 11, tzinfo=timezone.utc)),
    ("2026-10-11T08:00:00", datetime(2026, 10, 11, 8, tzinfo=timezone.utc)),
    (0, datetime(1970, 1, 1, tzinfo=timezone.utc)),
])
def test_valid_audit_timestamp_forms_preserve_utc_semantics(timestamp, want) -> None:
    assert AuditQueryEngine._extract_timestamp({"timestamp": timestamp}) == want
