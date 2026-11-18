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
