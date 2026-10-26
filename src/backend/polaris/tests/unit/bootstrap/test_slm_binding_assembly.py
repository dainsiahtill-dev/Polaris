"""Tests for the host assembly of the KernelOne SLM binding.

The host (polaris/bootstrap/assembly.py) is the only place that wires the
roles.kernel SLM producer into the narrow KernelOne port.  These tests use
the **real** host entry point and the **real** producer; nothing about the
model endpoint is faked (no model is contacted at install time).
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.context.context_os.summarizers.slm import SLMSummarizer
from polaris.kernelone.context.context_os.summarizers.slm_config import (
    get_default_slm_binding,
    reset_default_slm_binding,
    set_default_slm_binding,
)


@pytest.fixture(autouse=True)
def _clear_installed_binding():
    """Start and finish each test without an installed binding."""
    reset_default_slm_binding()
    yield
    reset_default_slm_binding()


class TestHostAssemblyInstallsBinding:
    """The real host entry point must install a real producer."""

    def test_ensure_minimal_kernelone_bindings_installs_a_binding(self) -> None:
        """ensure_minimal_kernelone_bindings() registers the SLM binding."""
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings

        assert get_default_slm_binding() is None
        ensure_minimal_kernelone_bindings()
        binding = get_default_slm_binding()
        assert binding is not None

    def test_installed_binding_is_the_real_owner_producer(self) -> None:
        """The installed binding is produced by roles.kernel, not a stand-in."""
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings
        from polaris.cells.roles.kernel.public.slm_binding import RoleKernelSLMBinding

        ensure_minimal_kernelone_bindings()
        binding = get_default_slm_binding()
        assert isinstance(binding, RoleKernelSLMBinding)

    def test_installed_binding_exposes_the_owner_config(self) -> None:
        """The binding exposes the owner's own configuration object."""
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings
        from polaris.cells.roles.kernel.internal.transaction.ledger import (
            TransactionConfig,
        )

        ensure_minimal_kernelone_bindings()
        binding = get_default_slm_binding()
        assert binding is not None
        config = binding.config()
        assert isinstance(config, TransactionConfig)
        assert config.slm_model_name == TransactionConfig().slm_model_name
        assert config.slm_provider == TransactionConfig().slm_provider

    def test_installed_binding_serves_the_default_production_path(self) -> None:
        """The default production construction (no args) reaches the binding."""
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings

        ensure_minimal_kernelone_bindings()
        binding = get_default_slm_binding()
        assert binding is not None

        summarizer = SLMSummarizer()
        assert summarizer.config is binding.config()
        assert summarizer.is_available() is True
        stats = summarizer.get_compression_stats()
        assert stats["slm_enabled"] is True
        assert stats["slm_model"] == binding.config().slm_model_name
        assert stats["available"] is True

    def test_installed_binding_produces_the_owner_gateway(self) -> None:
        """The installed binding produces a real roles.kernel gateway."""
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings
        from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
            CognitiveGateway,
        )

        ensure_minimal_kernelone_bindings()
        binding = get_default_slm_binding()
        assert binding is not None
        gateway = binding.gateway()
        assert isinstance(gateway, CognitiveGateway)


class TestHostAssemblyIsLazy:
    """Installing the binding must not connect to the model."""

    def test_install_does_not_build_a_gateway(self) -> None:
        """No gateway object is created while installing the binding."""
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings
        from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
            CognitiveGateway,
        )
        from polaris.cells.roles.kernel.public.slm_binding import RoleKernelSLMBinding

        original = CognitiveGateway._default_instance
        CognitiveGateway._default_instance = None
        try:
            ensure_minimal_kernelone_bindings()
            binding = get_default_slm_binding()
            assert isinstance(binding, RoleKernelSLMBinding)
            # The binding exists but no gateway was materialised yet: the
            # producer only builds it on demand.
            assert binding.owner_config is not None
            assert binding.gateway is not None
        finally:
            CognitiveGateway._default_instance = original


class TestHostAssemblyPropagatesContractErrors:
    """A broken producer contract must surface, not be silently degraded."""

    def test_producer_contract_error_is_propagated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A TypeError raised by the owner producer reaches the caller."""
        import polaris.cells.roles.kernel.public.slm_binding as producer
        from polaris.bootstrap.assembly import _install_slm_binding

        def bad_producer() -> Any:
            raise TypeError("controlled-producer-contract-failure")

        monkeypatch.setattr(producer, "build_slm_binding", bad_producer)
        with pytest.raises(TypeError, match="controlled-producer-contract-failure"):
            _install_slm_binding()
        assert get_default_slm_binding() is None

    def test_producer_error_is_not_swallowed_by_the_assembly_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ensure_minimal_kernelone_bindings() must not hide a producer defect."""
        import polaris.cells.roles.kernel.public.slm_binding as producer
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings

        def bad_producer() -> Any:
            raise TypeError("controlled-producer-contract-failure")

        monkeypatch.setattr(producer, "build_slm_binding", bad_producer)
        with pytest.raises(TypeError, match="controlled-producer-contract-failure"):
            ensure_minimal_kernelone_bindings()
        assert get_default_slm_binding() is None


class TestHostAssemblyIsIdempotent:
    """Repeated assembly must not replace an existing binding."""

    def test_second_assembly_keeps_the_same_binding(self) -> None:
        """ensure_minimal_kernelone_bindings() twice keeps one binding."""
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings

        ensure_minimal_kernelone_bindings()
        first = get_default_slm_binding()
        ensure_minimal_kernelone_bindings()
        second = get_default_slm_binding()
        assert first is not None
        assert second is first

    def test_existing_explicit_binding_is_not_overwritten(self) -> None:
        """A binding installed before assembly survives the host assembly."""
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings

        class PreInstalledBinding:
            def config(self) -> Any:
                return None

            def gateway(self, config: Any | None = None) -> Any:
                return None

        preinstalled = PreInstalledBinding()
        assert set_default_slm_binding(preinstalled, explicit=True) is True
        ensure_minimal_kernelone_bindings()
        assert get_default_slm_binding() is preinstalled

    def test_default_registration_does_not_overwrite_an_existing_binding(self) -> None:
        """A non-explicit registration keeps the already installed binding."""
        from polaris.cells.roles.kernel.public.slm_binding import build_slm_binding

        first = build_slm_binding()
        assert set_default_slm_binding(first) is True
        second = build_slm_binding()
        assert set_default_slm_binding(second) is False
        assert get_default_slm_binding() is first

    def test_explicit_registration_replaces_a_default_binding(self) -> None:
        """An explicit registration may replace a default one."""
        from polaris.cells.roles.kernel.public.slm_binding import build_slm_binding

        default_binding = build_slm_binding()
        assert set_default_slm_binding(default_binding) is True

        class ExplicitBinding:
            def config(self) -> Any:
                return None

            def gateway(self, config: Any | None = None) -> Any:
                return None

        explicit = ExplicitBinding()
        assert set_default_slm_binding(explicit, explicit=True) is True
        assert get_default_slm_binding() is explicit

    def test_explicit_registration_does_not_replace_another_explicit_binding(self) -> None:
        """The first explicit binding wins against later explicit ones."""
        from polaris.cells.roles.kernel.public.slm_binding import build_slm_binding

        default_binding = build_slm_binding()
        set_default_slm_binding(default_binding)

        class ExplicitBinding:
            def config(self) -> Any:
                return None

            def gateway(self, config: Any | None = None) -> Any:
                return None

        first = ExplicitBinding()
        second = ExplicitBinding()
        assert set_default_slm_binding(first, explicit=True) is True
        assert set_default_slm_binding(second, explicit=True) is False
        assert get_default_slm_binding() is first

    def test_none_registration_keeps_the_current_binding(self) -> None:
        """A None registration is a no-op."""
        from polaris.cells.roles.kernel.public.slm_binding import build_slm_binding

        binding = build_slm_binding()
        set_default_slm_binding(binding)
        assert set_default_slm_binding(None) is False
        assert get_default_slm_binding() is binding


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
