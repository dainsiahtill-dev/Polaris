# Runtime Artifact Store Cell

## Purpose

Own runtime artifact read/write access and provide a typed boundary for
runtime_v2 protocol types.

## Kind

`capability`

## Public Inputs

- `WriteRuntimeArtifactCommandV1`
- `ReadRuntimeArtifactQueryV1`
- `RuntimeV2ExportQueryV1`

## Public Outputs

- `RuntimeArtifactResultV1`
- `RuntimeArtifactWrittenEventV1`

## Depends On

- `policy.workspace_guard`
- `storage.layout`
- `audit.evidence`

## State Ownership

- `runtime/contracts/*`
- `runtime/results/*`
- `runtime/state/*`
- `runtime/status/*`
- `runtime/events/*`

## Effects Allowed

- `fs.read:runtime/**`
- `fs.write:runtime/contracts/*`
- `fs.write:runtime/results/*`
- `fs.write:runtime/state/*`
- `fs.write:runtime/status/*`
- `fs.write:runtime/events/*`

## Invariants

- artifact keys should map deterministically to runtime paths
- runtime_v2 export is a typed boundary, not a business workflow layer
- artifact read/write operations must preserve UTF-8 semantics

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/arrow_service.py`
- `polaris/cells/runtime/projection/internal/runtime_v2.py`

## Verification

- `tests/test_artifact_service.py`
- `tests/test_runtime_projection_snapshot_tasks.py`
