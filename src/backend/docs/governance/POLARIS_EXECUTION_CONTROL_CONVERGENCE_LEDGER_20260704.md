# Polaris Execution Control Convergence Ledger - 2026-07-04

Status: Active
Owner: Polaris backend governance
Scope: Execution-control typed fact convergence after the closed execution-contract
and legacy-residual ledgers.

## Purpose

This ledger tracks the remaining convergence work discovered from repeated
Factory Bench failures and recent control-plane hardening. The older ledgers
`POLARIS_EXECUTION_CONTRACT_GAP_LEDGER.md` and
`POLARIS_LEGACY_RESIDUAL_LEDGER_20260703.md` are closed intakes. New work must
not reopen their closed rows; it must enter this ledger with fresh evidence.

The target chain remains:

`Provider Response -> ToolCallLifecycle -> Effect Receipt -> Run Ledger ->
TaskBoundary Verdict -> QA Verdict -> Runtime Projection`

## Operating Rules

- Add or update exactly one ledger item per implementation commit unless a group
  shares one contract and one verification surface.
- Before editing, verify the code path with codegraph and a direct status/diff
  check.
- Close an item only after a focused test, lint/type check, or negative scan
  proves the new authority boundary.
- Do not add parallel fact sources. New helpers must move data toward a single
  typed owner or a read-only projection from that owner.
- Do not count documentation-only blueprint rows as runtime closure.

## Current Open Buckets

| ID | Priority | Bucket | Current Risk | Target State | Status |
| --- | --- | --- | --- | --- | --- |
| ECC-WS1 | P0 | ToolCallEnvelope / provider tool-call fact convergence | Native tool calls, response hashes, dispatch receipts, stream/non-stream anomalies, and monitoring projections still have remaining wrapper seams. | Provider-native tool calls are normalized once into typed envelope/fact helpers, stream and non-stream consume the same projections, and lifecycle receipts are append-only projections. | Partial |
| ECC-WS2 | P0 | Execution Ledger as single task-state source | TaskBoard/session/status projections can still become separate state writers outside the event stream. | Task status is derived from Execution Ledger projection; task-row/session writes become projections or guarded commands with a single owner. | Open |
| ECC-WS4 | P1 | Typed QualityIssue end-to-end | Quality scan, gate, and repair paths still contain string diagnostics and regex reparsing at several boundaries. | Scanners emit typed issue rows; display strings are projections; repair planning consumes issue codes/path/symbol fields without reparsing prose. | Partial |
| ECC-WS5 | P0 | ScopeAuthority and ownership handoff | Scope checks and deferred targets can still be local decisions without a single owner-routing protocol. | One ScopeAuthority resolves write scope and classifies out-of-scope diagnostics; ownership handoff requests route work to the owning task. | Partial |
| ECC-WS6 | P1 | ContextContract and FailureEvidenceV1 propagation | Failure evidence and context coverage are partially typed, but aggregate/runtime surfaces still include local merges and summaries. | Failure evidence is produced at detection points, projected through Run Ledger public helpers, and consumed by aggregate/runtime/QA without string reclassification. | Partial |

Open bucket count: 5.

## Closed In This Ledger

| ID | Commit | Bucket | Closure | Verification |
| --- | --- | --- | --- | --- |
| ECC-WS2-01 | `7c80cfdd` | ECC-WS2 | Director CLI task status updates now route through `TaskRuntimeService.update` instead of directly mutating task-board JSON. | Targeted Director CLI/task-runtime tests passed before commit. |
| ECC-WS1-01 | `f1645f5f` | ECC-WS1 | Tool lifecycle metadata projection moved behind a shared helper so role result surfaces stop hand-assembling lifecycle metadata. | Targeted role-result/lifecycle tests passed before commit. |
| ECC-WS4-01 | `2f8efb62` | ECC-WS4 | Artifact quality structural keys moved to the KernelOne quality helper; quality gate stopped reparsing typed issue structural keys through fallback prose parsing. | Targeted artifact-quality/quality-gate tests passed before commit. |
| ECC-WS6-01 | `ee1dfd43` | ECC-WS6 | Lifecycle failure evidence projection moved to Run Ledger public helpers. | Targeted role-runtime/result-mapping tests passed before commit. |
| ECC-WS6-02 | `9b8b84d9` | ECC-WS6 | Failure evidence metadata projection centralized through Run Ledger public helpers. | Targeted failure-evidence/result-mapping tests passed before commit. |
| ECC-WS6-03 | `990cf11d` | ECC-WS6 | Failure evidence summary generation centralized so callers stop maintaining local summary shapes. | Targeted failure-evidence tests passed before commit. |
| ECC-WS2-02 | `6e84f228` | ECC-WS2 | Task creation records are now appended into the execution stream, making creation observable in the same task-state ledger family. | Targeted task-runtime/execution-stream tests passed before commit. |
| ECC-WS1-02 | `cfabbd47` | ECC-WS1 | Native tool-call facts are projected from Run Ledger lifecycle facts. | Targeted Run Ledger / role-result tests passed before commit. |
| ECC-WS1-03 | `b49a6404` | ECC-WS1 | Native tool-call metadata projection moved to a shared Run Ledger helper. | Targeted lifecycle projection tests passed before commit. |
| ECC-WS6-04 | `5a386bfe` | ECC-WS6 | Failure evidence merging moved to a shared helper. | Targeted failure-evidence tests passed before commit. |
| ECC-WS1-04 | `47643a9b` | ECC-WS1 | Response-shape native tool-call extraction and fact projection moved from `decision_pipeline` private code into `llm_caller.tool_helpers`; stream now consumes the shared helper. | `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_llm_caller_helpers.py src/backend/polaris/cells/roles/kernel/internal/transaction/tests/test_decision_pipeline.py src/backend/polaris/cells/roles/kernel/internal/transaction/tests/test_stream_orchestrator_completion_evidence.py -q`; `rtk ruff check ...`. |
| ECC-WS1-05 | `653cd70e` | ECC-WS1 | Provider response hashing moved into `llm_caller.tool_helpers`; stream no longer imports the private decision hash helper. | Same focused WS1 test/ruff set passed. |
| ECC-WS1-06 | `cec7628c` | ECC-WS1 | Lifecycle tests were decoupled from decision-pipeline private wrappers and now exercise shared helper / Run Ledger public projection APIs. | Same focused WS1 test/ruff set passed. |
| ECC-WS1-07 | `d48f6bd8` | ECC-WS1 | Native tool-call envelope fallback and provider-label projection moved from `decision_pipeline` private anomaly helpers into `llm_caller.tool_helpers`; the dropped-dispatch anomaly builder now consumes the shared envelope projection. | `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_llm_caller_helpers.py src/backend/polaris/cells/roles/kernel/internal/transaction/tests/test_decision_pipeline.py src/backend/polaris/cells/roles/kernel/internal/transaction/tests/test_stream_orchestrator_completion_evidence.py -q`; `rtk ruff check ...`. |
| ECC-WS1-08 | `7ea37a46` | ECC-WS1 | `decision_pipeline` removed its remaining pure native tool-call count/fact/hash/projection wrapper functions and now calls the shared helper / Run Ledger projection APIs directly. | Same focused WS1 test/ruff set passed. |
| ECC-WS1-09 | `cd9b091d` | ECC-WS1 | Dropped-dispatch anomaly projection moved behind Run Ledger public `build_tool_dispatch_dropped_anomaly_projection`; `decision_pipeline` now supplies normalized response facts instead of hand-building lifecycle receipt, failure evidence, dispatch counts, and anomaly dicts. | `rtk pytest src/backend/polaris/cells/control_plane/run_ledger/tests/test_tool_lifecycle.py src/backend/polaris/cells/roles/kernel/internal/transaction/tests/test_decision_pipeline.py src/backend/polaris/cells/roles/kernel/internal/transaction/tests/test_stream_orchestrator_completion_evidence.py -q`; targeted ruff passed. |
| ECC-WS6-05 | `901a490f` | ECC-WS6 | Aggregate/runtime failure-evidence payload merging moved from `roles.runtime.public.aggregate_chat` local logic into Run Ledger public `merge_failure_evidence_payload`, preserving mapping overlays and structured row projections. | `rtk pytest src/backend/polaris/cells/roles/runtime/tests/test_aggregate_role_plan.py src/backend/polaris/cells/control_plane/run_ledger/tests/test_failure_evidence.py -q`; targeted ruff passed. |
| ECC-WS6-06 | `f28e23ba` | ECC-WS6 | Aggregate HTTP ingress now normalizes `failure_evidence` through Run Ledger public `merge_failure_evidence_payload`, so structured evidence rows are not dropped before reaching aggregate runtime. | `rtk pytest src/backend/polaris/delivery/http/routers/test_aggregate_chat.py src/backend/polaris/cells/roles/runtime/tests/test_aggregate_role_plan.py src/backend/polaris/cells/control_plane/run_ledger/tests/test_failure_evidence.py -q`; targeted ruff passed. |
| ECC-WS5-01 | `c3052728` | ECC-WS5 | Director task-boundary scope-filter evidence now uses KernelOne `scope_authority_decision_summary` instead of hand-slicing ScopeAuthority fields in the adapter. This keeps compact evidence as a read-only projection from the ScopeAuthority decision. | `rtk pytest src/backend/polaris/tests/unit/cells/roles/adapters/internal/director/test_quality_gate_scope_filter.py src/backend/polaris/tests/unit/kernelone/quality/test_scope_authority.py -q`; targeted ruff passed. |

## Next Closure Order

1. ECC-WS1: audit stream completion/monitoring projection for any remaining
   hand-written lifecycle receipt or dispatch-count summaries now that response
   facts and dropped-dispatch anomaly projection are shared.
2. ECC-WS6: continue replacing local runtime/context failure-evidence and
   coverage projections with Run Ledger public helpers without losing UI-facing
   summaries.
3. ECC-WS4: continue moving scanner outputs from string diagnostics to typed
   `QualityIssue` fields, one scanner family at a time.
4. ECC-WS5: continue routing ownership handoff requests from ScopeAuthority
   projections to owning task rows without expanding write authorization.
5. ECC-WS2: only after the smaller projection work is stable, migrate task-state
   writes toward event-sourced projection ownership.
