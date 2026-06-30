# Polaris Bench-Unblocking Runtime Ledger

Status: Active
Created: 2026-06-30
Scope: Polaris meta-platform runtime hardening only. This ledger is not a runtime fact source.

This ledger tracks the current bench-blocking platform gaps separately from the
longer legacy/shim retirement ledger. Items are closed only after a code change
or an existing guard is verified by targeted tests.

## Current Count

| Class | Count | Meaning |
| --- | ---: | --- |
| Closed in this pass | 8 | Verified and removed from the active bench-unblocking ledger. |
| P0 open | 0 | Still able to block L1-L12 bench convergence or poison final Director requests. |
| P1 open | 0 | Important follow-up hardening, but not currently counted as bench-blocking. |

## Ledger

| ID | Severity | Status | Gap | Evidence | Required Exit Criteria | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| RB-01 | P0 | Closed | Run Ledger command evidence could be projected as missing even when command evidence existed but failed or passed under a non-`ok` status. | Bench reports showing real `npm run build/test/start` receipts while gate reported missing command evidence. | Run Ledger projection distinguishes missing required modalities from failed required modalities and accepts canonical pass statuses. | `rtk pytest src/backend/polaris/cells/control_plane/run_ledger/tests/test_public_service.py -q -k "required_modalities or command or failed_required or environment_prep"`; `rtk pytest src/backend/polaris/cells/factory/pipeline/tests/test_bench_service.py src/backend/polaris/cells/factory/pipeline/tests/test_bench_gates.py -q -k "failed_required_modalities or missing_required_modalities or run_ledger_projection"`. |
| RB-02 | P0 | Closed | QA deterministic workspace-validation failures could cascade into `qa_llm_judgement_unavailable`, making deterministic failures look like QA route failures. | Bench feedback where deterministic workspace validation failed while QA route audit blamed unavailable LLM judgement. | Deterministic validation failure is reported as deterministic evidence; QA LLM judgement absence is not added unless QA LLM was actually required. | `rtk pytest src/backend/polaris/cells/factory/pipeline/tests/test_factory_stage_executor_characterization.py -q -k "qa_llm_judgement"`; `rtk pytest src/backend/polaris/cells/factory/pipeline/tests/test_run_ledger.py -q`. |
| RB-03 | P0 | Closed | Weak-model tool-schema slimming treated natural-language prompt lines such as `Required tools: repo_rg` as hard structured contracts, so schema filtering could fail-closed before a required write. | Context snapshots with text-only tool guidance colliding with write-only materialization turns. | Only structured `metadata.tool_contract` / `metadata.platform_tool_contract` removed tools can fail closed; text-only required-tool mentions remain audit-only. | `rtk pytest src/backend/polaris/cells/roles/kernel/internal/llm_caller/tests/test_tool_helpers.py -q -k "ToolFilterAudit or text_only_required_tool_removal or structured_contract_required_tool_removal"`; `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_role_kernel_transaction_wiring.py -q -k "blocks_slimming_prompt_required_tool or slim_tool_schema or tool_filter"`. |
| RB-04 | P0 | Closed | Write-tool target pinning covered canonical `file` but needed verification that common LLM alias parameters cannot bypass target scope after normalization. | User-reported repeated write/edit failures and requirement to support more LLM calling habits. | Write schema target enum is pinned onto canonical file field and common aliases such as `path`, `filepath`, `filename`, `target_file`, and camelCase variants. | `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_step_target_file_pinning.py -q`. |
| RB-05 | P0 | Closed | Final Director requests can contain conflicting single-batch instructions, such as a targeted single-file repair plus stale contract-required `execute_command` or multi-file write requirements. | Context snapshots showing targeted repair text and a later system message requiring `execute_command` in the same single-batch repair turn. | Repair turn prompt assembly removes stale generic tool sequence requirements and only preserves structured contract requirements that match the current repair mode and authorized targets. | `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py -q -k "single_target_quality_repair or benchmark_required_tools_hint or missing_required_tool"`; `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_decision_message_builder_char.py -q -k "quality_repair or materialize_write_first_guard"`. |
| RB-06 | P0 | Closed | Tool failure summaries can still flood final LLM context when repeated write/edit failures happen in the same repair loop. | User-provided context snapshots with many repeated `[tool_failure_summary]` messages. | Final request includes bounded, deduplicated failure evidence and references detailed receipts by id instead of replaying every repeated summary as a message. | `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_decision_message_builder_char.py -q -k "tool_failure or quality_repair"`; `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_tool_loop_controller.py -q -k "tool_failure"`; `rtk pytest src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_fallback.py -q -k "tool_failure or prompt_safe"`. |
| RB-07 | P0 | Closed | Coverage can still be misread as repairability; `coverage_gap_count=0` is not equivalent to planner patchability. | Bench reports where diagnostics were covered but `plan_probe.patch_count=0` / `covered_unplannable`. | Plan probe status is authoritative for convergence routing; `covered_unplannable` must route to Director/CE discrepancy handling, not fake deterministic convergence. | `rtk pytest src/backend/polaris/cells/factory/pipeline/tests/test_factory_stage_executor_characterization.py -q -k "plan_probe or workspace_quality_diagnostic_targets"`; `rtk pytest src/backend/polaris/cells/control_plane/run_ledger/tests/test_public_service.py -q -k "task_boundary_plan_probe"`; `rtk pytest src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_public_convergence.py -q -k "plan_probe"`. |
| RB-08 | P0 | Closed | Cross-file task-boundary interface consistency is not yet a hard enough pre/downstream contract across all language paths. | Bench reports where producer modules exported one symbol/signature while downstream consumers used another. | Task-boundary validator emits interface discrepancy receipts from actual exported/consumed symbols and blocks downstream or triggers local rework before final aggregate validation. | `rtk pytest src/backend/polaris/cells/chief_engineer/blueprint/internal/tests/test_step_boundary.py src/backend/polaris/cells/chief_engineer/blueprint/public/tests/test_public_contracts.py -q -k "module_interface_contract or task_boundary or mixed_artifact_roles or owner_conflict or actual_export"`; `rtk pytest src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_public_convergence.py -q -k "task_boundary_interface_discrepancy or unplannable"`; `rtk pytest src/backend/polaris/cells/factory/pipeline/tests/test_factory_stage_executor_characterization.py -q -k "interface_discrepancy or task_boundary_triage or plan_probe"`. |

## Operating Rule

Close one item at a time. A closed item requires:

1. A scoped code change or proof that an existing guard already covers the gap.
2. A targeted test or static gate.
3. The verification command recorded in this ledger.
4. No reuse of the same open item as a fresh gap in later progress reports.
