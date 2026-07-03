"""Architecture fence for the retired KernelOne ProviderManager class."""

from __future__ import annotations

from pathlib import Path

import polaris.cells.llm.provider_runtime as provider_runtime
import polaris.cells.llm.provider_runtime.public as provider_runtime_public
import polaris.cells.llm.provider_runtime.public.service as provider_runtime_service
import polaris.kernelone.llm.providers as providers
import polaris.kernelone.llm.providers.registry as registry

BACKEND_ROOT = Path(__file__).resolve().parents[3]
KERNELONE_PROVIDERS_ROOT = BACKEND_ROOT / "polaris" / "kernelone" / "llm" / "providers"
CELL_PROVIDER_RUNTIME_ROOT = BACKEND_ROOT / "polaris" / "cells" / "llm" / "provider_runtime"


def test_kernelone_provider_manager_class_is_not_exported() -> None:
    """Provider management is owned by infrastructure and reached through get_provider_manager()."""
    assert not hasattr(registry, "ProviderManager")
    assert not hasattr(registry, "provider_manager")
    assert not hasattr(providers, "ProviderManager")
    assert not hasattr(providers, "provider_manager")
    assert "ProviderManager" not in registry.__all__
    assert "provider_manager" not in registry.__all__
    assert "ProviderManager" not in providers.__all__
    assert "provider_manager" not in providers.__all__
    assert hasattr(registry, "get_provider_manager")
    assert hasattr(providers, "get_provider_manager")


def test_provider_runtime_cell_does_not_reexport_provider_manager() -> None:
    """The provider-runtime Cell should not publish a second ProviderManager type source."""
    assert not hasattr(provider_runtime_service, "ProviderManager")
    assert not hasattr(provider_runtime_public, "ProviderManager")
    assert not hasattr(provider_runtime, "ProviderManager")
    assert "ProviderManager" not in provider_runtime_service.__all__
    assert "ProviderManager" not in provider_runtime_public.__all__
    assert "ProviderManager" not in provider_runtime.__all__


def test_sources_do_not_reintroduce_kernelone_provider_manager() -> None:
    """Block reintroducing the removed KernelOne ProviderManager compatibility class/export."""
    offenders: list[str] = []
    for root in (KERNELONE_PROVIDERS_ROOT, CELL_PROVIDER_RUNTIME_ROOT):
        for source_file in sorted(root.rglob("*.py")):
            source = source_file.read_text(encoding="utf-8")
            lines = {line.strip() for line in source.splitlines()}
            if (
                "class ProviderManager:" in lines
                or "class _LazyProviderManager:" in lines
                or "provider_manager: Any" in source
                or '"provider_manager",' in lines
                or "ProviderManager," in lines
                or '"ProviderManager",' in lines
                or "from polaris.kernelone.llm.providers import BaseProvider, ProviderInfo, ProviderManager" in source
            ):
                offenders.append(source_file.relative_to(BACKEND_ROOT).as_posix())

    assert offenders == []
