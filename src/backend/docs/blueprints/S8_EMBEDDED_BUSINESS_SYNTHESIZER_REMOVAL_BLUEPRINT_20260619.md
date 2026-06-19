# §8 Embedded Business Synthesizer Removal — Evaluation & Proposal (2026-06-19)

> Status: **PROPOSAL — awaiting sign-off.** No core code changed by this document.
> Authority: CLAUDE.md §8 (绝对禁止在 Polaris 添加业务代码), AGENTS.md §8.6 (structural change ⇒ VC + ADR).
> Target: `polaris/cells/roles/adapters/internal/director/execute_method.py` (6718 lines).

## 1. Finding (what the codegraph audit established)

`execute_method.py` embeds a family of **deterministic content synthesizers** the Director
runs to repair/fill materialized artifacts. They split cleanly into two classes:

### Class A — generic, language/framework-level (KEEP)
Project-agnostic; fix syntax/structure regardless of domain. Always-on in the dispatcher
`_apply_deterministic_materialization_quality_repairs`:
- `_apply_deterministic_typeorm_model_normalization_repair`
- `_apply_deterministic_typescript_return_object_semicolon_repair`
- `_apply_deterministic_typescript_escaped_newline_repair`
- `_apply_deterministic_typescript_zod_type_class_collision_repair`
- `_apply_deterministic_npm_test_script_repair`
- `_apply_deterministic_runtime_dependency_repair`
- `_apply_deterministic_missing_declared_target_repair`
- `_apply_deterministic_unresolved_import_symbol_repair`
- `_apply_deterministic_node_test_script_contract_repair`, `_apply_deterministic_patch_residue_cleanup`,
  `_apply_deterministic_scaffold_marker_cleanup`, `_apply_deterministic_python_static_smoke`,
  `_apply_deterministic_python_runtime_smoke`, `_apply_deterministic_typescript_reexport_repair`

### Class B — project-specific business answers (§8 violation, propose DELETE)
These hardcode the domain answer of **one specific historical project**: a TypeScript
multi-tenant Task / Tenant / DAG / Audit backend. Gated behind
`_business_contract_synthesis_enabled()` (env `KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS`):
- repairs: `_apply_deterministic_task_model_contract_repair`, `_apply_deterministic_tenant_model_contract_repair`,
  `_apply_deterministic_audit_service_contract_repair`, `_apply_deterministic_task_service_contract_repair`,
  `_apply_deterministic_framework_free_service_repair`
- content synths: `_synthesize_task_model_contract_content`, `_synthesize_tenant_model_contract_content`,
  `_synthesize_audit_service_contract_content`, `_synthesize_audit_middleware_contract_content`,
  `_synthesize_task_service_contract_content`, `_synthesize_task_service_contract_content_for_crud`,
  `_synthesize_task_controller_contract_content`, `_synthesize_taskgraph_contract_content`,
  `_synthesize_taskgraph_test_contract_content`, `_synthesize_dag_service_contract_content`,
  `_synthesize_base_model_contract_content`*, `_synthesize_base_repository_contract_content`*
- the gate fn `_business_contract_synthesis_enabled` itself, once callers are removed

(\* `base_model`/`base_repository` are borderline — they exist to back the Task/Tenant domain models;
recommend deleting with Class B, decision flagged below.)

### Class S — scaffold-template synthesis (related, propose DELETE per its own docstring)
`_synthesize_declared_target_file_content` + `_synthesize_node_test_file_content` +
`_synthesize_workspace_test_contract_content`, gated by `_scaffold_synthesis_enabled()`
(env `KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS`). The gate docstring itself records that the
template table "embeds one specific historical project's contracts (task.model.ts /
tenant.model.ts / taskgraph, multi-tenant field assumptions)" and that it once wrote a
TypeScript scaffold README into a **Python calculator** project. Same §8 taint as Class B.

## 2. Current risk posture — the good news

Both gates **already default to OFF (fail-closed honesty)**, with docstrings citing §8:
- `KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS` → default `"0"`
- `KERNELONE_DIRECTOR_SCAFFOLD_SYNTHESIS` → default `"0"`

⇒ **Active false-signal risk is already neutralized.** On every default run the Director
surfaces the true materialization failure instead of fabricating a project-specific answer.
Confirmed: `scripts/factory_bench/projects_v1.json` (L1–L7, 40 projects) contains no project
whose graded output is produced by these synths under default flags, so **current factory-bench
data is not polluted** (consistent with prior campaign notes).

## 3. Residual debt — why removal still matters

The dormant code is still a real §8 liability:
1. **Letter violation:** business-domain answers for a specific product live in platform core.
2. **Re-activation footgun:** anyone exporting `KERNELONE_DIRECTOR_BUSINESS_CONTRACT_SYNTHESIS=1`
   silently re-arms the false signal — inflating any benchmark whose domain matches the
   hardcoded Task/Tenant/DAG/Audit backend, masking a true edit-chain failure.
3. **Bloat / cognition cost:** ≈ 1,200 lines of dead domain content in a 6,718-line hot file.

## 4. Containment proof (why deletion is safe)

- Codegraph + grep: **zero production callers** of any Class B/S symbol outside
  `execute_method.py`.
- Only external references: `polaris/cells/roles/adapters/tests/test_director_adapter_pure.py`
  — 3 sites, all opt-in (`setenv(...,"1")` / `delenv`). These tests must be removed/retargeted
  with the deletion.
- All Class B repairs are reachable ONLY inside `if _business_contract_synthesis_enabled():`
  branches (dispatcher lines 3416–3440, 3464–3478) plus the scaffold-gated content dispatch
  (`_synthesize_declared_target_file_content`, scaffold default-off). With flags default-off,
  no default run reaches them ⇒ deletion is **behavior-preserving for default runs**.

## 5. Proposed change

1. Delete all Class B functions + the `_business_contract_synthesis_enabled` gate and its
   three branch call-sites (keep the always-on Class A repairs in the dispatcher untouched).
2. Delete Class S synthesis (gate `_scaffold_synthesis_enabled`, `_synthesize_declared_target_file_content`,
   `_synthesize_node_test_file_content`, `_synthesize_workspace_test_contract_content`) — its own
   docstring documents the §8 taint. **(Decision point — see §7.)**
3. Keep `_apply_deterministic_missing_declared_target_repair` (generic) but drop its now-dead
   scaffold/business synth branch so it no longer routes to deleted content.
4. Remove the 3 opt-in hooks in `test_director_adapter_pure.py`; keep all Class A tests.
5. Remove the two env vars from any docs/sample configs.

## 6. Verification plan (the sign-off gate)

- `ruff check --fix && ruff format && mypy execute_method.py` clean.
- Full `pytest` on the director adapter suite green (minus deleted opt-in tests).
- **A/B factory-bench (decisive):** run L2–L6 with flags default-off **before vs after** deletion;
  product output must be **byte-identical** — proving no live dependency. Attach the diff to the VC.
- Governance: `catalog_governance_fail_on_new` `new_issue_count == 0`; add a regression assert that
  neither env var re-introduces business synthesis.

## 7. Decisions required from you

- **D1 — Scope:** delete Class B only, or Class B + Class S (scaffold)? (Recommend **both**.)
- **D2 — Borderline:** delete `_synthesize_base_model_contract_content` /
  `_synthesize_base_repository_contract_content` with Class B? (Recommend **yes** — they only
  back the Task/Tenant domain.)
- **D3 — Sequencing:** require the A/B byte-identical bench BEFORE merge (recommend), or delete-then-verify?

## 8. Non-goals

- No change to Class A generic repairs.
- No change to the Director write-convergence path (`write-convergence-multimodal`).
- This blueprint does not itself delete anything; execution follows sign-off + a VC under §8.6.
