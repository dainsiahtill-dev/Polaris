# QA Audit Verdict Cell

## Purpose

Run independent QA or Auditor validation and emit structured acceptance verdicts for Director outputs.

## Kind

`workflow`

## Public Contracts

- commands: `RunQaAuditCommandV1`
- queries: `GetQaVerdictQueryV1`
- events: `QaVerdictIssuedEventV1`
- results: `QaAuditResultV1`
- errors: `QaAuditError`

## Architecture

```
public/
  contracts.py   — 5 frozen dataclasses: Command, Query, Event, Result, Error
  service.py     — Re-exports from audit.verdict public boundary

internal/
  qa_agent.py   — QAAgent extends RoleAgent; review lifecycle + protocol FSM
  qa_service.py — QAService: audit_task(), path validation, Python syntax check
  quality_service.py — QualityService: ruff lint integration
```

## Cross-Cell Dependencies

- Imports cross-cell types (`AgentMessage`, `RoleAgent`, `create_protocol_fsm`, etc.)
  from `polaris.cells.roles.runtime.public.contracts` — **not** `public.service`.
  The narrow `contracts` module avoids loading the fat service module that
  transitively imports `qa.audit_verdict.internal.qa_agent`, which would cause
  a circular-import failure at startup.

## State Ownership

- `runtime/state/qa/*`

## Effects Allowed

- `fs.read:workspace/**`
- `fs.read:runtime/**`
- `fs.write:runtime/state/qa/*`
- `fs.write:runtime/events/runtime.events.jsonl`
- `process.spawn:qa/*`

## Verification

```bash
# Run all tests
pytest polaris/cells/qa/audit_verdict/tests/ -v

# Coverage target >80%
polaris/cells/qa/audit_verdict/internal/qa_service.py   — 83%
polaris/cells/qa/audit_verdict/internal/qa_agent.py     — 73%
polaris/cells/qa/audit_verdict/public/contracts.py        — 100%
```

## Exception Handling

All internal modules use structured logging (`logger.warning` / `logger.error`)
with `%`-style formatting. No bare `except:` or silent `pass` paths remain.
Notable security boundaries:

- `QAService._validate_path()` — path traversal prevention, null-byte rejection,
  workspace boundary enforcement
- `QAService._is_safe_filename()` — disallows `../`, `.`, `..`
- All `AgentMemory` writes go through `save_snapshot()` (atomic via `write_text_atomic`)

## Notes

- `quality_service.py` lint path (ruff subprocess) is excluded from unit-test
  coverage by design (requires ruff on PATH); integrate at the integration/E2E layer.
- `QAAgent._persist_reviews_snapshot()` requires `AgentMemory.save_snapshot()`,
  which was added to `agent_runtime_base.py` as the symmetric complement of
  `load_snapshot()` to complete the snapshot persistence contract.
