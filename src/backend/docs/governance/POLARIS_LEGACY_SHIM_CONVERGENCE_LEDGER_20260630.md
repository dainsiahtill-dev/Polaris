# Polaris Legacy/Shim Convergence Ledger

Status: Active  
Created: 2026-06-30  
Scope: Polaris meta-platform source only. This ledger is not a runtime fact source.  

This ledger tracks remaining compatibility, legacy, shim, and fallback surfaces that
can still affect architecture convergence. It separates actionable runtime debt from
historical documentation, protocol compatibility labels, provider "OpenAI-compatible"
business wording, React `Suspense fallback`, and accepted config migration code.

## Current Count

| Class | Count | Meaning |
| --- | ---: | --- |
| Closed in this convergence pass | 1 | Removed and verified. |
| P1 open | 7 | Still close to execution, LLM/tool, QA, or state projection paths. |
| P2 open | 5 | Exposed API/UI/CLI compatibility surfaces that should be retired after callers move. |
| P3 accepted with sunset | 2 | Kept for user config or historical data migration; requires an expiry policy, not immediate deletion. |

## Ledger

| ID | Severity | Status | Gap | Evidence | Required Exit Criteria | Next Action |
| --- | --- | --- | --- | --- | --- | --- |
| LS-00 | P2 | Closed | LLM Provider edit state kept a legacy `editingProvider` / `startEditProvider` / `stopEditProvider` path beside canonical `editingProviderId`. | `src/frontend/src/app/store/providerStore.ts`; `src/frontend/src/app/components/llm/state/providerReducer.ts`; `ProviderContext.tsx`; `CanonicalProviderBridge.tsx`. | No `startEditProvider`, `stopEditProvider`, `START_EDIT_PROVIDER`, `STOP_EDIT_PROVIDER`, or `editingProvider:` references remain in `src/frontend/src/app/components/llm` / `src/frontend/src/app/store`; `npx tsc --noEmit` passes. | Closed by commit `00717e4c Remove legacy provider edit actions`. |
| LS-01 | P1 | Open / production path cleared | `polaris.application.orchestration` is marked `shim_only` in the migration ledger; production callers have been removed, but tests and migration markers still keep the legacy surface alive. | `src/backend/docs/migration/ledger.yaml` unit `mig-application-batch1`; `rg "polaris\.application\.orchestration\.director_orchestrator|DirectorOrchestrator" src/backend/polaris/delivery src/backend/polaris/cells -g '!**/tests/**'` now has no production matches; tests still exercise `DirectorOrchestrator`; `check_shim_markers.py --json` fails on 15 files. `delivery/cli/pm/director_interface_core.py`, `delivery/cli/director/director_service.py`, and `delivery/cli/director_v2.py` have been migrated to public execution contracts. | Test coverage moves from direct legacy orchestrator assertions to public execution contract or tombstone-compatibility assertions; `polaris/application/orchestration/` is deleted or reduced to fail-closed tombstones; `check_shim_markers.py --json` passes. | Replace direct orchestrator tests, then delete or tombstone `application/orchestration` implementation files. |
| LS-02 | P1 | Open | Director adapter still exposes direct text/patch fallback control fields around `direct_fallback` and `allow_patch_fallback`. | `src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py`; `execution.py`; `quality_gate.py`; `adapter.py`. | No executable direct provider fallback remains; all repair/patch attempts enter typed runtime repair or tool lifecycle receipts; old fallback metadata becomes fail-closed evidence only. | Replace fallback execution with `TaskBoundaryVerdict` / runtime repair request, then add a negative grep fence. |
| LS-03 | P1 | Open | `materialization_quality_repair_bridge.py` remains a large adapter bridge between quality gates and Director Runtime repair. | `src/backend/polaris/cells/roles/adapters/internal/director/materialization_quality_repair_bridge.py`; runtime repair catalog tests. | Bridge stops owning repair decisions; it only translates diagnostics into `director.runtime.public` calls or is deleted; all receipts come from runtime repair kernel. | Inventory remaining bridge entrypoints and move each to typed runtime plan/compose/policy/execute/revalidate flow. |
| LS-04 | P1 | Open | `deterministic_repairs/*` language modules still contain concrete repair mutators even though package root was fenced. | `src/backend/polaris/cells/roles/adapters/internal/director/deterministic_repairs/`. | Concrete mutators are either migrated into `director.runtime.internal.repair_kernel` or removed; adapter imports cannot call mutators directly; architecture test blocks concrete repair imports outside runtime. | Start with Python unresolved-import and NPM/script repair paths because they caused high-frequency bench regressions. |
| LS-05 | P1 | Open | Role Kernel still keeps TurnEngine/KernelCore delegating shim surfaces over TransactionKernel. | `src/backend/polaris/cells/roles/kernel/internal/kernel/core.py`; `turn_engine.py`; `turn_execution.py`; `transaction_factory.py`. | TransactionKernel is the only implementation surface; remaining classes are either deleted or minimal public facades with no second state/result shape. | Count live imports of TurnEngine/Core helpers, migrate callers, then harden `test_turn_engine_compat_fence.py`. |
| LS-06 | P1 | Open | Deprecated `LLMCaller` facade remains beside `LLMInvoker`, with final request audit metadata duplicated across caller paths. | `src/backend/polaris/cells/roles/kernel/internal/llm_caller/caller.py`; `llm_caller/__init__.py`; `invoker.py`. | All role runtime paths call `LLMInvoker`; `LLMCaller` is deleted or raises a deprecation failure in production; final provider request audit has one canonical writer. | Move remaining imports to `LLMInvoker`, then add import hygiene test for `LLMCaller`. |
| LS-07 | P1 | Open | Legacy textual tool-call protocol detection is still present while native tool lifecycle is the canonical path. | `src/backend/polaris/cells/llm/tool_runtime/internal/role_integrations.py`; `roles/kernel/internal/tool_call_protocol.py`. | Text protocol can only be parsed through ToolSpecRegistry normalization and must always produce lifecycle receipts; no free-form protocol path can bypass dispatch accounting. | Convert detection to a normalization adapter with `tool_call_lifecycle_receipt.v1` evidence. |
| LS-08 | P2 | Open | LLM Provider state still has a broad Canonical-to-legacy bridge and mapped legacy state. | `src/frontend/src/app/components/llm/state/CanonicalProviderBridge.tsx`; `canonicalState.ts`. | UI components consume canonical hooks/state directly; bridge is removed or narrowed to a temporary test-only adapter. | Migrate consumers area by area after LS-00 removal. |
| LS-09 | P2 | Open | CLI/HTTP compatibility aliases still expose old route semantics. | `delivery/cli/__main__.py`; `delivery/cli/router.py`; `delivery/http/v2/pm.py`; `delivery/http/v2/director.py`. | Deprecated aliases return explicit migration responses or are removed; production docs only mention canonical routes; tests assert no PM->Director bypass. | List public callers, then retire aliases behind 410/migration responses. |
| LS-10 | P2 | Open | Runtime/log pipeline still accepts legacy channel and event field shapes. | `kernelone/runtime/defaults.py`; `domain/director/constants.py`; `infrastructure/log_pipeline/*`; frontend log adapters. | Ingest compatibility is isolated in one anti-corruption adapter; all UI/API projections read canonical runtime.v2 / Run Ledger facts. | Build a single runtime event normalization adapter and block new direct legacy channel reads. |
| LS-11 | P2 | Open | Frontend dialogue and ContextOS views still carry parser compatibility for multiple historical response shapes. | `src/frontend/src/app/components/ai-dialogue/useRoleChat.ts`; `ContextViewerModal.tsx`; context telemetry parsers. | Backend response contracts are stable enough that UI can drop historical response-shape parsing, or compatibility remains isolated in a named adapter with tests. | Audit current backend response schemas before deleting; avoid breaking user-facing dialogue UI. |
| LS-12 | P3 | Accepted with sunset required | Config and provider registries keep legacy key migration for existing user settings. | `bootstrap/config.py`; `bootstrap/config_loader.py`; provider `migrate_legacy_config` functions. | Keep migration, but add versioned sunset policy and telemetry for remaining legacy key usage. | Add metric/audit event for migrated keys before any deletion. |
| LS-13 | P3 | Accepted with sunset required | Historical artifact/path aliases remain for old workspace data. | `artifact_store`, `audit/verdict`, ContextOS/history materialization alias code. | Keep read compatibility until a data migration tool exists; all new writes use canonical keys only. | Add write-path fence and migration command; do not delete before migration path exists. |

## Not Counted As Debt

- Provider protocol labels such as "OpenAI compatible" or "Anthropic compatible".
- React `Suspense fallback` and ordinary UI error fallback messages.
- Historical governance/archive documents that describe already-closed incidents.
- Tests and fixtures intentionally containing "legacy" to assert retired behavior stays blocked.

## Operating Rule

Close one item at a time. Each closure must include:

1. A scoped code change that reduces or removes a compatibility surface.
2. A negative search proving the old symbol/path/field cannot reappear silently.
3. A targeted test or type check proving the canonical path still works.
4. A ledger update that moves the item to `Closed` with the verification command.
