# Cognitive Layer Convergence — 2026-11-18

Status: In progress
Scope: context plane (context.engine/context.catalog), knowledge pipeline
(knowledge_distiller), ContextOS (kernelone/context), audit causal chain.

## Architecture truth (graph-verified)

- context_plane subgraph: api_gateway/roles.runtime -> context.engine ->
  context.catalog -> workspace.integrity; roles.kernel -> kernelone.core
  (StateFirstContextOS); roles.kernel -> factory.cognitive_runtime (receipts).
- knowledge_pipeline subgraph: roles.session -> cognitive.knowledge_distiller
  -> context.catalog; distiller emits KnowledgeDistilledEventV1.
- Wiring evidence: `session_orchestrator.py:864` bootstraps each session with
  `RetrieveKnowledgeQueryV1`; aggregate_chat `_packs.py` consumes
  build_context_window + search_gateway + distiller; ContextOS projections
  flow through factory.cognitive_runtime for cross-role receipts.

## Defects found and fixed (all committed, suites green)

1. **context.catalog query-writability leak** — pure path resolution
   (`resolve_context_catalog_cache_path/index_state_path`) probed workspace
   writability, violating 查询不偷写; failed on any non-writable workspace.
   Fix: `read_only=True`; KFS write path keeps its own validation.
   Evidence: context suites 318 passed.
2. **ProjectionEngine adaptive-state leak** — module-global
   `_ADAPTIVE_STATE_STORE` shared across instances made
   `test_sort_events_route_priority` order-dependent (full kernelone/context
   run failed, isolation passed). Fix: autouse reset fixture per test.
   Evidence: kernelone/context 1690 passed.
3. **Audit causal attribution M03/M08 inversion** — the exact-run causal
   report extracted ledger tool-failure classes but never passed them into
   `attribute_residual`, so a TOOL_RESULT_FAILED residual attributed to M08
   (ledger lifecycle symptom) instead of effect-path M03 (tools/DEO).
   Fix: include failure_classes in attribution inputs (M03 precedes M08 on
   the cascade ladder). Evidence: audit suite 284 passed.
4. **Stale cognitive_runtime handoff test** — set
   KERNELONE_RUNTIME_CACHE_ROOT which writers reject since 9b0ac540e
   (runtime 单根制, rule 28); failing since Oct 22. Fix: two tmp workspaces
   already get isolated canonical roots; drop the redirect.
5. **Stale CE advisory-diagnosis test** — 2026-10-14 test asserted None for a
   guidance-only missing-recipe finding; the executor now returns a
   guidance_only/execution_authority=False advisory diagnosis (rule 26
   advisory semantics). Fix: assert the advisory shape.

## Structural gate state

- Catalog governance gate: 169 blockers are the fingerprinted 49-cell SCC
  (documented in cell-cycle-allowlist.yaml); **new_issue_count = 0** — no new
  violations; fail-on-new mode passes. Breaking the historical SCC is
  multi-wave work, out of this session's scope.
- Descriptor packs: regeneration requires a KernelOne workspace binding and
  is unchanged this session (no public contract/docstring drift).

## Remaining recorded gap (not a defect)

- context.engine cell.yaml verification.gaps: "Context assembly currently
  remains partially distributed in role adapters." The direct engineering
  loop intentionally uses deterministic role_instruction prompts (tool-loop
  mode); pack assembly serves aggregate_chat and pack consumers. Converging
  the direct loop onto pack assembly is a design decision, not a bug; left as
  the recorded known-gap per Truthful Migration.

## Verification commands

- pytest polaris/cells/context polaris/tests/unit/cells/test_context
- pytest polaris/kernelone/context polaris/cells/cognitive
- pytest polaris/cells/audit
- python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only

## Session 2 (2026-11-19) — additional convergence

6. **Evidence bundle HEAD sentinel** — `_get_current_commit` caught only
   (OSError, ValueError); a RuntimeError from CommandExecutionService
   propagated and broke bundle creation instead of the documented
   'sha-unknown' sentinel. Broadened to a fail-closed boundary catch.
7. **pm_planning boundary tests** — the opaque_isinstance hardening
   (3370b7d56) legitimately adopted polaris.kernelone.type_ops; four AST
   boundary tests predated it. kernelone is the shared底座; allowlists updated.
8. **Session workflow matrix** — mock kernel rejected the orchestrator's
   parent_span_id tracing kwarg (8 case failures); stagnation circuit-breaker
   case drifted from Phase 5.1 recovery (extra recovery turns); TurnId mypy
   strict. Added expected_kernel_calls to pin kernel-call counts independently
   of scripted specs. Matrix now 18/18.

## Full-suite snapshot (10h run, pre-session-2-fixes baseline)

48292 passed / 958 failed / 678 errors. Failure mass is NOT in the cognitive
layer: 887 of 958 are in polaris/tests/ (legacy root tests + NATS-dependent
fixtures + retired-factory coverage); 57 in cells/ (34 after re-run on
current HEAD; all pre-existing, none overlapping this session's changed
files). Remaining cells/ clusters needing individual root-cause work:
director adapter failure-closure semantics (7), verifier command profile
classification, kernel run-ledger scenarios, instances port allocation (env).

## Cognitive layer verification (current HEAD, this session)

context + knowledge + ContextOS + audit + eval-matrix suites:
2318 passed, 0 failed. Catalog governance gate: new_issue_count=0.

## Session 3 (2026-11-19) — director adapter cluster triage

Deep-traced the 7 test_director_adapter_failure_closure_b failures (all
3+ months stale since the 2026-08-12 authority hardening bbf9bb83e, zero
overlap with this session's changed files). Findings:

- The preflight gate IS entered (can_accept=True, quality clean) for the
  read-only-mutation-guard test, so the adapter preflight-completes
  (success=True) instead of rejecting after the provider's contract-violation
  failure. The receipt builder (build_current_task_project_artifact_receipt_evidence)
  and _can_accept_existing_workspace_scope are NOT on the deciding path for
  this flow, ruling out the vacuous-receipt hypothesis.
- The deciding path is the adapter's existing-scope fallback: a provider
  contract-violation on a requires_fresh task whose declared file exists on
  disk completes as "existing scope accepted" rather than fail-closed.
- Two vacuous-success probes were reverted (no behavioral change; tree clean).
- Verified no regression: closure_a + closure_b + build_evidence together
  120 passed / 7 pre-existing failed.

Root cause FOUND and FIXED (a2997fa4c): `_task_completion_projection`
returns `{}` (not None) for absent projections, so the vacuous-success guard
`is not None` was always True — minting ok=True receipt evidence for tasks
with NO projection. Any requires_fresh task whose declared files merely
existed on disk then preflight-completed without delivery proof, bypassing
provider guard violations. Fixed with `bool(...)`. Four closure_b tests now
reject correctly; adapters suite 2310 passed with no regressions.

Remaining 3 closure_b failures need the full settlement authority fixture
(task completion projection + JobToken + execution-broker sealed receipts +
claim evidence) — a per-test fixture build against the claim chain, recorded
as the next item. The other clusters (materialization rust bin-paths, quality
gate guard, r31 html revision, kernel run-ledger, instances ports) remain
pre-existing and individually triaged.
