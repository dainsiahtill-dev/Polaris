"""Tests for SLMCoprocessor — Cognitive Coprocessor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction.slm_coprocessor import (
    SLMCoprocessor,
)


@dataclass
class FakeInvokeResult:
    ok: bool
    output: str
    error: str | None = None


class FakeSLMClient:
    """Deterministic fake SLM client for unit tests."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._next_output: str = ""

    def set_next_output(self, text: str) -> None:
        self._next_output = text

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> FakeInvokeResult:
        self.calls.append((prompt, model, config))
        return FakeInvokeResult(ok=True, output=self._next_output)


class FakeProviderManager:
    """Fake provider manager that returns a preset SLM client."""

    def __init__(self, client: FakeSLMClient | None = None) -> None:
        self._client = client
        self.get_calls: list[str] = []

    def get_provider_instance(self, provider_type: str) -> FakeSLMClient | None:
        self.get_calls.append(provider_type)
        return self._client


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    SLMCoprocessor.reset_default()


class TestSLMCoprocessorEnabled:
    @pytest.fixture
    def coprocessor(self) -> SLMCoprocessor:
        client = FakeSLMClient()
        manager = FakeProviderManager(client)
        config = TransactionConfig(
            slm_enabled=True,
            slm_provider="ollama",
            slm_model_name="glm-4.7-flash:latest",
            slm_base_url="http://120.24.117.59:11434",
        )
        return SLMCoprocessor(config=config, provider_manager=manager)

    async def test_classify_intent_returns_matching_category(self, coprocessor: SLMCoprocessor) -> None:
        manager = coprocessor._provider_manager
        assert isinstance(manager, FakeProviderManager)
        client = manager.get_provider_instance("ollama")
        assert isinstance(client, FakeSLMClient)
        client.set_next_output("DEBUG_AND_FIX")

        result = await coprocessor.classify_intent("帮我修一下这个bug")
        assert result == "DEBUG_AND_FIX"

    async def test_classify_intent_unknown_when_no_match(self, coprocessor: SLMCoprocessor) -> None:
        manager = coprocessor._provider_manager
        client = manager.get_provider_instance("ollama")
        assert isinstance(client, FakeSLMClient)
        client.set_next_output("random gibberish")

        result = await coprocessor.classify_intent("anything")
        assert result == "UNKNOWN"

    async def test_distill_long_logs(self, coprocessor: SLMCoprocessor) -> None:
        manager = coprocessor._provider_manager
        client = manager.get_provider_instance("ollama")
        assert isinstance(client, FakeSLMClient)
        client.set_next_output("核心错误：缺少依赖包 numpy")

        result = await coprocessor.distill_long_logs("very long error log...")
        assert "缺少依赖包 numpy" in result

    async def test_heal_json_valid(self, coprocessor: SLMCoprocessor) -> None:
        manager = coprocessor._provider_manager
        client = manager.get_provider_instance("ollama")
        assert isinstance(client, FakeSLMClient)
        client.set_next_output('{"tool": "read_file", "path": "main.py"}')

        result = await coprocessor.heal_json("{tool: read_file, path: main.py}")
        assert result == {"tool": "read_file", "path": "main.py"}

    async def test_heal_json_invalid_returns_none(self, coprocessor: SLMCoprocessor) -> None:
        manager = coprocessor._provider_manager
        client = manager.get_provider_instance("ollama")
        assert isinstance(client, FakeSLMClient)
        client.set_next_output("still not json")

        result = await coprocessor.heal_json("broken")
        assert result is None

    async def test_expand_search_query(self, coprocessor: SLMCoprocessor) -> None:
        manager = coprocessor._provider_manager
        client = manager.get_provider_instance("ollama")
        assert isinstance(client, FakeSLMClient)
        client.set_next_output("- auth\n- login\n- jwt_token")

        result = await coprocessor.expand_search_query("用户登录鉴权的地方")
        assert result == ["auth", "login", "jwt_token"]

    async def test_expand_search_query_empty_fallback(self, coprocessor: SLMCoprocessor) -> None:
        manager = coprocessor._provider_manager
        client = manager.get_provider_instance("ollama")
        assert isinstance(client, FakeSLMClient)
        client.set_next_output("")

        result = await coprocessor.expand_search_query("login")
        assert result == ["login"]


class TestSLMCoprocessorDisabled:
    async def test_disabled_returns_defaults(self) -> None:
        config = TransactionConfig(slm_enabled=False)
        coprocessor = SLMCoprocessor(config=config, provider_manager=FakeProviderManager())

        assert await coprocessor.classify_intent("anything") == "UNKNOWN"
        assert await coprocessor.distill_long_logs("log") == "log"
        assert await coprocessor.heal_json("broken") is None
        assert await coprocessor.expand_search_query("query") == ["query"]


class TestSLMCoprocessorConfigPropagation:
    async def test_slm_config_uses_custom_base_url(self) -> None:
        client = FakeSLMClient()
        manager = FakeProviderManager(client)
        config = TransactionConfig(
            slm_enabled=True,
            slm_base_url="http://120.24.117.59:11434",
            slm_model_name="glm-4.7-flash:latest",
            slm_timeout=45,
        )
        coprocessor = SLMCoprocessor(config=config, provider_manager=manager)

        client.set_next_output("STRONG_MUTATION")
        await coprocessor.classify_intent("修改代码")

        assert manager.get_calls == ["ollama"]
        _prompt, _model, call_config = client.calls[0]
        assert call_config["base_url"] == "http://120.24.117.59:11434"
        assert call_config["timeout"] == 45

    async def test_intent_embedding_disabled_returns_none(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.intent_embedding_router import (
            IntentEmbeddingRouter,
        )

        config = TransactionConfig(intent_embedding_enabled=False)
        router = IntentEmbeddingRouter(config=config)
        router._centroids = {"TEST": [1.0, 0.0]}  # fake centroids
        result = await router.classify("anything")
        assert result is None

    async def test_intent_embedding_custom_threshold(self) -> None:
        from polaris.cells.roles.kernel.internal.transaction.intent_embedding_router import (
            IntentEmbeddingRouter,
        )

        config = TransactionConfig(intent_embedding_enabled=True, intent_embedding_threshold=0.99)
        router = IntentEmbeddingRouter(config=config)
        assert router._threshold == 0.99
