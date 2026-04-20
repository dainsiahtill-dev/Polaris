# Runtime Artifact Naming Convention (ACGA 2.0)

This document defines the canonical artifact keys and their corresponding logical paths used in the Polaris backend.

## 1. Core Principles
- **Separation of Concerns**: Keys describe the *nature* of the artifact (technical), not the *source* role (business semantics) where possible.
- **Technical Keying**: Use `category.name` or `category.sub.name` format.
- **Backward Compatibility**: Legacy uppercase keys (e.g., `PM_TASKS_CONTRACT`) are mapped to new technical keys in the `ArtifactService`.

## 2. Artifact Registry

| Artifact Key (Technical) | Logical Path | Description | Legacy Alias |
| :--- | :--- | :--- | :--- |
| `contract.plan` | `runtime/contracts/plan.md` | Implementation plan | `PLAN` |
| `contract.gap_report` | `runtime/contracts/gap_report.md` | Gap analysis report | `GAP_REPORT` |
| `contract.pm_tasks` | `runtime/contracts/pm_tasks.contract.json` | Task contract | `PM_TASKS_CONTRACT` |
| `runtime.report.pm` | `runtime/results/pm.report.md` | PM execution report | `PM_REPORT` |
| `runtime.state.pm` | `runtime/state/pm.state.json` | PM runtime state | `PM_STATE` |
| `contract.resident_goal` | `runtime/contracts/resident.goal.contract.json` | Resident goal contract | `RESIDENT_GOAL_CONTRACT` |
| `contract.resident_goal_plan` | `runtime/contracts/resident.goal.plan.md` | Resident goal plan | `RESIDENT_GOAL_PLAN` |
| `runtime.result.director` | `runtime/results/director.result.json` | Director result | `DIRECTOR_RESULT` |
| `runtime.status.director` | `runtime/status/director.status.json` | Director status | `DIRECTOR_STATUS` |
| `runtime.log.director` | `runtime/logs/director.runlog.md` | Director run log | `DIRECTOR_RUNLOG` |
| `audit.events.runtime` | `runtime/events/runtime.events.jsonl` | Runtime event stream | `RUNTIME_EVENTS` |
| `audit.events.pm` | `runtime/events/pm.events.jsonl` | PM event stream | `PM_EVENTS` |
| `audit.events.pm_llm` | `runtime/events/pm.llm.events.jsonl` | PM LLM interaction events | `PM_LLM_EVENTS` |
| `audit.events.pm_task_history` | `runtime/events/pm.task_history.events.jsonl` | Task history events | `PM_TASK_HISTORY` |
| `audit.events.director_llm` | `runtime/events/director.llm.events.jsonl` | Director LLM events | `DIRECTOR_LLM_EVENTS` |
| `audit.transcript` | `runtime/events/dialogue.transcript.jsonl` | Dialogue transcript | `DIALOGUE_TRANSCRIPT` |
| `runtime.result.qa` | `runtime/results/qa.review.md` | QA review report | `QA_REVIEW` |
| `runtime.log.pm_process` | `runtime/logs/pm.process.log` | PM subprocess log | `PM_SUBPROCESS_LOG` |
| `runtime.log.director_process` | `runtime/logs/director.process.log` | Director subprocess log | `DIRECTOR_SUBPROCESS_LOG` |
| `runtime.state.last` | `runtime/memory/last_state.json` | Last state snapshot | `LAST_STATE` |
| `runtime.status.engine` | `runtime/status/engine.status.json` | Engine status | `ENGINE_STATUS` |
| `runtime.control.pm_stop` | `runtime/control/pm.stop.flag` | PM stop flag | `PM_STOP_FLAG` |
| `runtime.control.director_stop` | `runtime/control/director.stop.flag` | Director stop flag | `DIRECTOR_STOP_FLAG` |
| `runtime.control.pause` | `runtime/control/pause.flag` | Pause flag | `PAUSE_FLAG` |
| `contract.agents_draft` | `runtime/contracts/agents.generated.md` | Agents.md draft | `AGENTS_DRAFT` |
| `contract.agents_feedback` | `runtime/contracts/agents.feedback.md` | Agents.md feedback | `AGENTS_FEEDBACK` |

## 3. Usage Guidelines
Always prefer using technical keys when interacting with `ArtifactService`. 
Avoid hardcoding strings; use constants or registry lookup.
