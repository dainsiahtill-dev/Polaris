# Startup readiness and failure evidence implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans.

**Goal:** Remove false startup/readiness and failed-launch reporting behavior
without changing the full fresh-isolated unattended L1–L12 success standard.

**Architecture:** Existing NATS adapter consumes a bounded startup policy and
proves protocol readiness. Existing FactStream maintenance owns prelaunch
storage enrollment; no-attempt errors stay nonterminal and never run verifiers.

**Tech Stack:** Python 3.12, asyncio, Pydantic, native NATS, KFS, pytest.

**Spec:** `src/backend/docs/blueprints/POLARIS_STARTUP_READINESS_AND_FAILURE_EVIDENCE_20261003.md`

## Global constraints

- Default startup budget 60s, finite, positive and at most 90s; no changed
  Factory 5400s / outer 6000s budgets or instance identity window.
- No generated target edits, branch, commit, global NATS cleanup or model swap.
- RTK shells, CodeGraph first, explicit UTF-8, preserve unrelated dirty work.
- Truthful negative evidence; no startup maintenance counted as runtime success.
- Existing role identity, final-request, scope, receipt and verifier gates stay.

## Review focus

- A living child must not count as a ready service.
- Non-NATS/TLS/auth-required services must not be terminated or replaced.
- Cancellation/early exit must drain owned startup processes without duplicates.
- Settings/environment must not silently drop the explicit startup budget.
- A never-started Factory must neither run physical verifiers nor fabricate
  successful ledger receipts; failure evidence remains durable and readable.

## Task 1: Bounded native readiness

Files: NATS `server_runtime.py`; `config/nats_config.py`; bootstrap
`config.py`/`config_loader.py`; KernelOne `config/flag_registry.py`; delivery
`http/app_factory.py`; tests `test_nats_server_runtime.py` and native lifespan
tests. Do not alter other existing dirty hunks.

Interfaces: `ensure_local_nats_runtime(url: str, *, startup_timeout_seconds:
float = 60.0) -> None`; existing shutdown contract unchanged.

- [ ] Write behavior tests first. Example:
  ```python
  with pytest.raises(RuntimeError, match="startup_deadline_exceeded"):
      await server.ensure_running(startup_timeout_seconds=0.1)
  assert server.process is None
  ```
  Exercise real temporary TCP/NATS protocol endpoints and owned subprocesses.
- [ ] Run tests RED; record which failures prove missing behavior.
- [ ] Add explicit Pydantic startup field and environment mappings; enforce
  finite positive input. Native lifespan passes that budget to the adapter.
- [ ] Replace child-exists shortcut with protocol/liveness readiness under one
  monotonic deadline; do not await a dead child until budget expiry. Handle
  cancellation with owned-child stop/drain and close log handles.
- [ ] Run new tests GREEN plus configuration/lifespan/NATS client regressions,
  Ruff/Mypy. Record results; no commit per user instruction.

## Task 2: Truthful failed-launch evidence

Files: internal harness `_bench_lib/cli.py`/`artifacts.py`; existing runner
component tests or one new component file. No target-project code, thresholds,
catalog, business scaffold or verification commands change.

Interfaces: public `BootstrapFactStreamWorkspaceCommandV1`,
`bootstrap_fact_stream_workspace`, `fact_stream_bootstrap_streams`; existing
`_chain_reached_terminal(chain) -> bool`.

- [ ] Add failing real-storage test for failed isolated launch. Assert
  `chain_attempt_started=false`, `chain_terminal=false`, no physical gates,
  preserved original launch error and durable negative ledger evidence.
- [ ] Run RED. Add the same failure categories to the authoritative classifier
  and invoke public idempotent bootstrap before any isolated instance launch.
  ```python
  bootstrap_fact_stream_workspace(BootstrapFactStreamWorkspaceCommandV1(
      workspace=str(workspace), streams=fact_stream_bootstrap_streams(),
      maintenance_reason="internal_harness_workspace_startup",
  ))
  ```
- [ ] Run GREEN and runner component suites. Check actual JSONL/NDJSON contents:
  maintenance contributes no successful run facts/receipts; startup failure
  stays negative. Do not add auto-enrollment to append/query.

## Task 3: Acceptance and next fresh probe

- [ ] Run combined relevant suites, lint/type gates and current KernelOne
  release gate. Record source hashes and exact exit codes.
- [ ] Fresh-context independent review of this task-only diff; fix important
  findings with RED/GREEN tests, then rerun affected gates.
- [ ] Update master progress, machine-readable defect record and memory note.
- [ ] With r08 terminal and current gates green, launch fresh r09 isolated
  L1-01 at full budgets, MiniMax roles unchanged; preserve exact evidence and
  current handles. No success claim before actual COMPLETED_VERIFIED.

## Self-review and execution ruling

Tasks share only readiness/reporting prerequisites, not role authority. Main
implements sequentially; prior independent audit is read-only input. User
approved direct implementation/acceptance under the master plan, disallowed
branches and repeated artifact approvals. Native task ledger is used; no
destructive cleanup or automatic commit.
