"""Tests for the roles.kernel public SLM binding producer.

Covers the contract the owner Cell must honour so the KernelOne foundation
can consume the SLM capability without importing a business Cell:

- the KernelOne side stays free of Cell imports (cold import check);
- an explicit configuration is forwarded **completely** (no field is
  projected away, no field is defaulted);
- an already existing gateway object keeps its identity;
- the producer really produces a gateway that can compress;
- disabled / unconfigured / failing paths stay explicit.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
    CognitiveGateway,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.cells.roles.kernel.internal.transaction.slm_coprocessor import (
    SLMCoprocessor,
)
from polaris.cells.roles.kernel.public.slm_binding import (
    RoleKernelSLMBinding,
    build_slm_binding,
)
from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummarizerGatewayProtocol,
)
from polaris.kernelone.context.context_os.summarizers.slm import SLMSummarizer
from polaris.kernelone.context.context_os.summarizers.slm_config import (
    reset_default_slm_binding,
    set_default_slm_binding,
)

BACKEND_ROOT = "/home/dains/Documents/polaris/src/backend"

CUSTOM_CONFIG = TransactionConfig(
    slm_enabled=True,
    slm_provider="ollama",
    slm_model_name="custom-model:1b",
    slm_base_url="http://127.0.0.1:9999",
    slm_keep_alive="30m",
    slm_timeout=77,
)


@pytest.fixture(autouse=True)
def _clear_installed_binding():
    """Keep the process-wide SLM binding out of these tests."""
    reset_default_slm_binding()
    yield
    reset_default_slm_binding()


class TestColdImportBoundary:
    """The KernelOne foundation must not import a business Cell."""

    def test_kernelone_summarizer_cold_import_loads_no_business_cell(self) -> None:
        """Importing the K1 summarizer must not pull in polaris.cells.*."""
        probe = (
            "import sys\n"
            f"sys.path.insert(0, {BACKEND_ROOT!r})\n"
            "import polaris.kernelone.context.context_os.summarizers.slm  # noqa: F401\n"
            "cells = [m for m in sys.modules if m.startswith('polaris.cells.')]\n"
            "print('CELLS', len(cells))\n"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", probe],
            capture_output=True,
            text=True,
            check=False,
            cwd=BACKEND_ROOT,
        )
        assert result.returncode == 0, result.stderr
        assert "CELLS 0" in result.stdout, result.stdout

    def test_kernelone_summarizer_source_has_no_cell_import(self) -> None:
        """Static check: the summarizer package must not import polaris.cells.*."""
        import ast
        import pathlib

        pkg = pathlib.Path(BACKEND_ROOT) / "polaris/kernelone/context/context_os/summarizers"
        offenders: list[str] = []
        for path in sorted(pkg.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                lineno = getattr(node, "lineno", 0)
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    if node.level:
                        continue
                    modules = [node.module or ""]
                for module in modules:
                    if module.startswith("polaris.cells."):
                        offenders.append(f"{path.name}:{lineno}: {module}")
        assert offenders == []


class TestBindingProducer:
    """The producer must expose the owner's own configuration object."""

    def test_build_returns_a_binding_port_implementation(self) -> None:
        """The produced object satisfies the KernelOne SLMBinding port."""
        binding = build_slm_binding()
        assert isinstance(binding, RoleKernelSLMBinding)
        assert callable(binding.config)
        assert callable(binding.gateway)

    def test_default_binding_uses_owner_default_config(self) -> None:
        """Without an explicit config the owner's own default is used."""
        binding = build_slm_binding()
        config = binding.config()
        assert isinstance(config, TransactionConfig)
        # defaults must come from the owner, not be re-declared here
        assert config.slm_model_name == TransactionConfig().slm_model_name
        assert config.slm_provider == TransactionConfig().slm_provider

    def test_explicit_config_is_kept_by_identity(self) -> None:
        """An explicit configuration is stored unchanged (same object)."""
        binding = build_slm_binding(CUSTOM_CONFIG)
        assert binding.config() is CUSTOM_CONFIG

    def test_binding_satisfies_the_kernelone_gateway_protocol(self) -> None:
        """The produced gateway satisfies the KernelOne capability protocol."""
        binding = build_slm_binding()
        gateway = binding.gateway()
        assert isinstance(gateway, SummarizerGatewayProtocol)
        assert hasattr(gateway, "is_slm_healthy")
        assert hasattr(gateway, "compress_text")


class TestExplicitConfigPropagation:
    """A caller-supplied configuration must reach the gateway unharmed."""

    def test_gateway_is_built_for_the_explicit_configuration(self) -> None:
        """The gateway carries the exact configuration object it was built for."""
        binding = build_slm_binding()
        gateway = binding.gateway(CUSTOM_CONFIG)
        assert isinstance(gateway, CognitiveGateway)
        assert gateway.config is CUSTOM_CONFIG

    def test_no_field_is_lost_or_defaulted(self) -> None:
        """base_url / provider / model / keep_alive / timeout survive intact."""
        binding = build_slm_binding()
        gateway = binding.gateway(CUSTOM_CONFIG)
        assert isinstance(gateway, CognitiveGateway)
        config = gateway.config
        assert config.slm_base_url == "http://127.0.0.1:9999"
        assert config.slm_provider == "ollama"
        assert config.slm_model_name == "custom-model:1b"
        assert config.slm_keep_alive == "30m"
        assert config.slm_timeout == 77
        assert config.slm_enabled is True

    def test_coprocessor_is_built_for_the_explicit_configuration(self) -> None:
        """The SLM coprocessor also receives the explicit configuration."""
        binding = build_slm_binding()
        gateway = binding.gateway(CUSTOM_CONFIG)
        assert isinstance(gateway, CognitiveGateway)
        coprocessor = gateway._slm_coprocessor
        assert isinstance(coprocessor, SLMCoprocessor)
        assert coprocessor.config is CUSTOM_CONFIG

    def test_binding_own_config_is_used_when_no_override(self) -> None:
        """Without an override the binding's own configuration is used."""
        binding = build_slm_binding(CUSTOM_CONFIG)
        gateway = binding.gateway()
        assert isinstance(gateway, CognitiveGateway)
        assert gateway.config is CUSTOM_CONFIG

    def test_non_transaction_config_is_rejected(self) -> None:
        """A foreign configuration type is refused instead of silently used."""

        class ForeignConfig:
            """A configuration shape that is not the owner's TransactionConfig."""

            slm_enabled = True
            slm_provider = "ollama"
            slm_model_name = "foreign-model"
            slm_keep_alive = "5m"

        binding = build_slm_binding()
        with pytest.raises(TypeError):
            binding.gateway(ForeignConfig())


class TestGatewayIdentity:
    """The producer must not create a second gateway singleton."""

    def test_warmed_singleton_is_reused_when_configuration_matches(self) -> None:
        """A warmed gateway built for the same configuration keeps its identity."""
        warmed = CognitiveGateway(config=CUSTOM_CONFIG, slm_coprocessor=SLMCoprocessor(config=CUSTOM_CONFIG))
        original = CognitiveGateway._default_instance
        CognitiveGateway._default_instance = warmed
        try:
            binding = build_slm_binding(CUSTOM_CONFIG)
            assert binding.gateway() is warmed
        finally:
            CognitiveGateway._default_instance = original

    def test_warmed_singleton_with_foreign_configuration_is_not_reused(self) -> None:
        """A warmed gateway built for a DIFFERENT configuration is not reused.

        The explicit configuration must actually govern the gateway that is
        returned; silently handing back the warmed instance would discard
        the requested model / base URL / provider / keep-alive / timeout.
        """
        warm_config = TransactionConfig(
            slm_enabled=True,
            slm_provider="ollama",
            slm_model_name="warm-model",
            slm_base_url="http://127.0.0.1:11434",
            slm_keep_alive="5m",
            slm_timeout=30,
        )
        warmed = CognitiveGateway(config=warm_config, slm_coprocessor=SLMCoprocessor(config=warm_config))
        original = CognitiveGateway._default_instance
        CognitiveGateway._default_instance = warmed
        try:
            binding = build_slm_binding(CUSTOM_CONFIG)
            gateway = binding.gateway(CUSTOM_CONFIG)
        finally:
            CognitiveGateway._default_instance = original

        assert gateway is not warmed
        assert gateway.config is CUSTOM_CONFIG
        assert gateway.config.slm_model_name == "custom-model:1b"
        assert gateway.config.slm_base_url == "http://127.0.0.1:9999"
        assert gateway.config.slm_keep_alive == "30m"
        assert gateway.config.slm_timeout == 77

    def test_non_gateway_singleton_is_not_reused(self) -> None:
        """A foreign object in the singleton slot is ignored (no mock leakage)."""

        class NotAGateway:
            pass

        original = CognitiveGateway._default_instance
        CognitiveGateway._default_instance = NotAGateway()  # type: ignore[assignment]
        try:
            binding = build_slm_binding()
            gateway = binding.gateway()
            assert isinstance(gateway, CognitiveGateway)
            assert gateway is not CognitiveGateway._default_instance
        finally:
            CognitiveGateway._default_instance = original

    def test_two_calls_return_the_same_warmed_gateway(self) -> None:
        """Repeated calls reuse the warmed singleton instead of rebuilding it."""
        warmed = CognitiveGateway(config=CUSTOM_CONFIG, slm_coprocessor=SLMCoprocessor(config=CUSTOM_CONFIG))
        original = CognitiveGateway._default_instance
        CognitiveGateway._default_instance = warmed
        try:
            binding = build_slm_binding(CUSTOM_CONFIG)
            assert binding.gateway() is binding.gateway() is warmed
        finally:
            CognitiveGateway._default_instance = original


class TestProducerToGatewayCompressionPath:
    """The producer must really drive the compression path."""

    def test_summarizer_uses_the_producer_gateway(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An installed producer binding is used by the summarizer end to end."""

        class SyntheticSLMTransport:
            """Explicit synthetic SLM transport (NOT a real model endpoint)."""

            def __init__(self) -> None:
                self.prompts: list[str] = []
                self.max_tokens: list[int] = []

            async def is_slm_healthy(self) -> bool:
                return True

            async def compress_text(self, prompt: str, *, max_tokens: int) -> str:
                self.prompts.append(prompt)
                self.max_tokens.append(max_tokens)
                return "Synthetic compressed summary of the conversation history."

        transport = SyntheticSLMTransport()

        class SyntheticBinding:
            def __init__(self) -> None:
                self._config = CUSTOM_CONFIG

            def config(self) -> Any:
                return self._config

            def gateway(self, config: Any | None = None) -> Any:
                return transport

        set_default_slm_binding(SyntheticBinding())
        try:
            summarizer = SLMSummarizer()
            assert summarizer.is_available() is True
            result = summarizer.summarize("A" * 500, max_tokens=64)
        finally:
            reset_default_slm_binding()

        assert result == "Synthetic compressed summary of the conversation history."
        assert len(transport.prompts) == 1
        assert transport.max_tokens == [64]

    def test_real_producer_gateway_reaches_compress_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The real producer gateway is the one that receives compress_text."""
        calls: list[str] = []

        async def fake_compress(self: Any, prompt: str, max_tokens: int = 500) -> str:
            calls.append(prompt)
            return "Synthetic compressed summary produced by the owner gateway."

        async def fake_healthy(self: Any) -> bool:
            return True

        monkeypatch.setattr(CognitiveGateway, "compress_text", fake_compress, raising=True)
        monkeypatch.setattr(CognitiveGateway, "is_slm_healthy", fake_healthy, raising=True)

        original = CognitiveGateway._default_instance
        CognitiveGateway._default_instance = None
        try:
            binding = build_slm_binding(CUSTOM_CONFIG)
            set_default_slm_binding(binding)
            try:
                summarizer = SLMSummarizer()
                result = summarizer.summarize("B" * 500, max_tokens=64)
            finally:
                reset_default_slm_binding()
        finally:
            CognitiveGateway._default_instance = original

        assert result == "Synthetic compressed summary produced by the owner gateway."
        assert len(calls) == 1


class TestDisabledAndFailureSemantics:
    """Disabled / unconfigured / failing paths must stay explicit."""

    def test_disabled_config_is_reported_as_unavailable(self) -> None:
        """A disabled configuration makes the summarizer unavailable."""
        binding = build_slm_binding(TransactionConfig(slm_enabled=False))
        set_default_slm_binding(binding)
        try:
            summarizer = SLMSummarizer()
            assert summarizer.is_available() is False
            assert summarizer.get_compression_stats()["slm_enabled"] is False
        finally:
            reset_default_slm_binding()

    def test_unconfigured_summarizer_raises_instead_of_truncating(self) -> None:
        """Without a binding the summarizer fails explicitly."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        summarizer = SLMSummarizer()
        with pytest.raises(SummarizationError):
            summarizer.summarize("C" * 500, max_tokens=64)

    def test_failing_gateway_surfaces_as_summarization_error(self) -> None:
        """A gateway failure is reported, never swallowed into a fake summary."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        class FailingGateway:
            async def is_slm_healthy(self) -> bool:
                return True

            async def compress_text(self, prompt: str, *, max_tokens: int) -> str:
                raise ConnectionError("synthetic transport failure")

        class FailingBinding:
            def config(self) -> Any:
                return CUSTOM_CONFIG

            def gateway(self, config: Any | None = None) -> Any:
                return FailingGateway()

        set_default_slm_binding(FailingBinding())
        try:
            summarizer = SLMSummarizer()
            with pytest.raises(SummarizationError):
                summarizer.summarize("D" * 500, max_tokens=64)
        finally:
            reset_default_slm_binding()

    def test_empty_compression_result_is_reported_as_failure(self) -> None:
        """An empty compression result is a failure, not an empty summary."""
        from polaris.kernelone.context.context_os.summarizers.contracts import (
            SummarizationError,
        )

        class EmptyGateway:
            async def is_slm_healthy(self) -> bool:
                return True

            async def compress_text(self, prompt: str, *, max_tokens: int) -> str:
                return ""

        class EmptyBinding:
            def config(self) -> Any:
                return CUSTOM_CONFIG

            def gateway(self, config: Any | None = None) -> Any:
                return EmptyGateway()

        set_default_slm_binding(EmptyBinding())
        try:
            summarizer = SLMSummarizer()
            with pytest.raises(SummarizationError):
                summarizer.summarize("E" * 500, max_tokens=64)
        finally:
            reset_default_slm_binding()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
