# NATS cold loopback readiness

Date: 2026-11-05. Task: NATS-COLD-START. Classification: pattern.

## Problem and evidence

`ensure_local_nats_runtime` gives `_observe_existing_endpoint` the entire startup
deadline. Its first `_probe_nats_endpoint` wraps DNS, TCP connect and INFO reading
in that remaining deadline. A silent connect timeout on a genuinely unbound local
port therefore consumes the full budget before shared service admission. The
caller reports `startup_deadline_exceeded` without reaching the existing owner.
CodeGraph CLI source inspection confirmed this path; MCP was unavailable.

## Architecture and responsibilities

```text
ensure_local_nats_runtime
  autostart eligibility (plain local endpoint, no credentials, caller allows)
  _observe_existing_endpoint
    _probe_nats_endpoint
      deadline-bounded DNS
      bounded TCP connect only when autostart is eligible
      connect timeout: exclusive OS bind proves address currently available
      established socket: INFO greeting retains remaining outer deadline
  existing local lock
  ensure_shared_nats_runtime
    KernelOne control lease / writer fence
    process, endpoint, store incarnation validation
    canonical shared owner spawn or attachment / native readiness
```

The availability check is an advisory observation, never service ownership or
readiness. It closes its socket immediately, does not listen, and uses no address
reuse. All resolved addresses must be refused or bindable before the endpoint is
absent. A bound or uncertain address remains pending. The coordinator remains
authoritative across the observation/startup race and preserves existing native
identity, store, generation and cancellation checks.

This is an infrastructure messaging adapter correction, not a new Cell. Existing
KernelOne storage layout and locked-file capabilities remain reused by the shared
coordinator. Catalog/subgraph review found no declared Cell owning this adapter;
`event_pipeline` downstream contracts and state owners do not change. No graph,
public contract, descriptor or product realtime protocol change is needed.
The only new effect is a temporary exclusive local socket bind during explicitly
eligible autostart; it creates no file, durable state or listening service.

## Assumptions and pre-mortem

- Timeout alone never proves absence. Only actual successful exclusive bind does.
- A listener with delayed INFO must retain the full overall readiness budget.
- Credentialed and `allow_autostart=False` endpoints must never run bind probes.
- A timeout while the port is occupied must not start a second writer.
- Successful availability proof must still enter the unchanged coordinator:
  missing/replaced stores and invalid owners remain fail-closed.
- IPv4 and IPv6 use the address family returned by bounded async resolution.
- A competing bind after observation is still possible; native admission and
  readiness remain mandatory. Bind failure conservatively retains waiting.

## Verification plan

Write failing native cold-connect regression first. Use private temporary home,
OS-assigned ports and mocked asynchronous transport timeout; keep real shared
coordinator, native NATS process, identity/store fencing and native client flush.
Add occupied-port, delayed INFO, external/incompatible, credentials/no-autostart,
and missing/replaced store regressions. Run existing managed startup and shared
lifecycle suites, Ruff, formatting and strict mypy for touched source. Run backend
KernelOne architecture and release gates when available; report any omissions.
All temporary service teardown must target captured private identities only.
No current backend, frontend, shared NATS or generated project is modified.

## Complexity and rollback

One constant-size socket bind per timed-out resolved address: O(A) time and O(1)
extra socket space for A resolved addresses; connect and greeting remain under
one existing absolute startup deadline. No new process, retry controller, owner,
ledger or persistence mechanism. Revert the wrapper/probe change to roll back;
existing coordinator/store format stays unchanged.

## Results

- RED: three native regressions failed with `startup_deadline_exceeded` before
  the implementation (cold admission, missing store, replaced store).
- GREEN: 66 tests passed in 25.08 seconds across managed startup, shared native
  lifecycle and 14 new cold-loopback regressions. This includes native JetStream
  publish/ack, delayed INFO, occupied sockets, IPv4/IPv6 bind release, explicit
  no-bind policy, store incarnation and existing owner identity fences.
- Ruff and formatting passed; strict mypy passed for the modified source.
- KernelOne release architecture tests: 18 passed in 13.48 seconds.
- The first combined run inherited the host home and reported 61 passed / 2
  failed: `test_slow_existing_greeting_uses_remaining_startup_budget` and
  `test_transient_existing_socket_close_is_retried_without_replacement` hit
  existing shared authority/store guards. Rerunning with a private invocation
  `KERNELONE_HOME` passed both; no original test expectations were changed.
- Default Python lacked `pydantic`; verified commands use repository `.venv`.
- Full KernelOne release tests: 373 passed / 2 skipped in 193.26 seconds;
  collection: 375 tests. Overall gate failed only at
  `roles_runtime_cell_import_boundary`: six existing cross-Cell internal imports
  in unchanged HEAD files `test_host_session_continuity.py` (20, 21, 96, 222)
  and `test_static_quality_observation_reconciliation.py` (155, 188). These are
  outside this task's write scope. The external task JSON retains the exact
  gate report; the full release is not claimed passing.

The full repository pytest suite and native Windows execution were not run.
Component evidence does not prove all-project acceptance; current long-running
services are untouched. Independent parent review and integration remain pending.
