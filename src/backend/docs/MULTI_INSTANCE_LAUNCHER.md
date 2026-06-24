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
- `backend_port`: local HTTP/WebSocket endpoint.
- `frontend_port`: optional Vite frontend for that instance.
- `token`: local auth token for frontend/backend binding.
- `polaris_root`: shared Polaris source tree. In development all instances may
  point at the same `polaris_root`, so code edits and Vite/HMR/backend reloads
  become visible across all running instances.

The Launcher is an operator surface. It is not a runtime fact source for PM,
Chief Engineer, Director, QA, ContextOS, ReceiptStore, or Run Ledger.

## Workspace Binding

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
- `observed`: register a `bench_project` record pointing at the shared backend
  after switching that backend to the project workspace. This exists only for
  explicit lightweight observation and backwards-compatible serial testing. It
  is not safe for multiple concurrent bench agents sharing one backend.

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

They must not depend on Bench sessions, Bench labels, or factory_bench audit
files.

## Development Modes

Backend instances can run with `--reload`, allowing changes to Polaris Python
code to restart the instance process automatically. Frontend instances can run
through Vite, allowing React/TypeScript changes to hot-refresh project pages.

The recommended development mode is:

```text
shared polaris_root
isolated workspace per instance
isolated runtime_root per instance
backend --reload enabled
frontend Vite enabled
```

This supports several agents repairing the Polaris source tree while multiple
L1-L12 projects remain observable through their own pages.
