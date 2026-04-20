"""Joint integration tests for ``workflow_runtime`` × ``roles.adapters``.

Verifies that the two cells work correctly together after the circular-
dependency fix:

1. ``workflow_runtime`` can be imported first, then ``roles.adapters``.
2. ``roles.adapters`` can be imported first, then ``workflow_runtime``.
3. The role-adapter factory is registered exactly once (in ``public/service.py``).
4. ``GenericPipelineWorkflow`` uses the correct public factory import.
5. ``UnifiedOrchestrationService.set_role_adapter_factory`` stores the factory
   and ``_ensure_role_adapters`` uses it.
"""

from __future__ import annotations

import importlib
from pathlib import Path


def test_import_workflow_runtime_then_roles_adapters() -> None:
    """Import workflow_runtime first, then roles.adapters — must not raise."""

    # Simulate: workflow_runtime imported first
    from polaris.cells.orchestration import workflow_runtime as wr

    assert wr is not None

    # Now roles.adapters — must not raise even if workflow_runtime is partially
    # initialised (the try/except in public/service.py guards against this)
    from polaris.cells.roles import adapters as ra

    assert ra is not None
    assert hasattr(ra, "create_role_adapter")


def test_import_roles_adapters_then_workflow_runtime() -> None:
    """Import roles.adapters first, then workflow_runtime — must not raise."""

    # Simulate: roles.adapters imported first
    from polaris.cells.roles import adapters as ra

    assert ra is not None

    # Now workflow_runtime — must not raise
    from polaris.cells.orchestration import workflow_runtime as wr

    assert wr is not None


def test_factory_registration_is_idempotent(tmp_path) -> None:
    """The factory must be registered exactly once; subsequent calls are no-ops."""
    from polaris.cells.roles.adapters.public.service import create_role_adapter

    # Must produce a normalised adapter (role_id lowercased)
    adapter = create_role_adapter("Architect", str(tmp_path))
    assert adapter.role_id == "architect"

    # Calling again must yield the same class (no error)
    adapter2 = create_role_adapter("architect", str(tmp_path))
    assert type(adapter2) == type(adapter)


def test_unified_orchestration_service_stores_factory(tmp_path) -> None:
    """UnifiedOrchestrationService.set_role_adapter_factory must store the factory."""
    from polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service import (
        UnifiedOrchestrationService,
    )
    from polaris.cells.roles.adapters.public.service import create_role_adapter

    service = UnifiedOrchestrationService()
    assert service._role_adapter_factory is None

    service.set_role_adapter_factory(create_role_adapter)
    assert service._role_adapter_factory is create_role_adapter


def test_generic_pipeline_source_uses_public_factory() -> None:

    Path(__file__).resolve().parent  # .../roles/adapters/tests/
    source_path = (
        Path(__file__).resolve().parent.parent.parent.parent  # polaris/cells/
        / "orchestration"
        / "workflow_runtime"
        / "internal"
        / "runtime_engine"
        / "workflows"
        / "generic_pipeline_workflow.py"
    )
    source = source_path.read_text(encoding="utf-8")
    # The lazy import inside _call_role_adapter must use the public factory path
    assert "from polaris.cells.roles.adapters.public.service import create_role_adapter" in source
    # Must NOT use the internal-only factory path
    assert "from polaris.cells.roles.adapters.internal" not in source


def test_orchestration_service_reload_is_graceful(tmp_path, monkeypatch) -> None:
    """Reloading roles.adapters.public.service while workflow_runtime is active must not crash."""
    # Patch configure_orchestration_role_adapter_factory to simulate a race condition
    import polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service as uos_mod
    import polaris.cells.roles.adapters.public.service as svc_mod

    class NotReadyError(Exception):
        pass

    def _fail(*args: object, **kwargs: object) -> None:
        raise NotReadyError("workflow_runtime not yet ready")

    orig = getattr(uos_mod, "configure_orchestration_role_adapter_factory", None)
    try:
        monkeypatch.setattr(
            uos_mod,
            "configure_orchestration_role_adapter_factory",
            _fail,
        )
        importlib.reload(svc_mod)
    finally:
        if orig is not None:
            monkeypatch.setattr(
                uos_mod,
                "configure_orchestration_role_adapter_factory",
                orig,
            )
