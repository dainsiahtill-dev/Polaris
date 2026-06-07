# Architect Boundary Validation Design

## Purpose

Implement the `architect` role capability `validate_cell_boundary_change` as a real Cognitive OS runtime port. The capability validates proposed Cell boundary changes before downstream mutation, using typed public contracts rather than prompt text or transcript state.

This design completes the missing Architect half of the Phase 4 sandbox slice. It does not claim the full RoleRuntimeObject program is complete.

## Current State

`roles.runtime` already composes stateful runtime objects and now has an `architect` runtime spec with these mounted asset references:

- `ConstraintTopology`: source of truth is `docs/graph/**`, represented through graph/catalog references.
- `ContextBudgetProfile`: owner is `finops.budget_guard`.
- `MutationBoundaryMap`: owner is `policy.workspace_guard`, with `policy.permission` as the permission decision owner.

`architect` already has these mounted capability descriptors:

- `allocate_context_token_budget` -> `ReserveBudgetCommandV1`
- `intercept_illegal_mutations` -> `WorkspaceWriteGuardQueryV1`
- `validate_cell_boundary_change` -> `GenerateArchitectureDesignCommandV1`

The first two ports have runtime adapters. `validate_cell_boundary_change` is currently only mounted; it still needs an invocation adapter.

## Non-Goals

- Do not create a second graph truth, Task Market, Turn Ledger, Handoff Pack, or Receipt Store.
- Do not write PM/CE/Architect/QA business objects into `polaris/kernelone/roles/`.
- Do not let `roles.runtime` own `architect.design`, `policy.permission`, or `policy.workspace_guard` state.
- Do not allow natural-language handoff or transcript content to become source of truth.
- Do not introduce legacy compatibility paths or prompt fallback behavior.

## Cell Ownership

- `roles.runtime` owns only runtime composition, mounted ports, typed invocation validation, and structured capability results.
- `architect.design` owns architecture design results and design-state artifacts under `runtime/state/architect/*`.
- `policy.permission` owns capability/action permission decisions.
- `policy.workspace_guard` owns workspace path and mutation guard decisions.
- `docs/graph/**` remains the source of truth for Cell topology, ownership, dependencies, state owners, and allowed effects.

## Public Contracts

The invocation adapter must use only public contracts and public service exports:

- `EvaluatePermissionCommandV1`
- `PermissionDecisionResultV1`
- `WorkspaceWriteGuardQueryV1`
- `WorkspaceGuardDecisionV1`
- `GenerateArchitectureDesignCommandV1`
- `ArchitectureDesignResultV1`
- `RoleCapabilityInvocationResultV1`

The `roles.runtime.public` package must not import any owner Cell `internal` module.

## Invocation Flow

1. `execute_role_capability_invocation()` runs the existing generic runtime gates:
   - invocation role matches runtime object role
   - capability is mounted
   - role is in `allowed_roles`
   - invocation contract equals mounted contract
   - invocation fingerprint equals the runtime object's `RoleCapabilityFingerprint`

2. If the capability is `validate_cell_boundary_change`, `roles.runtime` validates payload shape:
   - `target_cell`: non-empty Cell id
   - `change_id`: defaults to invocation id when omitted
   - `objective`: non-empty validation objective
   - `changed_paths`: sequence of path strings
   - `effects_requested`: sequence of effect strings
   - `depends_on_delta`: mapping
   - `state_owner_delta`: mapping
   - `metadata`: mapping

3. `roles.runtime` constructs `EvaluatePermissionCommandV1`:
   - `role`: `architect`
   - `action`: `validate_cell_boundary_change`
   - `resource`: `target_cell`
   - `workspace`: runtime workspace
   - `context`: change id, payload ref, fingerprint ref, changed paths, requested effects, dependency delta, state owner delta

4. If permission is denied, return a structured `RoleCapabilityInvocationResultV1`:
   - `ok=False`
   - `allowed=True`
   - `owner_cell="policy.permission"`
   - `error_code="permission_denied"`
   - no workspace guard or architect design call is made

5. If permission is allowed, `roles.runtime` checks each changed path with `WorkspaceWriteGuardQueryV1`.

6. If any workspace guard decision denies, return a structured result:
   - `ok=False`
   - `allowed=True`
   - `owner_cell="policy.workspace_guard"`
   - `error_code="workspace_guard_denied"`
   - no architect design call is made

7. If all guards allow, `roles.runtime` constructs `GenerateArchitectureDesignCommandV1`:
   - `workspace`: runtime workspace
   - `objective`: payload objective
   - `constraints`: target cell, graph source ref, changed paths, effects requested, depends-on delta, state owner delta
   - `context`: permission decision, workspace guard decisions, role invocation refs, capability fingerprint ref, asset mount refs

8. `roles.runtime` calls `architect.design` through a public service adapter.

9. The success result uses:
   - `ok=True`
   - `owner_cell="architect.design"`
   - `result_ref="architect.design:boundary-validation:<design_id>"`
   - `status` copied from `ArchitectureDesignResultV1.status`
   - metadata carrying `design_id`, `recommendation_paths`, permission details, and workspace guard decision details

## Error Handling

All refusals and adapter failures are typed:

- `capability_not_mounted`: non-Architect role attempts this capability.
- `capability_fingerprint_mismatch`: mounted capability was not unlocked by the current fingerprint.
- `invalid_boundary_change_payload`: payload shape is invalid.
- `permission_denied`: `policy.permission` refused the boundary validation.
- `workspace_guard_denied`: at least one path guard denied the mutation.
- `architect_design_failed`: the public architecture design call failed.
- `boundary_validation_rejected`: `architect.design` returned `ok=False`.

No error path writes another Cell's internal state.

## Testing

Add tests in `roles.runtime.public.tests.test_role_runtime_object_contracts`:

- Architect success path calls permission, workspace guard, and architect design public-contract fakes.
- Permission denial stops before workspace guard and architect design.
- Workspace guard denial stops before architect design.
- Non-Architect invocation is denied before any owner service call.
- Fingerprint mismatch is denied before any owner service call.

Add owner Cell wrapper tests if new public service wrappers are introduced.

## Verification

After implementation, run:

- `python -m ruff check --fix <changed_python_files>`
- `python -m ruff format <changed_python_files>`
- `python -m mypy <changed_python_files>`
- `python -m pytest -q polaris/cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py`
- `python -m pytest -q polaris/cells/roles/kernel/tests`
- `python -m pytest -q polaris/cells/roles/runtime/tests`
- `python -m pytest -q polaris/cells/runtime/task_market/tests`
- `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`
- `git diff --check`
- `rg` scan proving `roles.runtime.public` does not import owner Cell `internal` modules

## Self-Review

- Placeholder scan: no TBD or TODO entries.
- Consistency check: `roles.runtime` remains an adapter/composition boundary, not an owner of Architect or policy state.
- Scope check: the design is limited to the Architect boundary-validation capability and its required sandbox checks.
- Ambiguity check: refusal order is explicit: runtime gates, permission, workspace guard, then architect design.
