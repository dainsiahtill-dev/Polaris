# Polaris Execution Contract Gap Ledger

Status: Active
Owner: Polaris backend governance
Created: 2026-06-27
Scope: PM Contract -> Chief Engineer Blueprint/Handoff -> Execution Envelope -> Director Dispatch/Tools -> QA/Provenance

## Purpose

This ledger tracks the remaining gaps required to make Polaris task execution a long-term auditable contract system instead of a prompt-only multi-agent workflow.

The target chain is:

`Validated PM Contract -> Immutable CE Blueprint Snapshot -> Explicit Handoff Decision -> Execution Envelope -> Capability-Enforced Tools -> Final Provider Request Receipt -> QA Verdict -> Provenance Bundle`

## Severity

- P0: Can cause unauthorized Director dispatch, stale context replay, wrong task execution, or invisible audit drift.
- P1: Can degrade task quality, cross-role context fidelity, repair convergence, or post-run diagnosis.
- P2: Product/AGI experience hardening that should consume the same contracts but does not block the execution contract foundation.

## Ledger

| ID | Severity | Gap | Current State | Target State | Status | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| P0-01 | P0 | Handoff validation is duplicated across Director dispatch entrypoints. | Director dispatch/runtime paths now use `validate_director_handoff_from_payload`; remaining direct `evaluate_handoff_decision_for_blueprint` uses are public API, tests, HTTP read endpoint, or generated descriptor. | All Director-dispatching entrypoints call one shared public handoff validation service/helper. | Closed | Grep confirms no dispatch/runtime direct call sites; CE governance, PM dispatch, task-market/role-runtime, loop-director, factory enum, and Director adapter unit tests pass. |
| P0-02 | P0 | Execution envelope is not guaranteed at every Director dispatch boundary. | Director adapter/codegen paths generate envelopes through tasking strategy; public `execute_director_task` now creates an envelope when one is not supplied and audits missing PM/CE/handoff refs. | Every Director dispatch creates or carries `polaris.execution_envelope.v1` with PM/CE/profile/handoff/capability bindings. | Closed | Public execution contract tests assert generated envelope metadata and missing-ref audit; adapter/codegen propagation is covered by tasking strategy tests. |
| P0-03 | P0 | Director execution is still audit-only for missing envelope/handoff in some compatibility paths. | Public `execute_director_task` supports explicit strict mode via metadata and blocks before service execution when required refs are missing. | Strict mode blocks execution when envelope, CE decision, PM contract hash, blueprint hash, or execution profile hash is missing. | Closed | Public execution tests assert strict missing-ref block and strict complete-envelope allow path. |
| P0-04 | P0 | Capability token enforcement is not universally bound to all write/command tools and dispatch paths. | Role gateway derives runtime capability scope/token from job tokens and `director_execution_envelope.authorization`; toolkit write and command handlers enforce that runtime capability and ignore model/tool-argument scope expansion. | Tool/write/command guards validate run-scoped capability token bound to envelope hash, task id, run id, path, command, and expiry. | Closed | Negative tests cover job-token and execution-envelope write scope expansion plus command outside `allowed_commands`. |
| P0-05 | P0 | Final provider request audit does not yet hard-fail on missing required references everywhere. | Final request evidence coverage now has a shared enforcement function and is checked before sync, structured, and stream provider invocation when request/context/strategy/envelope policy requires it. | Required-ref coverage is computed and enforced for PM, CE, Director, QA calls according to role/stage policy. | Closed | Audit tests cover opt-in enforcement, envelope-required refs, current final request refs, and LLM caller component paths. |
| P0-06 | P0 | PM route/probe can still surface tool-guard misconfiguration symptoms. | PM route probe context pins text-only/no-tool flags; transaction-kernel wiring passes an empty tool schema set for PM probes; final provider request snapshots assert `tools=[]` and `tool_choice=none`. | PM planning/probe route has no tool schema in final provider request and cannot trigger Director tool guard. | Closed | PMAdapter probe, TransactionKernel no-tool, and final provider snapshot tests pass. |
| P1-07 | P1 | PM contract can become too mechanical if natural design intent is not preserved through CE/Director. | PM synthesis emits delivery plan/depth contracts, CE persists them into blueprint/handoff context, Director prompt renders intent/user journey/behavior rows, and final request evidence coverage now tracks both refs. | PM snapshot carries natural-language product intent, user journey, behavior matrix, deterministic checks, and target files; CE and Director must receive it. | Closed | CE public contract tests, Director prompt tests, and final request evidence coverage tests assert delivery plan/depth propagation. |
| P1-08 | P1 | CE blueprint does not yet define a canonical cross-file public interface contract for every multi-file task. | CE blueprint now emits `chief_engineer.module_interface_contract.v1` with per-target module roles, owner terms, planned public symbols, and import consistency rules; Director prompt renders the contract. | Blueprint includes exported symbols/import contracts or available symbol evidence for target files when task spans modules. | Closed | CE public contract and Director prompt tests assert module interface contract generation/rendering. |
| P1-09 | P1 | ContextOS coverage can still overemphasize token utilization instead of evidence completeness. | Final request evidence coverage is now ref-based; passing evidence coverage suppresses superseded token-utilization missing-context findings and records metadata-only redaction safety. | Coverage is ref-based: required refs, included refs, missing refs, pass/fail, plus redaction safety. | Closed | Context audit tests cover missing PM/CE/envelope/receipt refs, delivery intent refs, low-utilization complete-ref pass, and provider snapshot non-content redaction. |
| P1-10 | P1 | QA failure response is not yet a typed classification contract. | Director repair service now emits `polaris.qa_failure_classification.v1` and blocks non-Director routes before repair execution. | `qa_failure_classification.v1` decides Director repair vs CE replan vs PM revision vs infra retry vs hard stop. | Closed | Repair service tests cover implementation defect, scope mismatch, contract ambiguity, infra failure, invalid acceptance, security policy violation, and non-Director repair blocking. |
| P1-11 | P1 | Run provenance bundle is incomplete as a single post-run artifact. | Control Plane run ledger exposes `polaris.run_provenance_bundle.v1` through `read_run_provenance_bundle`, linking authority hashes, provider request hashes, tool/command receipts, QA hash, and evidence refs. | `run_provenance_bundle.v1` links run id, task id, commit, PM hash, CE hash, handoff hash, envelope hash, provider requests, tool receipts, diff hash, command receipts, QA result. | Closed | Public run-ledger tests cover successful provenance bundles and missing-authority blocked bundles. |
| P2-12 | P2 | AGI decision handoff consumes evidence but does not yet share the same envelope/capability ledger end-to-end. | Resident AGI handoff contracts and inbox projections now expose platform contract refs, missing platform refs, and blocked authority fields while forcing advisory-only authority. | AGI reads the same execution profile/envelope/audit/provenance contracts and can request governed actions without becoming a second authority source. | Closed | Resident contract/service tests show AGI handoff refs include execution profile, envelope, final request audit, provenance, and malicious authority fields are stripped. |
| P2-13 | P2 | AGI cockpit UI is too dense for users and not yet a task-control console. | Resident workspace now defaults to a Chinese AGI cockpit plus tactical console; raw matrices/registries are hidden behind advanced audit, and chat/quick commands emit evidence/action receipts. | Chinese-first AGI cockpit: robot-like command console, concise status push, chat pull-and-act, evidence cards, governed action receipts. | Closed | ResidentWorkspace tests cover shell readability, hidden dense audit by default, tactical chat, quick commands, and governed action receipts; frontend typecheck passes. |

## 2026-06-29 Enforcement Reconciliation

This audit reopened the difference between "implemented capability" and
"authoritative execution path." The following wiring is now enforced in code:

| ID | Area | Previous Drift | Enforced State | Verification |
| --- | --- | --- | --- | --- |
| R-01 | CE handoff authority | Strict `ce_handoff_decision.v1` existed, but CE handoff payloads could omit `execution_profile_hash`, and Director dispatch still validated with `require_strict=False`. | CE consumer and public blueprint generation freeze `director_execution_profile` plus hash/ref; Director TaskMarket, orchestration, factory handoff probes, adapter projection, and loop-director call the shared validator with `require_strict=True`. | `test_ce_consumer_integration.py`, `test_orchestration_command_service.py`, `test_director_consumer.py::TestDirectorExecutionConsumerHandoffGate`, `test_loop_director_dependency_planning.py`, `test_service_governance.py`. |
| R-02 | Execution envelope audit | Missing authority hashes were visible only indirectly through missing-ref values. | `audit_policy.execution_authority` records `ok`, `missing_bindings`, and `strict_handoff_required` for machine-readable ContextOS/final-request audit. | `test_execution_envelope.py`. |
| R-03 | QA verdict authority | `QAVerdictEngine` ran as shadow comparison while legacy QA routing stayed authoritative even when mode was set to `engine`. | `KERNELONE_QA_VERDICT_ENGINE_MODE=engine` now promotes the verdict engine route; engine errors fail closed to `pending_qa`, and authoritative payload is recorded in QA metadata. | `test_qa_consumer.py::test_authoritative_verdict_engine_requeues_implementation_defect`. |
| R-04 | Final request evidence projection | `.llm.events.jsonl`, realtime journal refs, Factory normalized LLM events, and role-specific audit files exposed final provider-request evidence through different shapes, forcing reverse lookup through ContextOS or CE-only `chief_engineer.llm_call` artifacts. | Both LLM event writers attach `llm.final_request_evidence.v1`, `audit_refs`, `context_snapshot_ref`, and final-request audit hashes; realtime log refs expose the same lightweight refs; Factory `collect_llm_events()` normalizes those refs without requiring role-specific reverse lookup. | `test_final_request_evidence.py`, `test_io_events_observability.py::test_emit_llm_event_projects_final_request_evidence_refs`, `test_llm_events.py::test_emit_llm_event_to_disk_preserves_final_request_context_audit`, `test_llm_realtime_bridge_refs.py::test_refs_include_final_request_audit_projection`, `test_bench_gates.py::test_collect_llm_events_projects_final_request_evidence`. |

Known unrelated active worktree risk: repair-kernel/advisory-overlay tests in
`test_director_adapter_pure.py` and factory workspace-quality repair
characterization currently fail in files already dirty before this change. Those
failures are not closed by this handoff/envelope/QA reconciliation and must be
handled by the repair-kernel owner before using those suites as release gates.

## 2026-06-30 Bench Hotspot Reconciliation

This reconciliation is based on the last 50 commits plus repeated Factory Bench
agent findings. The repeated failures cluster in the same platform seams:
Director repair runtime, Director adapter bridge, Factory/bench measurement,
Run Ledger/QA projection, and PM/CE/Director task-boundary evidence.

The architectural decision is not to replace the existing codebase. The decision
is to make the lessons from those repairs explicit:

1. Repair convergence must remain runtime-owned:
   `Typed Diagnostic -> RepairPlan -> Patch Composer -> Policy Gate -> Execute
   -> Receipt -> Revalidate`.
2. Adapter code may bind schedule runners, but must not become a second repair
   authority or duplicate runtime receipts.
3. Factory Bench remains an internal pressure tool; production facts come from
   Run Ledger, ReceiptStore, ContextOS, verifier policy, and QA verdicts.
4. Final provider request evidence must be ref-based and replayable; token
   utilization is only a health signal.
5. Migration debt projections must use one canonical field name per fact. Old
   compatibility aliases are treated as drift unless a named migration blocker
   requires them.

| ID | Severity | Hotspot / Drift | Resolution | Status | Verification |
| --- | --- | --- | --- | --- | --- |
| H-01 | P0 | Director repair strategy catalog still projected non-runtime rows as `legacy_strategy_host`, which made an adapter migration ledger look like a second repair fact source. | Public strategy catalog now names the remaining non-runtime bucket `adapter_strategy_host`; tests still assert the bucket is empty for migrated source tools. | Closed | `test_public_strategy_catalog_is_read_only_and_non_agi_authoritative`, `test_public_strategy_catalog_and_language_slots_keep_status_ledger_counts_explicit`, `test_typescript_source_tools_do_not_return_to_adapter_strategy_host`. |
| H-02 | P0 | Materialization quality summary exposed both `adapter_projection_debt` and the older `legacy_callback_debt` alias for the same evidence. | Materialization bridge now emits only `adapter_projection_debt`; tests assert `legacy_callback_debt` is absent from this payload. | Closed | `test_materialization_quality_migration_debt_marks_legacy_only_step_blocked`, `test_materialization_quality_migration_debt_lists_remaining_callback_only_steps`. |
| H-03 | P0 | Runtime schedule query summaries reported `adapter_callback_bridge=True` while run-result contracts forced `adapter_callback_bridge=False`, creating conflicting observability. | Schedule summaries and convergence payload defaults now report `adapter_projection_bridge=True` and `adapter_callback_bridge=False`. | Closed | Repair scheduler and adapter bridge tests cover projection bridge fields. |
| H-04 | P1 | Post-execution Rust aggregate debt still needs migration evidence because not every subcase is proven native typed-receipt-backed. | Rust post-execution shadow replay helpers, shadow workspace projection fields, and direct `_rust_record_to_tool_result` test hooks have been removed. Remaining `legacy_aggregate*` fields are migration-debt counters only, now expressed as typed-receipt cutover gaps rather than shadow replay authority. | Partial | `test_director_adapter_repair_bridge.py`, `test_director_repair_kernel_boundary.py`, and Rust repair/catalog tests prove the runtime bridge path and absence of shadow replay fields. Close only after remaining `legacy_aggregate*` debt counters are no longer needed. |
| H-05 | P1 | Factory/bench `summarize_run_ledger_projection` is close to the fact source but has weaker direct coverage than other Run Ledger projections. | Factory projection summary is characterized as a direct delegation to the Control Plane public summarizer, preventing bench/factory from becoming a second semantics source. | Closed | `test_factory_projection_summary_delegates_to_control_plane_public_contract`. |
| H-06 | P1 | Several prompt/body injection fixes improved outcomes but are still symptoms if not backed by envelope/final-request coverage gates. | Final-request coverage now ignores explicitly untrusted user-message bodies for required evidence refs; prompt text cannot satisfy PM/CE/target-files evidence when wrapped as untrusted content. | Closed | `test_final_request_evidence_ignores_untrusted_user_message_body_for_required_refs`. |
| H-07 | P0 | Some production code appended Run Ledger events directly, bypassing public projection publish. | Role tool gateway and Factory real-run gate persistence now call public `append_run_ledger_event`; direct appends remain only in the public service and ledger primitive tests. | Closed | `test_tool_gateway_run_ledger_receipt.py`, `test_run_ledger.py`, `test_public_service.py`; grep shows production write callers use public append. |
| H-08 | P0 | Runtime projection read the latest five Run Ledgers without binding a run id, allowing terminal overlay from another run. | Runtime projection and status snapshot builder now extract explicit `run_id/workflow_id`; without a run id they do not read a latest-run aggregate. | Closed | Runtime projection tests cover run-id extraction and no-run-id projection behavior. |
| H-09 | P0 | QA consumer could terminal-ack `resolved/rejected` even when Run Ledger append was missing. | Terminal QA acknowledgements now require Run Ledger evidence; missing token/append requeues `pending_qa` with a blocker instead of acknowledging terminal status. | Closed | `test_terminal_verdict_without_run_ledger_evidence_requeues_qa` plus QA consumer suite. |
| H-10 | P0 | Streaming provider responses can still decode native tool calls without the same `tool_dispatch_dropped` fail-closed lifecycle used by non-streaming decisions. | Streaming path now fail-closes before `record_decision` when native tool calls decode without an executable batch; streaming and non-streaming anomaly flags carry a non-empty provider response hash for lifecycle receipts. | Partial | Streaming and non-streaming controller tests prove `tool_dispatch_dropped` and non-empty provider hash. Close after full RoleExecutionKernel/Run Ledger test proves `tool_lifecycle.dropped_count=1` and QA/runtime projection cannot report success. |
| H-11 | P0 | `TaskBoundaryVerdict` exists but is not appended by the normal Director completion/failure path. | Director RoleExecution now appends task-boundary verdicts for dropped tool dispatch and non-followup Director turns with declared target/completed artifacts. Missing target and dropped dispatch verdict helpers are regression-tested; full Run Ledger projection coverage is still pending. | Partial | Close after end-to-end RoleExecution/Run Ledger tests prove `INCOMPLETE_MATERIALIZATION`, `MISSING_ENTRYPOINT_TARGET`, and `TOOL_DISPATCH_DROPPED` verdicts are committed and consumed by projection/QA. |
| H-12 | P0 | Director status and runtime snapshot still have local/workflow fallback paths that can diverge from Run Ledger terminal evidence. | `/v2/director/status` now defaults to `source=auto`, and the frontend status service requests `source=auto` explicitly. Runtime snapshots can still backfill legacy local files, so terminal Run Ledger precedence needs a dedicated projection test. | Partial | Backend route and frontend service tests prove auto default. Close after a status projection test proves Run Ledger `TOOL_DISPATCH_DROPPED` overrides local `IDLE`. |
| H-13 | P0 | QA verdict engine is ledger-aware but not yet the default authoritative route. | Task Market QA now defaults to authoritative engine routing, public `run_qa_audit` persists/returns canonical envelope metadata, and the role capability handler projects `failure_class/responsible_layer`. Legacy `QaAuditResultV1` remains as compatibility projection. | Partial | Close after a QAConsumer test proves legacy PASS cannot override ledger `TOOL_DISPATCH_DROPPED` / `INCOMPLETE_MATERIALIZATION`, and after public docs mark `QaVerdictEnvelopeV1` as the authoritative contract. |
| H-14 | P1 | QA failure taxonomy is duplicated across QA cell, Director repair, workflow QA, and integration QA strings. | QA engine now normalizes legacy `REQUEUE_*` verdicts into canonical `QaFailureClassificationV1`, and capability metadata carries canonical failure fields. Director repair, workflow QA, and integration QA still emit local strings. | Partial | Introduce shared public QA classification builder and migrate Director repair/workflow/integration QA consumers to it. |
| H-15 | P1 | Factory/bench observability still needs a platform Run Ledger projection bundle rather than bench-only status/event summaries. | Factory normalized events now project final-request refs, but HTTP audit bundle does not expose a canonical `control_plane_projection` contract. | Open | Factory audit bundle includes Run Ledger projection and requested/canonical/instance/workspace/ports without making bench a production fact source. |
| H-16 | P0 | Final provider request evidence still has multiple observable shapes. | ContextStore hash is the target truth source, but existing `.llm.events.jsonl` records and role event APIs can expose final-request refs only under nested metadata; structured LLM calls may not store the same ContextStore snapshot as non-structured calls. | Open | Normalize top-level `context_snapshot_ref`/`final_request_evidence` projection for PM/CE/Director/QA events, keep nested fallback for history, and prove `call_structured()` writes a readable final-request snapshot. |
| H-17 | P1 | Public `start_from="director"` factory route still carries old-chain semantics. | The route is guarded by pre-director evidence, but API/schema naming can still imply PM→Director or direct Director start instead of governed resume through PM→CE→Director evidence. | Open | Rename or normalize the mode to an explicit Director-resume semantic, update tests/docs, and reject direct starts without immutable PM/CE/evidence envelope. |

## Post-Closure Alignment Audit

These items were discovered after the P0/P1/P2 implementation ledger was closed. They are documentation-and-contract alignment gaps: leaving them open would let future developers miss already-implemented contracts by reading only the main audit specification.

| ID | Severity | Residual Gap | Resolution | Status | Verification |
| --- | --- | --- | --- | --- | --- |
| A-01 | P1 | Main audit spec did not name `chief_engineer.module_interface_contract.v1`, even though CE and Director already emit/render it. | `POLARIS_TASK_EXECUTION_AUDIT_SPEC.md` now defines the module interface contract, invariants, and Director prompt requirement. | Closed | CE blueprint tests and Director prompt tests cover generation/rendering. |
| A-02 | P1 | Main audit spec described QA failure routing but did not bind it to `polaris.qa_failure_classification.v1`. | QA section now names the typed contract, required fields, example payload, and non-Director route blocking rule. | Closed | Repair service tests cover classification and route blocking. |
| A-03 | P1 | Main audit spec did not document metadata-only `redaction_safety` or the rule suppressing stale low-utilization findings when ref coverage passes. | Evidence coverage section now records `polaris.final_request_redaction_safety.v1` and clarifies token utilization is not a substitute for ref coverage. | Closed | Final request audit tests cover redaction safety and low-utilization complete-ref pass. |
| A-04 | P2 | Main audit spec did not document Resident AGI handoff `platform_contract_refs`, `missing_platform_contract_refs`, and `blocked_authority_fields`. | AGI section now defines the advisory-only handoff projection and blocked authority field rules. | Closed | Resident contract/service tests cover platform refs and authority stripping. |

## Closed Items

| ID | Closed In | Result |
| --- | --- | --- |
| C-01 | `923e7895` | Final provider request audit tracks ReceiptStore refs. |
| C-02 | `37a40c3e` | Execution envelope consumes strict CE handoff bindings. |
| C-03 | `c28d5c91` | Command execution enforces allowed commands from capability tokens. |
| C-04 | `e50c2152` | Tool capability derivation reads execution envelopes. |
| C-05 | `f36a8e7b` | Director strategy propagates execution envelopes into context and metadata. |
| C-06 | `777732c6` | Public Director execution result exposes `director.execution_contract_audit.v1`. |
| C-07 | `67e047ac` | Director dispatch/runtime paths use shared handoff validation instead of local handoff evaluator copies. |
| C-08 | `a0179432` | Public Director execution creates an execution envelope when callers do not supply one. |
| C-09 | `3fbe373a` | Public Director execution strict mode blocks before service execution when required contract refs are missing. |
| C-10 | `70d0c284` | Execution-envelope capability evidence is proven to block write scope expansion and unlisted commands through RoleToolGateway and toolkit handlers. |
| C-11 | `e2c30deb` | Final provider request evidence coverage fails closed before sync, structured, and stream provider invocation when strict policy is active. |
| C-12 | `64326302` | PM deterministic route probe is regression-tested as text-only from adapter context through TransactionKernel tool schema and final provider snapshot. |
| C-13 | `2b51b7e1` | Final request evidence coverage treats `delivery_plan_document` and `delivery_depth_contract` as first-class required/included refs. |
| C-14 | `40941762` | CE blueprints emit module interface contracts and Director prompts render target module ownership/export guidance. |
| C-15 | `bbd273e3` | ContextOS final request coverage is ref-based under low token utilization and records metadata-only redaction safety. |
| C-16 | `e17bf247` | QA failure classification routes Director repair, CE replan, PM revision, infra retry, and hard-stop failures through a typed contract. |
| C-17 | `f14bf8e8` | Control Plane exposes `run_provenance_bundle.v1` with PM/CE/handoff/envelope/provider/receipt/QA evidence hashes. |
| C-18 | `94ea7fd2` | Resident AGI handoff projections share platform contract refs and strip advisory output authority fields. |
| C-19 | `602fb4f5` | Resident AGI UI ships cockpit overview, tactical console, quick commands, advanced audit containment, and governed action receipts. |
| C-20 | `a7c773ee` | Main audit specification records the closed module-interface, QA classification, final-request redaction safety, and Resident AGI handoff-ref contracts. |

## Update Rules

1. Every gap fix must update this ledger in the same commit or an immediately adjacent commit.
2. A gap may move to `Partial` only with a committed test or audit artifact.
3. A gap may move to `Closed` only when its target state is enforced or negative-tested.
4. Compatibility audit-only states must not be called closed.
5. New bypasses discovered by bench, UI observation, or final request audit must be added here before starting the fix.
