"""Typed dependency ports for the capability dispatch seam.

``CapabilityDeps`` is a frozen, Protocol-typed replacement for the twelve
``Any | None`` ``*_service`` keyword arguments on
``execute_role_capability_invocation``. Each port Protocol declares ONLY the
method(s) the dispatcher actually invokes on that injected service (verified
against the live branches), so the public seam carries zero ``Any``.

Design boundary
---------------
These are *consumer-owned* ports defined inside ``roles.runtime``: they describe
what the dispatcher needs, not the providers' concrete contract classes. The
cross-cell command/query/result objects are therefore typed as ``object`` — an
opaque, type-safe value (NOT ``Any``) that introduces no import-time edge to the
owner cells. The owner-cell contract imports stay function-local inside each
handler, preserving the "removes zero import-time graph edges" property of the
refactor while eliminating ``Any`` from the public interface.

All ports are runtime-checkable so a registry/handler can defensively probe an
injected fake without importing the provider cell.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class BudgetGuardPort(Protocol):
    """``finops.budget_guard`` reservation port (``allocate_context_token_budget``)."""

    def reserve_budget(self, command: object) -> object: ...


@runtime_checkable
class WorkspaceGuardPort(Protocol):
    """``policy.workspace_guard`` write-guard port (single + batch checks)."""

    def check_workspace_write_guard(self, query: object) -> object: ...

    def check_workspace_write_guard_batch(self, query: object) -> object: ...


@runtime_checkable
class PermissionPort(Protocol):
    """``policy.permission`` evaluation port (boundary validation prelude)."""

    def evaluate_permission(self, command: object) -> object: ...


@runtime_checkable
class ArchitectDesignPort(Protocol):
    """``architect.design`` boundary-validation invoker port.

    Consumer-owned seam for the ``validate_cell_boundary_change`` capability. The
    port takes the *validated primitives* the boundary handler has already
    extracted (workspace / objective / constraints / boundary context) and returns
    the owner cell's design result. Encapsulating BOTH the
    ``GenerateArchitectureDesignCommandV1`` construction AND the
    ``generate_architecture_design`` call behind this callable keeps
    ``roles.runtime/internal/capability/**`` free of any runtime-scope
    ``architect.design`` import: the composition root supplies a provider that
    owns those imports. ``constraints`` / ``context`` are opaque, type-safe
    mappings (NOT ``Any``) and the return value is an opaque ``object`` read
    structurally by the handler, so no owner-cell contract type leaks onto this
    seam.
    """

    def run_boundary_design(
        self,
        *,
        workspace: str,
        objective: str,
        constraints: Mapping[str, object],
        context: Mapping[str, object],
    ) -> object: ...


@runtime_checkable
class DirectorExecutionPort(Protocol):
    """``director.execution`` port (``execute_director_task``).

    The dispatcher also accepts a plain callable in this slot; that callable
    form is handled at the call site, so the port only declares the method form.
    """

    def execute_director_task(self, command: object) -> object: ...


@runtime_checkable
class QaAuditPort(Protocol):
    """``qa.audit_verdict`` port (traceback parse, audit verdict, visual audit)."""

    def parse_traceback_frames(self, command: object) -> object: ...

    def run_qa_audit(self, command: object) -> object: ...

    def run_visual_qa_audit(self, command: object) -> object: ...


@runtime_checkable
class BlueprintPort(Protocol):
    """``chief_engineer.blueprint`` port (``generate_diff_specification`` / ``record_arch_memo``)."""

    def generate_task_blueprint(self, command: object) -> object: ...


@runtime_checkable
class CodeIntelligencePort(Protocol):
    """``code_intelligence.engine`` port (``verify_ast_dependency``)."""

    def verify_ast_dependency(self, query: object) -> object: ...


@runtime_checkable
class TaskMarketPort(Protocol):
    """``runtime.task_market`` port (critical-path query + work-item dispatch)."""

    def query_status(self, query: object) -> object: ...

    def publish_work_item(self, command: object) -> object: ...


@runtime_checkable
class RuntimeProjectionPort(Protocol):
    """``runtime.projection`` port (``project_runtime_status``)."""

    def query_runtime_projection(self, query: object) -> object: ...


@runtime_checkable
class LlmControlPlanePort(Protocol):
    """``llm.control_plane`` port (model-capability check for visual QA)."""

    def check_model_capability(self, query: object) -> object: ...


@runtime_checkable
class VerificationGuardPort(Protocol):
    """``factory.verification_guard`` port (``invoke_container_pytest``)."""

    def verify_completion(self, command: object) -> object: ...


@dataclass(frozen=True)
class CapabilityDeps:
    """Frozen bundle of the optional service ports a handler may consume.

    Every port is optional: when ``None`` the handler falls back to its owner
    cell's module-level public function (the same fall-back the previous inline dispatcher
    branches already implement). This mirrors the twelve ``*_service=None``
    keyword defaults one-for-one — but with zero ``Any`` on the type surface.
    """

    task_market_service: TaskMarketPort | None = None
    blueprint_service: BlueprintPort | None = None
    code_intelligence_service: CodeIntelligencePort | None = None
    verification_guard_service: VerificationGuardPort | None = None
    qa_audit_service: QaAuditPort | None = None
    runtime_projection_service: RuntimeProjectionPort | None = None
    budget_guard_service: BudgetGuardPort | None = None
    workspace_guard_service: WorkspaceGuardPort | None = None
    permission_service: PermissionPort | None = None
    architect_design_service: ArchitectDesignPort | None = None
    llm_control_plane_service: LlmControlPlanePort | None = None
    director_execution_service: DirectorExecutionPort | None = None
