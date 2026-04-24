"""Public service exports for `roles.adapters` cell."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from polaris.cells.orchestration.workflow_runtime.public.service import (
    configure_orchestration_role_adapter_factory,
)

# ---------------------------------------------------------------------------
# NOTE on import order
# ---------------------------------------------------------------------------
# All imports from ``..internal.*`` below use RELATIVE paths (``from ..internal.X``).
# This is intentional: relative imports cause Python to execute ``internal/__init__.py``
# BEFORE any sub-module is imported, which guarantees that ``__init__.py``'s re-export
# statements (``X = X``) are fully evaluated before the sub-module is accessed.
#
# Absolute imports (``from polaris.cells.roles.adapters.internal.X``) bypass
# ``__init__.py`` entirely because Python registers the sub-module in ``sys.modules``
# directly, skipping the package-initialisation step.  This breaks re-export
# guarantees and causes ``ImportError`` when code tries to import adapter classes
# through the ``internal`` namespace.
# ---------------------------------------------------------------------------
from ..internal.architect_adapter import ArchitectAdapter
from ..internal.base import BaseRoleAdapter
from ..internal.chief_engineer_adapter import ChiefEngineerAdapter
from ..internal.pm_adapter import PMAdapter
from ..internal.qa_adapter import QAAdapter
from ..internal.schemas import (
    ROLE_OUTPUT_SCHEMAS,
    BaseToolEnabledOutput,
    BlueprintOutput,
    ConstructionPlan,
    DirectorOutput,
    PatchOperation,
    QAFinding,
    QAReportOutput,
    Task,
    TaskListOutput,
    ToolCall,
    get_schema_for_role,
)
from ..internal.workflow_adapter import (
    WorkflowRoleAdapter,
    WorkflowRoleResult,
    execute_workflow_role,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _build_registry() -> dict[str, Callable[[str], BaseRoleAdapter]]:
    registry: dict[str, Callable[[str], BaseRoleAdapter]] = {
        "pm": PMAdapter,
        "architect": ArchitectAdapter,
        "qa": QAAdapter,
        "chief_engineer": ChiefEngineerAdapter,
    }
    try:
        from ..internal.director_adapter import DirectorAdapter
    except (RuntimeError, ValueError):
        DirectorAdapter = None  # type: ignore[assignment, misc]  # noqa: N806
    if DirectorAdapter is not None:
        registry["director"] = cast("Callable[[str], BaseRoleAdapter]", DirectorAdapter)
    return registry


_ADAPTERS = _build_registry()


def create_role_adapter(role_id: str, workspace: str) -> BaseRoleAdapter:
    role_token = str(role_id or "").strip().lower()
    workspace_token = str(workspace or "").strip()
    if not role_token:
        raise ValueError("role_id must be a non-empty string")
    if not workspace_token:
        raise ValueError("workspace must be a non-empty string")
    adapter_class = _ADAPTERS.get(role_token)
    if adapter_class is None:
        raise ValueError(f"Unknown role: {role_token}, supported: {list(_ADAPTERS.keys())}")
    return adapter_class(workspace_token)


def register_all_adapters(service: object) -> None:
    """Register role adapter factory to orchestration service if supported."""
    if hasattr(service, "set_role_adapter_factory"):
        return


def get_supported_roles() -> list[str]:
    return list(_ADAPTERS.keys())


_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level factory registration — the authoritative one for this cell.
# Guard against import-order races where workflow_runtime internal modules are
# not yet fully initialised when this module is imported first.
# ---------------------------------------------------------------------------
try:
    configure_orchestration_role_adapter_factory(create_role_adapter)
except (RuntimeError, ValueError) as exc:
    _logger.debug(
        "workflow_runtime not yet fully initialised at import time "
        "(%s); factory will be configured lazily on first orchestration "
        "service access.  Import will still succeed.",
        exc,
    )


__all__ = [
    "ROLE_OUTPUT_SCHEMAS",
    "ArchitectAdapter",
    "BaseRoleAdapter",
    "BaseToolEnabledOutput",
    "BlueprintOutput",
    "ChiefEngineerAdapter",
    "ConstructionPlan",
    "DirectorOutput",
    "PMAdapter",
    "PatchOperation",
    "QAAdapter",
    "QAFinding",
    "QAReportOutput",
    "Task",
    "TaskListOutput",
    "ToolCall",
    "WorkflowRoleAdapter",
    "WorkflowRoleResult",
    "create_role_adapter",
    "execute_workflow_role",
    "get_schema_for_role",
    "get_supported_roles",
    "register_all_adapters",
]
