# Factory R51: CE structured-output convergence

Status: implementation verified; same-run live revalidation in progress  
Scope: `roles.kernel` stream error evidence propagation and `factory.pipeline` bounded CE schema repair.

## Observed facts

L1-04 r51 kept PM completed and retried only `chief_engineer_review`. The primary
physical request was correctly qualified as `go / implement / blueprint / cli`,
contained the validated PM contracts and targets, exposed exactly one strict
`submit_structured_role_output` tool, and forced that tool through `tool_choice`.

Two independent output failures then appeared:

1. One primary response returned only prose and no result-tool call.
2. A later primary response called the result tool but left a required completion
   array empty, yielding `structured_output_payload_schema_mismatch`.

The existing bounded schema-repair path was appropriate, but its request drifted
to `bugfix / library` because prompt profiles were re-inferred from the word
"repair" and a product-neutral repair objective.

## Deeper evidence propagation defect

`StreamEngine` built complete stream-error metadata for the telemetry
`call_error` event, including the final-request context audit. It nevertheless
yielded a different reduced metadata object to RoleRuntime. Factory therefore
received neither provider/model identity nor the primary
`prompt_profile_selection`, even though the persisted final-request snapshot
contained them.

This split violated final-request-as-SSoT: observability knew the request identity,
but the recovery consumer did not.

## Fix contract

- A stream error has one canonical metadata payload shared by telemetry and the
  downstream stream consumer.
- Include provider/model, snapshot diagnostics, final-request audit, ContextOS
  audit, and stream SLO evidence in that payload.
- CE schema repair inherits exact language/task/stage/artifact from the primary
  final provider request audit. Missing identity fails closed; no guessing.
- Keep exactly one separately claimed schema-repair attempt.
- Treat task-local CE plans as advisory overlays. Exact PM ids, targets, scope,
  dependencies, and entrypoints remain Factory/CE-owner projections.
- Do not modify generated target-project code and do not restart PM.

## Verification

- CE/PM/structured-output targeted suite: `341 passed`.
- Stream error metadata regression: `5 passed`.
- Ruff format/check: pass.
- Mypy on touched implementation files: pass.
- Live proof must show the repair final request retains
  `go / implement / blueprint / cli` and either produces a valid portfolio or a
  new, precisely attributable residual.

## QA-local repair context residual

After CE and Director completed, same-run `qa_gate` repair proved the mutation
chain itself healthy: `edit_file` executed, before/after hashes differed, the
authoritative effect receipt reached `RECEIPT_COMMITTED`, and `go test ./...`
reran. The remaining syntax diagnostic stayed at `main_test.go:616/618`.

Dynamic final-request inspection found a deeper context-selection defect. Both
the initial materialization-quality repair prompt and the bootstrap write retry
projected only the head of a long file. The exact verifier line was outside the
4000/9000-character windows. The forced writer therefore had write authority
but not the source text it needed, causing real yet ineffective edits.

Repair contract:

- Parse exact path/line references from structured verifier text.
- Project bounded, merged line windows centered on matching diagnostics.
- Preserve existing head-first bounded fallback when no diagnostic matches.
- Do not increase global token budgets, change verifier policy, expand write
  scope, restart PM/CE, or patch the generated project.
- Revalidate on the same r51 Factory run from `qa_gate` only.

## Instance restart regression exposed by the refactor

The first hot-restart attempt reached `Application startup complete` and served
`/health=200`, yet Launcher killed it after 75 seconds with
`backend identity check timed out for port 49978`. Dynamic timing showed each
identity probe called `/v2/runtime/fingerprint`, whose mutable-source check took
about 15 seconds after the large source split, exceeding the probe's 5-second
timeout on every attempt.

Process identity and mutable source freshness are separate contracts. Launcher
now probes a dedicated authenticated `/v2/runtime/process-identity` endpoint
that returns immutable startup facts without walking the source tree. The full
`/v2/runtime/fingerprint` endpoint remains unchanged for explicit freshness
audits. Raising timeouts was rejected because it would keep startup latency
coupled to repository size.
