# Startup readiness and failed-launch evidence

Status: local implementation and scoped review-finding fixes verified;
final release and fresh unattended qualification pending.

## Intent and constraints

Keep all 120 current L1–L12 catalog projects in the goal. A project must be
fresh isolated, unattended and physically verified. This change removes
startup/reporting defects; it does not relax artifact, verifier, authorization,
receipt or QA requirements. No generated target engineering edits. Preserve
dirty main; no branch, commit, shared-service restart or JetStream deletion.

## Observed facts and limits

- r08 runner exited 1 before Factory/Provider. The isolated instance is failed.
- Three owned NATS starts received SIGTERM approximately eight seconds after
  start, during consumer recovery. `ManagedNATSServer.ensure_running` has an
  independent eight-second readiness deadline and stops on its expiry.
- A full 6.6GB store copy recovered 719 consumers and 526,519 messages on a
  private port in 2.931s. Thus consumer count alone is NOT proof of inherently
  slow recovery. The exact historical I/O delay is not established.
- A living child currently returns success without proving readiness; early
  child exit is not distinguished from deadline exhaustion; cancellation has
  no explicit owned-child drain.
- `isolated_instance_start_failed` is falsely classified terminal. The runner
  then attempts physical gates and appends to a never-enrolled FactStream.
  Exact r08 storage authority is absent and its ledger is empty.
- Existing public `bootstrap_fact_stream_workspace` with the static catalog
  is idempotent maintenance, not a run receipt. Scratch probes confirmed no
  facts, ledger rows or successful execution evidence are created.

## Architecture

```text
NATSConfig startup budget
    existing managed NATS adapter
        one monotonic deadline, child-liveness + NATS INFO readiness
        ready | child exited | deadline exhausted | cancelled and drained

internal harness setup
    public FactStream workspace bootstrap (maintenance only)
    isolated Instance Supervisor launch
        launch failed: no Factory attempt, nonterminal, no real verifier run
        launch succeeded: unchanged PM / CE / Director / QA evidence gates
```

NATS remains an infrastructure adapter; no new Cell or second event bus.
Backend lifespan supplies a bounded configurable local-service startup budget,
separate from per-connection timeout and Factory/Provider execution budgets.
Default 60s, strictly positive and finite, maximum 90s. Existing 120s instance
identity window is unchanged. A shorter configured deadline remains enforced.
An open TCP port is not proof of a NATS/JetStream service. Remote or TLS NATS
endpoints remain externally managed; normal client authentication is unchanged.

The adapter must recheck an existing living child, stop/drain only its own child
on failed startup/cancellation, retain ownership until reap under repeated
cancellation, and report the actual exit/deadline reason. Slow or transiently
resetting existing services consume the remaining deadline; only explicit
incompatible protocol is rejected as such. DNS/connect/greeting/ownership waits
share one budget; cleanup is separate and must prove owned-process exit.
No successful readiness is inferred from consumer logs or process existence.

The harness initializes the existing runtime storage authority before launch
using its public maintenance contract, then classifies no-attempt launch
failures as nonterminal. It may record actual negative evidence, never mint a
successful execution receipt. Measurement guards remain fail-closed.

## Alternatives and ruling

Rejected: delete durable consumers; raise Bench timeout; declare TCP as ready;
swallow ledger exceptions; auto-enroll from query/append; disable NATS or locks.
Selected: explicit bounded readiness + existing maintenance initialization +
accurate no-attempt classification. Approved master-plan execution authority
permits implementation and independent acceptance without repeated approvals.

## Verification

Real local protocol server tests cover delayed readiness, wrong TCP service,
existing living-but-unready child, early exit, deadline and cancellation drain.
Settings/environment tests prove the startup budget travels through native
configuration. A real-storage failed-launch test exercises public bootstrap,
negative ledger append and absence of successful receipts/verifier execution.
Then relevant suites, Ruff/Mypy, KernelOne release gate and independent review.
Only after these gates may another fresh isolated Bench start.

## Remaining architectural risks

Shared NATS lifetime is still process-owned; backend shutdown can stop shared
infrastructure. Cross-process startup ownership and dedicated shared-service
lifetime require a separate evidence-backed task, not a false closure here.
The exact historical recovery delay remains open. Native NATS errors were
proven to escape the publisher retry boundary and kill the thread; the native
NoServers/NotFound retry, exact missing-stream initialization and partial-client
cleanup are included here. They do not prove permanent-disconnection recovery
or paid-provider/fresh-project completion.

## Additional implementation findings

- Explicit `env={}` in the internal home resolver incorrectly fell back to
  ambient environment. Use `None` only for inheritance; an empty mapping must
  remain empty. No directory layout or target runtime root changes.
- Split runnerb lost its existing shared audit-fixture imports. Restore those
  imports, not fake new gate success. Its old directory-text assertions are
  aligned with existing CE-owned topology while retaining mandatory tests and
  the prohibition on scaffold-only delivery. Physical gates are unchanged.
- Legacy unsplit runner tests retain obsolete PM/Director-only expectations
  and patch the wrong facade. They remain a separate OPEN migration/isolation
  gap; never run them against shared services. Component acceptance now uses
  real OS PID/network isolation and private runtime homes.
