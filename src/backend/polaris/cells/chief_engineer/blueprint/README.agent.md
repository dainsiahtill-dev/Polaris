# Chief Engineer Blueprint

## Purpose

Generate task-level implementation blueprints and dependency analysis for Director execution without performing code write operations.

## Kind

`capability`

## Public Contracts

- commands: GenerateTaskBlueprintCommandV1, BuildChiefEngineerBlueprintPortfolioCommandV1
- queries: GetBlueprintStatusQueryV1, QueryBlueprintProvenanceV1 (`query_blueprint_provenance`), QueryProjectCompletionContractV1 (`query_project_completion_contract`)
- events: TaskBlueprintGeneratedEventV1
- results: TaskBlueprintResultV1, TaskBlueprintProvenanceSnapshotV1, ChiefEngineerBlueprintPortfolioV1, ProjectCompletionContractV1
- immutable authority values: ProjectKindAuthorityV1, VerificationCommandAuthorityV1
- errors: ChiefEngineerBlueprintErrorV1

`BuildChiefEngineerBlueprintPortfolioCommandV1` never accepts raw PM, catalog,
verifier-policy, project-kind, or command-authority hashes. Advisory builds
require an opaque exact-type carrier issued by Factory only after revalidating
the committed PM stage artifact. The carrier binds workspace/project/run,
PM stage event/hash, exact tasks, catalog snapshot/version/hash/receipt, and
verifier-policy/command receipts. CE revalidates carrier signature and live
catalog both before contract construction and immediately before persistence;
cross-run reuse, replay, lookalikes, direct construction, and catalog TOCTOU
fail closed. Offline diagnostic portfolios carry no execution authority.

## Depends On

Semantic verification tasks may own no artifacts. Their immutable CE interface
binds `task_read_context` to exact active artifacts owned by their dependency
closure, and the completion contract retains the task's required verification
recipe even when another task owns the test sources. Public generation and the
task-market consumer preserve `target_files=[]` and `required_write_targets=[]`;
the derived Job Token grants those exact reads and commands with no file writes.
An unbound context path, an unrelated artifact owner, a changed interface hash,
or a missing required recipe blocks the handoff. Legacy PM file ownership still
requires at least one target. Optional artifacts remain write capabilities,
while only required artifacts become materialization obligations.
CE-required verifier coverage also derives ancestor input paths for tasks that
write their own tests when PM supplied no context-file hints. Task generation
and Job Token derivation load the exact owner-persisted portfolio and check
workspace, run, portfolio bytes, and every interface/completion fragment before
granting capabilities. Rehashing a supplied fragment cannot widen reads; extra
PM proposal paths cannot widen semantic-task writes beyond CE artifact ownership.

- `context.engine`
- `control_plane.run_ledger`
- `control_plane.verifier_policy`
- `director.tasking`
- `llm.control_plane`
- `policy.permission`
- `policy.workspace_guard`
- `finops.budget_guard`
- `audit.evidence`

## State Ownership

- `runtime/state/blueprints/*`
- `runtime/blueprints/*`

## Effects Allowed

- `fs.read:workspace/**`
- `fs.write:runtime/state/blueprints/*`
- `fs.write:runtime/blueprints/*`
- `fs.delete:runtime/blueprints/*`
- `fs.write:runtime/events/runtime.events.jsonl`
- `llm.invoke:chief_engineer/*`

## Verification

- `tests/test_chief_engineer_preflight.py`

## Semantic PM intent and technical handoff

`pm.task_intent.v1` delegates topology and verification to Chief Engineer.
`ChiefEngineerPortfolioTaskV1` binds the immutable objective, observable
acceptance, explicit constraints and dependencies; empty PM target/scope/command
arrays are valid for this version. Legacy PM contracts retain their strict
exact target and command authority.

The signed Factory carrier binds delegation and any explicit user command
constraints. CE authors exact artifact ownership plus `argv`/`cwd` verifier
recipes, which must match the existing control-plane planning profiles before
portfolio persistence. Required explicit user commands remain required. Invalid
recipes report `invalid_chief_engineer_verification_recipe` and require contract
correction. Planning classification grants no execution authority; the existing
runtime command policy still checks actual content and executable identity.

A validated task blueprint derives its target files, scopes, allowed commands
and verifier commands from the immutable completion contract, while preserving
PM observable acceptance. Its Job Token binds the blueprint/completion hashes
and recipe hashes. Portfolio construction alone grants no Director authority;
the validated CE handoff remains mandatory. Empty cross-task invariants are
valid for a minimal intent plan when no shared invariant is needed.

`target_files` and Job Token write paths include required and optional authorized
artifacts. `required_write_targets` separately projects required task-owned
artifacts; optional absence never becomes a required write failure. Public status
uses `None` for legacy snapshots without this projection and an explicit empty
tuple when no write is required. Allowed commands include optional capabilities,
while task verifier commands include only required verification obligations.
Product acceptance stays available as PM context; each task retains its own
observable acceptance without requiring a downstream product prematurely.
