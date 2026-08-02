# Factory Chain Projection GR1A

Date: 2026-08-02  
Status: implementation blueprint  
Bucket: GR1A_FACTORY_CHAIN_OWNER_QUERY

## Objective

Expose one typed, read-only `factory.pipeline` public query that proves the
Factory-owned chain state for an exact workspace and run. This is a prerequisite
for GR1 ProjectOutcome owner binding; `runtime.projection` must not inspect
Factory private files, internal stores, or free-text logs.

## Ownership

- `factory.pipeline` remains the sole owner of Factory run/stage facts.
- The new API reads strict Factory-owned run/event snapshots through a
  zero-write reader; constructing a query reader must not create runtime or
  lock-authority directories.
- It performs no writes, retries, scheduling, Provider calls, Bench work, or
  target-project changes.
- GR0 `ProjectOutcomeV1` remains sealed and unchanged in this bucket.

## Public contract

Add exact typed contracts to `factory.pipeline.public`:

- `GetFactoryChainProjectionQueryV1(workspace, run_id)`
- `FactoryChainProjectionV1`
- `get_factory_chain_projection(query)` (async)

The result must include:

- exact canonical workspace and run id
- explicit source/schema markers identifying the Factory owner query
- `available`
- Factory status
- configured/completed/failed stages
- missing configured stages
- `chain_completed`
- canonical Factory event count and stable event refs
- the successful terminal completion-event ref, when present
- deterministic `projection_hash`
- source/schema markers proving `factory.pipeline` ownership

## Completion invariant

`chain_completed=True` iff all hold:

1. the exact run exists;
2. `get_factory_chain_projection(query)` directly read the private Factory
   owner snapshots for this exact workspace/run;
3. configured stages are non-empty;
4. Factory status is `completed`;
5. failed stages are empty;
6. every configured stage is present in completed stages;
7. exactly one strict owner event has `type=completed` and `success=true`, and
   its stable ref is present in the projected event refs.

Missing runs return a typed `available=False` projection and never complete.
Raw strings, coerced identities, duplicated stages, mismatched run ids, or
directly constructed inconsistent result objects fail closed.

## Evidence identity

Event refs must be derived from the owner-returned event rows, not invented by
the caller. Prefer the strict chain event hash, then an existing
event/content/append id; otherwise use a
deterministic SHA-256 of canonical UTF-8 JSON. `projection_hash` binds the
normalized run facts, event refs, and terminal completion-event ref. The public
query accepts no service/store injection seam; tests may patch the private
reader constructor only. A projection DTO is evidence, not an authorization
capability: Python-level constructor privacy cannot confer authority. GR1B must
issue this query itself and is forbidden from accepting a caller-supplied
`FactoryChainProjectionV1` as proof of Factory ownership. Low-level hash/ref
helpers are not canonical package exports.

The reader resolves runtime storage only through the `storage.layout` public
`ResolveExistingRuntimeRootReadOnlyQueryV1`. On a cold cache it returns
unavailable when no existing namespace can be proven; it must never create a
directory, write a probe, refresh a storage cache, or acquire a lock authority.

## Validation

- focused unit tests for success, missing run, incomplete stage, failed stage,
  empty config, missing events, exact query types, deterministic normalization,
  and direct-result invariant rejection
- existing Factory public/service tests
- Ruff, format, Mypy, architecture manifest/catalog/descriptor gates
- independent main-agent diff review

## Out of scope

- ProjectOutcome owner aggregation (GR1B)
- TaskRuntime, Run Ledger, TaskBoundary, or QA changes
- Factory execution behavior
- HTTP/WS transport
- Bench or Provider execution
