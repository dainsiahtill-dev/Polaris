# Workflow Runtime

## Purpose

Own workflow engine runtime, activity and workflow registration, and workflow state persistence for PM, Director and QA orchestration.

## Kind

`capability`

## Public Contracts

- commands: StartWorkflowCommandV1, CancelWorkflowCommandV1
- queries: QueryWorkflowStatusV1, QueryWorkflowEventsV1
- events: WorkflowExecutionStartedEventV1, WorkflowExecutionCompletedEventV1
- results: WorkflowExecutionResultV1
- errors: WorkflowRuntimeErrorV1
- model ceiling: candidate locators are non-authoritative; only bootstrap-bound
  direct owner queries may produce the sealed ModelCeilingTerminalResultV1.
  Each repair round must join roles.kernel provider lifecycle, a material
  effect, the exact runtime.execution_broker verifier failure, and direct
  director.runtime repair coverage. Missing joins park as
  `CONTROL_PLANE_BLOCKED`; generic receipt hashes are never authority.
- project completion: this Cell owns only the typed durable cursor and CAS.
  Nonterminal cursor registrations include the exact identity and bounded
  convergence limits required for process-restart recovery. It has no
  projection, VerificationGuard, TaskMarket, or convergence policy. Public
  recovery data is `ProjectCompletionCursorRegistrationV1`; CAS conflicts are
  `ProjectCompletionCursorConflictError`.

## Depends On

- `runtime.state_owner`
- `policy.workspace_guard`
- `policy.permission`
- `context.engine`
- `director.runtime`
- `events.fact_stream`
- `factory.cognitive_runtime`
- `roles.kernel`
- `runtime.execution_broker`

## State Ownership

- `runtime/workflows/*`
- `runtime/state/workflow/*`

## Effects Allowed

- `fs.read:runtime/**`
- `fs.write:runtime/workflows/*`
- `fs.write:runtime/state/workflow/*`
- `fs.write:runtime/events/runtime.events.jsonl`
- `fs.write:runtime/cognitive_runtime/*`
- `db.read_write:workflow_runtime`
- `process.spawn:workflow/*`

## Verification

- `tests/orchestration/test_workflow_runtime.py`
- `tests/test_embedded_orchestration_dag.py`
- `polaris/tests/test_qa_workflow_cognitive_runtime.py`
