# Per-role multi-LLM parallelism on the market

> Blueprint (§4.1). Direction set by user 2026-06-15: every role (PM / CE / Director / QA / Scout)
> will eventually run multi-way parallel across heterogeneous LLMs (cloud / weak / local / LAN),
> and ALL of it must run on the REAL Polaris execution chain — no bypass.
> Status: Director stage is the live prototype; this blueprint generalizes it to every role-stage.

## 1. 真实链路已确认 (Real path — verified, MUST stay)

The factory-bench driver (`scripts/factory_bench/run_market_chain.py`) calls the production
`run_dispatch_pipeline(run_director=True, integration_qa=True)` with `KERNELONE_TASK_MARKET_MODE=mainline-full`
— the SAME entry the PM CLI uses, not a `_complete_for_role` bypass. Each stage executes through its real
role kernel: `DirectorExecutionConsumer._execute_task` → `create_role_adapter("director", workspace)` →
RoleExecutionKernel (TurnEngine) → `LLMInvoker.call` → provider (override-routed). The only bypass hook is
`consumer._task_executor` (= `None` in production, test-injection only). **Invariant: the parallelism feature
and its tests must always exercise this chain.**

## 2. 为什么"市场"天然支持每角色多路并行 (Why the market already supports it)

Every layer is already keyed by `role`, not by "director":

- **Config** — `get_role_provider_pool(role_id)`, `get_role_concurrency(role_id)`,
  `set_role_provider_override(role_id, provider_id)` / `get_role_provider_override(role_id)`.
- **Execution** — `create_role_adapter(role_id, workspace)` builds any role's real kernel.
- **Market** — each stage (`pending_design` / `blueprint` / `pending_exec` / `pending_qa`) is drained by a
  consumer whose `poll_once` → `claim_work_item(stage, worker_id, visibility_timeout)`. The lease/visibility
  mechanism guarantees N concurrent workers on ONE stage claim **distinct** items and DAG `depends_on` order is
  respected (`_exec_claim_ready`). This is the SAME guarantee that makes the Director pool correct — it is not
  Director-specific.
- **Resilience** — F15 (worker self-retirement + stall watchdog), fairness yield (non-resolving claim yields to
  idle siblings), and dynamic per-cycle reachability load-balancing all operate on `(consumer, provider_id)`
  pairs + a `role` override. Nothing in them is Director-special.

**Conclusion:** per-role multi-LLM parallelism is a *parameterization*, not a new mechanism.

## 3. 唯一的"Director 特化"点 (The only Director-special code today)

`polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py`:
- `_build_director_worker_pool(...)` hardcodes `get_role_concurrency("director")`, `get_role_provider_pool("director")`,
  the `DirectorExecutionConsumer` constructor kwargs, and worker-id prefix.
- `_drive_director_workers(...)` hardcodes `set_role_provider_override("director", …)` / `clear_role_provider_override("director")`.
- The cycle loop pools ONLY the Director stage; CE and QA use a single `poll_once()` per cycle.

## 4. 泛化设计 (Generalization design)

### 4.1 Engine — `_drive_role_workers(role_id, workers, …)`
Add a `role_id` param (the driver is already consumer-agnostic — it only needs `poll_once`). Replace the two
hardcoded `"director"` override calls with `role_id`. Behaviour-preserving for Director (`role_id="director"`).
F15/fairness/watchdog are untouched and now apply to any role.

### 4.2 Builder — `_build_role_worker_pool(role_id, consumer_factory, …)`
Generalize by injecting a `consumer_factory(worker_id) -> consumer` (role constructors differ: CE needs
`analysis_runner`, QA differs). Internals become `get_role_concurrency(role_id)` / `get_role_provider_pool(role_id)`
/ `_reachable_provider_pool(...)` (already concurrent) / worker-id prefix from `role_id`.

### 4.3 Dispatch loop — drive a pool per role-stage
For each role-stage with `concurrency>1` AND a multi-endpoint `provider_pool`, build+drive a pool that cycle;
otherwise fall back to the single inline consumer (current behaviour). Order across stages stays CE → Director → QA
(or evolve to concurrent stage draining — separate optimization, see §6).

### 4.4 Config — per-role heterogeneous pools
Today only `roles.director` has a 4-endpoint pool; PM/CE/QA point at single MiniMax. Generalization is config-only:
give any role a `provider_pool` + `concurrency`. Heterogeneous is fine — the pool can mix cloud (MiniMax), LAN, local,
and weak endpoints; `resolve_provider_model` already returns each bound provider's OWN model (RC3 fix), so a
heterogeneous pool routes correctly per backend.

## 5. 正确性 (Correctness — carried over for free)

- **Distinct claims**: market lease/visibility per stage (unchanged).
- **DAG order**: `depends_on` + file-ownership ledger (unchanged; role-agnostic).
- **Heterogeneous model names**: per-bound-provider model resolution (RC3).
- **Mid-run backend death / recovery**: F15 retirement + per-cycle reachability rebuild (role-agnostic).
- **Fairness**: non-resolving claim yields to idle siblings on other backends (role-agnostic).

## 6. 边界与后续 (Boundaries / follow-ups)

- **Cross-stage idle** (CE/QA run while Director idles, and vice-versa) is a SEPARATE optimization — make stages
  concurrent continuous drainers instead of a sequential per-cycle barrier. Bigger change; own blueprint.
- **MiniMax (cloud) rate limits**: a CE/QA pool over cloud endpoints must respect provider rate limits; pool size
  for cloud roles should be tuned (or mix in local/LAN endpoints) so concurrency doesn't trip 429s.
- **Per-role saturation**: like Director, a role's effective parallelism is capped by independent ready items in its
  stage; cross-project batching (see [[qwen-backend-saturation]]) remains the throughput lever.
- Do NOT wire all roles speculatively — roll out per-role behind the config (`concurrency>1` + `provider_pool`),
  one role at a time, each validated on the real chain with forensics.

## 7. 验证 (Verification plan)

Per role rolled out: unit tests mirroring `test_director_worker_pool.py` (round-robin binding, continuous drain,
F15 retire, stall watchdog, fairness pacing, dynamic reachability) but parameterized by `role_id`; then a live
market run on the real chain with `KERNELONE_DIRECTOR_POOL_TRACE`-equivalent tracing, audited via `market_forensics.py`.
Gate: ruff + mypy + pytest (fail-closed).
