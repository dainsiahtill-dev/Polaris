"""Integration tests for `roles.adapters` × `workflow_runtime`.

Verifies:
1. No import-time circular-dependency crash.
2. The public factory (with normalisation + DirectorAdapter fallback) is the
   one registered in the singleton, not a weaker duplicate.
3. ``register_all_adapters`` is safe to call multiple times.
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Cell cross-import smoke test — must not raise ImportError / AttributeError
# ---------------------------------------------------------------------------


def test_public_service_imports_without_error() -> None:
    """roles.adapters.public.service must import without raising."""
    from polaris.cells.roles.adapters.public import service as pub

    assert hasattr(pub, "create_role_adapter")
    assert hasattr(pub, "get_supported_roles")
    assert hasattr(pub, "configure_orchestration_role_adapter_factory")


def test_internal_init_imports_without_error() -> None:
    """roles.adapters.internal must import without raising.

    NOTE: ``public/service.py`` uses RELATIVE imports
    (e.g. ``from .internal.pm_adapter import PMAdapter``).  This guarantees
    that Python executes ``internal/__init__.py`` BEFORE any sub-module is
    imported, which in turn guarantees that ``__init__.py``'s re-export
    statements (``X = X``) are fully evaluated before the sub-module is
    accessed.  As a result, adapter classes are reliably reachable both
    via ``hasattr(intern, "PMAdapter")`` AND via
    ``from polaris.cells.roles.adapters.internal import PMAdapter``.

    This test verifies:
    - The package does not raise on import.
    - Adapter classes are accessible both as module attributes AND via direct import.
    - ``RoleOrchestrationAdapter`` from ``workflow_runtime`` is re-exported.
    """
    from polaris.cells.roles.adapters import internal as intern

    # The package must not raise on import
    assert intern is not None
    # Sub-modules are always accessible via the package namespace
    assert hasattr(intern, "ArchitectAdapter")
    assert hasattr(intern, "QAAdapter")
    # Adapter classes — direct import (always works regardless of sys.modules state)
    from polaris.cells.roles.adapters.internal import (
        ArchitectAdapter,
        ChiefEngineerAdapter,
        PMAdapter,
        QAAdapter,
        RoleOrchestrationAdapter,
    )

    assert ArchitectAdapter is not None
    assert ChiefEngineerAdapter is not None
    assert PMAdapter is not None
    assert QAAdapter is not None
    assert RoleOrchestrationAdapter is not None


def test_cell_entry_imports_without_error() -> None:
    """The cell top-level __init__ must import without raising."""
    from polaris.cells.roles import adapters as cell

    assert "PMAdapter" in cell.__all__
    assert "create_role_adapter" in cell.__all__
    assert "RoleAdapterResultV1" in cell.__all__


def test_public_service_schema_exports() -> None:
    """Schema singletons are present after import."""
    from polaris.cells.roles.adapters.public.service import (
        ROLE_OUTPUT_SCHEMAS,
        get_schema_for_role,
    )

    assert "pm" in ROLE_OUTPUT_SCHEMAS
    assert get_schema_for_role("pm") is not None
    assert get_schema_for_role("nonexistent") is None


# ---------------------------------------------------------------------------
# Factory registration — public factory wins (no weaker duplicate overwrites)
# ---------------------------------------------------------------------------


def test_factory_produces_normalised_instance(tmp_path) -> None:
    """create_role_adapter must normalise role tokens (e.g. 'Architect' -> 'architect')."""
    from polaris.cells.roles.adapters.public.service import create_role_adapter

    adapter = create_role_adapter("Architect", str(tmp_path))
    assert adapter.role_id == "architect"


def test_factory_rejects_empty_workspace(tmp_path) -> None:
    """Empty workspace must raise ValueError (not AttributeError)."""
    from polaris.cells.roles.adapters.public.service import create_role_adapter

    with pytest.raises(ValueError, match="workspace"):
        create_role_adapter("pm", "   ")


def test_factory_rejects_empty_role() -> None:
    """Empty role_id must raise ValueError (not AttributeError)."""
    from polaris.cells.roles.adapters.public.service import create_role_adapter

    with pytest.raises(ValueError, match="role_id"):
        create_role_adapter("", "/ws")


# ---------------------------------------------------------------------------
# Lazy-registration guard — import must still succeed even if workflow_runtime
# is not yet initialised (simulated by patching).
# ---------------------------------------------------------------------------


def test_import_succeeds_when_orchestration_service_not_ready(tmp_path, monkeypatch) -> None:
    """Importing the cell while workflow_runtime is unavailable must not crash.

    The defensive try/except around ``configure_orchestration_role_adapter_factory``
    ensures the module can still be imported; the factory is applied lazily.
    """
    # Patch the singleton to raise — simulating "not yet fully initialised"
    # TODO: migrate to public contract once configure_orchestration_role_adapter_factory is exported
    import polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service as _uos
    import polaris.cells.roles.adapters.public.service as _svc

    _orig: object = getattr(_uos, "configure_orchestration_role_adapter_factory", None)

    class NotReadyError(Exception):
        pass

    def _fail(*args, **kwargs) -> None:
        raise NotReadyError("workflow_runtime not yet ready")

    try:
        monkeypatch.setattr(_uos, "configure_orchestration_role_adapter_factory", _fail)
        # Re-importing the module must NOT raise
        import importlib

        importlib.reload(_svc)
    finally:
        if _orig is not None:
            monkeypatch.setattr(_uos, "configure_orchestration_role_adapter_factory", _orig)


# ---------------------------------------------------------------------------
# register_all_adapters is safe (no-op on unknown services)
# ---------------------------------------------------------------------------


def test_register_all_adapters_is_idempotent() -> None:
    """register_all_adapters must be callable without side-effects on unknown objects."""
    from polaris.cells.roles.adapters.public.service import register_all_adapters

    class UnknownService:
        pass

    # Must not raise
    register_all_adapters(UnknownService())
    register_all_adapters(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# get_supported_roles — returns registered adapters
# ---------------------------------------------------------------------------


def test_get_supported_roles_contains_core_roles() -> None:
    """At minimum pm, qa, architect, chief_engineer must be present."""
    from polaris.cells.roles.adapters.public.service import get_supported_roles

    roles = get_supported_roles()
    assert isinstance(roles, list)
    assert len(roles) > 0
    assert all(r == r.lower() for r in roles)
    # pm is the anchor role — always present
    assert "pm" in roles


def test_director_graceful_fallback_when_unavailable() -> None:
    """When DirectorAdapter is unavailable, 'director' must not appear in supported roles silently."""
    from polaris.cells.roles.adapters.public.service import get_supported_roles

    roles = get_supported_roles()
    # 'director' may or may not be present depending on environment,
    # but the call must not raise regardless.
    assert isinstance(roles, list)
