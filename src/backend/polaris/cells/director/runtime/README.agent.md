# Director Runtime

## Kind

`workflow`

## Purpose

Intended to own code/patch application, file application, existence gate, and the
repair loop for Director tasks, plus the KernelOne tool chain execution capability
(`polaris/kernelone/tools/`).

## Migration Status

⚠️ **PARTIAL IMPLEMENTATION — MIGRATION NOT COMPLETED** (status 2026-06-25)

`runtime/internal/repair_kernel/` is the cell-private implementation for Director
Repair Kernel contracts, diagnostic normalization, patch composition, policy
gating, transactional execution shell, repair receipts, and non-authoritative
advisory overlay model. Cross-cell callers must use `director.runtime.public`
or `director.runtime.public.service`; they must not import
`director.runtime.internal.repair_kernel`.
Public repair planning/execution is intentionally generic:
`PlanDirectorRepairCommandV1` / `RunDirectorRepairCommandV1` with
`plan_director_repair` / `run_director_repair`. Do not add
`plan_director_<language>_*`, `run_director_<language>_*`, or per-rule public
facades; language dispatch belongs behind the internal dispatcher/registry.
Current executable runtime bindings include TypeScript object-literal comma
repair, TypeScript nullable canvas/context repair, TypeScript duplicate object
property repair, TypeScript enum member separator repair, Go bare import string
repair, Java accessor alias repair, C++ quote include path repair, C++ standard
include repair, C++ missing private members repair, C++ placeholder declaration
repair, C++ struct getter field-access repair, and generic patch residue cleanup.
Future migrations must extend the internal dispatcher, registry, strategy
catalog, and contract tests first; they must not route these rules back through
legacy direct-write helpers.
Runtime execution is fail-closed: only source tools returned by
`runtime_repair_bindings()` with `implementation_status=executable_runtime` may
be invoked through `PlanDirectorRepairCommandV1` or
`RunDirectorRepairCommandV1`. Unknown, unregistered, `reserved_only`, or
catalog-only source tools must return `unsupported_repair_source_tool` as a
first-class public result `error_code`, must not write the workspace, and must
not fall back to legacy regex/direct-write helpers.

Runtime owns repair planning, composition, policy, scheduler, receipts, and
advisory validation. It does not own Director tool execution. Write effects must
enter through the public runtime command plus callables injected by
`roles.adapters`: precise `text_replace` operations should use the injected
`edit_file` callable when the expected text is uniquely and safely located,
while whole-file generation, structured file serialization, fallback, or
rollback may use the injected `write_file` callable. Do not import or call
`DirectorToolExecutor` from `director.runtime`; the Director adapter remains the
only place that translates kernel-approved operations into policy-gated tools.
This matters for large files: new rules should prefer structured operations over
full-file replacement whenever the target edit can be expressed precisely.

The first rule-discovery layer is also owned here. `repair_kernel/registry.py`
defines typed rule metadata and read-only coverage reports so diagnostics can
surface `known_rule_matched=false` as an auditable platform gap before QA timeout
or manual log inspection. Cross-cell callers must use
`query_director_repair_coverage`; coverage reporting is read-only and must not
write files or register new rules implicitly.
`query_director_repair_strategy_catalog` is also the authoritative read model for
deterministic repair migration status. Each strategy item must expose
`implementation_status`, and the summary must count and list both
`executable_runtime` and `legacy_strategy_host` source tools. Future migration
work must use this catalog to decide what remains; grep-based counts are not
authoritative evidence.

Convergence scheduling is owned here as well. `repair_kernel/schedule_catalog.py`
models plan ordering, `priority`, `depends_on`, `round_number`, `max_rounds`,
cycle detection, and post-round verifier snapshots. Post-execution repair callers
must use `run_director_post_execution_repair_schedule(..., max_rounds=3)` for
bounded multi-round repair. The scheduler injects `scheduler_round_number`,
`scheduler_rounds_run`, `convergence_status`, and
`convergence_stopped_reason`, and it breaks repeated result fingerprints.
Language-specific post repair functions must not hide their own unbounded
convergence loops; they must be migrated behind this scheduler contract.
The post-execution language repair schedule catalog is exposed through
`query_director_repair_post_execution_schedule`. `roles.adapters` may bind
runtime-declared `step_id` values to legacy runners during migration, but it
must not redefine phase, priority, or dependency metadata locally.
The materialization-quality repair path follows the same rule:
`query_director_repair_materialization_quality_schedule` and
`run_director_materialization_quality_repair_schedule` are the public schedule
boundary, while `roles.adapters` may only bind declared `step_id` values to
legacy runners.

Task-boundary validation is the preferred quality loop for completed CE tasks.
QA may observe intermediate evidence, but final task-level repair convergence
must happen after Director has written the complete task file set, not after an
individual partial file write. Cross-cell callers should use
`query_director_repair_plan_probe` to prove that coverage-matched rules can
produce concrete patches, and `run_director_task_boundary_quality_loop` for the
full `coverage -> plan_probe -> convergence -> environment_prep -> revalidation
-> receipt` loop. Coverage alone is not execution evidence:
`known_rule_matched=true` or `executable_runtime_plan_matched=true` only means a
rule family is known. If the planner produces no changed patch, the condition
must be reported as `coverage_matched_but_unplannable` with an interface
discrepancy receipt instead of pretending the task converged.
`covered_unplannable` should route to Director retry when the CE task interface
contract exists, or to CE local interface-contract revision when the contract is
missing or contradictory. It must not trigger whole-blueprint regeneration by
default.

The long-term task-boundary loop reserves three hardening planes without moving
their execution into `execute_method.py` or QA: topology-weighted convergence
scoring backed by a `SymbolIndexSnapshot`, copy-on-write workspace isolation
through OverlayFS/VFS diff logs, and diagnostic-driven hot/cold context slicing.
Until those planes are implemented, the runtime public result must explicitly
report the current policies and keep using hash-checked transactional patches.

Repair receipts must close the loop with revalidation evidence. A receipt that
claims an applied deterministic repair should be able to point at the verifier
command, exit code, before/after diagnostics, resolved/residual diagnostic ids,
and errors_before/errors_after/net_error_reduction. This evidence is
authoritative receipt data; AGI advisory notes are projection-only. A direct
`run_director_repair`/executor write result only proves that a patch was applied.
Until revalidation evidence is attached, the receipt must remain
`authoritative=false` with `metadata.requires_revalidation=true`. Public
`RepairReceiptV1` projections must expose `authority_hash` and `projection_hash`;
revalidation evidence is part of the authority hash material. Every summary must
expose `revalidation_coverage` so callers can distinguish missing post-check
evidence from failed post-check evidence. If evidence exists but its exit code is
non-zero, the receipt/summary is not authoritative and must not be reported as
merely missing evidence. Failed post-check coverage must identify the affected
receipt ids and source tools, not just a count, so retry routing and LLM context
can avoid blind repairs.

Dark-launch comparison is read-only. Use
`compare_director_repair_shadow_run` to compare legacy `tool_results` against
new kernel receipts by changed files and source tools before cutting over a
legacy path. The comparison must not write files or register rules.
`CompareDirectorRepairShadowRunV1.comparison_mode` must explicitly distinguish
`independent_shadow_run` from `legacy_projection_self_check`; only an
`independent_shadow_run` with matching scope, matching before/after hashes,
passing revalidation evidence, and authoritative applied receipts may report
`cutover_ready=true`. The
`dark_launch_comparison` embedded in legacy summary projection is only a
`legacy_projection_self_check`; it is not independent cutover evidence and must
remain `cutover_ready=false` with `independent_shadow_required` until an
external shadow run has been compared through `compare_director_repair_shadow_run`.

Legacy `tool_results` summary projection is a typed runtime public boundary.
Cross-cell callers must use `ProjectDirectorRepairKernelSummaryV1` with
`project_director_repair_kernel_summary` to project legacy writes into
repair-kernel receipts and coverage reports. `build_director_repair_kernel_summary`
is a compatibility helper only; `roles.adapters` must not call it directly.

The production deterministic repair strategies are still migrated through
`roles.adapters/internal/director/deterministic_repairs/` during cutover. That
directory is a legacy strategy host only: it must not own a repair kernel,
strategy catalog, policy gate, receipt contract, or AGI advisory contract.

Future language coverage must grow through this kernel, not by adding more
branches to `execute_method.py`. Bench agents running L1-L12 or other project
sets may add language-specific strategies for TypeScript, Go, Rust, C++, Java,
Python, shell, SQL, or future programming/script ecosystems. Before adding a
language repair, use `query_director_repair_language_slots` to inspect reserved
extension slots. The registry already reserves slots for common future targets
such as Vue/Svelte, Scala/Groovy, Elixir/Erlang, Haskell/OCaml/F#, Zig/Nim/
Crystal, Perl/PowerShell/Julia, Objective-C/MATLAB/Fortran, Terraform/HCL,
Dockerfile/Make/Bazel/Starlark, YAML/JSON/TOML/Nix, GraphQL/Proto, and
Solidity/Vyper. These slots are scaffolding only and do not mean the language
has an authoritative deterministic rule. Treat `implementation_status` as a
hard boundary: `reserved_only` is a future extension target,
`metadata_rule_registered` is catalog/coverage only, and `executable_runtime`
is the only state that may be called through `RunDirectorRepairCommandV1`.
Missing slots must be added to
`director.runtime` as read-only reservation metadata first, not as empty
execution branches. Each new repair must declare a stable `source_tool`,
language/archetype metadata, coverage behavior, receipt projection, and verifier
evidence. If a diagnostic is not yet covered, surface it as
`known_rule_matched=false` first; do not add speculative rules without bench
evidence.

Remaining code still lives elsewhere:

- File application / repair: `polaris/cells/director/tasking/internal/file_apply_service.py`,
  `polaris/cells/director/tasking/internal/repair_service.py`
- Patch application / existence gate: module-level functions in
  `polaris/cells/director/tasking/internal/patch_apply_engine.py` and
  `polaris/cells/director/tasking/internal/existence_gate.py` (there are no
  `PatchApplyEngine` / `ExistenceGate` classes).
- The original source also remains in `polaris/cells/director/execution/internal/`.
- Legacy deterministic repair strategy functions remain in
  `polaris/cells/roles/adapters/internal/director/deterministic_repairs/`; those
  modules must remain thin migration shims or strategy hosts until the runtime
  cutover is complete. Any new deterministic repair planning, patch composition,
  receipt projection, strategy catalog, or future AGI advisory contract must be
  added to `polaris/cells/director/runtime/` first and exposed through the
  generic public repair commands before legacy callers can consume it.
- The post-execution language path must stay behind
  `roles.adapters/internal/director/post_execution_repair_bridge.py` while
  cutover is incomplete. The bridge must consume
  `query_director_repair_post_execution_schedule` and only provide runner
  bindings for runtime-declared step ids. Its runner key set must exactly match
  the runtime-owned schedule; it must not add, remove, or reorder schedule
  entries in adapter code. `execute_method.py`, Factory, QA, and bench harnesses
  must not import language-specific repair functions directly.
- The materialization-quality path must stay behind
  `roles.adapters/internal/director/materialization_quality_repair_bridge.py`.
  The bridge must consume `run_director_materialization_quality_repair_schedule`
  and only provide runner bindings for runtime-declared step ids. Its runner key
  set must exactly match the runtime-owned schedule; it must not add, remove, or
  reorder schedule entries in adapter code. The current runtime-owned steps are
  `materialization.hygiene_scaffold`,
  `materialization.typescript_scaffold`, `materialization.typescript_compiler`,
  `materialization.node_manifest`, `materialization.rust_compiler`,
  `materialization.target_runtime`, `materialization.python_import`, and
  `materialization.go_import`; do not collapse them back into a single
  `materialization.quality_repair_host` step. The legacy
  `_apply_deterministic_materialization_quality_repairs` facade has been
  hard-cut and must not be restored, forwarded, or used by tests, bench harnesses,
  or agents.

AGI/resident advisory is intentionally not part of the Director repair execution
path. Future AGI integration may consume repair diagnostics and receipts to emit
non-authoritative advisory notes or suggested rule patterns only; it must not
write files, register rules, select success, override policy gates, or become
Run Ledger / ReceiptStore truth. Cross-cell callers can inspect the read-only
advisory boundary through `query_director_repair_advisory_policy`. Any future
AGI suggested-rule payload must first pass
`validate_director_repair_advisory`; validation is read-only and returns a
normalized advisory projection or explicit rejection, never a repair plan or
registered rule. Validation summaries must also explicitly report
`agi_execution_authority=false`, `writes_allowed=false`,
`registration_allowed=false`, `authoritative_receipts_allowed=false`, and
`suggested_rules_are_advisory_only=true` so downstream UI/LLM retry code cannot
mistake advisory suggestions for executable repair authority.
