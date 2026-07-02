# Polaris Legacy Residual Ledger - 2026-07-03

Status: Active intake  
Scope: Polaris meta-platform production source, excluding generated packs, tests, fixtures, and historical docs unless they expose a runtime surface.

This ledger records the residual convergence pass after the Execution Control Plane,
Director runtime repair-kernel, task-boundary, QA verdict, and tool-lifecycle hardening
work. It exists because the older convergence ledger is closed intake and cannot be used
as the live work queue for new findings.

## Operating Invariants

- Do not delete by keyword. Every item must be classified as runtime debt, compatibility migration, domain vocabulary, or test-only counterexample before edits.
- Close one item per commit unless a group shares the same contract and test surface.
- A closed item must include a negative scan for the retired symbol/path/field and a focused test or type check.
- Generated descriptor packs are refreshed only in a deliberate descriptor wave; stale generated text alone is not runtime debt.
- Existing dirty changes from other Agents are not included in these commits.

## Closed In This Pass

| ID | Layer | Problem | Closure | Verification |
| --- | --- | --- | --- | --- |
| LR-01 | QA verdict | `KERNELONE_QA_VERDICT_ENGINE_MODE=legacy` disabled the QA verdict engine, allowing a stale config token to bypass the new verdict path. | Removed `legacy` as an off switch; unknown values now fall back to shadow audit. | `rtk pytest src/backend/polaris/cells/qa/audit_verdict/internal/tests/test_qa_consumer.py src/backend/polaris/cells/qa/audit_verdict/internal/tests/test_verdict_engine.py -q`; `rtk ruff check`; `rtk mypy`. |
| LR-02 | Director adapter | Disabled text/PATCH fallback errors used `legacy_*` error codes, polluting evidence even though the path was fail-closed. | Renamed error/protocol codes to `text_tool_protocol_disabled` and `patch_file_protocol_disabled`. | `rtk pytest src/backend/polaris/cells/roles/adapters/tests/test_director_realtime_file_events.py -q`; negative source scan. |
| LR-03 | LLM tool runtime | Role tool integrations still emitted a `legacy_text_tool_protocol_disabled` protocol violation for text-wrapped tool blocks. | Renamed the guard to native-tool-only terminology and aligned the protocol violation with Director adapter errors. | `rtk pytest src/backend/polaris/cells/llm/tool_runtime/tests/test_role_integrations.py src/backend/polaris/tests/architecture/test_tool_calling_canonical_gate.py -q`; negative source scan. |
| LR-04 | Director planning | `director.planning.internal.director_logic.py` retained a simplified write gate and `_dl_*` aliases beside canonical `director_logic_rules`. | Deleted the retired module and removed `_dl_*` package-root exports. | `rtk pytest src/backend/polaris/tests/test_director_logic.py src/backend/polaris/tests/unit/cells/test_director/execution/test_logic.py -q`; negative import scan. |
| LR-05 | CE blueprint | `enable_director_pool` and `director_pool_assignment` names implied CE still managed Director execution when the actual path is ADRStore persistence plus TaskMarket handoff. | Renamed to `enable_adr_blueprint_store` and `director_execution_assignment`. | `rtk pytest src/backend/polaris/cells/chief_engineer/blueprint/internal/tests/test_ce_consumer.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_ce_consumer_integration.py src/backend/polaris/cells/chief_engineer/blueprint/tests/test_director_pool.py -q`; negative source scan. |
| LR-06 | Storage layout | `save_persisted_settings` still dual-wrote the old home settings file, creating a second settings fact source. | Stopped dual-writing the migration path; retained one-way read migration into canonical global settings. | `rtk pytest src/backend/polaris/cells/storage/layout/tests/test_storage_layout_cell.py src/backend/polaris/tests/test_workspace_settings_sync.py src/backend/polaris/tests/test_llm_phase0_regression.py -q -k "settings"`; negative source scan. |
| LR-07 | Log pipeline | Log pipeline compatibility projection used `LEGACY_CHANNEL_MAPPING`, `normalize_legacy_event`, `to_legacy_projection`, and `legacy_*` model fields, making compatibility input look like a second event fact source. | Renamed the layer to `COMPAT_CHANNEL_MAPPING`, `normalize_compat_event`, `to_compat_projection`, and `compat_*` fields while keeping canonical event writing behavior. | `rtk pytest src/backend/polaris/tests/infrastructure/log_pipeline/test_canonical_event.py -q`; `rtk ruff check`; `rtk mypy`; negative source scan. |
| LR-08 | Workflow embedded bridge | Embedded workflow child payloads and result wrappers still used `mode == "legacy"` as the non-DAG compatibility contract, leaving old terminology in the runtime boundary. | Renamed the compatibility mode to `compat` across the embedded workflow APIs, KernelOne workflow contract parser, PM activity summaries, and focused tests. | `rtk pytest src/backend/polaris/tests/orchestration/test_workflow_engine.py src/backend/polaris/tests/unit/cells/orchestration/workflow_runtime/internal/test_embedded_api.py src/backend/polaris/tests/unit/cells/orchestration/workflow_runtime/internal/test_workflow_client.py src/backend/polaris/kernelone/workflow/tests src/backend/polaris/tests/test_kernelone_safety_regressions.py -q -k "workflow_contract or compat or legacy or from_payload or unwrap_workflow_result or workflow_client"`; `rtk ruff check`; `rtk mypy`; negative source scan. |
| LR-09 | PM dispatch projection | Active PM dispatch metadata still published `legacy_shadow_normalized`, and the cell-local registry used `legacy_id` as an alternate task identity. | Renamed the publish marker to `task_market_contract_normalized` and the external task identity field to `source_task_id` inside pm_dispatch. | `rtk pytest src/backend/polaris/cells/orchestration/pm_dispatch/tests/test_shangshuling_registry.py src/backend/polaris/tests/test_pm_dispatch_shangshuling_registry.py src/backend/polaris/cells/orchestration/pm_dispatch/tests/test_dispatch_pipeline.py src/backend/polaris/tests/unit/cells/orchestration/pm_dispatch/internal/test_dispatch_pipeline.py src/backend/polaris/tests/unit/cells/orchestration/pm_dispatch/internal/test_pm_task_utils.py -q`; `rtk ruff check`; `rtk mypy`; negative source scan. |
| LR-10 | Delivery PM CLI import | Delivery-layer PM import code used `legacy_id`, `legacy_task`, `legacy_sync`, and `sync_from_legacy_tasks` while importing source payloads into canonical PM task rows. | Renamed the import boundary to `source_task_id`, `source_task`, `source_task_sync`, and `sync_from_source_tasks`; updated loop-director and Director node selectors to use source-task terminology. | `rtk pytest src/backend/polaris/tests/unit/delivery/cli/test_pm_cli.py -q`; `rtk ruff check`; `rtk mypy`; negative source scan. |

## Open Residual Buckets

These are not all equal. The first bucket is the only one that can affect bench
execution correctness directly.

| Bucket | Priority | Current Evidence | Exit Criteria |
| --- | --- | --- | --- |
| Factory resume task-directory import | P1 | `delivery/http/routers/factory.py` still uses `legacy_task_dirs` naming while rehydrating Director resume evidence. | Rename the import boundary to task directory/source evidence terminology, or remove it if modern run-ledger resume evidence has fully replaced it. |
| Role runtime old task-ref rejection | P1 | Role runtime contracts intentionally reject old task-market task-ref shapes but still expose helper names containing `legacy`. | Rename negative-validation helpers/tests to `retired` or `old_shape` terminology while preserving fail-closed behavior. |
| Audit diagnosis script payload flattening | P2 | Audit diagnosis toolkit still emits `legacy` script-friendly payloads. | Replace with explicit `script_projection` naming or remove if no active caller needs the flattened shape. |
| CLI compatibility surfaces | P2 | CLI/router/console/director_v2 still accept or warn about retired modes, test-window, textual/rich aliases, and `--state`. | Either remove the compatibility options or fence them as explicit fail-closed/deprecation errors with tests. |
| Workspace/docs migration paths | P3 | Docs/workspace integrity still references old docs layout and metadata paths for migration. | Keep as accepted read-only migration only, or remove after confirming no workspace bootstrap relies on it. |
| Domain vocabulary, not debt | Not counted | Tech Radar uses `deprecated` as a library ring; PM requirements use `deprecated` as a soft-delete state. | Do not remove; these are business states, not architecture drift. |
| Generated descriptor text | Not counted | Generated packs may still contain retired names until descriptor refresh. | Refresh in a descriptor wave after source convergence; do not hand-edit generated packs. |

## Next Closure Order

1. P1 Factory resume task-directory import.
2. P1 Role runtime old task-ref rejection.
3. P2 audit diagnosis script projection.
4. P2 CLI compatibility surfaces.
5. P3 workspace/docs migration paths.
