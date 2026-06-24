# Platform Embedded AGI Decision Governance Blueprint

Date: 2026-06-25
Status: Implementation blueprint
Scope: embedded AGI decision boundaries, structured evidence, audit, and UI surface

## Problem

Polaris should not hard-code every project, architecture, library, repair, or
planning decision. Hard-coded rules are useful for platform invariants, but they
become a liability when they encode fast-changing engineering judgment. The
platform was designed around an embedded LLM/AGI planning layer, so variable
decisions should be made by an intelligent advisor that can read the task
contract, project documents, current code, dependency manifests, runtime
constraints, and verification evidence.

The platform must still remain safe and auditable. AGI decisions cannot bypass
path gates, tool authorization, output contracts, realtime policy, role flow, or
final provider-request audit. The correct architecture is therefore not
"everything hard-coded" and not "LLM can do anything"; it is:

```text
hard invariants -> evidence package -> AGI/LLM advisor -> structured decision
    -> schema/risk validation -> handoff/runtime/audit/UI projection
```

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

Embedded AGI/strong-model advisors should handle context-dependent decisions:

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
- source (`platform_signal_guidance`, `embedded_agi_advisor`, `chief_engineer`,
  `user`, `project_document`)
- selected option only when an explicit decision was made
- options considered as families or evaluation dimensions, not fixed product
  endorsements
- rationale, tradeoffs, risks, confidence, constraints, and evidence refs
- validator result and whether the decision was accepted for handoff

## Current Landing

`chief_engineer_auto_decision` now demonstrates the desired pattern:

1. Deterministic hard rules still block no-task, blocked/failed, and
   `needs_review` cases.
2. Non-blocked cases can optionally call an `IntelligentDecisionAdvisor`.
3. The advisor receives `chief_engineer.decision_evidence.v1` evidence.
4. Advisor output must contain a boolean `proceed` and non-empty `reason`.
5. Invalid advisor output or advisor exceptions fail closed and require review.

This is intentionally a small landing point. It proves the boundary without
making AGI a hidden runtime dependency for every path.

## UI Requirements

The AGI UI should not expose a vague "AI says yes" panel. It should show:

- hard-rule result and blockers
- evidence schema/version used by the advisor
- advisor source/model/run id when available
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
- AGI advisors must never bypass tool/path/security/output-contract gates.
- Every AGI decision that affects execution must be structured and auditable.
- If AGI evidence is insufficient, the correct result is `blocked` or
  `guidance`, not a fabricated decision.
- Director should consume CE/AGI decisions; it should not independently choose
  architecture or project strategy from its own prompt heuristics.

## Next Landing Steps

1. Add a shared `AgiDecisionRecordV1` or promote `ArchitectureDecisionV1` into a
   role-neutral decision contract.
2. Add CE architecture advisor invocation behind an opt-in runtime flag.
3. Project AGI decisions into ContextOS and runtime.v2 events.
4. Add frontend UI for decision timeline, evidence, validation status, and
   accepted/rejected handoff impact.
5. Add final-provider-request audit fields that show which structured AGI
   decisions were present in the request context.
