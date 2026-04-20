"""Tests for layout.py: no Cell internal import, injected resolver works.

Verifies that:
1. Importing polaris.kernelone.storage.layout does NOT import anything from
   polaris.cells (regression guard for the P0-1 fix).
2. register_business_roots_resolver() correctly replaces the direct Cell import.
3. resolve_storage_roots() uses the registered resolver when available.
4. resolve_storage_roots() falls back to generic resolution when no resolver
   is registered or when the resolver returns None.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

# ─── Test 1: No Cell internal import ──────────────────────────────────────────


def test_layout_no_cell_internal_import() -> None:
    """polaris.kernelone.storage.layout must not import from polaris.cells.

    This is the architectural invariant: KernelOne must never depend on Cell
    internal modules.
    """
    # Force a clean import so we inspect the live module, not a cached one.
    mod_name = "polaris.kernelone.storage.layout"
    module = importlib.import_module(mod_name)

    # Collect all modules that were loaded as a side-effect of importing layout.
    # None of those should have been loaded *because of* the layout import.
    # We check the module's source attributes rather than sys.modules because
    # other tests may already have loaded cell modules for their own reasons.
    # The definitive check: layout's source text must not contain the forbidden import.
    source_file = getattr(module, "__file__", None)
    assert source_file is not None
    with open(source_file, encoding="utf-8") as fh:
        source = fh.read()

    # The old direct import that must be absent:
    assert "from polaris.cells.storage.layout.internal" not in source, (
        "layout.py must not contain a direct import from polaris.cells.storage.layout.internal. "
        "Use register_business_roots_resolver() instead."
    )
    assert "import polaris.cells" not in source, "layout.py must not directly import polaris.cells modules."


# ─── Test 2: Resolver registration and delegation ─────────────────────────────


def test_register_business_roots_resolver_is_called(tmp_path: Path) -> None:
    """resolve_storage_roots() must call the registered resolver."""
    from polaris.kernelone._runtime_config import (
        get_workspace_metadata_dir_name,
        set_workspace_metadata_dir_name,
    )
    from polaris.kernelone.storage.layout import (
        StorageRoots,
        clear_business_roots_resolver,
        clear_storage_roots_cache,
        register_business_roots_resolver,
        workspace_key,
    )

    ws = tmp_path / "myworkspace"
    ws.mkdir()

    # Build a minimal but valid StorageRoots to return from our fake resolver.
    def fake_resolver(workspace_abs: str, ramdisk_root: str | None) -> StorageRoots:
        key = workspace_key(workspace_abs)
        return StorageRoots(
            workspace_abs=workspace_abs,
            workspace_key=key,
            storage_layout_mode="injected",
            home_root="/injected/home",
            global_root="/injected/home",
            config_root="/injected/home/config",
            projects_root=str(ws / ".polaris"),
            project_root=str(ws / ".polaris"),
            project_persistent_root=str(ws / ".polaris"),
            runtime_projects_root="/injected/runtime/projects",
            runtime_project_root="/injected/runtime/projects/" + key + "/runtime",
            workspace_persistent_root=str(ws / ".polaris"),
            runtime_base="/injected/runtime",
            runtime_root="/injected/runtime/projects/" + key + "/runtime",
            runtime_mode="injected",
            history_root=str(ws / ".polaris" / "history"),
        )

    original_meta = get_workspace_metadata_dir_name()
    set_workspace_metadata_dir_name(".polaris")
    register_business_roots_resolver(fake_resolver)
    clear_storage_roots_cache()
    try:
        from polaris.kernelone.storage.layout import resolve_storage_roots

        roots = resolve_storage_roots(str(ws))
        assert roots.storage_layout_mode == "injected", "resolve_storage_roots() must use the registered resolver"
        assert roots.config_root == "/injected/home/config"
    finally:
        clear_business_roots_resolver()
        clear_storage_roots_cache()
        set_workspace_metadata_dir_name(original_meta)


def test_resolver_none_return_falls_through_to_generic(tmp_path: Path) -> None:
    """If the resolver returns None, generic resolution must be used."""
    from polaris.kernelone.storage.layout import (
        clear_business_roots_resolver,
        clear_storage_roots_cache,
        register_business_roots_resolver,
        resolve_storage_roots,
    )

    ws = tmp_path / "myworkspace"
    ws.mkdir()

    register_business_roots_resolver(lambda _ws, _rd: None)
    clear_storage_roots_cache()
    try:
        roots = resolve_storage_roots(str(ws))
        # Generic mode is "project_local"
        assert roots.storage_layout_mode == "project_local"
    finally:
        clear_business_roots_resolver()
        clear_storage_roots_cache()


def test_no_resolver_uses_generic_resolution(tmp_path: Path) -> None:
    """Without a registered resolver, generic resolution must be used."""
    from polaris.kernelone.storage.layout import (
        clear_business_roots_resolver,
        clear_storage_roots_cache,
        resolve_storage_roots,
    )

    ws = tmp_path / "myworkspace"
    ws.mkdir()

    clear_business_roots_resolver()
    clear_storage_roots_cache()
    roots = resolve_storage_roots(str(ws))
    assert roots.storage_layout_mode == "project_local"


def test_storage_package_exports_business_resolver_hooks() -> None:
    """The storage package must export resolver hooks for bootstrap wiring."""
    from polaris.kernelone import storage

    assert hasattr(storage, "register_business_roots_resolver")
    assert hasattr(storage, "clear_business_roots_resolver")
    exported = set(getattr(storage, "__all__", []))
    assert "register_business_roots_resolver" in exported
    assert "clear_business_roots_resolver" in exported
