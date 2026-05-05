# Principal Core Audit Batch Blueprint - 2026-05-06

Status: active
Scope: Polaris core reliability audit and minimal hardening

## 1. Current Understanding

Polaris is an ACGA/Cell-governed meta-tool platform. The current backend code
is rooted under `src/backend/polaris`, with graph truth under
`src/backend/docs/graph`. The active task is not a feature request. It is a
production-style reliability audit across core code paths, followed by minimal
high-value fixes only when evidence is sufficient.

Primary core paths in this audit:

- `bootstrap`: startup assembly, DI, runtime bindings, lifecycle.
- `delivery`: HTTP, WebSocket, CLI transport and protocol translation.
- `cells`: capability owners and state/project workflows.
- `kernelone`: technical substrate, storage, events, audit, transactions.
- `infrastructure`: external adapters for messaging, logs, LLM, subprocesses.
- `frontend/electron`: runtime stream consumers, desktop process bridge.

## 2. Evidence Register

Read before this blueprint:

- `src/backend/AGENTS.md`
- `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`
- `src/backend/docs/FINAL_SPEC.md`
- `src/backend/docs/graph/catalog/cells.yaml`
- `src/backend/docs/graph/subgraphs/*` directory listing
- Repository root and `src/backend/polaris` structure
- Current `git status --short`

Confirmed facts:

- The graph catalog lives under `src/backend/docs/graph/catalog/cells.yaml`,
  not root-level `docs/graph/catalog/cells.yaml`.
- The working tree is already dirty across backend, frontend, Electron, and
  tests. This audit must not revert unrelated edits.
- A direct full-repo rewrite is inappropriate. The safe path is parallel
  read-only auditing followed by narrow fixes with regression tests.
- Tool-level active subagent concurrency is limited. At least 20 subagent
  audits will run in batches.

Open assumptions to verify:

- Whether recent runtime WebSocket fixes introduced any ordering, duplicate, or
  snapshot regression outside the covered tests.
- Whether path resolution is now consistent across every canonical process
  boundary, including scripts and legacy helpers.
- Whether audit/log/event writes are consistently best-effort or mandatory
  according to their owning Cell contract.
- Whether transaction and Director state machines preserve idempotency under
  retry, cancellation, and partial write failures.

## 3. Audit Slices

The audit is split into at least 20 read-only subagent scopes:

1. Bootstrap and FastAPI lifespan.
2. HTTP routers, auth, settings, workspace APIs.
3. Runtime WebSocket delivery.
4. NATS and JetStream messaging.
5. Log pipeline and canonical/legacy event writes.
6. KernelOne storage and KFS.
7. Storage layout Cell and settings helpers.
8. Audit runtime and diagnosis Cell.
9. Message bus and realtime signal fanout.
10. Transaction kernel and rollback/retry guards.
11. Director planning/tasking/execution.
12. PM planning and Director handoff.
13. LLM control plane and provider adapters.
14. Frontend runtime hooks and transport.
15. Electron backend process/config scripts.
16. Pytest/conftest/import shadowing.
17. Graph and Cell boundary compliance.
18. File write reliability and UTF-8/atomicity.
19. Async lifecycle, subprocess, task cancellation.
20. Security, token auth, workspace guard, path traversal.

## 4. Risk Hypotheses

Potential high-risk failure modes to prove or reject:

- WebSocket connections can be accepted but event streams still drop messages
  due to channel, role, or protocol mismatch.
- Runtime status can be split across settings, workspace, cache, and NATS
  paths when environment variables differ between Electron, Python, and scripts.
- Audit/log writes can unintentionally become hard dependencies and break user
  flows when validation or filesystem writes fail.
- Process-local fanout and watcher-based fallback can race on event-loop close
  or workspace cache reuse.
- Test scaffolding can shadow production modules and hide real import failures.
- Transaction/Director flows can leave partial state after retries or timeouts.

## 5. Minimal Fix Strategy

No code will be changed until a defect has:

- a concrete file/line evidence path,
- a reproducible trigger or deterministic counterexample,
- a root cause distinct from the symptom,
- a minimal modification point,
- a regression test plan.

Priority order:

1. Blocker: core runtime crash, data loss/corruption, auth bypass, state owner
   violation, stream loss in primary UI workflow.
2. Major: stale or split runtime state, non-idempotent retry, resource leak,
   failed recovery path.
3. Minor: localized hardening with low compatibility risk.

Explicit non-goals:

- No broad refactor.
- No public contract change unless a verified contract mismatch requires it.
- No graph/doc truth rewrite unless code changes alter actual boundaries.
- No target-project/business-specific code.

## 6. Test Plan

Back-end verification candidates:

- Targeted pytest for each fixed module.
- Runtime WebSocket tests for snapshot, incremental, v2 subscribe, dialogue,
  LLM stream, and local fanout fallback.
- Storage path tests for `KERNELONE_HOME`, `KERNELONE_ROOT`, Windows AppData,
  legacy home fallback, and runtime cache isolation.
- Audit/log failure-path tests where writes are expected to be best-effort.
- Transaction/Director regression tests if state-machine issues are fixed.

Front-end/Electron verification candidates:

- Vitest for runtime hooks/store/transport.
- TypeScript typecheck.
- ESLint on frontend sources.
- Node test runner for Electron config path behavior.

Quality gates for modified Python:

- `ruff check <paths> --fix`
- `ruff format <paths>`
- `mypy <paths>`
- `pytest <tests> -q`

## 7. Rollback Plan

Rollback must be file-scoped:

- Revert only files modified in the specific fix.
- Keep unrelated dirty worktree changes intact.
- For each fix, list changed files and the tests that prove behavior.
- If a fix touches runtime/event/storage public behavior, rerun the closest
  integration tests before and after rollback.

## 8. Current Execution State

- 20 read-only subagent audit slices were dispatched and collected.
- Minimal fixes were applied only where the evidence pointed to a bounded
  runtime defect with a direct regression test:
  - WebSocket journal short-circuit prevented non-journal channel snapshots and
    incrementals from being evaluated after a journal send.
  - WebSocket client message handling crashed on non-object JSON payloads.
  - Runtime v2 WebSocket loop did not own cleanup for the active JetStream
    consumer on disconnect/error paths.
  - NATS numeric environment parsing used resolved values as variable names.
  - NATS publish self-heal deleted the runtime stream before repair.
  - Factory router was mounted through the `/v2` aggregate despite already
    carrying its own `/v2/factory` prefix.
  - `polaris.kernelone.storage.resolve_runtime_path` exported the legacy paths
    resolver instead of the safe storage layout resolver.
- Verification completed for the modified Python paths:
  - `ruff format --no-cache`
  - `ruff check --no-cache --fix`
  - `mypy --no-incremental`
  - targeted `pytest -q -p no:cacheprovider`
- Remaining subagent findings are tracked as follow-up risks rather than folded
  into this patch, because they require broader contract/graph/lifecycle design
  work and should not be mixed into a WS/NATS/path hardening batch.
