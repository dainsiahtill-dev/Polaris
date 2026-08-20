# Exact-run causal audit and Run Ledger revision recovery

Status: Implemented and live-verified  
Date: 2026-08-21  
Owners: `control_plane.run_ledger`, `audit.diagnosis`

## Problem

An exact Factory run can finish physical verification while remaining red in
the control plane. Live run `factory_ec5697b14a71` exposed one deterministic
case: a same-run QA retry wrote `workspace_validation` revision `4` after the
canonical FactStream already contained revision `7`. The writer derived the
next revision from a restarted local NDJSON projection rather than the
canonical FactStream. Two independent revision roots then made Run Ledger
integrity fail forever although 31 project tests and final QA revalidation
passed.

Manual correlation across provider, tool, verifier, TaskBoundary, TaskRuntime,
Run Ledger, QA, and Factory evidence is too slow for unattended development.

## Architecture

```text
Factory gate producer
  -> control_plane.run_ledger public append
       -> read canonical execution.control_plane facts
       -> select canonical gate branch head
       -> allocate next revision
       -> explicitly resolve every orphan branch head, when present
       -> append immutable Fact + local projection
  -> Run Ledger projection
       -> unresolved fork: fail closed
       -> explicit complete fork resolution: keep history, select new head

Exact-run causal auditor
  -> provider request / response
  -> tool lifecycle / effect receipt
  -> verifier evidence
  -> TaskBoundary
  -> TaskRuntime
  -> Run Ledger
  -> QA verdict
  -> Factory terminal state
  -> one root_cause_code + owner Cell + evidence refs + next action

Bound HTTP API
  -> GET /v2/audit/runs/{run_id}/causal
  -> workspace comes only from backend instance binding
  -> current diagnosis remains separate from historical error counts
```

## Responsibilities

- `control_plane.run_ledger` owns revision allocation and branch resolution.
  Factory may declare gate identity and evidence but may not inspect a local
  ledger copy to invent canonical revision metadata.
- A normal fork remains an integrity failure. Recovery requires a new revision
  that continues one valid head and explicitly lists every discarded branch
  head by immutable content id.
- `audit.diagnosis` owns causal audit reports. Reports are derived evidence,
  never a new execution source of truth and never a Bench success condition.
- Target project files remain read-only to diagnosis. Repair changes Polaris
  only.
- Explicit workspace is local storage-layout authority. Diagnosis must not
  call a backend layout endpoint first: that can deadlock the current request,
  inherit an HTTP proxy, or read another instance's runtime.
- Loopback backend hints are allowed only when no workspace is supplied. They
  must disable environment proxies and remain non-authoritative.

## Causal audit output

Each report must include exact `workspace`, `factory_run_id`, `project_id`, and:

1. link status for every fact-chain layer;
2. one primary `root_cause_code` and responsible Cell;
3. secondary contradictions without overriding primary cause;
4. immutable evidence refs or exact source paths;
5. `next_action` naming the narrow retry boundary;
6. explicit distinction between current blockers and historical errors.

## Technical reasons

- FactStream is already the canonical ledger source. Moving revision allocation
  into its owner removes dual truth instead of adding another repair file.
- Explicit fork-head resolution preserves fail-closed behavior and immutable
  history. Silently choosing the newest event would hide real concurrent-write
  corruption.
- A deterministic auditor makes the fact chain executable as a diagnostic
  invariant instead of relying on UI error counts or human log interpretation.

## Verification

- Unit: sequential revisions supersede correctly.
- Unit: unresolved same-parent fork remains blocked.
- Unit: restarted independent chain is recoverable only through an explicit
  resolver revision listing every orphan head.
- Integration: Factory writer allocates from canonical facts even when local
  NDJSON is missing or stale.
- Live: retry only QA for `factory_ec5697b14a71`; no PM/CE/Director replay;
  Run Ledger, QA, Factory, independent tests, and entrypoint must agree.

## Live closure evidence

- Before recovery: `CONTROL_PLANE_FAIL`, root cause
  `control_plane.run_ledger.gate_revision_fork_after_runtime_reentry`, retry
  boundary `same_run_quality_gate_only`, `pm_ce_restart_allowed=false`.
- Same-run retry: Factory advanced only `quality_gate`; PM, Chief Engineer, and
  Director remained completed.
- After recovery: run `factory_ec5697b14a71` is `completed`; quality gate score
  `100`; Run Ledger `integrity_ok=true` and `outcome_ok=true`; required command
  and QA evidence pass.
- Exact-run audit API returns `DELIVERY_VERIFIED`, no root cause, no evidence
  gap, while retaining `47` historical errors (`42` old failed gates and `5`
  old failed task boundaries) as non-authoritative history.
- Dynamic API debugging also found and closed a self-HTTP/proxy defect:
  explicit workspace resolution now avoids `/v2/runtime/storage/layout`;
  optional loopback hints use `requests.Session(trust_env=False)` and catch
  `RequestException`.
- Tests: causal classifier + HTTP contract + runtime-root authority = `11`
  passed; focused Run Ledger recovery suite = `8` passed; Ruff and Mypy clean
  for changed production files.
