"""Tests for polaris.kernelone.events.topics."""

from __future__ import annotations

from polaris.kernelone.events.topics import (
    TOPIC_RUNTIME_AUDIT,
    TOPIC_RUNTIME_FINGERPRINT,
    TOPIC_RUNTIME_LLM,
    TOPIC_RUNTIME_STREAM,
    UEP_PERSISTENCE_TOPICS,
    UEP_RUNTIME_TOPICS,
    UEP_SECURE_TOPICS,
    UEP_TOPIC_TO_CATEGORY,
)


class TestTopicConstants:
    def test_runtime_stream(self) -> None:
        assert TOPIC_RUNTIME_STREAM == "runtime.event.stream"

    def test_runtime_llm(self) -> None:
        assert TOPIC_RUNTIME_LLM == "runtime.event.llm"

    def test_runtime_fingerprint(self) -> None:
        assert TOPIC_RUNTIME_FINGERPRINT == "runtime.event.fingerprint"

    def test_runtime_audit(self) -> None:
        assert TOPIC_RUNTIME_AUDIT == "runtime.event.audit"

    def test_uep_runtime_topics(self) -> None:
        assert TOPIC_RUNTIME_STREAM in UEP_RUNTIME_TOPICS
        assert TOPIC_RUNTIME_LLM in UEP_RUNTIME_TOPICS
        assert TOPIC_RUNTIME_FINGERPRINT in UEP_RUNTIME_TOPICS
        assert TOPIC_RUNTIME_AUDIT in UEP_RUNTIME_TOPICS

    def test_uep_persistence_topics(self) -> None:
        assert TOPIC_RUNTIME_STREAM in UEP_PERSISTENCE_TOPICS
        assert TOPIC_RUNTIME_LLM in UEP_PERSISTENCE_TOPICS
        assert TOPIC_RUNTIME_FINGERPRINT in UEP_PERSISTENCE_TOPICS
        assert TOPIC_RUNTIME_AUDIT not in UEP_PERSISTENCE_TOPICS

    def test_uep_secure_topics(self) -> None:
        assert TOPIC_RUNTIME_AUDIT in UEP_SECURE_TOPICS
        assert TOPIC_RUNTIME_STREAM not in UEP_SECURE_TOPICS

    def test_topic_to_category(self) -> None:
        assert UEP_TOPIC_TO_CATEGORY[TOPIC_RUNTIME_STREAM] == "tool"
        assert UEP_TOPIC_TO_CATEGORY[TOPIC_RUNTIME_LLM] == "lifecycle"
        assert UEP_TOPIC_TO_CATEGORY[TOPIC_RUNTIME_FINGERPRINT] == "context"
        assert UEP_TOPIC_TO_CATEGORY[TOPIC_RUNTIME_AUDIT] == "audit"
