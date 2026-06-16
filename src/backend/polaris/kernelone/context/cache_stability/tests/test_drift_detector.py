"""Unit tests for the prefix-drift observer (Headroom T1-B step 1).

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

from polaris.kernelone.context.cache_stability.drift_detector import (
    PrefixDriftObserver,
    PrefixSlice,
    VolatileKind,
    extract_prefix,
    fingerprint_prefix,
    get_prefix_drift_observer,
    scan_volatile_tokens,
)


class TestExtractPrefix:
    def test_system_prompt_plus_leading_system_messages(self) -> None:
        messages = [
            {"role": "system", "content": "frozen system hint"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "should NOT be included (after user)"},
        ]
        prefix = extract_prefix(messages, system_prompt="ROLE PROMPT")
        assert "ROLE PROMPT" in prefix.text
        assert "frozen system hint" in prefix.text
        assert "should NOT be included" not in prefix.text
        # Only the single leading system message is counted (system_prompt is separate).
        assert prefix.message_count == 1
        assert prefix.segment_roles == ("system",)

    def test_stops_at_first_non_system(self) -> None:
        messages = [
            {"role": "user", "content": "first is user"},
            {"role": "system", "content": "later system"},
        ]
        prefix = extract_prefix(messages, system_prompt=None)
        assert prefix.text == ""
        assert prefix.message_count == 0

    def test_handles_none_and_malformed(self) -> None:
        assert extract_prefix(None, None).text == ""
        # Malformed (non-mapping) entry ends the contiguous system prefix.
        prefix = extract_prefix([{"role": "system", "content": "ok"}, "junk"], "P")  # type: ignore[list-item]
        assert "ok" in prefix.text
        assert prefix.message_count == 1

    def test_does_not_mutate_input(self) -> None:
        messages = [{"role": "system", "content": "x"}]
        original = list(messages)
        extract_prefix(messages, "p")
        assert messages == original


class TestFingerprint:
    def test_deterministic(self) -> None:
        prefix = PrefixSlice(text="abc", message_count=1)
        assert fingerprint_prefix(prefix) == fingerprint_prefix(prefix)

    def test_empty_prefix_has_no_fingerprint(self) -> None:
        assert fingerprint_prefix(PrefixSlice(text="", message_count=0)) == ""

    def test_one_byte_change_changes_fingerprint(self) -> None:
        a = fingerprint_prefix(PrefixSlice(text="hello", message_count=1))
        b = fingerprint_prefix(PrefixSlice(text="hellp", message_count=1))
        assert a != b


class TestVolatileScan:
    def test_static_prefix_has_no_findings(self) -> None:
        assert scan_volatile_tokens("You are a helpful assistant. Tools: read, write.") == ()

    def test_detects_uuidv4(self) -> None:
        findings = scan_volatile_tokens("run uuid 550e8400-e29b-41d4-a716-446655440000 here")
        kinds = {f.kind for f in findings}
        assert VolatileKind.UUIDV4 in kinds

    def test_detects_iso8601(self) -> None:
        findings = scan_volatile_tokens("generated at 2026-06-16T12:30:00Z for this turn")
        kinds = {f.kind for f in findings}
        assert VolatileKind.ISO8601_TIMESTAMP in kinds

    def test_detects_run_id_like(self) -> None:
        findings = scan_volatile_tokens("session pm-00001 active")
        kinds = {f.kind for f in findings}
        assert VolatileKind.RUN_ID_LIKE in kinds

    def test_uuid_not_double_reported_as_run_id(self) -> None:
        findings = scan_volatile_tokens("id 550e8400-e29b-41d4-a716-446655440000")
        kinds = [f.kind for f in findings]
        assert VolatileKind.UUIDV4 in kinds
        assert VolatileKind.RUN_ID_LIKE not in kinds

    def test_counts_occurrences(self) -> None:
        findings = scan_volatile_tokens("pm-00001 and run-99999 both here")
        run_findings = [f for f in findings if f.kind is VolatileKind.RUN_ID_LIKE]
        assert run_findings and run_findings[0].count == 2

    def test_empty_text(self) -> None:
        assert scan_volatile_tokens("") == ()


class TestObserver:
    def test_first_observation_is_not_drift(self) -> None:
        obs = PrefixDriftObserver()
        report = obs.observe("s1", PrefixSlice(text="stable", message_count=1))
        assert report.first_seen is True
        assert report.drifted is False
        assert report.previous_fingerprint == ""

    def test_same_prefix_no_drift(self) -> None:
        obs = PrefixDriftObserver()
        prefix = PrefixSlice(text="stable", message_count=1)
        obs.observe("s1", prefix)
        second = obs.observe("s1", prefix)
        assert second.first_seen is False
        assert second.drifted is False

    def test_changed_prefix_drifts(self) -> None:
        obs = PrefixDriftObserver()
        first = obs.observe("s1", PrefixSlice(text="v1", message_count=1))
        second = obs.observe("s1", PrefixSlice(text="v2", message_count=1))
        assert second.drifted is True
        assert second.previous_fingerprint == first.fingerprint

    def test_sessions_are_isolated(self) -> None:
        obs = PrefixDriftObserver()
        obs.observe("s1", PrefixSlice(text="a", message_count=1))
        report = obs.observe("s2", PrefixSlice(text="b", message_count=1))
        # s2's first observation must not see s1's fingerprint as drift.
        assert report.first_seen is True
        assert report.drifted is False

    def test_empty_prefix_is_not_drift(self) -> None:
        obs = PrefixDriftObserver()
        obs.observe("s1", PrefixSlice(text="", message_count=0))
        report = obs.observe("s1", PrefixSlice(text="", message_count=0))
        assert report.drifted is False
        assert report.fingerprint == ""

    def test_reset_clears_state(self) -> None:
        obs = PrefixDriftObserver()
        obs.observe("s1", PrefixSlice(text="x", message_count=1))
        obs.reset()
        report = obs.observe("s1", PrefixSlice(text="x", message_count=1))
        assert report.first_seen is True

    def test_report_carries_volatile_findings(self) -> None:
        obs = PrefixDriftObserver()
        report = obs.observe("s1", PrefixSlice(text="ts 2026-06-16T00:00:00Z", message_count=1))
        assert any(f.kind is VolatileKind.ISO8601_TIMESTAMP for f in report.volatile_findings)

    def test_singleton_is_stable(self) -> None:
        assert get_prefix_drift_observer() is get_prefix_drift_observer()
