# QA Audit Verdict Cell

## Purpose

Run independent QA or Auditor validation and emit structured acceptance verdicts for Director outputs.

## Kind

`workflow`

## Public Contracts

- commands: `ParseTracebackFramesCommandV1`
- commands: `RunQaAuditCommandV1`
- commands: `RunVisualQaAuditCommandV1`
- queries: `GetQaVerdictQueryV1`
- events: `QaVerdictIssuedEventV1`
- results: `FailureSignalV1`
- results: `ParseTracebackFramesResultV1`
- results: `QaAuditResultV1`
- results: `TracebackFrameV1`
- results: `VisualAuditFindingV1`
- results: `VisualQaAuditResultV1`
- errors: `QaAuditError`

## Architecture

```
public/
  contracts.py   — 5 frozen dataclasses: Command, Query, Event, Result, Error
  service.py     — run_qa_audit(Command) plus public re-exports from audit.verdict

internal/
  qa_agent.py   — QAAgent extends RoleAgent; review lifecycle + protocol FSM
  qa_service.py — QAService: audit_task(), path validation, Python syntax check
  quality_service.py — QualityService: ruff lint integration
```

## Public Service

- `run_qa_audit(RunQaAuditCommandV1) -> QaAuditResultV1` is the owner-cell
  adapter for QA verdict issuance. Cross-role callers, including
  `roles.runtime`, must call this public service and must not import
  `qa.audit_verdict.internal.*`.
- `parse_traceback_frames(ParseTracebackFramesCommandV1) ->
  ParseTracebackFramesResultV1` emits typed `FailureSignalV1` data for the
  `FailureSignalIndex` asset mount.
- `run_visual_qa_audit(RunVisualQaAuditCommandV1) -> VisualQaAuditResultV1`
  records typed image evidence refs after the caller has obtained an
  `llm.control_plane` image-input model capability ref. This Cell does not
  accept natural-language image descriptions as visual source of truth. The
  visual audit TruthLog receipt is appended through
  `audit.evidence.public.service.append_evidence_event`; QA does not directly
  write `runtime/evidence/*`.

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
