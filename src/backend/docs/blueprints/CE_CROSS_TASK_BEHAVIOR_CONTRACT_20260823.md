# CE cross-task behavior contract

Status: Implemented; live repair path hardened, incremental repair envelope pending  
Date: 2026-08-23  
Owner: `chief_engineer.blueprint`

## Problem

Exact run `factory_a9812b43a06a` reached same-run Director repair with real
edits and stable Go verifier identities, but two named physics tests could not
converge. The immutable CE portfolio assigned source and test files to
different tasks without defining one shared coordinate-system and floor
contract. Source and tests therefore invented incompatible semantics.

This is an authority defect, not a missing regex repair: once the portfolio is
immutable, Director may repair only inside the same task contract and must not
restart PM or CE.

## Architecture

```text
PM task graph
  -> CE structured portfolio response
       -> shared_behavior_contract.behavior_invariants
       -> task_plans[*].behavior_invariant_refs
       -> pre-freeze feasibility validator
            -> reject unknown/duplicate/unconsumed invariants
            -> require source/test cross-owner linkage
       -> immutable CE portfolio + independent stable contract hash/ref
            -> every task blueprint
                 -> Director handoff/final request
                      -> source/test implementation share one semantic SSoT
```

## Contract

Each invariant is domain-neutral and contains:

- stable `invariant_id`;
- precise `statement`;
- one `owner_task_id`;
- one or more `consumer_task_ids`;
- at least one `verification_example` with non-empty `given`, `when`, `then`.

Every task plan names `behavior_invariant_refs`. The owner and every consumer
must reference the invariant. When project completion obligations put
production artifacts and test artifacts in different tasks, every test owner
must share at least one invariant with a production owner.

The validator does not understand physics, games, languages, or frameworks.
It verifies identity, coverage, and cross-owner consumption only. CE remains
responsible for choosing concrete conventions such as signs, units, boundary
half-spaces, ordering, and rounding.

## Invariants

1. The behavior contract is frozen before Director dispatch and has its own
   hash/ref. It must not inherit the advisory-only authority semantics of the
   project-interface declarations.
2. Director receives the exact same contract reference and invariant payload
   for all linked tasks.
3. Unknown task ids, dangling refs, owner-only invariants, or test owners with
   no production-owner invariant fail closed before persistence.
4. Offline diagnostic portfolios remain non-authoritative and need not invent
   behavior invariants.
5. No target-project code is changed by this platform hardening.

## Assumption register

- A1: source/test cross-task semantic drift is preventable only before the CE
  portfolio freezes. Verified by the preserved L3-22 run.
- A2: portfolio task context is the existing shared cross-task projection
  carrier, but behavior needs an independent typed authority contract.
  Verified by the project-interface DTO being explicitly advisory-only.
- A3: structured examples are sufficient to bind sign/boundary conventions
  without domain-specific platform logic. Verified by typed-contract and
  pre-freeze feasibility tests.
- A4: historical persisted portfolios remain readable; new Factory provider
  requests and transport validation require the behavior field, while direct
  legacy V1 construction remains explicitly compatible. Verified by the
  public-contract and Factory characterization suites.

## Pre-mortem

- Models may emit vague statements. Mitigation: require concrete given/when/then
  examples and task references.
- Strictness may block single-task projects. Mitigation: cross-owner linkage is
  required only when production and test obligations have different owners.
- A valid contract may be persisted but omitted from Director context.
  Mitigation: handoff characterization asserts invariant ids/statements/examples.
- Hashing may omit the new contract. Mitigation: mutation test proves changing
  an invariant changes the behavior and portfolio hashes.

## Verification

- `208 passed`: CE public contracts plus pure feasibility tests cover
  normalization, serialization, reference closure, cross-owner rejection,
  hashing, persistence, and projection.
- `60 passed`: Factory CE characterization proves one-call portfolio success,
  bounded schema/semantic repair, progress-aware three-call capping, unique
  TaskRuntime identities, and that deterministic fallback cannot bypass the
  cross-task behavior gate.
- Director handoff characterization proves the invariant id, statement, and
  concrete example reach the task handoff.
- Exact offline replay of preserved run `factory_a9812b43a06a` returned
  `blueprint_portfolio_behavior_contract_infeasible` for TASK-1/TASK-2/TASK-3
  before persistence; the generated target workspace was read-only.
- Ruff is clean for the changed production/new-test surface. Mypy is clean for
  eight changed source files.
- Fresh isolated Bench `factory_4c0f4a28473f` proved the new contract reached
  the physical MiniMax provider tool schema. The provider returned a
  semantically complete portfolio (11 artifacts, 7 production paths, 2 test
  paths), but displaced nested members such as `task_plans`, `TASK-2`,
  `TASK-3`, `shared_behavior_contract`, and `provider_declarations` to the
  closed root object. Strict transport validation rejected that shape; the
  bounded repair then consumed 8,192 completion tokens without a tool call.
  No Director task ran and no target-project file changed.

## Provider-envelope recovery hardening

The roles-kernel transport may recover this exact structural defect before
spending a second Provider call. Recovery is generic and schema-proven:

1. apply only when the current closed object has unknown members;
2. relocate an unknown member only when its name has exactly one declared
   descendant property path in the caller-owned JSON Schema;
3. preserve existing destination members and reject collisions;
4. reject every residual unknown root member; an open descendant object is not
   a sink for arbitrary Provider keys;
5. record the normalization policy in structured-output transport evidence;
6. otherwise remain fail-closed and use the existing bounded repair path.

This does not invent CE semantics or weaken completion/behavior feasibility.
It turns an already complete physical Provider result into the exact shape the
caller declared, avoiding one expensive and less reliable reconstruction call.
Fresh isolated Bench remains the final live acceptance gate.

## Fresh isolated live progression

All runs used current source, isolated backends, full `5400s` project budget,
project-local `.polaris/runtime`, and no generated-project edits.

- `factory_1d8521d1ae19` (`r03`): transport schema recovered far enough to
  expose an immutable delivery-depth deficit (`prod_files=4 < 7`). This proved
  deadline projection was not the blocker.
- `factory_39c6a74ecfe3` (`r04`): one schema-valid CE candidate reached owner
  projection. A behavior invariant repeated its owner in
  `consumer_task_ids`; DTO construction failed before semantic repair. The
  pre-projection validator now instantiates the typed invariant contract and
  routes this exact defect into bounded same-CE repair.
- `factory_ef271ece40a9` (`r05`): primary output had no JSON; repair omitted
  `project_completion_contract`. The executor previously stopped after the
  second schema error even though the diagnostic changed. It now admits one
  distinct final repair only when schema diagnostics progress, or when a
  schema-valid candidate still violates the immutable semantic contract.
- `factory_578960eba640` (`r06`): all three distinct TaskRuntime attempts ran.
  Call 1 misplaced `verification`; call 2 became schema-valid but had
  `prod_files=6 < 7`; call 3 supplied seven production and three test paths,
  but its final README artifact truly omitted `path`, `semantic_role`, and
  `owner_task_id`. No alias existed, so transport correctly failed closed.

The remaining architecture gap is not another retry. Semantic repair still
asks the Provider to regenerate the entire portfolio, so fixing one deficit can
damage unrelated already-valid fields. Next hardening must preserve the last
schema-valid candidate and request a typed incremental patch, compose it under
CE authority, then revalidate the complete portfolio. Until that exists,
`L3-22` remains not completed.
