# Polaris KFS Direct-Write Convergence Ledger

Status: Active  
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
| Closed in this convergence pass | 2 | Owner surfaces migrated to KFS and verified with focused tests. |
| P1 open | 4 | Execution, repair, verifier, or platform-control write paths close to runtime correctness. |
| P2 open | 3 | Internal Factory/bench, instance, or benchmark write paths that still must converge. |

## Closed Cuts

| ID | Severity | Status | Owner Surface | Evidence | Verification |
| --- | --- | --- | --- | --- | --- |
| KFS-00 | P1 | Closed | `chief_engineer.blueprint` workspace JSON ledgers | `ADRDecisionLog`, `RiskRegister`, `TechDebtLedger`, `TechRadarLedger`, and `PostMortemLog` now persist via `KernelFileSystem.write_json_atomic(...)` while keeping existing read/list compatibility for historical JSON files. | `rtk pytest src/backend/polaris/cells/chief_engineer/blueprint/tests/test_adr_log.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_risks.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_tech_debt.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_tech_radar.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_post_mortem.py -q` passed; direct-write scan no longer reports any `polaris/cells/chief_engineer/blueprint/internal/*.py` unbaselined writer. |
| KFS-08 | P2 | Closed | `orchestration.pm_planning` governance ledgers | `DecisionRegister`, `MilestoneRegister`, `RaidRegister`, and `build_pm_project_report` now persist through `KernelFileSystem.write_json_atomic(...)` / `KernelFileSystem.write_text_atomic(...)`; historical read/list behavior stays path-compatible under `runtime/pm`. | `rtk pytest src/backend/polaris/kernelone/fs/tests/test_kernel_filesystem.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_decision_log.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_milestones.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_raid_register.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_project_report.py -q` passed; direct-write scan reports 14 unbaselined files, down from 18. |

## Open Direct-Write Gap Ledger

| ID | Severity | Owner Surface | Current Unbaselined Files | Closure Cut |
| --- | --- | --- | --- | --- |
| KFS-01 | P1 | Verifier/control-plane policy | `polaris/cells/control_plane/verifier_policy/public/service.py` | Move policy persistence to KFS atomic JSON without changing public service contract. |
| KFS-02 | P1 | Director repair/convergence | `polaris/cells/roles/adapters/internal/director/deterministic_repairs/_common.py`; `polaris/cells/roles/adapters/internal/director/post_execution_repair_bridge.py`; `polaris/cells/roles/adapters/internal/director/repair_convergence_verifier.py` | Replace direct writes with runtime repair receipts/KFS helpers; do not add baseline debt in legacy adapter strategy host. |
| KFS-03 | P1 | Factory HTTP control surface | `polaris/delivery/http/routers/factory.py` | Move HTTP-triggered artifact/session writes behind Factory cell public service or KFS helper. |
| KFS-04 | P1 | KernelOne LLM/quality runtime | `polaris/kernelone/llm/engine/context_store_retention.py`; `polaris/kernelone/llm/engine/executor.py`; `polaris/kernelone/quality/artifact_quality.py` | Migrate runtime state and quality artifacts to KernelOne FS helpers with receipts. |
| KFS-06 | P2 | Factory pipeline internals | `polaris/cells/factory/pipeline/internal/bench_service.py`; `polaris/cells/factory/pipeline/internal/factory_artifact_store.py`; `polaris/cells/factory/pipeline/internal/factory_stage_executor.py`; `polaris/cells/factory/pipeline/internal/factory_workspace_quality.py` | Introduce or reuse Factory artifact KFS store; keep Bench as internal test producer, not runtime fact source. |
| KFS-07 | P2 | Instance registry | `polaris/cells/instances/internal/service.py` | Route registry writes through the existing storage abstraction/KFS boundary while preserving launcher behavior. |
| KFS-09 | P2 | KernelOne benchmark/holographic cases | `polaris/kernelone/benchmark/holographic/cases/platform_services.py` | Keep benchmark code internal, but move any persisted artifacts to KFS/test-owned storage helpers. |

## Operating Rule

1. Do not refresh `kfs_direct_write_baseline.txt` to hide these regressions.
2. Close one owner surface at a time with focused tests and a before/after
   direct-write scan.
3. A full `test_polaris_kernel_fs_guard.py` pass is the exit criterion for this
   ledger.
