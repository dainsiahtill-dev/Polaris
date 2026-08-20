# Polaris Multi-Instance Launcher

Status: Draft implementation baseline  
Scope: local development, internal stress testing, and operator-managed project instances

## Intent

Polaris keeps the existing single-workspace runtime invariant inside each backend
process. Multi-project development is implemented above that invariant by a
Launcher that manages multiple isolated Polaris instances.

```text
Launcher
  -> Instance A: one backend, one workspace, one runtime root
  -> Instance B: one backend, one workspace, one runtime root
  -> Instance C: one backend, one workspace, one runtime root
```

This avoids turning `settings.workspace` and `AppState(settings)` into an
unsafe multi-tenant data structure before the full control plane is project
scoped.

## Instance Contract

Each instance owns:

- `workspace`: the single project workspace managed by that backend.
- `runtime_root`: isolated logs, contexts, receipts, and ledger artifacts.
  Default: `<workspace>/.polaris/runtime`. External roots require explicit opt-in;
  Instance Registry and backend process arguments must record the same resolved path.
- `backend_port`: local HTTP/WebSocket endpoint.
- `frontend_port`: optional Vite frontend for that instance.
- `token`: local auth token for frontend/backend binding.
- `polaris_root`: shared Polaris source tree. In development all instances may
  point at the same `polaris_root`, so code edits and Vite/HMR/backend reloads
  become visible across all running instances.

The Launcher is an operator surface. It is not a runtime fact source for PM,
Chief Engineer, Director, QA, ContextOS, ReceiptStore, or Run Ledger.

## Workspace Binding

The workspace is a process-startup authority, not mutable desktop state. The
canonical backend CLI records the resolved startup workspace in both the
legacy `KERNELONE_WORKSPACE` value and the immutable
`KERNELONE_INSTANCE_WORKSPACE` binding. Once that binding exists:

- `POST /v2/settings` must reject a different workspace with
  `INSTANCE_WORKSPACE_REBIND_FORBIDDEN`.
- `POST /v2/factory/runs` must reject a different workspace with
  `INSTANCE_WORKSPACE_BINDING_MISMATCH`, including requests that set
  `persist_workspace=false`.
- `/v2/runtime/process-identity` and `/v2/runtime/fingerprint` must report the
  startup binding as `workspace`, the mutable settings projection as
  `active_workspace`, and their relationship as `workspace_binding_match`.

This prevents a bench/settings request from poisoning the main backend while
its PID, registry record, port, and command line still claim the Polaris source
checkout. A mismatch is a P0 control-plane integrity defect; it must be exposed
and rejected rather than silently synchronized.

Opening an instance workspace from the Launcher must carry an explicit binding:

- `instance`: registry identifier for observability and diagnostics.
- `backend`: backend base URL for that instance.
- `token`: local auth token for that backend.
- `workspace`: absolute workspace path owned by that backend.

The frontend may receive these values through URL query parameters or
`VITE_POLARIS_*` environment variables. API clients and the `/v2/ws/runtime`
WebSocket must consume the same workspace binding. They must not silently fall
back to the default backend, default workspace, or the Polaris source checkout
runtime when an explicit instance binding is present.

Validation must prove the binding at the network layer, not only through UI
labels. When a project instance is opened from Launcher, browser request logs
must show:

- HTTP API calls target that instance's `backend` port.
- `/v2/ws/runtime` connects to that instance's backend port.
- The WebSocket URL carries the bound `workspace` query parameter.
- No request or WebSocket falls back to the main development backend
  (`49977`) unless the opened instance is the `main` instance itself.

For internal stress tests, a `kind=bench_project` registry entry may point at a
shared backend in observed mode. That registration is only a test-observation
surface; it does not prove that the project is running as an isolated production
instance.

When such an observed bench entry is restarted from the Launcher/API, the
supervisor must promote it to an isolated instance by allocating fresh backend
and frontend ports. It must not reuse the shared backend port from the observed
registration.

## Startup Paths

Instances may be registered by more than one path:

1. Launcher API: `POST /v2/instances/start`
2. Backend CLI: `python -m polaris.delivery.cli.backend serve --register-instance ...`
3. Internal stress tooling, such as factory_bench, by calling the same API/CLI

The registry is the shared discovery mechanism, so an instance started by an
agent or internal runner is visible in the Launcher UI.

For internal factory_bench pressure tests, the runner supports two Launcher
instance modes:

- `isolated` (default): start a project-scoped backend/frontend instance and run
  that project's Factory chain against the instance backend. Use this for
  parallel multi-agent bench work, because each project gets its own workspace,
  runtime root, WebSocket stream, and ContextOS surface.
- `observed`: register a read-only `bench_project` observation record for an
  already matching unbound compatibility backend. A CLI-started backend with
  `KERNELONE_INSTANCE_WORKSPACE` must never be switched to the project
  workspace; a mismatch must fail closed. This mode is not safe for concurrent
  bench agents and cannot reuse the bound main backend.

```bash
python src/backend/scripts/factory_bench/run_factory_bench.py \
  --project-ids L1-04 \
  --launcher-instance-mode isolated \
  --bench-session-reporting off
```

In isolated mode, Launcher visibility comes from Instance Registry plus the
project instance's own runtime.v2 stream. Shared `/v2/factory/bench/sessions`
POSTs are an internal compatibility observation bridge only; enable them with
`--bench-session-reporting shared` only for explicit serial debugging.

Every registry write emits a best-effort runtime.v2 event:

```text
channel: status.instances
subject: hp.runtime.instances.status.instances
payload: instance summary with token redacted
```

The Launcher subscribes to `status.instances` and refreshes the registry view
when an update arrives. This keeps discovery on the existing runtime WebSocket
rail instead of adding HTTP polling or a second realtime mechanism.

A backend instance watchdog refreshes registry process-state projections and
publishes `status.instances` when a registered backend/frontend process changes
state outside an explicit start/stop/restart/delete action. This is a server-side
registry monitor; Launcher must still consume changes through runtime.v2
WebSocket events rather than timer-driven HTTP refreshes.

The instance that currently serves the Launcher API is not allowed to stop,
restart, or delete itself through `/v2/instances/{id}`. Self-management must
fail closed; otherwise the control plane can terminate before it records the
new state or starts the replacement process. Operators should manage the
current `main` backend from the shell/process supervisor, while Launcher manages
other project instances.

Restarting a non-current instance is ordered:

1. Terminate the old frontend/backend process group.
2. Wait until the owned backend/frontend ports are actually free.
3. Start the replacement processes with the same instance binding.

If an owned port does not become free, restart must fail closed instead of
silently choosing a different port or returning success. Registry records that
only observe a shared backend and do not own a process pid must not wait for the
shared main port to become free.

Automatic port allocation must avoid every port already declared by another
registry record, including stopped internal test records. Operators should
delete stale internal records before intentionally reusing their ports. After a
backend process starts, Launcher must verify identity through
`/v2/runtime/process-identity` using that instance's token; `/health` alone is
not enough because it can accidentally hit an older backend still bound to the
same port.

All child-instance identity and health probes are local control-plane traffic
and must explicitly bypass process-wide HTTP/HTTPS proxies. Do not rely on
environment entries such as `NO_PROXY=127.*`: Python `urllib` does not treat
that form as a loopback wildcard. A proxied identity probe can produce a
dangerous false timeout: the child backend is healthy and answers direct HTTP,
but Launcher never contacts it and then terminates it as a failed start. The
required implementation is a proxy-free opener (for example
`ProxyHandler({})`) at the probe boundary. Regression tests must force a dead
`HTTP_PROXY`, clear `NO_PROXY`, and still prove that the actual local identity
endpoint receives the authenticated request.

When diagnosing `backend identity check timed out`, compare both sides before
increasing any timeout:

1. Probe `/v2/runtime/process-identity` directly with the instance token.
2. Check the child backend access log for the Supervisor's probe.
3. Inspect `urllib.request.proxy_bypass("127.0.0.1")` in the controlling
   process environment.
4. Treat "direct probe succeeds but no Supervisor request is logged" as a
   control-plane transport defect, not slow child startup.

## Bench Boundary

`factory_bench`, L1-L12 catalogs, and benchmark harnesses are internal
development and stress-test tools only. They may create or observe instances
with `kind=bench_project`, but Bench is not a production workspace concept and
must not become a formal user-facing fact source.

Formal project workspaces must depend on platform-level infrastructure:

- Instance registry
- Runtime WebSocket
- Run Ledger
- ContextOS
- ReceiptStore
- Verifier policy

Run Ledger projections are platform facts, not Bench facts. Consumers must keep
missing evidence and failed evidence separate:

- `missing_required_modalities` means the control plane did not record a
  required evidence modality, such as a missing command receipt.
- `failed_required_modalities` means the evidence exists and the verifier/gate
  failed, such as a non-zero test command, failed browser smoke, or failed user
  script.

UI, QA, ContextOS, and internal stress tools must not render a failed verifier
as "missing evidence". Missing evidence is a ledger/tooling gap; failed evidence
is a product or validation failure.

Each opened instance page must bind API calls and runtime WebSocket subscriptions
to that instance's backend/workspace. ContextOS full-context links may only be
created from `context_snapshot_ref` values that are 24-character hexadecimal
snapshot keys readable through `/v2/context/{hash}` on the bound backend.
`request_hash`, `prompt_hash`, call ids, turn ids, file paths, and legacy event
strings are audit metadata only; they must not be rendered as "view full
context" links.

They must not depend on Bench sessions, Bench labels, or factory_bench audit
files.

Stopped internal bench records may be cleaned from Launcher only when they are
clearly internal test records: `kind=bench_project`, not running, backend dead,
and `metadata.internal_test_only=true`. Running instances and formal project
records must remain visible until explicitly stopped and removed by the user.

## Development Modes

Backend instances can run with `--reload`, allowing changes to Polaris Python
code to restart the instance process automatically. Frontend instances can run
through Vite, allowing React/TypeScript changes to hot-refresh project pages.

The recommended multi-agent / factory_bench observation mode is:

```text
shared polaris_root
isolated workspace per instance
isolated runtime_root per instance
backend --reload disabled by default
frontend Vite enabled
```

This supports several agents repairing the Polaris source tree while multiple
L1-L12 projects remain observable through their own pages.

Enable backend `--reload` only for a single developer's focused backend
debugging session. In shared multi-agent pressure testing it can create reload
storms: unrelated edits under `src/backend` restart the main backend while
operators are observing Launcher, ContextOS, and runtime WebSocket streams.

## Startup Workspace Authority

`KERNELONE_INSTANCE_WORKSPACE` is the immutable workspace authority for a
backend process. Runtime HTTP guards are insufficient by themselves: a stale
settings object can otherwise bind resident services and projection owners to
another workspace before the first request arrives.

`create_app()` must therefore pin settings to the process-bound workspace
before initializing any resident service. A healthy instance requires all of
these values to agree:

- backend CLI `--workspace`
- Instance Registry `workspace`
- `/v2/settings.workspace`
- `/v2/runtime/fingerprint.workspace`
- `/v2/runtime/fingerprint.active_workspace`
- `workspace_binding_match=true`

Any mismatch is a P0 authority defect even when both ports and `/live` return
success.
