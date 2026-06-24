# Platform Embedded AGI Decision Governance Blueprint

Date: 2026-06-25
Status: Implementation blueprint
Scope: embedded AGI decision boundaries, structured evidence, audit, and UI surface

## Problem

Polaris already has an embedded AGI/Resident layer. It is not a new sidecar and
not a replacement for the role kernel. It is a platform-level Role running on the
same RoleRuntime / ContextOS / TurnEngine foundation as PM, Chief Engineer,
Director, and QA. Its difference is responsibility and capability: it replaces
the human who would otherwise watch agent runs, inspect evidence, decide whether
to continue, pause, escalate, or ask for more proof, and turn repeated lessons
into governed goals.

Polaris should not hard-code every project, architecture, library, repair, or
planning decision. Hard-coded rules are useful for platform invariants, but they
become a liability when they encode fast-changing engineering judgment. Variable
decisions should be made by the embedded Resident/AGI supervisor from structured
evidence: task contracts, project documents, current code, dependency manifests,
runtime constraints, ContextOS, final provider-request audit, Run Ledger, CE
blueprints, task execution profiles, runtime events, and verification results.

The platform must still remain safe and auditable. AGI decisions cannot bypass
path gates, tool authorization, output contracts, realtime policy, role flow, or
final provider-request audit. The correct architecture is therefore not
"everything hard-coded" and not "LLM can do anything"; it is:

```text
hard invariants -> evidence package -> Resident/AGI Role -> structured decision
    -> schema/risk validation -> handoff/runtime/audit/UI projection
```

## Runtime Foundation

AGI/Resident is a role-level control-plane capability:

- role id: `resident_agi`
- role profile source: `roles.profile` / `core_roles.yaml`
- runtime foundation: `roles.runtime` + ContextOS + TurnEngine
- durable state: `resident.autonomy`
- decision trace: Resident decision trace
- public capability surface: `resident.agi_capability_surface.v1`

It must not become a parallel orchestration stack. PM, Chief Engineer, Director,
and QA remain the execution roles. AGI/Resident supervises and decides from a
wider evidence surface, then writes decisions/goals through governed Resident
contracts. Code mutation still belongs to Director and tool/security gates.

## Decision Layers

### Hard Rules

Hard rules protect platform invariants and must remain deterministic:

- workspace/path authorization and target-project isolation
- dangerous command, file write, and tool-call gates
- PM -> Chief Engineer -> Director runtime chain
- NATS JetStream + `/v2/ws/runtime` as Polaris product realtime rail
- output protocol contracts such as patch/file blocks
- schema validation, context snapshot, final provider-request audit
- fail-closed behavior when tools, schemas, paths, or model calls are invalid
- quality gate and receipt requirements before claiming success

### AGI Decisions

Embedded Resident/AGI should handle context-dependent decisions:

- task decomposition and sequencing when task contracts are ambiguous
- architecture and dependency tradeoffs
- when a task needs refactor vs minimal repair
- which tests or verifiers best prove completion
- risk prioritization and escalation
- context selection and evidence compression
- retry/repair strategy after verification failures
- UI/UX approach when building frontend work
- whether to proceed, pause for review, or request a stronger blueprint

### Structured Contracts

AGI output must become durable data, not unstructured prose. Decision records
should include:

- decision id / concern / status (`guidance`, `proposed`, `accepted`,
  `rejected`, `blocked`)
- source (`platform_signal_guidance`, `resident_agi_supervisor`, `chief_engineer`,
  `user`, `project_document`)
- selected option only when an explicit decision was made
- options considered as families or evaluation dimensions, not fixed product
  endorsements
- rationale, tradeoffs, risks, confidence, constraints, and evidence refs
- validator result and whether the decision was accepted for handoff

## Current Landing

`chief_engineer_auto_decision` now demonstrates the desired pattern without
creating a second AGI path:

1. Deterministic hard rules still block no-task, blocked/failed, and
   `needs_review` cases.
2. Non-blocked cases can optionally call a `ResidentDecisionSupervisor`.
3. The supervisor receives `chief_engineer.decision_evidence.v1` evidence.
4. Supervisor output must contain a boolean `proceed` and non-empty `reason`.
5. Invalid supervisor output or supervisor exceptions fail closed and require review.
6. Every result carries a `resident_decision` payload that can be written to
   Resident decision trace.

This is intentionally a small landing point. It proves the boundary without
making AGI a hidden runtime dependency for every path.

## Decision Event Projection

Resident decision trace remains the source of truth:
`workspace/meta/resident/decision_trace.jsonl`.

Each successfully recorded decision is now also projected as a best-effort
runtime audit observation:

- event name: `resident_decision_recorded`
- schema: `resident.decision_event.v1`
- actor: `ResidentAGI`
- runtime path: `runtime/events/resident.decisions.jsonl`
- source pointer: `meta.source_of_truth`

This gives ContextOS dashboards, runtime subscribers, and audit tools a stable
way to observe AGI decisions without treating the event stream as a second fact
store. If event projection fails, the decision trace write remains authoritative
and the failure is logged.

## AGI Capability Surface

The Resident/AGI role needs platform facts, not just prompt prose. Polaris now
exposes a governed capability surface:

- schema: `resident.agi_capability_surface.v1`
- endpoint: `/v2/resident/capabilities`
- status projection: `/v2/resident/status?details=true`

Capabilities are grouped by access class:

- read-only evidence: ContextOS, final request audit, Run Ledger, evidence
  bundles, CE blueprint, task execution profile, runtime events
- controlled decision writes: Resident decision trace, goal proposals
- controlled execution: Resident goal bridge into PM -> Chief Engineer ->
  Director, never direct code mutation

The capability surface is a whitelist, a UI contract, and a role-context
contract. `RoleSignalPlane` injects it as the must-have
`resident_agi_capability_surface` signal only for `resident_agi`, so AGI sees
its platform audit powers and non-bypass rules inside the same ContextOS/TurnEngine
request path used by PM, Chief Engineer, Director, and QA. The signal is
traceable through `context_sources`, budget-controlled through ReceiptStore, and
can be replaced by a provider-injected dynamic capability catalog later without
forking the role runtime.

It does not bypass tool/path/security/output gates.

## UI Requirements

The AGI UI should not expose a vague "AI says yes" panel. It should show:

- hard-rule result and blockers
- evidence schema/version used by the supervisor
- role id, runtime foundation, and available capability surface
- supervisor source/model/run id when available
- structured decision status and rationale
- selected option only when status is proposed/accepted
- rejected alternatives or evaluation dimensions
- confidence and risk flags
- validator result and handoff effect
- links to ContextOS snapshots, final provider request audit, receipts, and
  verification evidence

The UI must distinguish:

- `guidance`: platform detected a concern; AGI/CE still needs to decide
- `proposed`: AGI proposed a decision; platform/user may accept or reject
- `accepted`: decision is approved for downstream execution
- `rejected`: decision was considered and rejected with rationale
- `blocked`: decision cannot be made from available evidence

## Maintenance Rules

- Do not encode fast-changing technology trends as hard-coded product lists.
- Hard-code only platform invariants, schema contracts, and safety gates.
- AGI supervisors must never bypass tool/path/security/output-contract gates.
- Every AGI decision that affects execution must be structured and auditable.
- If AGI evidence is insufficient, the correct result is `blocked` or
  `guidance`, not a fabricated decision.
- Director should consume CE/AGI decisions; it should not independently choose
  architecture or project strategy from its own prompt heuristics.

## Next Landing Steps

1. Add a shared `AgiDecisionRecordV1` or promote `ArchitectureDecisionV1` into a
   role-neutral decision contract.
2. Add CE architecture Resident/AGI supervisor invocation behind an opt-in runtime flag.
3. Add frontend UI for decision timeline, evidence, validation status, and
   accepted/rejected handoff impact.
4. Add final-provider-request audit fields that show which structured AGI
   decisions were present in the request context.
