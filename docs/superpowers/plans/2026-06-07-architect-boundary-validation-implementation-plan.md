# Architect Boundary Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Architect role capability `validate_cell_boundary_change` as a typed, auditable runtime object invocation through `policy.permission`, `policy.workspace_guard`, and `architect.design` public contracts.

**Architecture:** `roles.runtime` remains the stateless composition and orchestration boundary: it validates the mounted capability, evaluates lightweight permission, checks changed paths through workspace guard, and calls `architect.design` through a public command wrapper. The authoritative state owners remain `policy.permission`, `policy.workspace_guard`, `architect.design`, and graph governance assets; `roles.runtime` only carries refs, metadata, and structured invocation results.

**Tech Stack:** Python 3.12 dataclasses, pytest, mypy, ruff, AST import-fence tests, existing Polaris Cell graph/catalog governance.

---

## Scope And Constraints

- Latest-only runtime behavior: do not add compatibility shims or old prompt-driven fallbacks.
- `RoleCapabilityInvocationResultV1.allowed` means execution authorization. For any permission, workspace guard, fingerprint, role, or contract denial, return `allowed=False`.
- If consumers need discoverability, put `capability_available=True` in `metadata`; do not overload `allowed`.
- `EvaluatePermissionCommandV1.context` must stay lightweight: role invocation refs, target cell, capability id, and resource type only. Structural deltas stay in the `architect.design` command.
- `WorkspaceWriteGuardQueryV1` is single-path today. Batch behavior is implemented inside `roles.runtime` by deduping paths and running bounded-concurrent calls to the existing public query.
- `architect.design` receives typed `GenerateArchitectureDesignCommandV1`; runtime does not import or instantiate `architect.design.internal`.
- No state writes are introduced in `roles.runtime` for this capability.

## Files

- Create: `src/backend/docs/governance/templates/verification-cards/vc-20260607-architect-boundary-validation.yaml`
- Create: `src/backend/docs/governance/decisions/adr-0089-role-capability-allowed-semantics.md`
- Create: `src/backend/polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py`
- Modify: `src/backend/polaris/cells/policy/permission/internal/permission_service.py`
- Modify: `src/backend/polaris/cells/policy/permission/public/service.py`
- Modify: `src/backend/polaris/cells/policy/permission/public/tests/test_public_contracts.py`
- Modify: `src/backend/polaris/cells/architect/design/public/service.py`
- Modify: `src/backend/polaris/cells/architect/design/tests/test_contracts.py`
- Modify: `src/backend/polaris/cells/roles/runtime/public/service.py`
- Modify: `src/backend/polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py`
- Modify: `src/backend/polaris/cells/roles/runtime/public/contracts.py`
- Modify: `src/backend/polaris/cells/roles/runtime/README.agent.md`
- Modify: `src/backend/polaris/cells/roles/runtime/cell.yaml`
- Modify: `src/backend/polaris/cells/roles/runtime/context.pack.json`
- Modify: `src/backend/polaris/cells/policy/permission/README.agent.md`
- Modify: `src/backend/polaris/cells/policy/permission/cell.yaml`
- Modify: `src/backend/polaris/cells/policy/permission/generated/context.pack.json`
- Modify: `src/backend/polaris/cells/architect/design/README.agent.md`
- Modify: `src/backend/polaris/cells/architect/design/cell.yaml`
- Modify: `src/backend/polaris/cells/architect/design/context.pack.json`
- Modify: `src/backend/docs/graph/catalog/cells.yaml`
- Modify: `src/backend/docs/graph/subgraphs/execution_governance_pipeline.yaml`
- Modify: `src/backend/polaris/tests/architecture/test_roles_cell_governance.py`

---

### Task 1: Governance Record For Structural Fix

**Files:**
- Create: `src/backend/docs/governance/templates/verification-cards/vc-20260607-architect-boundary-validation.yaml`
- Create: `src/backend/docs/governance/decisions/adr-0089-role-capability-allowed-semantics.md`

- [ ] **Step 1: Add the verification card**

Create `src/backend/docs/governance/templates/verification-cards/vc-20260607-architect-boundary-validation.yaml` with:

```yaml
id: vc-20260607-architect-boundary-validation
title: Architect role boundary validation runtime capability
date: "2026-06-07"
classification: structural
owner: architecture-team
scope:
  cells:
    - roles.runtime
    - policy.permission
    - policy.workspace_guard
    - architect.design
  governance_assets:
    - docs/graph/catalog/cells.yaml
    - docs/graph/subgraphs/execution_governance_pipeline.yaml
assumption_register:
  - id: A1
    statement: RoleCapabilityInvocationResultV1.allowed is an execution-authorization signal.
    evidence:
      - src/backend/polaris/cells/roles/runtime/public/contracts.py
      - src/backend/polaris/cells/roles/runtime/public/service.py
    decision: Denied capability, permission, workspace guard, and fingerprint checks return allowed=false.
  - id: A2
    statement: Capability discoverability is separate from execution authorization.
    evidence:
      - src/backend/polaris/cells/roles/runtime/public/contracts.py
    decision: Denial metadata may include capability_available=true.
  - id: A3
    statement: roles.runtime may orchestrate but must not own architect.design, permission, or workspace guard state.
    evidence:
      - src/backend/polaris/cells/roles/runtime/cell.yaml
      - src/backend/polaris/cells/architect/design/cell.yaml
      - src/backend/polaris/cells/policy/permission/cell.yaml
      - src/backend/polaris/cells/policy/workspace_guard/cell.yaml
    decision: Runtime calls only public service wrappers.
pre_mortem:
  likely_failure_modes:
    - Runtime returns ok=false but allowed=true on a denied guard result.
    - Permission wrapper imports or exposes foreign internal state to roles.runtime.
    - Workspace guard path checks run once per duplicate path and cause avoidable latency.
    - Architect design call blocks indefinitely without a structured timeout result.
    - roles.runtime.public imports a foreign Cell internal module.
verification_plan:
  tests:
    - path: src/backend/polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py
      expected: Architect validation success, permission denial, workspace guard denial, deduped guard paths, and timeout are covered.
    - path: src/backend/polaris/cells/policy/permission/public/tests/test_public_contracts.py
      expected: evaluate_permission maps EvaluatePermissionCommandV1 to PermissionDecisionResultV1.
    - path: src/backend/polaris/cells/architect/design/tests/test_contracts.py
      expected: generate_architecture_design returns typed ArchitectureDesignResultV1.
    - path: src/backend/polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py
      expected: roles.runtime.public has no foreign Cell internal imports.
  commands:
    - ruff check src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/public/contracts.py src/backend/polaris/cells/policy/permission/public/service.py src/backend/polaris/cells/policy/permission/internal/permission_service.py src/backend/polaris/cells/architect/design/public/service.py src/backend/polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py src/backend/polaris/cells/policy/permission/public/tests/test_public_contracts.py src/backend/polaris/cells/architect/design/tests/test_contracts.py src/backend/polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py --fix
    - ruff format src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/public/contracts.py src/backend/polaris/cells/policy/permission/public/service.py src/backend/polaris/cells/policy/permission/internal/permission_service.py src/backend/polaris/cells/architect/design/public/service.py src/backend/polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py src/backend/polaris/cells/policy/permission/public/tests/test_public_contracts.py src/backend/polaris/cells/architect/design/tests/test_contracts.py src/backend/polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py
    - mypy src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/public/contracts.py src/backend/polaris/cells/policy/permission/public/service.py src/backend/polaris/cells/policy/permission/internal/permission_service.py src/backend/polaris/cells/architect/design/public/service.py
    - python -m pytest -q src/backend/polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py src/backend/polaris/cells/policy/permission/public/tests/test_public_contracts.py src/backend/polaris/cells/architect/design/tests/test_contracts.py src/backend/polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py
    - python -m pytest -q src/backend/polaris/cells/roles/kernel/tests
    - python -m pytest -q src/backend/polaris/cells/roles/runtime/tests
    - python -m pytest -q src/backend/polaris/cells/runtime/task_market/tests
    - python src/backend/docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
rollback:
  strategy: Revert the runtime adapter branch and public wrappers in one commit; keep ADR and verification card if they document the rejected design.
```

- [ ] **Step 2: Add the ADR**

Create `src/backend/docs/governance/decisions/adr-0089-role-capability-allowed-semantics.md` with:

```markdown
# ADR-0089: Role Capability Allowed Semantics

Status: Accepted
Date: 2026-06-07

## Context

`RoleCapabilityInvocationResultV1` exposes `ok` and `allowed`. A denied capability
can be structurally configured and still not authorized to execute. Returning
`allowed=true` when `ok=false` for permission or workspace guard denials makes
downstream consumers likely to gate on the wrong boolean.

## Decision

`allowed` means execution authorization for the concrete invocation. It is
`false` for role mismatch, missing capability mount, role allow-list denial,
contract mismatch, fingerprint mismatch, permission denial, workspace guard
denial, and timeout before execution completes.

Capability availability is represented only as metadata, using
`capability_available=true` when a mounted capability exists but execution is
denied by a later sandbox layer.

## Consequences

- Consumers can use `if result.allowed` as a security gate without needing to
also inspect error codes.
- `ok=false, allowed=true` remains valid only for target Cell processing
failures after an authorized call was made, such as a downstream service error
or a business-level rejection.
- Runtime adapters must distinguish sandbox denial from downstream rejection.
```

- [ ] **Step 3: Run governance docs sanity check**

Run:

```bash
git diff -- src/backend/docs/governance/templates/verification-cards/vc-20260607-architect-boundary-validation.yaml src/backend/docs/governance/decisions/adr-0089-role-capability-allowed-semantics.md
```

Expected: the diff contains only the verification card and ADR above.

- [ ] **Step 4: Commit**

```bash
git add src/backend/docs/governance/templates/verification-cards/vc-20260607-architect-boundary-validation.yaml src/backend/docs/governance/decisions/adr-0089-role-capability-allowed-semantics.md
git commit -m "docs: define role capability authorization semantics"
```

---

### Task 2: Failing Runtime Tests For Architect Boundary Validation

**Files:**
- Modify: `src/backend/polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py`

- [ ] **Step 1: Add imports and fakes**

Add these imports near the existing public contract imports:

```python
import time

from polaris.cells.architect.design.public.contracts import (
    ArchitectureDesignResultV1,
    GenerateArchitectureDesignCommandV1,
)
from polaris.cells.policy.permission.public.contracts import (
    EvaluatePermissionCommandV1,
    PermissionDecisionResultV1,
)
```

Add these fake services after `FakeWorkspaceGuardService`:

```python
class FakePermissionService:
    def __init__(self, *, allowed: bool) -> None:
        self.allowed = allowed
        self.evaluated: list[EvaluatePermissionCommandV1] = []

    def evaluate_permission(self, command: EvaluatePermissionCommandV1) -> PermissionDecisionResultV1:
        self.evaluated.append(command)
        return PermissionDecisionResultV1(
            allowed=self.allowed,
            role=command.role,
            action=command.action,
            resource=command.resource,
            reason="allowed by fake policy" if self.allowed else "denied by fake policy",
            matched_policy="fake.allow" if self.allowed else "fake.deny",
            context={"decision_source": "fake"},
        )


class FakeArchitectDesignService:
    def __init__(self) -> None:
        self.generated: list[GenerateArchitectureDesignCommandV1] = []

    def generate_architecture_design(
        self,
        command: GenerateArchitectureDesignCommandV1,
    ) -> ArchitectureDesignResultV1:
        self.generated.append(command)
        return ArchitectureDesignResultV1(
            ok=True,
            workspace=command.workspace,
            design_id="design-boundary-1",
            status="completed",
            summary=f"Boundary validation for {command.context.get('target_cell', '')}",
            recommendation_paths=("runtime/state/architect/design-boundary-1.json",),
        )


class SlowArchitectDesignService:
    def generate_architecture_design(
        self,
        command: GenerateArchitectureDesignCommandV1,
    ) -> ArchitectureDesignResultV1:
        time.sleep(0.25)
        return ArchitectureDesignResultV1(
            ok=True,
            workspace=command.workspace,
            design_id="slow-design",
            status="completed",
        )
```

- [ ] **Step 2: Add helper for Architect validation runtime object**

Add this helper after `_profile_binding`:

```python
def _architect_validation_runtime_object() -> RoleRuntimeObject:
    spec = runtime_contracts.get_builtin_role_runtime_spec("architect")
    return spec.instantiate(
        identity=_identity("architect"),
        profile_binding=_profile_binding("architect"),
        ledger_binding=RoleLedgerBinding(turn_ledger_ref="roles.kernel:turn-ledger:architect-run"),
        policy_fingerprint="architect-policy",
        capability_id="validate_cell_boundary_change",
    )
```

- [ ] **Step 3: Update existing workspace guard denial expectation**

In `test_architect_intercept_illegal_mutation_uses_workspace_guard_refusal`, change:

```python
assert result.allowed is True
```

to:

```python
assert result.allowed is False
assert result.metadata["capability_available"] is True
```

- [ ] **Step 4: Add success test for validate_cell_boundary_change**

Append:

```python
def test_architect_validate_cell_boundary_change_invokes_permission_guard_and_design() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref="roles.runtime:typed-input:architect-boundary-1",
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    permission = FakePermissionService(allowed=True)
    workspace_guard = FakeWorkspaceGuardService(allowed=True)
    architect_design = FakeArchitectDesignService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate roles.runtime boundary change",
                "target_cell": "roles.runtime",
                "changed_paths": (
                    "src/backend/polaris/cells/roles/runtime/public/service.py",
                    "src/backend/polaris/cells/roles/runtime/public/service.py",
                    "src/backend/polaris/cells/roles/runtime/public/contracts.py",
                ),
                "constraints": {
                    "depends_on_delta": ("architect.design",),
                    "state_owner_delta": (),
                    "effects_delta": ("architect.validate_cell_boundary",),
                },
                "context": {"graph_ref": "docs/graph/catalog/cells.yaml"},
                "timeout_seconds": 1.0,
            },
        ),
        permission_service=permission,
        workspace_guard_service=workspace_guard,
        architect_design_service=architect_design,
    )

    assert result.ok is True
    assert result.allowed is True
    assert result.owner_cell == "architect.design"
    assert result.command_contract == "GenerateArchitectureDesignCommandV1"
    assert result.result_ref == "architect.design:boundary-validation:design-boundary-1"
    assert result.metadata["permission_allowed"] is True
    assert result.metadata["workspace_guard_allowed"] is True
    assert result.metadata["checked_paths"] == (
        "src/backend/polaris/cells/roles/runtime/public/service.py",
        "src/backend/polaris/cells/roles/runtime/public/contracts.py",
    )
    assert len(permission.evaluated) == 1
    assert permission.evaluated[0].context["capability_id"] == "validate_cell_boundary_change"
    assert "depends_on_delta" not in permission.evaluated[0].context
    assert len(workspace_guard.checked) == 2
    assert len(architect_design.generated) == 1
    design_command = architect_design.generated[0]
    assert design_command.workspace == "/repo"
    assert design_command.objective == "Validate roles.runtime boundary change"
    assert design_command.constraints["depends_on_delta"] == ("architect.design",)
    assert design_command.context["target_cell"] == "roles.runtime"
    assert design_command.context["role_invocation_id"] == "invoke-boundary-1"
```

- [ ] **Step 5: Add permission denial test**

Append:

```python
def test_architect_validate_cell_boundary_permission_denial_has_allowed_false() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-denied-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref="roles.runtime:typed-input:architect-boundary-denied-1",
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    permission = FakePermissionService(allowed=False)
    workspace_guard = FakeWorkspaceGuardService(allowed=True)
    architect_design = FakeArchitectDesignService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate denied change",
                "target_cell": "roles.runtime",
                "changed_paths": ("src/backend/polaris/cells/roles/runtime/public/service.py",),
            },
        ),
        permission_service=permission,
        workspace_guard_service=workspace_guard,
        architect_design_service=architect_design,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "permission_denied"
    assert result.metadata["capability_available"] is True
    assert result.metadata["permission_allowed"] is False
    assert workspace_guard.checked == []
    assert architect_design.generated == []
```

- [ ] **Step 6: Add workspace guard denial test**

Append:

```python
def test_architect_validate_cell_boundary_workspace_guard_denial_has_allowed_false() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-guard-denied-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref="roles.runtime:typed-input:architect-boundary-guard-denied-1",
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )
    permission = FakePermissionService(allowed=True)
    workspace_guard = FakeWorkspaceGuardService(allowed=False)
    architect_design = FakeArchitectDesignService()

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate denied path",
                "target_cell": "roles.runtime",
                "changed_paths": ("../outside-project/secret.py",),
            },
        ),
        permission_service=permission,
        workspace_guard_service=workspace_guard,
        architect_design_service=architect_design,
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "workspace_guard_denied"
    assert result.metadata["capability_available"] is True
    assert result.metadata["workspace_guard_allowed"] is False
    assert result.metadata["denied_path"] == "../outside-project/secret.py"
    assert len(workspace_guard.checked) == 1
    assert architect_design.generated == []
```

- [ ] **Step 7: Add design timeout test**

Append:

```python
def test_architect_validate_cell_boundary_design_timeout_has_structured_failure() -> None:
    runtime_object = _architect_validation_runtime_object()
    invocation = RoleCapabilityInvocation(
        invocation_id="invoke-boundary-timeout-1",
        capability_id="validate_cell_boundary_change",
        role_id="architect",
        command_contract="GenerateArchitectureDesignCommandV1",
        payload_ref="roles.runtime:typed-input:architect-boundary-timeout-1",
        fingerprint_ref=runtime_object.capability_fingerprint.fingerprint,
    )

    result = execute_role_capability_invocation(
        ExecuteRoleCapabilityInvocationCommandV1(
            runtime_object=runtime_object,
            invocation=invocation,
            payload={
                "objective": "Validate slow change",
                "target_cell": "roles.runtime",
                "changed_paths": ("src/backend/polaris/cells/roles/runtime/public/service.py",),
                "timeout_seconds": 0.01,
            },
        ),
        permission_service=FakePermissionService(allowed=True),
        workspace_guard_service=FakeWorkspaceGuardService(allowed=True),
        architect_design_service=SlowArchitectDesignService(),
    )

    assert result.ok is False
    assert result.allowed is False
    assert result.error_code == "architect_design_timeout"
    assert result.metadata["capability_available"] is True
```

- [ ] **Step 8: Run tests and verify failures**

Run:

```bash
cd src/backend
python -m pytest -q polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py
```

Expected: FAIL. Failures include unexpected `allowed=True` in the existing workspace guard denial and unsupported adapter for `validate_cell_boundary_change`.

- [ ] **Step 9: Commit tests**

```bash
git add src/backend/polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py
git commit -m "test: cover architect boundary validation runtime invocation"
```

---

### Task 3: Permission Public Wrapper

**Files:**
- Modify: `src/backend/polaris/cells/policy/permission/internal/permission_service.py`
- Modify: `src/backend/polaris/cells/policy/permission/public/service.py`
- Modify: `src/backend/polaris/cells/policy/permission/public/tests/test_public_contracts.py`

- [ ] **Step 1: Add permission wrapper tests**

Append to `src/backend/polaris/cells/policy/permission/public/tests/test_public_contracts.py`:

```python
from polaris.cells.policy.permission.public.service import evaluate_permission


class TestEvaluatePermissionPublicService:
    def test_architect_boundary_validation_is_allowed_by_builtin_policy(self) -> None:
        result = evaluate_permission(
            EvaluatePermissionCommandV1(
                role="architect",
                action="execute",
                resource="architect.design:validate_cell_boundary_change",
                workspace="/repo",
                context={
                    "resource_type": "api",
                    "task_id": "task-1",
                    "session_id": "session-1",
                    "request_id": "invoke-boundary-1",
                },
            )
        )

        assert result.allowed is True
        assert result.role == "architect"
        assert result.action == "execute"
        assert result.resource == "architect.design:validate_cell_boundary_change"
        assert result.matched_policy == "architect-execute-boundary-validation"
        assert result.context["decision"] == "allow"

    def test_pm_boundary_validation_is_denied(self) -> None:
        result = evaluate_permission(
            EvaluatePermissionCommandV1(
                role="pm",
                action="execute",
                resource="architect.design:validate_cell_boundary_change",
                workspace="/repo",
                context={"resource_type": "api"},
            )
        )

        assert result.allowed is False
        assert result.role == "pm"
        assert result.reason
        assert result.context["decision"] == "deny"
```

- [ ] **Step 2: Run wrapper tests and verify failure**

Run:

```bash
cd src/backend
python -m pytest -q polaris/cells/policy/permission/public/tests/test_public_contracts.py::TestEvaluatePermissionPublicService
```

Expected: FAIL with `ImportError` for `evaluate_permission` or denial because the Architect execute policy is missing.

- [ ] **Step 3: Add Architect boundary validation policy**

In `PermissionService._load_builtin_policies`, add this policy after `architect-read-all`:

```python
            Policy(
                id="architect-execute-boundary-validation",
                name="Architect validate Cell boundary changes",
                effect=PolicyEffect.ALLOW,
                subjects=[Subject(type=SubjectType.ROLE, id="architect")],
                resources=[Resource(type=ResourceType.API, pattern="architect.design:validate_cell_boundary_change")],
                actions=[Action.EXECUTE],
                priority=40,
            ),
```

- [ ] **Step 4: Implement evaluate_permission wrapper**

In `src/backend/polaris/cells/policy/permission/public/service.py`, replace the current file content with:

```python
"""Public service exports for `policy.permission` cell."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from polaris.cells.policy.permission.internal.permission_service import (
    DecisionContext,
    PermissionService,
    get_permission_service,
)
from polaris.cells.policy.permission.public.contracts import (
    EvaluatePermissionCommandV1,
    PermissionDecisionResultV1,
    PermissionDeniedEventV1,
    PermissionPolicyError,
    QueryPermissionMatrixV1,
)
from polaris.cells.roles.profile.public.service import (
    Action,
    Resource,
    ResourceType,
    Subject,
    SubjectType,
)

_T = TypeVar("_T")


def _run_async(factory: Callable[[], "asyncio.Future[_T]"]) -> _T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    def _runner() -> _T:
        return asyncio.run(factory())

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_runner).result()


def _resource_type_from_command(command: EvaluatePermissionCommandV1) -> ResourceType:
    requested = str(command.context.get("resource_type") or "").strip().lower()
    if requested:
        return ResourceType(requested)
    if command.action == Action.EXECUTE.value:
        return ResourceType.API
    if command.action in {Action.WRITE.value, Action.DELETE.value, Action.READ.value}:
        return ResourceType.FILE
    return ResourceType.API


def evaluate_permission(command: EvaluatePermissionCommandV1) -> PermissionDecisionResultV1:
    """Evaluate a permission command through the public policy contract."""
    if not isinstance(command, EvaluatePermissionCommandV1):
        raise TypeError("command must be an EvaluatePermissionCommandV1")

    async def _evaluate() -> PermissionDecisionResultV1:
        service = await get_permission_service(command.workspace)
        result = await service.check_permission(
            subject=Subject(type=SubjectType.ROLE, id=command.role),
            resource=Resource(
                type=_resource_type_from_command(command),
                pattern=command.resource,
                path=command.resource,
            ),
            action=Action(command.action),
            context=DecisionContext(
                task_id=str(command.context.get("task_id") or "") or None,
                session_id=str(command.context.get("session_id") or "") or None,
                request_id=str(command.context.get("request_id") or "") or None,
                workspace=command.workspace,
                metadata=dict(command.context),
            ),
        )
        matched_policy = result.matched_policies[0] if result.matched_policies else None
        return PermissionDecisionResultV1(
            allowed=result.allowed,
            role=command.role,
            action=command.action,
            resource=command.resource,
            reason=result.reason,
            matched_policy=matched_policy,
            context={
                "decision": result.decision,
                "matched_policies": tuple(result.matched_policies),
            },
        )

    try:
        return _run_async(_evaluate)
    except ValueError as exc:
        raise PermissionPolicyError(str(exc), code="invalid_permission_command") from exc


__all__ = [
    "DecisionContext",
    "EvaluatePermissionCommandV1",
    "PermissionDecisionResultV1",
    "PermissionDeniedEventV1",
    "PermissionPolicyError",
    "PermissionService",
    "QueryPermissionMatrixV1",
    "evaluate_permission",
    "get_permission_service",
]
```

- [ ] **Step 5: Run permission wrapper tests**

Run:

```bash
cd src/backend
python -m pytest -q polaris/cells/policy/permission/public/tests/test_public_contracts.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/backend/polaris/cells/policy/permission/internal/permission_service.py src/backend/polaris/cells/policy/permission/public/service.py src/backend/polaris/cells/policy/permission/public/tests/test_public_contracts.py
git commit -m "feat: expose permission command evaluation wrapper"
```

---

### Task 4: Architect Design Public Wrapper

**Files:**
- Modify: `src/backend/polaris/cells/architect/design/public/service.py`
- Modify: `src/backend/polaris/cells/architect/design/tests/test_contracts.py`

- [ ] **Step 1: Add public service tests**

Append to `src/backend/polaris/cells/architect/design/tests/test_contracts.py`:

```python
from polaris.cells.architect.design.public.service import generate_architecture_design


class TestGenerateArchitectureDesignPublicService:
    def test_generate_architecture_design_returns_typed_result(self) -> None:
        result = generate_architecture_design(
            GenerateArchitectureDesignCommandV1(
                workspace="/repo",
                objective="Validate roles.runtime boundary change",
                constraints={"depends_on_delta": ("architect.design",)},
                context={
                    "target_cell": "roles.runtime",
                    "changed_paths": (
                        "src/backend/polaris/cells/roles/runtime/public/service.py",
                    ),
                },
            )
        )

        assert isinstance(result, ArchitectureDesignResultV1)
        assert result.ok is True
        assert result.workspace == "/repo"
        assert result.status == "completed"
        assert result.design_id.startswith("boundary-")
        assert "roles.runtime" in result.summary
        assert result.recommendation_paths == ("runtime/state/architect/boundary-validation.json",)
```

- [ ] **Step 2: Run service test and verify failure**

Run:

```bash
cd src/backend
python -m pytest -q polaris/cells/architect/design/tests/test_contracts.py::TestGenerateArchitectureDesignPublicService
```

Expected: FAIL with `ImportError` for `generate_architecture_design`.

- [ ] **Step 3: Implement public wrapper**

In `src/backend/polaris/cells/architect/design/public/service.py`, replace the current file content with:

```python
"""Stable public service exports for `architect.design` cell."""

from __future__ import annotations

import hashlib
import json

from polaris.cells.architect.design.internal.architect_agent import ArchitectAgent
from polaris.cells.architect.design.internal.architect_service import (
    ArchitectConfig,
    ArchitectService,
    ArchitectureDoc,
)
from polaris.cells.architect.design.public.contracts import (
    ArchitectDesignErrorV1,
    ArchitectureDesignResultV1,
    GenerateArchitectureDesignCommandV1,
)


def _stable_design_id(command: GenerateArchitectureDesignCommandV1) -> str:
    payload = {
        "workspace": command.workspace,
        "objective": command.objective,
        "constraints": command.constraints,
        "context": command.context,
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"boundary-{digest[:16]}"


def generate_architecture_design(command: GenerateArchitectureDesignCommandV1) -> ArchitectureDesignResultV1:
    """Generate a typed architecture design result for a public command."""
    if not isinstance(command, GenerateArchitectureDesignCommandV1):
        raise TypeError("command must be a GenerateArchitectureDesignCommandV1")

    target_cell = str(command.context.get("target_cell") or "").strip()
    changed_paths = tuple(str(path) for path in command.context.get("changed_paths", ()) if str(path).strip())
    summary_target = target_cell or "unspecified cell"
    summary = (
        f"Boundary validation prepared for {summary_target}; "
        f"{len(changed_paths)} changed path(s) were supplied."
    )
    try:
        return ArchitectureDesignResultV1(
            ok=True,
            workspace=command.workspace,
            design_id=_stable_design_id(command),
            status="completed",
            summary=summary,
            recommendation_paths=("runtime/state/architect/boundary-validation.json",),
        )
    except ValueError as exc:
        raise ArchitectDesignErrorV1(str(exc), code="invalid_architecture_design_result") from exc


__all__ = [
    "ArchitectAgent",
    "ArchitectConfig",
    "ArchitectService",
    "ArchitectureDoc",
    "generate_architecture_design",
]
```

- [ ] **Step 4: Run architect design tests**

Run:

```bash
cd src/backend
python -m pytest -q polaris/cells/architect/design/tests/test_contracts.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/backend/polaris/cells/architect/design/public/service.py src/backend/polaris/cells/architect/design/tests/test_contracts.py
git commit -m "feat: expose architecture design command wrapper"
```

---

### Task 5: Runtime Adapter Implementation

**Files:**
- Modify: `src/backend/polaris/cells/roles/runtime/public/contracts.py`
- Modify: `src/backend/polaris/cells/roles/runtime/public/service.py`
- Modify: `src/backend/polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py`

- [ ] **Step 1: Update Architect capability endpoint ref**

In `src/backend/polaris/cells/roles/runtime/public/contracts.py`, change the endpoint for `validate_cell_boundary_change` from:

```python
endpoint_ref="polaris.cells.architect.design.public.contracts.GenerateArchitectureDesignCommandV1",
```

to:

```python
endpoint_ref="polaris.cells.architect.design.public.service.generate_architecture_design",
```

- [ ] **Step 2: Add runtime imports**

In `src/backend/polaris/cells/roles/runtime/public/service.py`, add:

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError
```

near the existing standard library imports.

- [ ] **Step 3: Add runtime helper functions**

Add these helpers after `_payload_string_tuple`:

```python
def _capability_available_metadata(
    capability_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = dict(metadata or {})
    payload["capability_available"] = True
    payload["capability_id"] = capability_id
    return payload


def _run_with_timeout(callable_obj: Any, timeout_seconds: float) -> Any:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(callable_obj)
        return future.result(timeout=timeout_seconds)


def _check_workspace_guard_paths(
    *,
    paths: tuple[str, ...],
    operation: str,
    workspace_guard_service: Any | None,
    max_workers: int,
) -> tuple[bool, tuple[str, ...], str, str]:
    from polaris.cells.policy.workspace_guard.public.contracts import WorkspaceWriteGuardQueryV1
    from polaris.cells.policy.workspace_guard.public.service import check_workspace_write_guard

    if not paths:
        return True, (), "", ""

    def _check(path: str) -> tuple[str, bool, str]:
        query = WorkspaceWriteGuardQueryV1(path=path, operation=operation)
        if workspace_guard_service is None:
            decision = check_workspace_write_guard(query)
        else:
            decision = workspace_guard_service.check_workspace_write_guard(query)
        return path, bool(decision.allowed), str(decision.reason or "")

    workers = max(1, min(max_workers, len(paths)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        decisions = list(executor.map(_check, paths))

    for path, allowed, reason in decisions:
        if not allowed:
            return False, paths, path, reason
    return True, paths, "", ""
```

- [ ] **Step 4: Extend function signature and capability classifier**

Change the signature of `execute_role_capability_invocation` to include:

```python
    permission_service: Any | None = None,
    architect_design_service: Any | None = None,
```

after `workspace_guard_service`.

Add:

```python
    is_architect_boundary_validation = (
        capability.capability_id == "validate_cell_boundary_change"
        and capability.owner_cell == "architect.design"
        and capability.contract_name == "GenerateArchitectureDesignCommandV1"
    )
```

after `is_architect_workspace_guard`.

- [ ] **Step 5: Fix existing workspace guard denial semantics**

In the `intercept_illegal_mutations` branch, change the denial result field from:

```python
                allowed=True,
```

to:

```python
                allowed=False,
```

In the same branch, replace the metadata literal:

```python
        metadata = {
            "mutation_allowed": guard_result.allowed,
            "guard_reason": guard_result.reason,
            "path": guard_query.path,
            "operation": guard_query.operation,
        }
```

with:

```python
        metadata = {
            "capability_available": True,
            "mutation_allowed": guard_result.allowed,
            "guard_reason": guard_result.reason,
            "path": guard_query.path,
            "operation": guard_query.operation,
        }
```

- [ ] **Step 6: Add validate_cell_boundary_change branch**

Add this branch after the `intercept_illegal_mutations` branch and before QA verification:

```python
    if is_architect_boundary_validation:
        boundary_context = _payload_mapping(command.payload, "context")
        if boundary_context is None:
            return _capability_invocation_failure(
                command,
                allowed=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_boundary_context",
                error_message="payload.context must be a mapping when provided",
            )
        boundary_constraints = _payload_mapping(command.payload, "constraints")
        if boundary_constraints is None:
            return _capability_invocation_failure(
                command,
                allowed=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_boundary_constraints",
                error_message="payload.constraints must be a mapping when provided",
            )
        changed_paths = _payload_string_tuple(command.payload, "changed_paths")
        if changed_paths is None:
            return _capability_invocation_failure(
                command,
                allowed=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_boundary_changed_paths",
                error_message="payload.changed_paths must be a sequence of strings when provided",
            )
        target_cell = _payload_string(command.payload, "target_cell")
        if not target_cell:
            return _capability_invocation_failure(
                command,
                allowed=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_boundary_target_cell",
                error_message="payload.target_cell must be a non-empty string",
            )

        permission_context = {
            "resource_type": "api",
            "task_id": runtime_object.identity.task_id or "",
            "session_id": runtime_object.identity.session_id or "",
            "request_id": invocation.invocation_id,
            "capability_id": capability.capability_id,
            "target_cell": target_cell,
            "role_payload_ref": invocation.payload_ref,
            "role_fingerprint_ref": invocation.fingerprint_ref,
        }
        try:
            from polaris.cells.policy.permission.public.contracts import EvaluatePermissionCommandV1
            from polaris.cells.policy.permission.public.service import evaluate_permission

            permission_command = EvaluatePermissionCommandV1(
                role=role_id,
                action="execute",
                resource="architect.design:validate_cell_boundary_change",
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                context=permission_context,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                allowed=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_permission_command",
                error_message=str(exc),
            )

        try:
            if permission_service is None:
                permission_result = evaluate_permission(permission_command)
            else:
                permission_result = permission_service.evaluate_permission(permission_command)
        except Exception as exc:
            return _capability_invocation_failure(
                command,
                allowed=True,
                owner_cell=capability.owner_cell,
                error_code="permission_evaluation_failed",
                error_message=str(exc),
            )

        permission_metadata = {
            "permission_allowed": permission_result.allowed,
            "permission_reason": permission_result.reason,
            "permission_matched_policy": permission_result.matched_policy or "",
        }
        if not permission_result.allowed:
            return _capability_invocation_failure(
                command,
                allowed=False,
                owner_cell=capability.owner_cell,
                error_code="permission_denied",
                error_message=permission_result.reason or "permission denied",
                metadata=_capability_available_metadata(capability.capability_id, permission_metadata),
            )

        guard_allowed, checked_paths, denied_path, guard_reason = _check_workspace_guard_paths(
            paths=changed_paths,
            operation=_payload_string(command.payload, "operation", "write"),
            workspace_guard_service=workspace_guard_service,
            max_workers=int(command.payload.get("workspace_guard_max_workers", 8)),
        )
        guard_metadata = {
            **permission_metadata,
            "workspace_guard_allowed": guard_allowed,
            "checked_paths": checked_paths,
            "denied_path": denied_path,
            "guard_reason": guard_reason,
        }
        if not guard_allowed:
            return _capability_invocation_failure(
                command,
                allowed=False,
                owner_cell=capability.owner_cell,
                error_code="workspace_guard_denied",
                error_message=guard_reason or "workspace guard denied mutation",
                metadata=_capability_available_metadata(capability.capability_id, guard_metadata),
            )

        boundary_context.update(
            {
                "target_cell": target_cell,
                "changed_paths": changed_paths,
                "role_invocation_id": invocation.invocation_id,
                "role_payload_ref": invocation.payload_ref,
                "role_fingerprint_ref": invocation.fingerprint_ref,
                "role_capability_id": capability.capability_id,
                "permission_ref": "policy.permission:decision",
                "workspace_guard_ref": "policy.workspace_guard:decision",
            }
        )
        try:
            from polaris.cells.architect.design.public.contracts import GenerateArchitectureDesignCommandV1
            from polaris.cells.architect.design.public.service import generate_architecture_design

            design_command = GenerateArchitectureDesignCommandV1(
                workspace=_payload_string(command.payload, "workspace", runtime_object.identity.workspace),
                objective=_payload_string(command.payload, "objective"),
                constraints=boundary_constraints,
                context=boundary_context,
            )
        except (TypeError, ValueError) as exc:
            return _capability_invocation_failure(
                command,
                allowed=True,
                owner_cell=capability.owner_cell,
                error_code="invalid_architect_design_command",
                error_message=str(exc),
            )

        timeout_seconds = float(command.payload.get("timeout_seconds", 30.0))
        try:
            if architect_design_service is None:
                design_result = _run_with_timeout(
                    lambda: generate_architecture_design(design_command),
                    timeout_seconds,
                )
            else:
                design_result = _run_with_timeout(
                    lambda: architect_design_service.generate_architecture_design(design_command),
                    timeout_seconds,
                )
        except TimeoutError:
            return _capability_invocation_failure(
                command,
                allowed=False,
                owner_cell=capability.owner_cell,
                error_code="architect_design_timeout",
                error_message=f"architect design timed out after {timeout_seconds:g}s",
                metadata=_capability_available_metadata(capability.capability_id, guard_metadata),
            )
        except Exception as exc:
            return _capability_invocation_failure(
                command,
                allowed=True,
                owner_cell=capability.owner_cell,
                error_code="architect_design_failed",
                error_message=str(exc),
            )

        result_ref = f"architect.design:boundary-validation:{design_result.design_id}"
        metadata = {
            **guard_metadata,
            "design_id": design_result.design_id,
            "summary": design_result.summary,
            "recommendation_paths": tuple(design_result.recommendation_paths),
        }
        if not design_result.ok:
            return RoleCapabilityInvocationResultV1(
                ok=False,
                invocation_id=invocation.invocation_id,
                role_id=role_id,
                capability_id=capability.capability_id,
                command_contract=capability.contract_name,
                allowed=True,
                owner_cell=capability.owner_cell,
                payload_ref=result_ref,
                result_ref=result_ref,
                task_id=runtime_object.identity.task_id or "",
                status=design_result.status,
                metadata=metadata,
                error_code="architect_design_rejected",
                error_message=design_result.summary or "architect design rejected boundary change",
            )
        return RoleCapabilityInvocationResultV1(
            ok=True,
            invocation_id=invocation.invocation_id,
            role_id=role_id,
            capability_id=capability.capability_id,
            command_contract=capability.contract_name,
            allowed=True,
            owner_cell=capability.owner_cell,
            payload_ref=result_ref,
            result_ref=result_ref,
            task_id=runtime_object.identity.task_id or "",
            status=design_result.status,
            metadata=metadata,
        )
```

- [ ] **Step 7: Run runtime object tests**

Run:

```bash
cd src/backend
python -m pytest -q polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/backend/polaris/cells/roles/runtime/public/contracts.py src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py
git commit -m "feat: execute architect boundary validation capability"
```

---

### Task 6: Automated Import Fence

**Files:**
- Create: `src/backend/polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py`

- [ ] **Step 1: Add import-fence test**

Create `src/backend/polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
ROLES_RUNTIME_PUBLIC = BACKEND_ROOT / "polaris" / "cells" / "roles" / "runtime" / "public"


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def _is_foreign_cell_internal_import(module: str) -> bool:
    parts = module.split(".")
    if len(parts) < 6:
        return False
    if parts[0:2] != ["polaris", "cells"]:
        return False
    if "internal" not in parts:
        return False
    return parts[2:4] != ["roles", "runtime"]


def test_roles_runtime_public_does_not_import_foreign_cell_internal_modules() -> None:
    violations: list[str] = []
    for path in sorted(ROLES_RUNTIME_PUBLIC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for module in _imported_modules(path):
            if _is_foreign_cell_internal_import(module):
                rel = path.relative_to(BACKEND_ROOT).as_posix()
                violations.append(f"{rel} -> {module}")

    assert not violations, "roles.runtime.public must use foreign Cell public contracts:\n" + "\n".join(violations)
```

- [ ] **Step 2: Run import-fence test**

Run:

```bash
cd src/backend
python -m pytest -q polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py
```

Expected: PASS. This test name starts with `test_kernelone_`, so `run_kernelone_release_gate.py --mode all` discovers it through the existing glob.

- [ ] **Step 3: Commit**

```bash
git add src/backend/polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py
git commit -m "test: gate roles runtime public import boundaries"
```

---

### Task 7: Governance Synchronization

**Files:**
- Modify: `src/backend/polaris/cells/roles/runtime/README.agent.md`
- Modify: `src/backend/polaris/cells/roles/runtime/cell.yaml`
- Modify: `src/backend/polaris/cells/roles/runtime/context.pack.json`
- Modify: `src/backend/polaris/cells/policy/permission/README.agent.md`
- Modify: `src/backend/polaris/cells/policy/permission/cell.yaml`
- Modify: `src/backend/polaris/cells/policy/permission/generated/context.pack.json`
- Modify: `src/backend/polaris/cells/architect/design/README.agent.md`
- Modify: `src/backend/polaris/cells/architect/design/cell.yaml`
- Modify: `src/backend/polaris/cells/architect/design/context.pack.json`
- Modify: `src/backend/docs/graph/catalog/cells.yaml`
- Modify: `src/backend/docs/graph/subgraphs/execution_governance_pipeline.yaml`
- Modify: `src/backend/polaris/tests/architecture/test_roles_cell_governance.py`

- [ ] **Step 1: Sync roles.runtime governance text**

In `src/backend/polaris/cells/roles/runtime/README.agent.md`, append this design note:

```markdown
- Architect Cell boundary validation delegates lightweight authorization to
  `policy.permission`, checks unique changed paths through `policy.workspace_guard`,
  and invokes `architect.design` through `GenerateArchitectureDesignCommandV1`.
  Denied sandbox checks return `allowed=false`; mounted capability discoverability
  is represented only by `metadata.capability_available`.
```

In `src/backend/polaris/cells/roles/runtime/context.pack.json`, update the `notes` string so it includes:

```text
Architect boundary validation uses policy.permission EvaluatePermissionCommandV1, policy.workspace_guard WorkspaceWriteGuardQueryV1, and architect.design GenerateArchitectureDesignCommandV1 through public services; sandbox denials return allowed=false.
```

- [ ] **Step 2: Sync permission cell governance**

In `src/backend/polaris/cells/policy/permission/cell.yaml`, add:

```yaml
current_modules:
- polaris.cells.policy.permission.public.contracts
- polaris.cells.policy.permission.public.service
owned_paths:
- polaris/cells/policy/permission/internal/**
- polaris/cells/policy/permission/public/**
- polaris/domain/entities/capability.py
- polaris/domain/entities/policy.py
```

Keep existing contract lists and append this verification test:

```yaml
  - polaris/cells/policy/permission/public/tests/test_public_contracts.py
```

In `src/backend/polaris/cells/policy/permission/README.agent.md`, add:

```markdown
## Public Service

- `evaluate_permission(command: EvaluatePermissionCommandV1)` maps public role,
  action, resource, workspace, and lightweight context into a typed permission
  decision. It does not evaluate structural architecture deltas.
```

In `src/backend/polaris/cells/policy/permission/generated/context.pack.json`, add `polaris/cells/policy/permission/public/**` to `owned_paths`, add `public/service.py` to `read_order`, and add `evaluate_permission` to the summary text.

- [ ] **Step 3: Sync architect.design governance**

In `src/backend/polaris/cells/architect/design/cell.yaml`, add public service ownership:

```yaml
current_modules:
- polaris.cells.architect.design.public.contracts
- polaris.cells.architect.design.public.service
owned_paths:
- polaris/cells/architect/design/internal/**
- polaris/cells/architect/design/public/**
```

In `src/backend/polaris/cells/architect/design/README.agent.md`, add:

```markdown
## Public Service

- `generate_architecture_design(command: GenerateArchitectureDesignCommandV1)`
  returns a typed `ArchitectureDesignResultV1` for runtime boundary validation.
  Runtime callers must use this public service and must not import `internal/**`.
```

In `src/backend/polaris/cells/architect/design/context.pack.json`, update exports to:

```json
[
  "ArchitectService",
  "ArchitectConfig",
  "ArchitectureDoc",
  "generate_architecture_design"
]
```

- [ ] **Step 4: Sync graph catalog and subgraph**

In `src/backend/docs/graph/catalog/cells.yaml`, update:

- `policy.permission.current_modules` includes `polaris.cells.policy.permission.public.service`.
- `policy.permission.owned_paths` includes `polaris/cells/policy/permission/public/**`.
- `architect.design.current_modules` includes `polaris.cells.architect.design.public.service`.
- `architect.design.owned_paths` includes `polaris/cells/architect/design/public/**`.
- `roles.runtime.depends_on` includes `policy.permission`, `policy.workspace_guard`, and `architect.design`.
- `roles.runtime.effects_allowed` includes `architect.validate_cell_boundary`.

In `src/backend/docs/graph/subgraphs/execution_governance_pipeline.yaml`, ensure these edges exist:

```yaml
- from: roles.runtime
  to: policy.permission
  contract: EvaluatePermissionCommandV1
  effect: permission.evaluate
- from: roles.runtime
  to: policy.workspace_guard
  contract: WorkspaceWriteGuardQueryV1
  effect: mutation.guard:workspace
- from: roles.runtime
  to: architect.design
  contract: GenerateArchitectureDesignCommandV1
  effect: architect.validate_cell_boundary
```

- [ ] **Step 5: Update roles governance architecture test**

In `src/backend/polaris/tests/architecture/test_roles_cell_governance.py`, update the `expected` set in `test_roles_runtime_depends_on_matches_imports` to:

```python
    expected = {
        "archive.run_archive",
        "architect.design",
        "audit.diagnosis",
        "chief_engineer.blueprint",
        "cognitive.knowledge_distiller",
        "context.catalog",
        "context.engine",
        "director.execution",
        "factory.verification_guard",
        "finops.budget_guard",
        "llm.control_plane",
        "orchestration.pm_planning",
        "policy.permission",
        "policy.workspace_guard",
        "qa.audit_verdict",
        "roles.adapters",
        "roles.engine",
        "roles.kernel",
        "roles.profile",
        "roles.session",
        "runtime.state_owner",
        "runtime.task_market",
    }
```

- [ ] **Step 6: Run governance tests**

Run:

```bash
cd src/backend
python -m pytest -q polaris/tests/architecture/test_roles_cell_governance.py polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/backend/polaris/cells/roles/runtime/README.agent.md src/backend/polaris/cells/roles/runtime/cell.yaml src/backend/polaris/cells/roles/runtime/context.pack.json src/backend/polaris/cells/policy/permission/README.agent.md src/backend/polaris/cells/policy/permission/cell.yaml src/backend/polaris/cells/policy/permission/generated/context.pack.json src/backend/polaris/cells/architect/design/README.agent.md src/backend/polaris/cells/architect/design/cell.yaml src/backend/polaris/cells/architect/design/context.pack.json src/backend/docs/graph/catalog/cells.yaml src/backend/docs/graph/subgraphs/execution_governance_pipeline.yaml src/backend/polaris/tests/architecture/test_roles_cell_governance.py
git commit -m "docs: sync architect boundary validation governance"
```

---

### Task 8: Full Verification

**Files:**
- Verify all changed Python, governance, and runtime test paths.

- [ ] **Step 1: Run ruff check with fix**

```bash
cd src/backend
ruff check polaris/cells/roles/runtime/public/service.py polaris/cells/roles/runtime/public/contracts.py polaris/cells/policy/permission/public/service.py polaris/cells/policy/permission/internal/permission_service.py polaris/cells/architect/design/public/service.py polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py polaris/cells/policy/permission/public/tests/test_public_contracts.py polaris/cells/architect/design/tests/test_contracts.py polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py polaris/tests/architecture/test_roles_cell_governance.py --fix
```

Expected: command exits 0.

- [ ] **Step 2: Run ruff format**

```bash
cd src/backend
ruff format polaris/cells/roles/runtime/public/service.py polaris/cells/roles/runtime/public/contracts.py polaris/cells/policy/permission/public/service.py polaris/cells/policy/permission/internal/permission_service.py polaris/cells/architect/design/public/service.py polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py polaris/cells/policy/permission/public/tests/test_public_contracts.py polaris/cells/architect/design/tests/test_contracts.py polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py polaris/tests/architecture/test_roles_cell_governance.py
```

Expected: command exits 0.

- [ ] **Step 3: Run mypy**

```bash
cd src/backend
mypy polaris/cells/roles/runtime/public/service.py polaris/cells/roles/runtime/public/contracts.py polaris/cells/policy/permission/public/service.py polaris/cells/policy/permission/internal/permission_service.py polaris/cells/architect/design/public/service.py
```

Expected: `Success: no issues found`.

- [ ] **Step 4: Run focused pytest**

```bash
cd src/backend
python -m pytest -q polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py polaris/cells/policy/permission/public/tests/test_public_contracts.py polaris/cells/architect/design/tests/test_contracts.py polaris/tests/architecture/test_kernelone_roles_runtime_public_import_fence.py polaris/tests/architecture/test_roles_cell_governance.py
```

Expected: PASS.

- [ ] **Step 5: Run required role/runtime/task market suites**

```bash
cd src/backend
python -m pytest -q polaris/cells/roles/kernel/tests
python -m pytest -q polaris/cells/roles/runtime/tests
python -m pytest -q polaris/cells/runtime/task_market/tests
```

Expected: each command exits 0.

- [ ] **Step 6: Run KernelOne release gate**

```bash
cd src/backend
python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
```

Expected: command exits 0. The generated report under `workspace/meta/governance_reports/kernelone_release_gate_report.json` includes `test_kernelone_roles_runtime_public_import_fence.py`.

- [ ] **Step 7: Run diff hygiene checks**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. `git status --short` shows only intended changes and existing unrelated workspace artifacts.

- [ ] **Step 8: Final commit**

```bash
git add src/backend
git commit -m "test: verify architect boundary validation gates"
```

Expected: commit succeeds or reports no staged changes if previous task commits already captured every file.

---

## Self-Review

- Spec coverage: the plan covers `allowed=false` denial semantics, lightweight permission evaluation, deduped bounded workspace guard checks, architect design timeout handling, public contract wrappers, import-fence automation, graph/cell governance sync, and required gates.
- No placeholder scan: this plan does not use deferred implementation markers; every task has exact files, code blocks, commands, and expected results.
- Type consistency: runtime uses existing `EvaluatePermissionCommandV1`, `WorkspaceWriteGuardQueryV1`, and `GenerateArchitectureDesignCommandV1`; result refs use `architect.design:boundary-validation:<design_id>`.
- Risk: `permission.public.service._run_async` introduces a sync wrapper around an async service. Tests cover sync usage; any future async runtime entry should call a native async wrapper added in `policy.permission` instead of calling this sync function inside an event loop-heavy path.
