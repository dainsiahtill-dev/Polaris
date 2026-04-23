# runtime.task_market

## Purpose

`runtime.task_market` is the asynchronous collaboration broker for governed Agent work items.
It owns publish/claim/lease/requeue/dead-letter primitives used by PM, ChiefEngineer, Director, and QA.

## Polaris Role Flow (Current Governance)

- `Architect` (optional): produces project charter and planning briefs.
- `PM` (mandatory): splits planning briefs into executable Task Contracts and publishes to task market.
- `ChiefEngineer` (mandatory): consumes design work, outputs blueprint, republishes to execution queue.
- `Director` (scalable workers): consumes execution work, delivers code/test result, republishes to QA queue.
- `QA` / `Architect` / `HITL`: consume review or dead-letter workloads and issue final verdicts.

Stage mapping:

- `pending_design` -> consumed by `ChiefEngineer`
- `pending_exec` -> consumed by `Director`
- `pending_qa` -> consumed by `QA`
- `waiting_human` -> consumed by `HITL` / escalation flow

## Public Surface

- contracts: `polaris.cells.runtime.task_market.public.contracts`
- service: `polaris.cells.runtime.task_market.public.service`

## Contract Summary

- commands:
  - `PublishTaskWorkItemCommandV1`
  - `ClaimTaskWorkItemCommandV1`
  - `RenewTaskLeaseCommandV1`
  - `AcknowledgeTaskStageCommandV1`
  - `FailTaskStageCommandV1`
  - `RequeueTaskCommandV1`
  - `MoveTaskToDeadLetterCommandV1`
- query:
  - `QueryTaskMarketStatusV1`
- results:
  - `TaskWorkItemResultV1`
  - `TaskLeaseRenewResultV1`
  - `TaskMarketStatusResultV1`

## Runtime Storage

- `runtime/task_market/work_items.json`
- `runtime/task_market/dead_letters.json`
- fact stream side effects under `runtime/events/*` via `events.fact_stream`.

## Rollout Mode

- `KERNELONE_TASK_MARKET_MODE=off`: disabled (default)
- `KERNELONE_TASK_MARKET_MODE=shadow`: PM dispatch keeps legacy direct workflow and also mirrors tasks into market
- `KERNELONE_TASK_MARKET_MODE=mainline`: PM publishes to `pending_design` and exits mainline dispatch
- `KERNELONE_TASK_MARKET_MODE=mainline-design`: alias of `mainline`
- `KERNELONE_TASK_MARKET_MODE=mainline-full`: PM publishes then runs bounded CE -> Director -> QA inline consumer loop

## Notes

- This Cell is not a replacement for `runtime.execution_broker`.
- This Cell is not a replacement for `events.fact_stream`.
- Query path must remain side-effect free.
