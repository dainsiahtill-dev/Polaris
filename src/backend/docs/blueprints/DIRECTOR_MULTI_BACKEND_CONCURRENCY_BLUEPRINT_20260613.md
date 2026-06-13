# Director Multi-Backend Concurrency — 20260613

## Result
Let a role (primarily Director) run on **multiple LLM backends in parallel** — e.g.
two qwen3.6-27b instances (localhost + LAN machine) — with a configurable
**concurrency** count. The market's per-step leasing is already concurrency-safe,
so the bottleneck is purely that `mainline-full` runs a single-threaded inline
poll loop and a role binds to exactly one provider/`base_url`. This unlocks the
unrealized "worker-pool concurrency" the I3 reports flagged, cutting wall-clock
roughly proportional to the number of independent steps × backends.

Delivery: **Phase 1 backend** (this blueprint), **Phase 2 UI** (follow-up).

## Decisions (user, 2026-06-13)
- Backend first, UI after.
- **Pool of providers per role**: each qwen instance is its own provider entry
  (own `base_url`); a role references a LIST of provider_ids + a concurrency int.
  Reuses the existing provider abstraction; mixes local/LAN/other backends.
- LAN endpoint is reachable; live 2-backend test (r17) once its URL is provided.

## Current resolution (mapped)
- `~/.polaris/config/llm/llm_config.json`: `roles.director = {provider_id, model}`
  → `providers[provider_id] = {base_url, type, model, ...}`.
- `runtime_config.get_role_model(role)` → `(provider_id, model)` is the SINGLE
  chokepoint; `engine/_executor_base.py:_resolve_provider_and_model` uses an
  explicit provider_id if present, else falls back to `get_role_model(role)`.
- Dispatch `mainline-full`: `dispatch_pipeline._run_inline_task_market_consumers`
  calls `director_consumer.poll_once()` once per cycle (single thread).
- Leasing (`task_market` `claim_work_item` + `LeaseManager`): unique uuid
  `lease_token`, atomic workspace-locked claim, visibility timeout → **already
  safe for N concurrent claimers** (each gets a different leaf step).

## Design (Phase 1)

### 1. Config schema (additive, backward-compatible)
```jsonc
"roles": {
  "director": {
    "provider_id": "openai_compat-localhost",     // kept = primary / fallback
    "model": "qwen3.6-27b-int4",
    "provider_pool": ["openai_compat-localhost",   // NEW: endpoints to spread over
                      "openai_compat-lan"],
    "concurrency": 2                                // NEW: parallel Director workers
  }
}
```
Absent `provider_pool` → `[provider_id]`; absent `concurrency` → 1. Pure
superset of today's behaviour.

### 2. runtime_config
- `RoleModelConfig` gains `provider_pool: tuple[str, ...]` and `concurrency: int`
  (defaults `(provider_id,)` / `1`).
- `get_role_config` parses both (validates pool entries are known provider ids
  at call sites; unknown ids are dropped with a warning, never crash).
- **Thread-local provider override** (the per-worker binding):
  `set_role_provider_override(role, provider_id)` /
  `clear_role_provider_override(role)` store a `threading.local` map;
  `get_role_model(role)` returns the override provider_id (with the role's
  configured model) when one is set for the current thread. One injection point
  covers every downstream resolver for free.

### 3. Director concurrency driver
A small helper `run_director_workers(consumer_factory, *, pool, concurrency)`:
spawn `concurrency` threads; worker *i* binds `pool[i % len(pool)]` via the
thread-local override, builds a Director consumer with a distinct
`worker_id=director-{i}`, runs its bounded `poll_once()` claim→execute loop,
then clears its override. Join all. Wired into
`_run_inline_task_market_consumers` in place of the single
`director_consumer.poll_once()`; falls back to the existing single call when
`concurrency <= 1` (zero behaviour change for current configs).

Dependency safety: `_exec_claim_ready` already blocks a step until its
`depends_on` leave the exec queue, so workers never violate the DAG; parallelism
is realised across INDEPENDENT steps/parents — which is exactly why the r15
DAG-minimization fix compounds this (flatter DAG → more parallel-eligible steps).

### 4. Health / observability
- A one-line health probe of each pool provider before the run
  (reuse the existing provider health action) so a dead LAN endpoint is reported,
  not silently retried-to-death; unhealthy endpoints are dropped from the pool
  with a logged warning (run continues on the survivors).
- Each worker stamps its `worker_id` + bound provider into the existing receipt
  metadata, so forensics can attribute steps to backends.

## Data flow
```
dispatch (mainline-full)
  └─ run_director_workers(pool=[locA, lanB], concurrency=2)
       ├─ thread director-0: set_override(director→locA); poll_once() → claims S?
       └─ thread director-1: set_override(director→lanB); poll_once() → claims S?
            each LLM call → get_role_model("director") → thread override pid
              → provider base_url (localhost:8189 | 192.168.x.x:8189)
   (leasing guarantees distinct steps; _exec_claim_ready guarantees DAG order)
```

## Risks & boundaries
- Thread-local override must be SET inside each worker thread (it is) — a
  thread-local set on the parent would not propagate. Cleared in `finally`.
- Config caching: `RuntimeConfigManager` caches by mtime; the override path must
  not be cached (it reads thread-local each call). Verified: override is checked
  before/around the cached lookup, not stored in the cache.
- Visibility timeout must exceed slowest backend's turn latency (LAN + 27B);
  keep `KERNELONE_TASK_MARKET_EXEC_VISIBILITY_TIMEOUT_SECONDS` ≥ 1800.
- Workspace-lock contention rises with N workers; claims are short critical
  sections (lease grant only), execution is outside the lock — acceptable at
  N≈2–4. Not for N in the dozens.
- Scope: role-generic by construction, validated on Director. PM/CE/QA (cloud
  MiniMax) can also use a pool later, but their bottleneck is not local compute.

## Verification
- Unit: provider_pool/concurrency parsing + defaults; thread-local override
  isolates per-thread; `get_role_model` honors override.
- Unit: `run_director_workers` spawns N, round-robins the pool, each worker's
  resolved provider matches its binding (no real LLM needed — assert resolution).
- Integration: 2 workers over 2 fake providers claim distinct steps concurrently.
- Live: r17 on L2-12 with localhost+LAN pool, concurrency 2 — wall-clock vs r16,
  receipts attribute steps to both backends.
- ruff + mypy + pytest green; fail-closed.

## Phase 2 (follow-up, not this pass)
Extend `LLMVisualEditor` so a role node accepts multiple provider→model edges
(the pool) + a concurrency field; `POST /llm/config` already persists arbitrary
role keys, so mostly a frontend + schema-typing change.
