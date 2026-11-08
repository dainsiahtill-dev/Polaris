"""Runtime behavior behind engineering bootstrap's static type boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest
from polaris.bootstrap.config import Settings
from polaris.bootstrap.engineering_runtime import _prepare_model_runtime
from polaris.cells.roles.runtime.public.contracts.engineering_run import PermissionScope
from polaris.infrastructure.llm.provider_bootstrap import ProviderAdapter, inject_kernelone_provider_runtime
from polaris.infrastructure.llm.providers.provider_registry import provider_manager
from polaris.kernelone.llm import config_store
from polaris.kernelone.llm.providers.provider_injection import (
    get_provider_manager_port,
    reset_provider_manager_port,
    set_provider_manager_port,
)
from polaris.kernelone.llm.toolkit.contracts import ServiceLocator
from polaris.kernelone.ports.provider_registry import IProviderRegistryPort


class _StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "invalid", [None, True, 1, "", "READ_ONLY", "read-write", [], {}, b"read_only", _StringSubclass("read_only")]
)
def test_command_cache_runtime_guard_keeps_exact_string_boundary(invalid: Any) -> None:
    with pytest.raises(ValueError, match="command cache mode"):
        PermissionScope((), (), frozenset(), False, command_cache=invalid)


@pytest.mark.parametrize("mode", ["disabled", "read_only", "read_write"])
def test_command_cache_literal_values_remain_accepted(mode: Literal["disabled", "read_only", "read_write"]) -> None:
    assert PermissionScope((), (), frozenset(), False, command_cache=mode).command_cache == mode


@pytest.mark.parametrize("bootstrap_path", ["engineering", "assembly"])
def test_bootstrap_wires_registry_and_generation_to_their_distinct_ports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bootstrap_path: str
) -> None:
    previous_registry = get_provider_manager_port()
    previous_runtime = ServiceLocator.get_provider()
    # Config I/O is outside the injection behavior; avoid touching host settings.
    loaded: list[tuple[str, str]] = []

    def load_local(workspace: str, cache_root: str, *, settings: Settings) -> dict[str, Any]:
        loaded.append((workspace, cache_root))
        return {}

    monkeypatch.setattr(config_store, "load_llm_config", load_local)
    try:
        if bootstrap_path == "engineering":
            _prepare_model_runtime(Settings(workspace=tmp_path), tmp_path)
        else:
            from polaris.bootstrap.assembly import _inject_provider_manager_port

            inject_kernelone_provider_runtime()
            _inject_provider_manager_port()
        registry = get_provider_manager_port()
        assert isinstance(registry, IProviderRegistryPort)
        assert registry is provider_manager
        assert registry.list_provider_types() == provider_manager.list_provider_types()
        for provider_type in registry.list_provider_types():
            assert registry.get_provider_class(provider_type) is provider_manager.get_provider_class(provider_type)
        assert registry.get_provider_for_config({"type": "openai_compat"}) == "openai_compat"
        assert isinstance(registry.get_provider_default_config("openai_compat"), dict)
        assert registry.validate_provider_config("nonexistent-test-provider", {}) is False
        runtime: object = ServiceLocator.get_provider()
        assert isinstance(runtime, ProviderAdapter)
        assert runtime.manager is registry
        expected_loads = (
            [(str(tmp_path), str(tmp_path / ".polaris" / "runtime"))] if bootstrap_path == "engineering" else []
        )
        assert loaded == expected_loads
    finally:
        ServiceLocator._provider = previous_runtime
        if previous_registry is None:
            reset_provider_manager_port()
        else:
            set_provider_manager_port(previous_registry)


def test_registered_storage_adapter_preserves_all_values_and_readonly_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import FrozenInstanceError, asdict, fields

    from polaris.bootstrap import assembly
    from polaris.cells.storage.layout.public.service import resolve_polaris_roots
    from polaris.kernelone.storage import layout

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "host"))
    monkeypatch.setattr(layout, "_business_roots_resolver", layout._business_roots_resolver)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = resolve_polaris_roots
    observed: list[object] = []

    def watched(workspace_abs: str, ramdisk_root: str | None = None) -> Any:
        roots = original(workspace_abs, ramdisk_root)
        observed.append(roots)
        return roots

    monkeypatch.setattr(assembly, "resolve_polaris_roots", watched)
    layout.clear_storage_roots_cache()
    try:
        assembly.ensure_minimal_kernelone_bindings()
        resolver = layout._business_roots_resolver
        assert resolver is not None
        result = resolver(str(workspace), None)
        assert len(observed) == 1
        assert isinstance(result, layout.StorageRoots)
        expected = {field.name: getattr(observed[0], field.name) for field in fields(layout.StorageRoots)}
        assert len(expected) == 16 and all(type(value) is str for value in expected.values())
        assert asdict(result) == expected
        assert result.workspace_abs == str(workspace)
        assert result.runtime_root == str(workspace / ".polaris" / "runtime")
        assert result.config_root == str(tmp_path / "host" / "config")
        for field in fields(layout.StorageRoots):
            with pytest.raises(FrozenInstanceError):
                setattr(result, field.name, "changed")

        # The existing public read-only resolver must neither call the writer
        # resolver nor create absent project/control paths.
        missing = tmp_path / "missing"
        before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
        readonly = layout.resolve_storage_roots_read_only(str(missing))
        assert readonly.runtime_root == str(missing / ".polaris" / "runtime")
        assert len(observed) == 1
        assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before
    finally:
        layout.clear_storage_roots_cache()
