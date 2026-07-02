# Polaris KFS Direct-Write Convergence Ledger

Status: Closed
Created: 2026-07-01  
Scope: Polaris meta-platform source only. This ledger tracks runtime/business
Python files that still perform direct write I/O outside `KernelFileSystem` or
the approved `kernelone.fs` JSONL/text helpers.

This ledger exists because `test_polaris_kernel_fs_guard.py` exposed direct
write regressions while retiring log-pipeline legacy adapters. The fix policy is
fail-forward: do not add new baseline debt for fresh direct writes. Migrate one
bounded owner surface at a time to canonical KFS APIs, then rerun the guard.

## Current Count

| Class | Count | Meaning |
| --- | ---: | --- |
| Closed in this convergence pass | 9 | Owner surfaces migrated to KFS and verified with focused tests. |
| P1 open | 0 | Runtime/control-plane direct-write gaps remaining at P1. |
| P2 open | 0 | Internal Factory/bench write paths that still must converge. |

## Closure and Intake Rules

This ledger is closed for the current KFS direct-write convergence pass. It must
not be used as an open-ended search list for every `write_text` symbol in the
repository. New findings belong here only when the architecture guard or a
concrete runtime/bench artifact proves an unbaselined direct write in Polaris
runtime/business code outside the approved `KernelFileSystem` or
`kernelone.fs` helper boundaries.

Do not reopen a closed KFS row to describe a new symptom. Open a new `KFS-*`
item with:

1. The owner surface and exact unbaselined file path.
2. Evidence from `test_polaris_kernel_fs_guard.py` or an equivalent guard output.
3. The intended canonical write boundary (`KernelFileSystem`,
   `kernelone.fs.text_ops`, or another approved KFS helper).
4. Focused behavior tests plus a before/after direct-write scan.

Not every write-like symbol is debt. Protocol methods, adapter interfaces,
tests/fixtures, generated descriptor text, benchmark-owned temporary work, and
historical docs are out of scope unless they become production runtime facts.

## Closed Cuts

| ID | Severity | Status | Owner Surface | Evidence | Verification |
| --- | --- | --- | --- | --- | --- |
| KFS-00 | P1 | Closed | `chief_engineer.blueprint` workspace JSON ledgers | `ADRDecisionLog`, `RiskRegister`, `TechDebtLedger`, `TechRadarLedger`, and `PostMortemLog` now persist via `KernelFileSystem.write_json_atomic(...)` while keeping existing read/list compatibility for historical JSON files. | `rtk pytest src/backend/polaris/cells/chief_engineer/blueprint/tests/test_adr_log.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_risks.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_tech_debt.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_tech_radar.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_post_mortem.py -q` passed; direct-write scan no longer reports any `polaris/cells/chief_engineer/blueprint/internal/*.py` unbaselined writer. |
| KFS-01 | P1 | Closed | Verifier/control-plane policy | `update_verifier_policy` now writes `.polaris/verifier_policy.json` through `KernelFileSystem.workspace_write_text_atomic(...)`, preserving sorted JSON payload shape and the public service contract. | `rtk pytest src/backend/polaris/cells/control_plane/verifier_policy/tests/test_public_service.py src/backend/polaris/tests/unit/delivery/http/routers/test_control_plane_router.py -q` passed; direct-write scan reports 12 unbaselined files, down from 13. |
| KFS-02 | P1 | Closed | Director repair/convergence | Legacy controlled repair writers and repair convergence raw-output logs now write through `kernelone.fs.text_ops.write_text_atomic(...)`, retaining existing workspace escape guards, non-authoritative receipt metadata, and verifier evidence refs. | `rtk pytest src/backend/polaris/cells/roles/adapters/tests/test_director_repair_writers.py src/backend/polaris/cells/roles/adapters/tests/test_director_repair_convergence_verifier.py -q` passed; direct-write scan reports 6 unbaselined files, down from 9. |
| KFS-03 | P1 | Closed | Factory HTTP control surface | Director-resume TaskBoard rehydration/reset JSON writes in `factory.py` now use a local `write_text_atomic(...)` JSON helper, preserving existing payload shape and API behavior while removing HTTP-router direct writes. | `rtk pytest src/backend/polaris/tests/test_factory_contract_snapshot.py src/backend/polaris/tests/integration/delivery/test_factory_lifecycle.py -q` passed; direct-write scan reports 5 unbaselined files, down from 6. |
| KFS-04 | P1 | Closed | KernelOne LLM/quality runtime | Context sweep state and final provider context snapshots now use `kernelone.fs.text_ops.write_text_atomic(...)`; JavaScript snippet syntax checks use `node --check -` via stdin instead of writing temp JS files. | `rtk pytest src/backend/polaris/kernelone/llm/engine/tests/test_context_store.py src/backend/polaris/kernelone/llm/engine/tests/test_context_store_retention.py src/backend/polaris/kernelone/llm/engine/tests/test_final_request_receipt.py src/backend/polaris/kernelone/quality/tests/test_artifact_quality.py src/backend/polaris/kernelone/quality/tests/test_syntax_gate.py src/backend/polaris/tests/unit/kernelone/quality/test_artifact_quality.py -q` passed; direct-write scan reports 9 unbaselined files, down from 12. |
| KFS-07 | P2 | Closed | Instance registry | `InstanceRegistry` now writes `registry.json` through `KernelFileSystem.workspace_write_text_atomic(...)`, preserving the existing `POLARIS_INSTANCE_HOME` / launcher file location while removing the local temp-file replace write. | `rtk pytest src/backend/polaris/kernelone/fs/tests/test_kernel_filesystem.py src/backend/polaris/cells/instances/tests/test_instance_service.py -q` passed; direct-write scan reports 13 unbaselined files, down from 14. |
| KFS-08 | P2 | Closed | `orchestration.pm_planning` governance ledgers | `DecisionRegister`, `MilestoneRegister`, `RaidRegister`, and `build_pm_project_report` now persist through `KernelFileSystem.write_json_atomic(...)` / `KernelFileSystem.write_text_atomic(...)`; historical read/list behavior stays path-compatible under `runtime/pm`. | `rtk pytest src/backend/polaris/kernelone/fs/tests/test_kernel_filesystem.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_decision_log.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_milestones.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_raid_register.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_project_report.py -q` passed; direct-write scan reports 14 unbaselined files, down from 18. |
| KFS-06 | P2 | Closed | Factory pipeline internals | Factory bench session JSON/JSONL writes, factory artifact text/audit writes, pre-Director snapshot manifest writes, and workspace-quality patch writes now use `kernelone.fs.text_ops.write_json_atomic(...)`, `write_text_atomic(...)`, or `append_text_atomic(...)`. Bench remains an internal test producer; these writes are not promoted to product runtime facts. | Direct-write scan reports 0 unbaselined files; `rtk pytest src/backend/polaris/tests/architecture/test_polaris_kernel_fs_guard.py -q` passed. |
| KFS-09 | P2 | Closed | KernelOne benchmark/holographic cases | TC-KS-002 prompt hot-reload benchmark writes now use `kernelone.fs.text_ops.write_text_atomic(...)` inside the benchmark-owned temporary directory. | `rtk proxy python - <<'PY' ... _exec_tc_ks_002(...) ... PY` passed with `zero_interrupt_percent=100.0`; direct-write scan reports 4 unbaselined files, down from 5. |

## Open Direct-Write Gap Ledger

| ID | Severity | Owner Surface | Current Unbaselined Files | Closure Cut |
| --- | --- | --- | --- | --- |
| — | — | — | None | All direct-write gaps tracked by this ledger are closed. |

## Operating Rule

1. Do not refresh `kfs_direct_write_baseline.txt` to hide these regressions.
2. Close one owner surface at a time with focused tests and a before/after
   direct-write scan.
3. A full `test_polaris_kernel_fs_guard.py` pass is the exit criterion for this
   ledger.
