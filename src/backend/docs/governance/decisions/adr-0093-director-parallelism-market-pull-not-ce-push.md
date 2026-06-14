# ADR-0093: Director Parallelism via Market-Pull Worker Pool, NOT CE-Direct Push

Status: Accepted
Date: 2026-06-14

## Context

ADR-0088 established `runtime.task_market` as the single broker for PM→ChiefEngineer→Director
routing: CE claims `pending_design`, fissions, and advances leaf steps to `pending_exec`;
Director workers claim `pending_exec`. PM-direct and CE-mediated routes coexist without
competing state owners because the market is a **staged pipeline**, not a flat pool.

Adding multiple Director backends (for throughput) surfaced an architectural fork, because
two mechanisms for Director parallelism exist in the codebase:

1. **Market-pull worker pool** (live): `dispatch_pipeline._build_director_worker_pool` spawns
   N `DirectorExecutionConsumer` workers that each PULL distinct `pending_exec` leaf steps from
   the market; market leasing guarantees distinct claims, `_exec_claim_ready` enforces
   `depends_on` DAG order. This is what the live mainline uses.
2. **CE-direct push** (`chief_engineer.blueprint.internal.director_pool.DirectorPool`): the CE
   directly assigns tasks to managed Directors, with conflict-free selection and explicit
   reassignment. This class exists but is **not instantiated in any live, non-test path** —
   the CE consumer explicitly records `director_pool_assignment = "deferred_to_task_market"`
   (`ce_consumer.py`), i.e. assignment is delegated to the market.

The risk: a future contributor, reading `DirectorPool`, re-introduces CE-direct assignment,
re-centralizing scheduling into the CE and bypassing the market.

## Decision

**Director parallelism is realized via the market-pull worker pool. The CE coordinates
Directors DECLARATIVELY — by publishing `pending_exec` leaf steps + `depends_on` to the market
— and the market's leasing performs the imperative scheduling, parallelism, and failure
recovery. CE-direct task assignment (DirectorPool push) MUST NOT be wired into the live market
mainline.**

Concretely:

- N Director worker-consumers each poll `pending_exec` and PULL distinct leaf steps (lease +
  visibility-timeout = at-least-once with lease-token dedup). Each worker binds one backend
  endpoint from the role `provider_pool` (`set_role_provider_override`).
- The worker pool is **resilient**: at spawn it health-checks each pool endpoint
  (`_reachable_provider_pool`), skips offline backends, and round-robins the requested
  parallelism over the LIVE backends only — a single offline endpoint never strands a worker
  or the run; if none are reachable it falls back to the single inline consumer.
- Same-file write safety is enforced declaratively by the market: `_exec_claim_ready` requires
  a same-`target_file` dependency to be fully `resolved` (not merely at QA) before a dependent
  is claimable, and the file-ownership ledger serializes cross-parent writers via injected
  `depends_on` — so two Directors can never concurrently write the same file.

## Rationale

1. **Pull > push for heterogeneous backends.** Backends differ in speed (local int4 vs remote
   GPUs). A free worker grabs the next ready step → automatic load-balancing. Push would force
   the CE to model each backend's speed.
2. **Lease/timeout failure handling (crash-only) > explicit reassignment.** A dead worker's
   lease expires and another re-claims — no central failure detection, no double-execution /
   split-brain risk. "Skip an offline backend and keep running" is a property the pull model
   gives for free.
3. **Separation of concerns.** CE's job is PLANNING (fission: steps + `depends_on` + interface
   contracts). SCHEDULING (who runs which step when) belongs to the market's leasing. Don't
   conflate them.
4. **Decentralized coordination (stigmergy/blackboard) > central orchestrator.** No single
   point of failure / bottleneck. The market is the shared coordination substrate.
5. **No new state owner.** This is a direct extension of ADR-0088's "market as single broker";
   re-introducing CE-direct assignment would create a second scheduling authority.

## Consequences

- `DirectorPool` is retained ONLY as a legacy / non-market utility (e.g. dashboards, conflict
  views). It MUST NOT assign or launch Directors in a way that bypasses the market. Any "CE
  manages parallel Directors" requirement is satisfied by publishing `pending_exec` leaf steps
  + `depends_on` (declarative), never by direct RPC assignment.
- Director throughput scales by adding backends to the role `provider_pool` + raising
  `concurrency`; the resilient pool auto-adjusts to whatever is reachable.
- Known follow-ups for `concurrency > 1` (do not regress this ADR while fixing them): F6
  (worker shared `run_id` telemetry collision) and the conc>1 interface-exploration stall.

## Verification

- `test_director_worker_pool.py`: round-robin binding + `TestResilientPool` (skip offline
  backend, auto-adjust, single-consumer fallback when none reachable).
- `test_exec_claim_ready_samefile.py`: same-file dependency requires `resolved` (no concurrent
  same-file write at conc>1).
- Live: 4-backend (local + LAN + 2 remote) parallel market run on the held-out L2 brick-breaker.
