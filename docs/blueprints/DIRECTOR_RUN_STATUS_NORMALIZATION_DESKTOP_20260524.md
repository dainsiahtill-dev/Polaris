# Director Run Status Normalization Desktop Blueprint

Date: 2026-05-24

## Problem

The PM run detail route already normalizes orchestration snapshot statuses that may arrive as either enum-like objects or plain strings. The Director run detail and cancel routes still read `snapshot.status.value` directly. When the workflow runtime returns a string status, the desktop Director and Chief Engineer run evidence surfaces can receive a 500 while polling `/v2/director/runs/{run_id}`.

## Scope

- `polaris.delivery.http.v2.director`
- `test_v2_director_router.py`

## Design

1. Add a small Director route helper to normalize snapshot status values.
2. Add a response builder shared by Director get and cancel routes.
3. Treat missing task collections as zero queued tasks.
4. Extend tests to cover string status snapshots for get and cancel.

## Verification

- Ruff check and format for changed backend files.
- Mypy for the Director router.
- Targeted pytest for Director v2 router.
