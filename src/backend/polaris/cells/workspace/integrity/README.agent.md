# Workspace Integrity

## Purpose

Workspace legality checks, docs bootstrap safety, and file/change integrity primitives.

## Kind

`capability`

## Public Inputs

- `ValidateWorkspaceCommandV1`
- `EnsureDocsReadyCommandV1`
- `GenerateDocsTemplatesCommandV1`

## Public Outputs

- `DocsTemplatesResultV1`

## Depends On

- `policy.workspace_guard`
- `runtime.projection`
- `storage.layout`

## State Ownership

- `workspace/meta/workspace_status.json`

## Effects Allowed

- `fs.read:workspace/**`
- `fs.write:workspace/meta/workspace_status.json`
- `fs.write:workspace/docs/**`
- `process.spawn:workspace/indexer`

## Does Not

- define transport endpoints directly
- own runtime task state
- bypass workspace guard checks

## Invariants

- All path validation runs through guard-aware checks.
- Docs bootstrap writes remain within `workspace/docs/**`.
- Text writes are explicit UTF-8.

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/workspace_service.py`
- `internal/fs_utils.py`
- `internal/diff_tracker.py`

## Read Order for AI

1. `cell.yaml`
2. `public/contracts.py`
3. `public/service.py`
4. `internal/workspace_service.py`
5. `internal/fs_utils.py`

## Verification

- `tests/test_docs_template_quality.py`
- `tests/test_workspace_policy_guard.py`
- `tests/test_hyper_opt.py`

