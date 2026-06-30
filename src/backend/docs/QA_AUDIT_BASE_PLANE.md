# QA Audit Base Plane

Polaris QA is an evidence judge, not a free-form reviewer. Its job is to
classify a completed execution from immutable evidence, route responsibility to
the right stage, and leave a replayable verdict trail.

## Core Invariants

1. QA verdicts consume validated PM contract data, CE handoff evidence, JobToken
   authority, Run Ledger projections, tool receipts, verifier outputs, and
   artifact-quality evidence.
2. QA must not invent required evidence after Director execution. Evidence
   requirements are compiled before Director work and carried in the JobToken
   `gate_policy`.
3. A PASS verdict requires required evidence modalities to be present and
   passing. Missing or failed required evidence cannot be treated as success.
4. QA must not route every failure to Director. Scope, contract, handoff, and
   security failures route to CE, PM/human, or hard stop.
5. Run Ledger reads used for QA must support a barrier: if the projection has
   not consumed the referenced effect receipt, QA returns `BLOCKED`/`pending_qa`
   rather than judging stale evidence.
6. The Verdict Engine is side-effect free. Consumers apply returned transitions
   through task-market contracts and ledger writers.
7. `qa.verdict_envelope.v1` is the authoritative QA decision contract. Legacy
   `QaAuditResultV1` remains only as a compatibility projection for older
   callers and must not override envelope routing.

## Evidence Policy Compiler

`control_plane.verifier_policy.compile_evidence_policy` turns task/project
context into a `gate_policy` before Director execution:

- always considers QA/code/tool-receipt evidence for code-writing tasks;
- adds command evidence when build/test/lint/compile/smoke checks are part of
  the task;
- maps API work to API contract and integration evidence;
- maps web/canvas work to browser evidence only when browser verifier support is
  available or explicitly required;
- places unavailable optional modalities into `waived_modalities` and
  `advisory_modalities`;
- emits `unavailable_required_blockers` only when a policy/user explicitly
  requires an unavailable modality.

The CE JobToken embeds the compiled `gate_policy`; Director and QA consume the
same token instead of maintaining separate evidence assumptions.

## Verdict Envelope

`qa.verdict_envelope.v1` is the canonical QA decision object. It includes:

- authority: JobToken id, contract hash, blueprint hash, target/allowed paths;
- ledger: projected Run Ledger state;
- evidence: required/missing/failed modalities and barrier metadata;
- artifact quality: deterministic code and cross-artifact findings;
- classification: failure class, owner, route, and Director repairability;
- lineage: previous verdict refs and repeat-failure counters;
- content hash: stable hash of the verdict evidence.

Consumers that still return `QaAuditResultV1` must embed the envelope metadata
and project `failure_class`, `responsible_layer`, `repairable_by_director`, and
the envelope content hash. Task movement must be driven by the envelope route,
not by legacy PASS/FAIL strings when ledger evidence contradicts them.

## Failure Routing

| Failure class | Owner | Next stage | Director repairable |
| --- | --- | --- | --- |
| `IMPLEMENTATION_DEFECT` | Director | `pending_exec` | yes |
| `EXECUTION_EVIDENCE_MISSING` | Director | `pending_exec` | yes |
| `BLUEPRINT_SCOPE_MISMATCH` | Chief Engineer | `pending_design` | no |
| `BLUEPRINT_VERIFY_INVALID` | Chief Engineer | `pending_design` | no |
| `CONTRACT_AMBIGUOUS` | PM/Human | `waiting_human` | no |
| `SECURITY_POLICY_VIOLATION` | Security/Human | `waiting_human` | no |
| `TEST_ENVIRONMENT_FAILURE` | QA/Infra | `pending_qa` | no |
| `PASSED` | QA | resolved | no |

`repairable_by_director=false` is a hard routing signal: Task Market must not
send that verdict back to Director repair.

## Migration Plan

1. Keep `qa.verdict_envelope.v1` authoritative for Task Market routing.
2. Continue emitting compatibility metadata for legacy consumers while they
   migrate to envelope fields.
3. Remove duplicated workflow/runtime QA taxonomy strings after consumers read
   the shared `QaFailureClassificationV1` projection.

## Negative Test Matrix

| Scenario | Expected result |
| --- | --- |
| Missing required command evidence | FAIL -> `pending_exec` |
| Failed required verifier evidence | FAIL -> `pending_exec` |
| Stale Run Ledger projection barrier | BLOCKED -> `pending_qa` |
| Cross-file contract amendment requested | FAIL -> `pending_design` |
| Missing acceptance / ambiguous contract | NEEDS_REVIEW -> `waiting_human` |
| Security/path authorization failure | BLOCKED -> `waiting_human` |
| Browser verifier unavailable for web task | browser advisory/waived, not hard-required |
| Browser explicitly required but unavailable | `unavailable_required_blockers` present |
