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
