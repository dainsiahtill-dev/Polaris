# Durable Project Completion Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task with a fresh implementer, specification review, and code-quality review for every bucket.

**Goal:** Make Polaris finish one project unattended with a typed definition of done, a sole owner-bound completion verdict, durable residual convergence, and receipt-backed runnable evidence.

**Architecture:** Extend existing Cells only. Chief Engineer owns the typed completion contract; Director hash-binds it into execution; VerificationGuard reports every residual; workflow_runtime persists only the convergence cursor and drives existing TaskMarket/TaskRuntime operations; runtime.projection alone emits authoritative completion.

**Tech Stack:** Python 3.12, Pydantic/dataclasses as existing contracts require, pytest, Ruff, Mypy, Polaris Cell graph/catalog/descriptor tooling, NATS/JetStream-backed runtime ports, isolated Factory Bench for final acceptance.

---

## Execution protocol

- Work only in `/tmp/polaris-unattended-completion-wt` on branch
  `codex/unattended-completion-loop-20260808`.
- Preserve unrelated main-worktree changes.
- For every production behavior: write a focused failing test, run it and record
  the expected failure, implement the minimum change, then rerun focused gates.
- After each task: specification review first, then code-quality review; fix all
  findings before the next task.
- Do not run Provider/Bench while unit or architecture gates are red.
- Do not edit generated target-project code.

## Task 1: F0 governance and contradiction characterization

**Files:**

- Add `src/backend/docs/blueprints/DURABLE_PROJECT_COMPLETION_LOOP_20260808.md`
- Add `src/backend/docs/governance/decisions/adr-0100-durable-project-completion-contract-and-convergence.md`
- Add `src/backend/docs/governance/templates/verification-cards/vc-20260808-durable-project-completion-loop.yaml`
- Add focused characterization tests beside the current owner logic.

**Required red cases:**

1. `completed_verified` TaskBoundary with failed/pending TaskRuntime remains
   blocked.
2. Existing target path without a current-run effect receipt remains unverified.
3. Build pass plus required test failure remains failed.
4. Director `ok=True` with empty effect evidence remains incomplete.
5. Unbound `ProjectOutcomeQueryV1` never becomes authoritative.

## Task 2: F1 owner-bound ProjectOutcome

**Files:**

- Modify `src/backend/polaris/cells/runtime/projection/public/contracts.py`
- Modify `src/backend/polaris/cells/runtime/projection/public/service.py`
- Add/modify `src/backend/polaris/cells/runtime/projection/internal/project_outcome_*.py`
- Modify bootstrap composition only through public ports.
- Migrate Factory/Bench completion consumers; do not add a parallel verdict.
- Update runtime.projection cell manifest/README/context pack/descriptor as
  required by graph gates.

**Steps:**

1. Add exact owner-bound query/result contracts with workspace/project/run and
   contract-hash identity.
2. Gather six axes through public owner readers; normalize deterministic refs.
3. Permit authoritative completion only when every owner and identity is bound.
4. Remove TaskBoundary-overrides-TaskRuntime and path-exists authority shortcuts.
5. Route existing canonical completion consumers through this public query.

**Focused gates:** runtime projection, Factory owner binding, TaskBoundary,
Run Ledger evidence policy, graph/fence tests.

## Task 3: F2 typed ProjectCompletionContract

**Files:**

- Modify `src/backend/polaris/cells/chief_engineer/blueprint/public/contracts.py`
- Modify CE public service/producer serialization and tests.
- Modify `src/backend/polaris/cells/director/tasking/internal/execution_contract.py`
- Modify Director public contracts/envelope and tests.
- Update relevant cell manifests/descriptors/docs.

**Steps:**

1. Define canonical artifacts, entrypoint probes, environment preparation,
   verifier modalities, test obligations and explicit N/A semantics.
2. Canonically hash the contract with its completion-predicate version.
3. Include it in CE blueprint result/provenance.
4. Compile it without loss into TaskExecutionContract and ExecutionEnvelope.
5. Fail closed before physical dispatch when a runnable app lacks a required
   entrypoint/test/verifier obligation.

**Focused proof:** a contract fixture keeps the same hash and obligations at CE,
task contract, envelope and execution receipt boundaries.

## Task 4: F3a complete VerificationGuard diagnostics

**Files:**

- Modify `src/backend/polaris/cells/factory/verification_guard/public/contracts.py`
- Modify `src/backend/polaris/cells/factory/verification_guard/public/service.py`
- Modify internal verifier evaluation and tests.

**Steps:**

1. Add `EvaluateProjectCompletionCommandV1` and deterministic
   `ProjectCompletionDiagnosticsV1`.
2. Evaluate all completion obligations; never fail fast.
3. Emit stable diagnostic ids, module owner, dependency ids, evidence refs,
   coverage and allowed next action.
4. Keep missing and failed modalities disjoint.
5. Preserve the existing single-claim API as a compatibility facade over the
   new evaluator where possible.

## Task 5: F3b durable convergence workflow

**Files:**

- Modify `src/backend/polaris/cells/orchestration/workflow_runtime/public/contracts.py`
- Modify `src/backend/polaris/cells/orchestration/workflow_runtime/public/service.py`
- Add internal completion-loop state/cursor implementation and tests.
- Modify bootstrap wiring through public ports.
- Replace Factory HTTP-router business rework loop with workflow service calls.

**Steps:**

1. Define a CAS/versioned cursor keyed by workspace/project/run/contract hash.
2. Re-read outcome and diagnostics from owners at every transition.
3. Select one dependency-ready diagnostic deterministically.
4. Publish/reopen through TaskMarket/TaskRuntime public ports.
5. Bind attempt, lease, settlement and receipt ids to the cursor.
6. On restart, skip committed effects and resume the unresolved diagnostic.
7. Enforce attempt/time/cost/no-progress budgets without extending hard role
   deadlines.
8. Terminate only as completed verified, structured model ceiling,
   control-plane blocked, or budget exhausted.

## Task 6: F4 structured model ceiling and architecture seal

**Files:**

- Replace heuristic logic in
  `src/backend/polaris/kernelone/platform_modules/residual_attribution.py` or
  migrate it behind the owning Cell public contract.
- Update cell YAML, graph catalog, generated context/descriptor packs and docs.
- Update `UNATTENDED_AUTONOMOUS_DEVELOPMENT_FOUNDATION_ROADMAP_20260802.md`.

**Steps:**

1. Add typed evidence inputs for provider request, context/tools, execution
   authority, deterministic-repair coverage and bounded same-diagnostic attempts.
2. Reject ceiling classification when any platform/provider/environment blocker
   exists.
3. Add graph tests proving no alternate completion authority and no internal
   cross-Cell imports.
4. Update VC evidence with exact gate commands/counts.

## Task 7: integration and real acceptance

1. Run focused Ruff, format check, Mypy and pytest for every touched Cell.
2. Run architecture catalog/graph/fence/descriptor gates.
3. Start one fresh isolated project through the documented factory_bench
   command, never using `49977/5173`.
4. Audit every role's final provider request snapshot.
5. Require owner-bound completion and all four runnable pillars.
6. If red, emit one machine-readable defect, close its general platform cause,
   rerun focused gates, and retry the same project.
7. After first success, run three fresh batches across at least two language
   families with no new general root cause.
8. March L1-L12 sequentially only after the seal gate is green.

## Final verification commands

Run from `src/backend` with the repository Python 3.12 interpreter:

```bash
ruff check --fix <touched paths>
ruff format <touched paths>
mypy <touched packages>
pytest -q <focused tests>
pytest -q polaris/tests/architecture/test_manifest_schema_canonical.py \
  polaris/tests/architecture/test_catalog_governance_gate.py \
  polaris/tests/architecture/test_graph_reality.py \
  polaris/tests/architecture/test_descriptor_pack_runtime_sources.py
```

Then run the fresh isolated acceptance command from root exactly as governed by
`AGENTS.md`, preserving `pipefail`, `--launcher-instance-mode isolated`, and
`--bench-session-reporting off`.
