# PM Planning

## Purpose

Produce PM task contracts from project goals and docs, enforce planning quality gates, and publish governed planning artifacts.

## Kind

`workflow`

## Public Contracts

- commands: GeneratePmTaskContractCommandV1
- queries: GetPmPlanningStatusQueryV1
- events: PmTaskContractGeneratedEventV1
- results: PmTaskContractResultV1, PmTaskIntentV1
- errors: PmPlanningErrorV1

## Depends On

- `context.engine`
- `llm.control_plane`
- `policy.permission`
- `policy.workspace_guard`
- `runtime.state_owner`
- `audit.evidence`
- `finops.budget_guard`

## State Ownership

- `runtime/state/pm/*`

## Effects Allowed

- `fs.read:workspace/docs/*`
- `fs.read:runtime/contracts/*`
- `fs.write:runtime/state/pm/*`
- `fs.write:runtime/events/pm.events.jsonl`
- `llm.invoke:pm/*`

## Verification

- `tests/test_pm_task_quality_gate.py`
- `tests/test_pm_orchestration_api.py`

## Product Intent Protocol

The default model producer emits `pm.task_intent.v1`: product goal, required and
optional requirements, observable product acceptance, explicit user constraints,
and implementation tasks with stable IDs, goals, task acceptance and dependencies.
A small coupled product may have one task; PM does not manufacture phase documents,
file topology, steps or verifier commands to satisfy a count or formatting floor.

`public.task_intent.normalize_pm_task_intent` validates the complete contract and
projects it to existing task transport. `project_pm_task_intent_draft` preserves
correctable product omissions without qualifying execution; the public quality
admission gate must revalidate every projected row. `is_pm_task_intent` identifies
the versioned protocol, never permission or a quality verdict. Metadata alone
cannot activate the semantic route. Ordinary semantic corrections retain the
original immutable product contract and revision manifest.

Task `observable_acceptance` and `acceptance_criteria` contain task-local
acceptance. Global `product_acceptance` remains context for the CE portfolio and
independent QA. Advisory readiness diagnostics do not grant revision fields;
only blocking diagnosed errors can extend a narrowed edit manifest.

Chief Engineer owns technical file topology, generated outputs and omitted
verifier recipes. Explicit typed paths and command values remain exact constraints;
prose never creates argv or write authority. Unsafe explicit paths fail closed
before ordinary draft retry. Optional requirements remain optional. Legacy
unversioned task contracts continue through their existing strict admission gates.
Director still requires CE handoff and policy-derived execution authority.

Focused verification: `polaris/cells/roles/adapters/tests/test_pm_task_intent.py`.
Provider-fixture success is component evidence, not native project qualification.


PM intent admission keeps required semantic fields strict and stores bounded JSON product advice separately. Unknown nonreserved product facts relocate losslessly into `planning_advisory`; direct control-plane authority fields reject. Nested advice cannot promote roles, run identity, paths, commands, global acceptance, or trusted depth policy. Aggregate advice limits: 65536 UTF-8 bytes, 4096 value nodes, depth 8. Complementary description/scope prose remains separate. Optional technical constraints come only from original explicit user declarations; omission is valid and typed `.` scope remains forbidden.
