# ADR-0108: CE delegated topology is an explicit bounded authority

Status: Accepted  
Date: 2026-08-20  
Encoding: UTF-8

## Context

PM owns delivery intent and verifier policy, while Chief Engineer now owns concrete source topology
for deterministic fallback contracts. The delegation was stored only in free task metadata and was
lost at the Factory-to-CE contract boundary. CE was required to invent a source entrypoint but its
artifact and command were later deleted because they were not exact PM paths/commands.

## Decision

Topology delegation becomes a typed, immutable fact on `ChiefEngineerPortfolioTaskV1` and therefore
part of the Factory-issued carrier signature. `chief_engineer.blueprint` may accept a CE-created source
artifact only for its explicit delegated owner and a safe relative source-topology path. Entrypoint
execution authority may be resolved only by a deterministic command/path rule and is then frozen as
an exact `VerificationCommandAuthorityV1` in the project completion contract.

This is not general CE shell authority. Non-delegated tasks retain exact PM authority. Unsafe paths,
toolchain paths, owner mismatch, unsupported commands and ambiguous ownership fail closed.

## Consequences

- PM can delegate topology without prescribing one hard-coded directory layout.
- CE-owned applications can produce an executable completion contract instead of failing by design.
- Director and QA still consume exact hashed artifacts, owners and command authorities.
- Each new delegated language command rule requires focused correlation tests; no arbitrary command
  parser or shell fallback is permitted.
