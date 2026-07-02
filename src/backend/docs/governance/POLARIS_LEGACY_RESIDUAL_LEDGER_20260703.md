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

## Open Residual Buckets

These are not all equal. The first bucket is the only one that can affect bench
execution correctness directly.

| Bucket | Priority | Current Evidence | Exit Criteria |
| --- | --- | --- | --- |
| Log/event projection compatibility | P1 | `infrastructure.log_pipeline.*` still exposes legacy channel/event projection wording and conversion helpers. | Confirm whether these are still required by runtime.v2/EventBus consumers; if not, remove or rename to canonical projection language and keep one event truth source. |
| Workflow embedded compatibility mode | P1 | `workflow_activity/internal/embedded_api.py` and `workflow_runtime/internal/embedded_api.py` still branch on `mode == "legacy"`. | Determine whether workflow snapshots can be normalized before this boundary; remove legacy mode or fence it as read-only import migration. |
| PM/orchestration migration paths | P1 | PM integration and orchestration modules still sync or materialize legacy tasks into canonical task rows. | Ensure TaskMarket/Execution Ledger is the only active execution fact source; keep only one-way import migration if needed. |
| Audit diagnosis script payload flattening | P2 | Audit diagnosis toolkit still emits `legacy` script-friendly payloads. | Replace with explicit `script_projection` naming or remove if no active caller needs the flattened shape. |
| CLI compatibility surfaces | P2 | CLI/router/console/director_v2 still accept or warn about retired modes, test-window, textual/rich aliases, and `--state`. | Either remove the compatibility options or fence them as explicit fail-closed/deprecation errors with tests. |
| Workspace/docs migration paths | P3 | Docs/workspace integrity still references old docs layout and metadata paths for migration. | Keep as accepted read-only migration only, or remove after confirming no workspace bootstrap relies on it. |
| Domain vocabulary, not debt | Not counted | Tech Radar uses `deprecated` as a library ring; PM requirements use `deprecated` as a soft-delete state. | Do not remove; these are business states, not architecture drift. |
| Generated descriptor text | Not counted | Generated packs may still contain retired names until descriptor refresh. | Refresh in a descriptor wave after source convergence; do not hand-edit generated packs. |

## Next Closure Order

1. P1 log/event projection compatibility.
2. P1 workflow embedded compatibility mode.
3. P1 PM/orchestration migration paths.
4. P2 audit diagnosis script projection.
5. P2 CLI compatibility surfaces.
6. P3 workspace/docs migration paths.
