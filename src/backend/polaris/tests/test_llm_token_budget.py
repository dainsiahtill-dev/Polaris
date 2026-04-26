from __future__ import annotations

from polaris.kernelone.llm.engine import ModelCatalog, TokenBudgetManager, estimate_tokens
from polaris.kernelone.llm.engine.contracts import ModelSpec


def test_token_budget_allows_prompt_within_limit() -> None:
    manager = TokenBudgetManager()
    spec = ModelSpec(
        provider_id="test",
        provider_type="openai_compat",
        model="gpt-4o-mini",
        max_context_tokens=8192,
        max_output_tokens=1024,
    )

    decision = manager.enforce("hello " * 200, spec, requested_output_tokens=512)

    assert decision.allowed is True
    assert decision.compression_applied is False
    assert decision.requested_prompt_tokens <= decision.allowed_prompt_tokens


def test_token_budget_compresses_prompt_when_overflow() -> None:
    manager = TokenBudgetManager()
    spec = ModelSpec(
        provider_id="test",
        provider_type="openai_compat",
        model="gpt-4o-mini",
        max_context_tokens=2048,
        max_output_tokens=512,
    )
    long_prompt = "\n".join(f"line-{idx}: {idx} details" for idx in range(8000))

    decision = manager.enforce(long_prompt, spec, requested_output_tokens=512)

    assert decision.allowed is True
    assert decision.compression_applied is True
    assert decision.compression is not None
    assert decision.compression.compressed_tokens <= decision.allowed_prompt_tokens
    assert estimate_tokens(decision.compression.compressed_input) <= decision.allowed_prompt_tokens


def test_model_catalog_resolves_model_specific_limits(tmp_path) -> None:
    catalog = ModelCatalog(workspace=str(tmp_path))
    provider_cfg = {
        "type": "gemini_api",
        "model_specific": {
            "gemini-1.5-pro": {
                "context_window": 2_000_000,
                "max_tokens": 8192,
                "supports_tools": True,
                "supports_json_schema": True,
            }
        },
    }

    spec = catalog.resolve("gemini", "gemini-1.5-pro-latest", provider_cfg=provider_cfg)

    assert spec.max_context_tokens == 2_000_000
    assert spec.max_output_tokens == 8192
    assert spec.supports_tools is True
    assert spec.supports_json_schema is True


def test_model_catalog_prefers_longest_model_prefix(tmp_path) -> None:
    catalog = ModelCatalog(workspace=str(tmp_path))
    provider_cfg = {
        "type": "openai_compat",
        "model_specific": {
            "gpt-4": {
                "context_window": 8192,
                "max_tokens": 2048,
            },
            "gpt-4o": {
                "context_window": 128_000,
                "max_tokens": 16_384,
            },
        },
    }

    spec = catalog.resolve("openai", "gpt-4o-mini", provider_cfg=provider_cfg)

    assert spec.max_context_tokens == 128_000
    assert spec.max_output_tokens == 16_384


def test_model_catalog_matches_repo_prefixed_model_alias(tmp_path) -> None:
    catalog = ModelCatalog(workspace=str(tmp_path))

    spec = catalog.resolve(
        "ollama",
        "modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest",
        provider_cfg={"type": "ollama"},
    )

    assert spec.supports_tools is True
    assert spec.supports_json_schema is True
    assert spec.max_context_tokens == 32_768


def test_model_catalog_loads_config_once_per_resolve(monkeypatch, tmp_path) -> None:
    catalog = ModelCatalog(workspace=str(tmp_path))
    calls = {"count": 0}

    def _fake_load_llm_config(workspace: str, cache_root: str, settings=None) -> dict:
        del workspace, cache_root, settings
        calls["count"] += 1
        return {
            "providers": {
                "openai": {
                    "type": "openai_compat",
                }
            },
            "model_limits": {
                "openai": {
                    "gpt-4o-mini": {
                        "context_window": 64_000,
                        "max_output_tokens": 8_000,
                    }
                }
            },
        }

    monkeypatch.setattr(
        "polaris.kernelone.llm.engine.model_catalog.llm_config.load_llm_config",
        _fake_load_llm_config,
    )

    spec = catalog.resolve("openai", "gpt-4o-mini")

    assert spec.max_context_tokens == 64_000
    assert spec.max_output_tokens == 8_000
    assert calls["count"] == 1
