# ADR-0109: Runtime state is project-local by default

Status: Accepted  
Date: 2026-08-20  
Encoding: UTF-8

## Context

Polaris project metadata and history are rooted at `<workspace>/.polaris`, but
Instance Registry and Factory Bench claim `<workspace>/runtime` while the storage
resolver rejects that in-workspace path and writes to a global system cache. One
project therefore has multiple runtime identities, breaking lock, cleanup and
evidence locality.

## Decision

Default runtime root is `<workspace>/.polaris/runtime`. `storage.layout` is the
only selection authority. Explicit external roots and RAM-disk deployments remain
supported and are workspace-key namespaced. New callers must consume the resolved
or canonical project-local root; they may not construct `<workspace>/runtime`.

Legacy external namespaces remain read-only discoverable. Polaris will not
automatically copy, move or dual-write active runtime state. A new process starts
on one selected root and records that root in Instance Registry and launch evidence.

## Consequences

- Runtime, ContextOS, receipts and project history are inspectable under one target
  `.polaris` tree by default.
- Deleting a target project removes its default runtime identity with it.
- External high-performance storage remains an explicit operational choice.
- Existing processes keep their selected root until restart; no live root switch is
  attempted.

