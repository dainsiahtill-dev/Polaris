# CE Delegated Topology Entrypoint Authority Blueprint

Status: Implementing  
Date: 2026-08-20  
Encoding: UTF-8

## Problem

PM deterministic synthesis now delegates concrete source topology to Chief Engineer and records
`topology_authority=chief_engineer` plus `required_source_kinds`. The Factory projection currently
drops that delegation when it creates `ChiefEngineerPortfolioTaskV1`. Completion-contract assembly
then accepts only exact PM target paths and exact PM entrypoint commands. An application therefore
requires CE to choose an entrypoint while simultaneously forbidding CE from registering it.

L3-21 run `factory_f11604129d5b` proved the contradiction: the final CE provider request was complete,
and the persisted structured candidate declared a valid Python package entrypoint, but the contract
normalizer deleted it and raised `application project requires a required entrypoint`.

## Architecture

```text
Committed PM task metadata
  topology_authority + required_source_kinds
              |
              v
Factory authority projection (signed carrier)
  ChiefEngineerPortfolioTaskV1 delegation facts
              |
              v
CE structured candidate
  source artifact + entrypoint obligation
              |
              v
chief_engineer.blueprint validation
  safe relative source path
  exact delegated owner
  required entrypoint kind
  deterministic command/path correlation
              |
              v
ProjectCompletionContractV1
  immutable artifact + entrypoint + verifier authority
              |
              v
Task-local JobToken
  PM exact paths + signed CE-owned artifact paths
  sibling-owned paths excluded
              |
              v
Blueprint provenance
  top-level target_files == PM paths + task-owned completion artifacts
  project-level CE topology satisfies support-task provenance
```

## Module responsibilities

- `factory.pipeline`: projects only committed PM delegation metadata into the opaque authority carrier.
- `chief_engineer.blueprint` contracts: preserve delegation as typed, hash-bound task facts.
- `chief_engineer.blueprint` portfolio builder: accepts CE-created source paths only for explicitly
  delegated owners and source topology; resolves a delegated Python entrypoint only when command and
  source path form one deterministic safe pair.
- `ProjectCompletionContractV1`: remains the immutable authority consumed by Director/QA.

## Safety invariants

1. No delegation metadata means existing exact-PM path and command rules remain unchanged.
2. Delegated paths must be normalized relative source paths; absolute, traversal, malformed and
   toolchain/manifest paths remain rejected.
3. The CE artifact owner and entrypoint owner must be the same explicit delegated PM task.
4. Delegated entrypoint commands are not arbitrary shell. The command must match a deterministic
   language/path rule; the first supported rule is Python package `__main__.py`.
5. The resolved command is materialized as an exact `VerificationCommandAuthorityV1`, included in
   the immutable completion-contract hash and reused by handoff/QA.
6. Optional or uncorrelated CE entrypoint suggestions remain dropped.
7. Director JobToken write scope is derived from the immutable completion contract's
   task-owned artifacts. Raw CE suggestions and sibling-owned artifacts never widen it.
8. The persisted blueprint, Director execution profile, ownership registry and JobToken use the
   same expanded task-local target set. Provenance rejects any dropped task-owned completion
   artifact, while a tests/docs support task may rely on source topology named elsewhere in the
   same immutable project-completion contract.
9. Factory stage binding freezes both the embedded PM baseline projection and the expanded CE
   target projection. Their hashes may differ under delegated topology; exact immutable
   revalidation, not hash equality, proves that neither projection drifted.

## Verification

- DTO/carrier tests prove delegation survives hashing and identity checks.
- Portfolio tests prove the L3-21 shape succeeds and produces exact entrypoint verifier authority.
- Negative tests prove non-delegated, traversal, mismatched module and ambiguous-owner cases fail.
- Handoff tests prove the JobToken covers the delegated owner while excluding sibling artifacts.
- Provenance tests prove producer and consumer agree on expanded target authority and fail closed
  when a signed task-owned artifact is removed.
- Existing Chief Engineer portfolio suite, Ruff, Mypy and focused Factory tests stay green.
- The isolated L3-21 instance is restarted to load current source, then the existing Factory run is
  retried only from `chief_engineer_review`; PM evidence is reused.
