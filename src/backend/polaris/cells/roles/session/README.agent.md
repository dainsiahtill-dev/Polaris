# Roles Session Cell

## Purpose

Manage role session lifecycle, session attachments, and persistent session
data storage.

## Kind

`capability`

## Public Inputs

- `CreateRoleSessionCommandV1`
- `UpdateRoleSessionCommandV1`
- `AttachRoleSessionCommandV1`

## Public Outputs

- `RoleSessionResultV1`
- `RoleSessionLifecycleEventV1`

## Depends On

- `policy.workspace_guard`
- `storage.layout`
- `audit.evidence`

## State Ownership

- `runtime/roles/*`
- `runtime/role_sessions/*`
- `runtime/conversations/*`

## Effects Allowed

- `fs.read:runtime/**`
- `fs.write:runtime/roles/*`
- `fs.write:runtime/role_sessions/*`
- `fs.write:runtime/conversations/*`
- `db.read_write:role_sessions`

## Invariants

- session lifecycle state changes must be explicit and auditable
- attachment modes must remain compatible with existing session storage
- data store writes must preserve UTF-8 semantics

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/conversation.py`
- `internal/session_attachment.py`
- `internal/role_session_service.py`
- `internal/data_store.py`

## Verification

- `tests/test_resident_api.py`
- `tests/test_resident_pm_bridge.py`
- `tests/test_conversation_model.py`
