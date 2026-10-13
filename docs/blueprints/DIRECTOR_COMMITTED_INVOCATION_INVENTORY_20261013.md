# Director committed invocation inventory

Approved scope: four-role delivery convergence, Task B. Existing state owner remains roles.kernel TransactionKernel and TurnLedger. No new engine, ledger, repair kernel, policy or authority is introduced.

## Evidence and root cause

Immutable failed run factory_780d3983bf14 stopped the primary session with committed_tool_transcript_missing_native_arguments and its quality repair with committed_tool_receipt_invocation_mismatch. The primary bridge replaced committed tool calls with started-event observations, which need not cover denied or superseded calls. The repair fact recorded five earlier read IDs in its receipt and six later write IDs, but only the six writes in execution.invocations: the final decision had replaced the bootstrap decision.

## Owner correction

TurnLedger records detached typed invocation batches before normal, denied, bootstrap and deferred dispatch. Invocation IDs cannot collide. Native decoder inputs preserve native_arguments separately from normalized arguments; native_arguments are observations and never authorize effects. The durable execution record joins real receipt IDs to these inputs, preserves invocation_batches, and rejects unknown, duplicate or tool-mismatched receipt IDs. Role result projection verifies committed batch/call/tool/status, arguments, result/error and effect fields, and complete receipt membership. The session bridge consumes this projection and never replaces it with dispatched started events.

Ordinary failing verification remains a failed receipt in the same Director task/session, followed by scoped repair and actual verifier execution. Successful writes alone cannot complete the task. Explicit single-batch policy remains opt-in; default Director tools stay complete. No PM/CE restart, invented successful receipt, inferred native arguments from output, or generated Bench write is part of the fix.

## Boundary assessment

ToolBatchExecution adds an optional typed invocation_batches projection; ToolInvocation adds optional native_arguments observations. Existing public turn_contracts owns both. No Cell dependency, state owner, effect allowance, tool authority or policy scope changes. roles.kernel README documents the invariant. Existing graph/catalog/public boundary remains valid; no KernelOne source changed. KernelOne all-release and fresh isolated native acceptance remain root integration work.

## Verification

New regressions cover bootstrap plus final batches with reordered receipts; typed fact persistence/rehydration; detached argument input; native tool and argument aliases; duplicate IDs; changed/omitted receipt results; forged effects; mixed valid write and denied command; superseded no-effect accounting; full AIExecutor/native Anthropic HTTP qualification; failing real verification repaired and rerun under the same task/session. The existing forged-wire counterexample stays rejected before the second provider request.

Twenty historical fixture failures were independently reproduced using exact HEAD projection bytes loaded in memory. Their fixtures now provide explicit single-batch policy, actual invocation input/outcome pairings, and Request metadata. Existing behavioral assertions remain.

Source/component results are recorded in ~/.polaris/audit_archives/role-delivery-full-20261013/director-full-report.json. These do not establish fresh native delivery, independent QA or namespace release.
