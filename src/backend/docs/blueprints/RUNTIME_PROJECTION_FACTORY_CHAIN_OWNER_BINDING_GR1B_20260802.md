# GR1B / GR1C / GR1D-B1 — Runtime Projection Owner Ports

Status: `accepted` for GR1C and GR1D-B1; GR1D-wide SCC remediation remains active

## Goal

Retain GR1B Factory chain ownership and fail-closed outcome semantics while
removing the static `runtime.projection -> factory.pipeline` Cell edge.  GR1D-B1
extends the same composition rule to the Director status read and removes one
separately-proven stale catalog edge.  It does not claim global SCC closure.

## Classification

Structural. GR1B placed the Factory public query inside the projection Cell,
making a read-only authority boundary also a static Cell dependency. GR1C moves
cross-Cell composition to bootstrap without changing the authoritative owner.

## Assumption register

1. `factory.pipeline.public.get_factory_chain_projection` remains the sole
   Factory chain-fact query and returns its exact self-validating public DTO.
2. `runtime.projection` needs only normalized immutable observation fields; it
   does not need Factory types or imports.
3. Process bootstrap may bind one adapter exactly once. Rebinding the same
   object is idempotent; absence or a different object fails closed.
4. Callers may provide workspace, run id, and typed non-Factory claims only.
   They may not provide a port, Factory DTO, projection hash, or chain evidence.
5. GR1B reduction semantics remain unchanged: delivery is independent,
   control-plane failure is never guessed, and non-authoritative input cannot
   yield `COMPLETED_VERIFIED`.

## Architecture

```text
caller
  | ProjectOutcomeFactoryOwnerQueryV1(workspace, run_id, non-Factory claims)
  v
runtime.projection public service
  | obtains process-bound projection-owned observation port
  v
FactoryChainOwnerObservationPortV1
  | returns FactoryChainOwnerObservationV1 only
  v
runtime.projection reducer and binding validation

bootstrap composition root
  | sole import of factory.pipeline.public.get_factory_chain_projection
  v
FactoryChainOwnerObservationAdapter
  | exact Factory DTO + canonical workspace/run + hash/evidence validation
  v
Factory owner public query
```

## Module responsibilities

- `runtime.projection.public.contracts`: owns immutable normalized observation,
  observation protocol, query, result, and typed error contracts. No Factory
  import.
- `runtime.projection.internal.project_outcome_factory_owner`: owns one process
  binding slot, bootstrap-only bind semantics, observation-to-axis mapping, and
  outcome reduction. No Factory import.
- `polaris.bootstrap.runtime_projection_factory_owner`: sole cross-Cell
  composition adapter. Imports the exact Factory public query/DTO, validates
  identity and evidence, converts to the projection-owned observation, and
  binds one singleton adapter.
- `polaris.delivery.http.app_factory`: invokes bootstrap composition only; it
  never imports Factory contracts for projection.

### GR1D-B1 Director status cut

`runtime.projection` previously imported and resolved
`director.execution.service.DirectorService` at two projection call sites.
The replacement is intentionally symmetrical with the Factory port:

```text
runtime.projection readers
  -> DirectorStatusObservationPortV1
  -> DirectorStatusObservationV1

polaris.bootstrap.runtime_projection_director_status
  -> DirectorService.get_status()
  -> validates requested workspace identity
  -> binds one process singleton during app composition
```

The projection Cell owns only the typed observation/port and its unavailable
semantics.  The bootstrap adapter is the only cross-Cell importer of
`DirectorService`.  An unbound, invalid, identity-mismatched, or failed
observation is projected as `running=false`, `source=none`, `status=null`, and
an explicit typed `projection_error`; it can never manufacture a ready
Director. This applies to both the asynchronous runtime projection and the
synchronous HTTP/artifact helper: neither may collapse unavailable, timeout,
or owner-query failure into a normal idle status.

`director.execution -> factory.pipeline` was separately proven to be stale
metadata (there is no production `factory.pipeline` import/call), so GR1D-B1
removes that declaration while retaining the truthful
`director.execution -> factory.cognitive_runtime` coupling.

## Data and error flow

1. Bootstrap binds its singleton adapter. Missing binding raises
   `factory_chain_owner_port_unbound`; conflicting rebind raises
   `factory_chain_owner_port_conflicting_rebind`.
2. Caller submits exact `ProjectOutcomeFactoryOwnerQueryV1`; its field set stays
   `workspace`, `run_id`, `claims`.
3. Adapter calls the Factory public owner query and accepts only exact
   `FactoryChainProjectionV1`.
4. Adapter verifies canonical workspace/run identity, stable Factory projection
   hash, event evidence, completion evidence, and constructs the exact
   projection-owned observation.
5. Projection consumes only that observation. Query/validation failures become
   typed fail-closed owner-observation errors.

## Preserved GR1B mapping

- unavailable: `NOT_STARTED`
- completed evidence: `COMPLETED`
- `pending`, `running`, `paused`, `recovering`: `ACTIVE`
- every other available state: `INCOMPLETE`

No state maps to `CONTROL_PLANE_FAILED` from Factory status alone. Returned
`ProjectOutcomeV1` remains `authority_bound=false` and
`completed_verified=false`; Factory observation binds only the chain axis.

## Pre-mortem

- Wrong layer imports Factory: static dependency test scans all projection
  Python and metadata for `polaris.cells.factory` / `factory.pipeline`.
- Test or caller injects authority: exact query-field regression rejects port,
  DTO, hash, and evidence fields.
- Bootstrap runs twice: same singleton bind is idempotent; distinct bind fails.
- Adapter weakens Factory DTO validation: exact-type, identity, projection hash,
  event count/ref, and completion ref tests fail closed.
- Unbound unit/runtime path silently guesses: typed unbound test requires an
  explicit error.
- Director status recreates the old cycle: static source regression forbids
  `director.execution` imports under projection and proves the bootstrap
  adapter is the sole composition importer.
- A stale declaration is deleted as if it were a runtime decoupling: the
  regression proves no `factory.pipeline` production import while requiring
  `factory.cognitive_runtime` to remain declared.

## Verification plan

1. Red tests for unbound/conflicting binding and observation-only consumption.
2. Red adapter tests for exact Factory DTO, identity, hash/evidence, and mapping.
3. Static test proving zero Factory imports/dependencies under
   `runtime.projection` and its catalog/context metadata.
4. Focused runtime projection, Factory chain, bootstrap, public-contract, and
   metadata tests.
5. Ruff format/check, Mypy, descriptor regeneration/consistency, catalog audit,
   and scoped `git diff --check`.

## Complexity

Binding and validation are O(1) except immutable stage/event tuple validation,
which is O(S + E) time and O(S + E) space. Projection reduction remains linear
in evidence tuple sizes. No new I/O, retry, polling, Provider, or Bench path is
introduced.

## GR1C re-verification evidence

- projection + Factory chain + bootstrap focused tests: `173 passed`
- app bootstrap + descriptor/catalog reconciliation tests: `31 passed`
- Ruff check/format: passed on eight changed Python files
- Mypy: passed on five production files
- catalog governance audit-only gate: exit code `0`, no new issues and no
  manifest/catalog mismatch; two unrelated pre-existing blocker fingerprints
  remain outside GR1C scope
- CodeGraph post-edit path: public query -> projection-owned port -> bootstrap
  adapter -> Factory public owner query

Independent main-agent review remains required before accepted closure.

## GR1D-B1 implementation evidence

- Director port/bootstrap/edge regression: `15 passed`
- projection + Factory chain + both bootstrap adapters: `188 passed`
- runtime status and projection observability compatibility: `10 passed`
- independent review found and the main agent closed the sync-path
  unavailable-versus-idle information-loss defect; targeted status,
  observability, and port tests: `35 passed`
- targeted descriptor generation completed; its pre-existing missing-required-
  field-descriptor warning remains visible and is not reclassified as a GR1D
  success signal

Closure evidence:

- scoped Ruff/format, Mypy, `git diff --check`, and strict UTF-8/YAML/JSON
  parsing: passed
- catalog governance audit-only: exit `0`, `new_issue_count=0`,
  `new_mismatch_count=0`; two pre-existing blocker fingerprints remain outside
  this bucket
- independent GR1D-B1 re-review: `CLEAR`; it specifically verified that sync
  unavailable/query-failure/timeout states retain explicit error evidence while
  valid `IDLE` remains a clean owner status

## Explicit residual graph boundary

The original shortest catalog route was
`runtime.projection -> director.execution -> factory.pipeline ->
runtime.projection`.  GR1D-B1 removes its two classified edges; it does **not**
assert that the global 49-Cell / 334-edge SCC disappeared.  In particular,
`factory.pipeline -> runtime.projection` remains a real LLM-readiness import
until a future owner-port wave classifies and replaces it.  GR1D stays active;
GR1D-B1 is accepted, but it does not seal the global SCC.
