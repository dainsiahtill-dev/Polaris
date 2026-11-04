# Local model L1-L12 acceptance

## User scope and closure criteria

Validate every project in the original projects_v2.json catalog: 120 cases,
10 at each level L1 through L12. Each model candidate is configured through the
existing host LLM config; no business-specific platform implementation or
manual target-source repair. This finite catalog does not establish a universal
claim that every conceivable future project can be completed.

Closure requires each current-candidate project to have platform verification
passed, preserved user-required checks, same-candidate independent QA approved,
all original behavioral requirements and executable-entry/browser evidence,
readable final provider request snapshots, and confirmed process/resource drain.
NOT_RUN, failed, rejected, interrupted, UNKNOWN, absent QA, missing evidence and
transport-only success never count as accepted.

## Existing architecture

Original catalog + frozen host/model config -> internal Bench adapter ->
Instance Supervisor/Registry -> isolated backend/frontend -> EngineeringRunService
-> Director native tools -> current-candidate verifier -> independent QA ->
engineering SQLite and runtime.v2/NATS WebSocket projections.

Supervisor reserves main ports 49977/5173. Test instances get allocated ports.
Every target runtime remains <workspace>/.polaris/runtime. Existing shared-state
records and prior attempts stay intact. No legacy Factory fallback or automatic
repair scheduler.

## Execution

Track all 120 cases and immutable attempts in
~/.polaris/bench_runs/qwen38-all120-20261105/manifest.json. Begin with L1-01; audit
its final request and product behavior before moving on. Advance serially until
resource ownership and local-model performance justify a different schedule.
On obstruction, first preserve final physical request (or explicit pre-dispatch
N/A), events, logs, candidate and resource identity; formulate and reproduce one
root-cause hypothesis. Repair only generic Polaris code with regression tests.
Re-run the same project through its owner; never fix its generated source by hand.
Frozen configuration changes start a distinct attempt with new input hashes.

## Initial candidate

Qwen3.8-27B at an operator-authorized OpenAI-compatible LAN endpoint. The server
advertises 262144 total context tokens; reserve at most 32768 output tokens.
Provider and role concurrency are one until measured evidence supports more.
All prompt-required tools remain present. Configure other similar models through
the same provider and capability contracts, without model-name branches.

The current endpoint rejects image inputs. Do not hard-require nonexistent model
vision or claim image QA. Original UI projects still need actual browser behavior
and supervising-agent visual inspection, alongside the local model's executable
DOM/behavior checks. If a required modality cannot be supplied, report the gap.

## Evidence and outstanding issues

Compatibility commit be20e3843 has 93 focused tests, Ruff, strict mypy, and real
CLI independent-QA acceptance. A wider provider sweep exposed 19 open component
regressions; preserve them separately from local-model Bench qualification.
First Bench attempt encountered NATS startup readiness failure before any LLM
request. Investigate the actual backend process/logs rather than inferring cause
from an incomplete Registry reservation or asynchronous publisher warnings.
Environment isolation may preserve historical JetStream data; it is mitigation,
not proof that existing NATS lifecycle defects have been fixed.
