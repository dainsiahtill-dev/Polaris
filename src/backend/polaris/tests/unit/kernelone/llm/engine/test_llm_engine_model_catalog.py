"""Tests for polaris.kernelone.llm.engine.model_catalog."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from polaris.kernelone.llm.engine.model_catalog import (
    ModelCatalog,
    _iter_longest_prefix_matches,
    _model_key_candidates,
    _normalize_model_key,
    _to_int,
)
from polaris.kernelone.llm.shared_contracts import ModelSpec


class TestToInt:
    def test_positive_int(self) -> None:
        assert _to_int(42) == 42

    def test_positive_string(self) -> None:
        assert _to_int("100") == 100

    def test_zero_returns_none(self) -> None:
        assert _to_int(0) is None

    def test_negative_returns_none(self) -> None:
        assert _to_int(-5) is None

    def test_invalid_string_returns_none(self) -> None:
        assert _to_int("abc") is None

    def test_none_returns_none(self) -> None:
        assert _to_int(None) is None

    def test_float_truncates(self) -> None:
        assert _to_int(42.9) == 42


class TestNormalizeModelKey:
    def test_lowercase(self) -> None:
        assert _normalize_model_key("GPT-4") == "gpt-4"

    def test_strips_whitespace(self) -> None:
        assert _normalize_model_key("  gpt-4  ") == "gpt-4"

    def test_empty(self) -> None:
        assert _normalize_model_key("") == ""

    def test_none(self) -> None:
        assert _normalize_model_key(None) == ""  # type: ignore[arg-type]


class TestModelKeyCandidates:
    def test_empty(self) -> None:
        assert _model_key_candidates("") == []

    def test_simple(self) -> None:
        candidates = _model_key_candidates("gpt-4")
        assert "gpt-4" in candidates

    def test_query_params_stripped(self) -> None:
        candidates = _model_key_candidates("gpt-4?temperature=0.7")
        assert "gpt-4" in candidates
        assert "gpt-4?temperature=0.7" in candidates

    def test_colon_stripped(self) -> None:
        candidates = _model_key_candidates("gpt-4:latest")
        assert "gpt-4" in candidates
        assert "gpt-4:latest" in candidates

    def test_path_tail(self) -> None:
        candidates = _model_key_candidates("openai/gpt-4")
        assert "gpt-4" in candidates
        assert "openai/gpt-4" in candidates

    def test_segments(self) -> None:
        candidates = _model_key_candidates("a/b/c")
        assert "a" in candidates
        assert "b" in candidates
        assert "c" in candidates
        assert "a/b/c" in candidates

    def test_no_duplicates(self) -> None:
        candidates = _model_key_candidates("gpt-4/gpt-4")
        # Should not have duplicates
        assert len(candidates) == len(set(candidates))


class TestIterLongestPrefixMatches:
    def test_sorts_by_length(self) -> None:
        mapping = {"a": 1, "ab": 2, "abc": 3}
        result = _iter_longest_prefix_matches(mapping)
        keys = [k for k, _ in result]
        assert keys == ["abc", "ab", "a"]

    def test_skips_non_string_keys(self) -> None:
        mapping: dict[str | int, int] = {"a": 1, 123: 2, "ab": 3}
        result = _iter_longest_prefix_matches(mapping)  # type: ignore[arg-type]
        keys = [k for k, _ in result]
        assert "a" in keys
        assert "ab" in keys
        assert 123 not in [k for k, _ in result]

    def test_skips_empty_keys(self) -> None:
        mapping = {"a": 1, "": 2, " ": 3}
        result = _iter_longest_prefix_matches(mapping)
        keys = [k for k, _ in result]
        assert "a" in keys
        assert "" not in keys
        assert " " not in keys


class TestModelCatalogInit:
    def test_default_workspace(self) -> None:
        catalog = ModelCatalog("")
        assert catalog.workspace == "."

    def test_custom_workspace(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        assert catalog.workspace == "/tmp/ws"

    def test_ramdisk_root_from_env(self) -> None:
        with patch("polaris.kernelone.llm.engine.model_catalog.resolve_env_str", return_value="/ram"):
            catalog = ModelCatalog("/tmp/ws")
            assert catalog.ramdisk_root == "/ram"

    def test_custom_ramdisk_root(self) -> None:
        catalog = ModelCatalog("/tmp/ws", ramdisk_root="/custom")
        assert catalog.ramdisk_root == "/custom"


class TestModelCatalogResolve:
    def test_resolve_with_provider_cfg(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_context_tokens": 8192,
            "max_output_tokens": 4096,
            "supports_tools": True,
        }
        spec = catalog.resolve("openai", "gpt-4", provider_cfg=provider_cfg)
        assert isinstance(spec, ModelSpec)
        assert spec.provider_id == "openai"
        assert spec.provider_type == "openai"
        assert spec.model == "gpt-4"
        assert spec.max_context_tokens == 8192
        assert spec.max_output_tokens == 4096
        assert spec.supports_tools is True

    def test_resolve_model_specific_overrides(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_context_tokens": 8192,
            "max_output_tokens": 4096,
            "model_specific": {
                "gpt-4": {
                    "max_context_tokens": 32000,
                    "supports_vision": True,
                }
            },
        }
        spec = catalog.resolve("openai", "gpt-4", provider_cfg=provider_cfg)
        assert spec.max_context_tokens == 32000
        assert spec.supports_vision is True

    def test_resolve_missing_context_raises(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_output_tokens": 4096,
        }
        with pytest.raises(ValueError, match="Context window not configured"):
            catalog.resolve("openai", "gpt-4", provider_cfg=provider_cfg)

    def test_resolve_missing_output_limit_raises(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_context_tokens": 8192,
        }
        with pytest.raises(ValueError, match="Output token limit not configured"):
            catalog.resolve("openai", "gpt-4", provider_cfg=provider_cfg)

    def test_resolve_output_capped_by_context(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_context_tokens": 100,
            "max_output_tokens": 200,
        }
        spec = catalog.resolve("openai", "gpt-4", provider_cfg=provider_cfg)
        assert spec.max_output_tokens == 100

    def test_resolve_defaults_false_for_capabilities(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_context_tokens": 8192,
            "max_output_tokens": 4096,
        }
        spec = catalog.resolve("openai", "gpt-4", provider_cfg=provider_cfg)
        assert spec.supports_tools is False
        assert spec.supports_json_schema is False
        assert spec.supports_vision is False

    def test_resolve_tokenizer(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_context_tokens": 8192,
            "max_output_tokens": 4096,
            "tokenizer": "cl100k_base",
        }
        spec = catalog.resolve("openai", "gpt-4", provider_cfg=provider_cfg)
        assert spec.tokenizer == "cl100k_base"

    def test_resolve_model_specific_tokenizer(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_context_tokens": 8192,
            "max_output_tokens": 4096,
            "tokenizer": "cl100k_base",
            "model_specific": {
                "gpt-4": {
                    "tokenizer": "o200k_base",
                }
            },
        }
        spec = catalog.resolve("openai", "gpt-4", provider_cfg=provider_cfg)
        assert spec.tokenizer == "o200k_base"

    def test_resolve_cost_hint(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_context_tokens": 8192,
            "max_output_tokens": 4096,
            "cost_hint": "expensive",
        }
        spec = catalog.resolve("openai", "gpt-4", provider_cfg=provider_cfg)
        assert spec.cost_hint == "expensive"

    def test_resolve_prefix_match(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {
            "type": "openai",
            "max_context_tokens": 8192,
            "max_output_tokens": 4096,
            "model_specific": {
                "gpt-4": {
                    "max_context_tokens": 16000,
                }
            },
        }
        spec = catalog.resolve("openai", "gpt-4o-mini", provider_cfg=provider_cfg)
        # gpt-4o-mini starts with gpt-4, so prefix match should apply
        assert spec.max_context_tokens == 16000


class TestModelCatalogLoadConfig:
    def test_load_provider_cfg(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        payload = {
            "providers": {
                "openai": {
                    "type": "openai",
                    "max_context_tokens": 8192,
                    "max_output_tokens": 4096,
                }
            }
        }
        cfg = catalog._load_provider_cfg("openai", payload)
        assert cfg["type"] == "openai"

    def test_load_provider_cfg_missing(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        payload: dict[str, Any] = {"providers": {}}
        cfg = catalog._load_provider_cfg("openai", payload)
        assert cfg == {}

    def test_load_provider_cfg_non_dict_payload(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        cfg = catalog._load_provider_cfg("openai", None)
        assert cfg == {}

    def test_load_global_model_limits(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        payload: dict[str, Any] = {"model_limits": {"openai": {"gpt-4": {"max_context_tokens": 16000}}}}
        limits = catalog._load_global_model_limits("openai", "gpt-4", payload)
        assert limits == {"max_context_tokens": 16000}

    def test_load_global_model_limits_direct(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        payload: dict[str, Any] = {"model_limits": {"gpt-4": {"max_context_tokens": 16000}}}
        limits = catalog._load_global_model_limits("openai", "gpt-4", payload)
        assert limits == {"max_context_tokens": 16000}

    def test_load_global_model_limits_missing(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        payload: dict[str, Any] = {}
        limits = catalog._load_global_model_limits("openai", "gpt-4", payload)
        assert limits == {}

    def test_extract_model_specific_exact(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {"model_specific": {"gpt-4": {"max_context_tokens": 16000}}}
        result = catalog._extract_model_specific(provider_cfg, "gpt-4")
        assert result == {"max_context_tokens": 16000}

    def test_extract_model_specific_prefix(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {"model_specific": {"gpt-4": {"max_context_tokens": 16000}}}
        result = catalog._extract_model_specific(provider_cfg, "gpt-4o")
        assert result == {"max_context_tokens": 16000}

    def test_extract_model_specific_no_match(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        provider_cfg = {"model_specific": {"claude": {"max_context_tokens": 100000}}}
        result = catalog._extract_model_specific(provider_cfg, "gpt-4")
        assert result == {}

    def test_extract_model_specific_empty(self) -> None:
        catalog = ModelCatalog("/tmp/ws")
        assert catalog._extract_model_specific({}, "gpt-4") == {}
        assert catalog._extract_model_specific(None, "gpt-4") == {}  # type: ignore[arg-type]
