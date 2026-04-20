# API Gateway

## Purpose

Own inbound HTTP and WebSocket protocol translation and route dispatch.

## Kind

`capability`

## Public Inputs

- `ApiCommandV1`
- `ApiQueryV1`

## Public Outputs

- `ApiResponseV1`
- `ApiResponseEventV1`

## Depends On

- `runtime.projection`
- `policy.workspace_guard`

## State Ownership

- None

## Effects Allowed

- `http.inbound:*`
- `ws.inbound:*`
- `fs.read:runtime/*`
- `fs.read:workspace/history/*`

## Invariants

- delivery remains stateless
- delivery does not write source-of-truth state
- business writes are delegated downstream

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts.py`
4. owned implementation files only if needed

## Verification

- `tests/architecture/test_polaris_layout.py`
