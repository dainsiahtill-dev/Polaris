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
| P1-11 | P1 | Run provenance bundle is incomplete as a single post-run artifact. | Evidence exists across Run Ledger, ContextOS, receipts, audit snapshots, and QA outputs. | `run_provenance_bundle.v1` links run id, task id, commit, PM hash, CE hash, handoff hash, envelope hash, provider requests, tool receipts, diff hash, command receipts, QA result. | Not started | A completed run emits one provenance bundle with stable hashes. |
| P2-12 | P2 | AGI decision handoff consumes evidence but does not yet share the same envelope/capability ledger end-to-end. | AGI authority/evidence UI and contracts are partially present; AGI remains advisory/controlled. | AGI reads the same execution profile/envelope/audit/provenance contracts and can request governed actions without becoming a second authority source. | Not started | AGI decision tests show advisory-only outputs cannot add authority fields. |
| P2-13 | P2 | AGI cockpit UI is too dense for users and not yet a task-control console. | Current UI exposes raw matrices/registries with high visual noise. | Chinese-first AGI cockpit: robot-like command console, concise status push, chat pull-and-act, evidence cards, governed action receipts. | Not started | Playwright visual checks and UX snapshot tests for dashboard readability and action flow. |

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
| C-16 | pending | QA failure classification routes Director repair, CE replan, PM revision, infra retry, and hard-stop failures through a typed contract. |

## Update Rules

1. Every gap fix must update this ledger in the same commit or an immediately adjacent commit.
2. A gap may move to `Partial` only with a committed test or audit artifact.
3. A gap may move to `Closed` only when its target state is enforced or negative-tested.
4. Compatibility audit-only states must not be called closed.
5. New bypasses discovered by bench, UI observation, or final request audit must be added here before starting the fix.
