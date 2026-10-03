# Parallel unattended hardening: bounded recovery, shared service, truthful state

Status: assigned design/implementation; not unattended qualification.

## Goal and authority

All current 120 L1–L12 projects must complete fresh isolated without external
Agent intervention during qualification. Preserve physical gates and valid
checkpoints; ordinary failures recover locally. User approved master-plan direct
implementation/acceptance and now explicitly requests parallel experts with
main-agent supervision. No branch, commit, target engineering edits or paid
Bench from workers. Existing dirty changes are the baseline, not disposable.

## Three independent work boundaries

### A: CE contract-shape recovery

Exact r09 factory_925f19bc355d failed at CE after PM success. Native original
argument hash c4b0cef7a7406254ed2e66ef4da60e2c155991d8c999f9a09d280d501b3af304
matches journal complete.payload.result.structured_output. Artifact row13 is
not_applicable, id ART-assets-extra, owner null, path assets/, semantic_role assets.
Actual DTO replay raises canonical-path ValueError. Strict gate remains correct;
generic constructor failure currently bypasses typed local recovery.

Implement generic producer-owned artifact-shape diagnosis and recovery, not a
special case for this project/id/path. Keep draft/candidate evidence distinct
from accepted authority. A bad artifact shape must either safely normalize an
inert syntactic representation with an explicit receipt/diagnostic or reach the
existing bounded CAS semantic repair with exact selectors and allowed operation.
Absolute/dot/parent/empty/foreign owner/collision/unknown authority remain denied.
Never widen immutable PM grants, silently drop IDs or invent target assets.
Final contract/handoff requires unchanged native revalidation.

### B: Shared NATS lifecycle

Existing startup/readiness/cancel fixes stay. Live r09 NATS65597 belongs to
backend65177; shutdown of that backend can kill a bus other instances use.
Process-local startup lock also cannot serialize independent backend starts.

First deliver a dynamically proven design comparing dedicated platform-owned
service with fenced shared ownership/reference leases. Reuse KernelOne process,
lock and Instance Supervisor facilities before inventing state. Specify crash,
PID reuse, cancellation, identity, takeover and current/shared store migration.
Backend shutdown must release its client, not destroy other users' bus; concurrent
starts must never spawn competing writers to the same store. No adoption based
only on PID/port or unverified manifest. Main reviews the design and authorizes
the exact implementation files before worker product edits. Probe ONLY private
PID/network/tmp/home; do not stop/restart/reparent live NATS or delete consumers.

### C: Attempt authority and safe validation

Generic _runner_exception without backend run_id still infers attempt started
and may substitute the harness ID as Factory ID. Eliminate that false authority;
distinguish attempted transport, observed backend run and terminal execution.
Preserve startup failure negative evidence and event-wait races. Unknown cannot
be promoted to success or silently treated as safe terminal.

Legacy unsplit runner tests use retired two-role semantics and wrong facade
mocks; host execution spawned four unintended instances. Add concrete test IO
isolation and migrate genuinely obsolete fixtures to canonical helpers/interfaces
without deleting cases, relaxing physical gates or resurrecting PM–Director.
Current split tests and real failed-launch storage must remain green. No target
catalog/business/scaffold/score changes. Record remaining incompatible cases.

## Architectural tradeoffs and ruling

Selected: three independent boundaries with explicit ownership, typed state and
evidence. Rejected: global path relaxation, larger retry-only fixes, fake run IDs,
deleting JetStream state, lowering QA/Bench thresholds or three paid Bench loops.
B is architectural and gates product edits on main's reviewed design. A/C can
implement from their bounded specs with TDD. Public APIs crossing A/B/C retain
existing callers unless an explicit scoped interface update is approved.

## Verification and integration

Each worker captures pre-edit source baseline, reproduces RED dynamically, edits
only its bucket, runs focused/full affected suites in bwrap private namespaces,
Ruff/Mypy and self-review, and reports JSON + task-only diff + hashes. Main does
independent task reviews, resolves findings, reruns integration/release, freezes
source and supervises one current isolated qualification. Local green and
external worker status are never Run Ledger/ContextOS/ReceiptStore facts.

Priority: A unblocks present CE; B prevents multi-instance disruption; C prevents
false progression/unsafe test side effects. No all-project claim until every
catalog project has native COMPLETED_VERIFIED evidence and stable batch results.
