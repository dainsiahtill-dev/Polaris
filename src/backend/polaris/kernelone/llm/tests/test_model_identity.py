"""Tests for LLM model identity normalization."""

from polaris.kernelone.llm.model_identity import model_identity_aliases, model_identity_equal, model_identity_key


def test_model_identity_key_normalizes_common_separator_variants() -> None:
    assert model_identity_key(" Qwen3 Max ") == "qwen3-max"
    assert model_identity_key("qwen3_max") == "qwen3-max"
    assert model_identity_key("QWEN3---MAX") == "qwen3-max"


def test_model_identity_equal_preserves_version_dots() -> None:
    assert model_identity_equal("Qwen3 Max", "qwen3-max")
    assert not model_identity_equal("gpt-4.1", "gpt-41")


def test_model_identity_equal_matches_provider_qualified_model_name() -> None:
    assert model_identity_aliases("qwen/qwen3-max") == {"qwen-qwen3-max", "qwen3-max"}
    assert model_identity_equal("qwen/qwen3-max", "Qwen3-Max")
    assert model_identity_equal("modelscope.cn/qwen/Qwen3_Max", "qwen3 max")
    assert not model_identity_equal("qwen/qwen3-max-preview", "Qwen3-Max")
