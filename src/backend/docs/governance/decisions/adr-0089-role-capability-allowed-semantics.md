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
