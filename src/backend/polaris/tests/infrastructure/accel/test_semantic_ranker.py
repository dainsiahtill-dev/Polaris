"""Tests for accel semantic-ranker configuration normalization."""

from __future__ import annotations

from pathlib import Path

from polaris.infrastructure.accel.semantic_ranker import (
    normalize_semantic_provider,
    probe_semantic_runtime,
)

BACKEND_ROOT = Path(__file__).resolve().parents[4]
SEMANTIC_RANKER_SOURCE = BACKEND_ROOT / "polaris" / "infrastructure" / "accel" / "semantic_ranker.py"


def test_normalize_semantic_provider_accepts_current_tokens() -> None:
    """Semantic ranker config tokens remain explicit and bounded."""
    assert normalize_semantic_provider("off") == "off"
    assert normalize_semantic_provider("AUTO") == "auto"
    assert normalize_semantic_provider("flagembedding") == "flagembedding"
    assert normalize_semantic_provider("unsupported", default_value="auto") == "auto"


def test_probe_semantic_runtime_reports_removed_runtime_without_legacy_language() -> None:
    """The runtime probe is current introspection, not a compatibility layer."""
    probe = probe_semantic_runtime({"runtime": {"semantic_ranker_provider": "auto"}})
    assert probe["provider_requested"] == "auto"
    assert probe["provider_resolved"] == "off"
    assert probe["reason"] == "removed_from_build"


def test_semantic_ranker_source_uses_current_config_token_language() -> None:
    """Provider token normalization should not describe accepted values as old config."""
    source = SEMANTIC_RANKER_SOURCE.read_text(encoding="utf-8").lower()
    assert "legacy " + "tokens" not in source
    assert "backward" + "-compatible" not in source
    assert "accepted configuration tokens" in source
