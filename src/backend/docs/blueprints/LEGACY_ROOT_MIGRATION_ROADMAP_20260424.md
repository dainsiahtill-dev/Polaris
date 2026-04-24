# Legacy Root Migration Roadmap

## Status: ANALYSIS COMPLETE

Generated: 2026-04-24

## 1. Executive Summary

### 1.1 Key Finding: Phantom Migration Problem

The app/, core/, and api/ directories do not exist in this repository and have never existed in its git history. The actual legacy surface is:

| Directory/File | Files | Lines | Status |
|---|---|---|---|
| scripts/ | 56 | 2,617 | Active (43 modified 2026-04-21+) |
| server.py | 1 | 170 | Stable entry point |
| director_interface.py | 1 | 493 | Referenced by 3 polaris files + 1 test |
| **Total** | **58** | **3,280** | **0.4% of polaris/ codebase** |

### 1.2 Migration Feasibility: HIGH

- Low coupling: Only 3 polaris files import from old roots (all director_interface.py)
- No reverse coupling: server.py and director_interface.py only import from polaris/
- Self-contained scripts: 51 of 56 scripts import polaris but are not imported by any polaris file
- Critical bug found: process_launcher.py references non-existent scripts/pm/cli.py and scripts/loop-director.py

## 2. Detailed Analysis

### 2.1 Old Root Inventory

#### scripts/ (56 files, 2,617 lines)

By category:

| Category | Files | Lines | Action |
|---|---|---|---|
| One-off analysis | 15 | 254 | Delete |
| Test utilities | 12 | 237 | Migrate to tests/ or polaris/tests/ |
| Run helpers | 11 | 405 | Migrate to polaris/delivery/cli/ |
| Migration scripts | 9 | 501 | Archive or migrate to polaris/cells/ |
| Dev tools | 5 | 943 | Migrate to polaris/delivery/cli/ |
| Diagnostic | 4 | 277 | Evaluate individually |

By modification date:
- Recent (2026-04-21+): 43 files
- Older (pre-2026-04-21): 13 files

#### server.py (170 lines)

- Thin CLI adapter for BackendBootstrapper
- Only imports: polaris._env_compat, polaris.bootstrap, polaris.bootstrap.contracts.backend_launch
- No polaris file imports server.py
- Already follows ACGA 2.0 pattern

#### director_interface.py (493 lines)

- Abstract director layer with DirectorInterface, DirectorTask, DirectorResult
- Imports: polaris.kernelone.runtime.shared_types, polaris.kernelone.storage
- Imported by 3 polaris files:
  - polaris/cells/orchestration/workflow_activity/internal/activities/director_activities.py
  - polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/director_activities.py
  - polaris/delivery/cli/pm/director_interface_integration.py
- Imported by 1 test: tests/test_director_interface_timeout.py

### 2.2 Dependency Graph

#### polaris/ to Old Roots

- polaris/cells/orchestration/workflow_activity/internal/activities/director_activities.py imports director_interface (DirectorTask, create_director)
- polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/director_activities.py imports director_interface (DirectorTask, create_director)
- polaris/delivery/cli/pm/director_interface_integration.py imports director_interface (DirectorInterface, DirectorTask, create_director)

#### Old Roots to polaris/

server.py imports:
- polaris._env_compat.normalize_env_prefix
- polaris.bootstrap.BackendBootstrapper
- polaris.bootstrap.contracts.backend_launch.BackendLaunchRequest

director_interface.py imports:
- polaris.kernelone.runtime.shared_types.normalize_path_list
- polaris.kernelone.runtime.shared_types.timeout_seconds_or_none
- polaris.kernelone.storage

scripts/ (13 files) import:
- polaris.cells.roles.kernel.internal.transaction.constants (5 files)
- polaris.cells.roles.kernel.internal.transaction.intent_classifier (3 files)
- polaris.kernelone.storage (3 files)
- polaris.kernelone.storage.layout (2 files)
- polaris.bootstrap.assembly (1 file)
- polaris.cells.llm.evaluation.public.service (1 file)
- polaris.delivery.cli.agentic_eval (1 file)
- polaris.infrastructure.llm.adapters.stub_embedding_adapter (1 file)
- polaris.infrastructure.storage (1 file)
- polaris.kernelone.fs (1 file)
- polaris.kernelone.llm.embedding (1 file)
- polaris.infrastructure.db.adapters (1 file)
- polaris.kernelone.db (1 file)
- polaris.kernelone.context.engine.* (3 files)
- polaris.kernelone.events.message_bus (1 file)
- polaris.cells.storage.layout.internal.settings_utils (1 file)
- polaris.kernelone.llm.runtime_config (1 file)

### 2.3 Critical Issues Found

#### BUG-1: Broken Path References in process_launcher.py

Location: polaris/cells/orchestration/workflow_runtime/internal/process_launcher.py lines 339 and 383

Problem:
- pm_script = backend_root / "scripts" / "pm" / "cli.py"        (Does not exist)
- director_script = backend_root / "scripts" / "loop-director.py"  (Does not exist)

Actual locations:
- polaris/delivery/cli/pm/cli.py
- polaris/delivery/cli/loop-director.py

Impact: launch_pm() and launch_director() methods will fail at runtime if called.

Root cause: Code was migrated from old scripts/ layout but path constants were not updated.

#### BUG-2: Internal Module Imports from scripts/

Multiple scripts import polaris/ internal modules, violating Cell boundary rules:

- audit_markers.py: polaris.cells.roles.kernel.internal.transaction.constants
- test_intent*.py: polaris.cells.roles.kernel.internal.transaction.intent_classifier
- test_imports.py: polaris.cells.storage.layout.internal.settings_utils
- decouple_config.py: Multiple internal configs

### 2.4 polaris/ Internal Coupling (for context)

| Source | Target | Files |
|---|---|---|
| cells | kernelone | 656 |
| delivery | kernelone | 244 |
| delivery | cells | 222 |
| infrastructure | kernelone | 160 |
| cells | domain | 77 |

## 3. Migration Priority Matrix

### P0: Critical (Blocker)

| Item | Effort | Risk | Action |
|---|---|---|---|
| Fix process_launcher.py broken paths | 1h | Low | Update path constants |
| Migrate director_interface.py to polaris/ | 4h | Medium | Move to polaris/delivery/cli/pm/ or polaris/domain/director/ |

### P1: High (Breaking Dependencies)

| Item | Effort | Risk | Action |
|---|---|---|---|
| Update 3 polaris files to import from new director_interface location | 2h | Low | Update import paths |
| Update test_director_interface_timeout.py | 1h | Low | Update import path |
| Migrate conftest.py to polaris/tests/ | 2h | Low | Move and update references |

### P2: Medium (Script Cleanup)

| Item | Effort | Risk | Action |
|---|---|---|---|
| Delete 15 one-off analysis scripts | 1h | None | These are disposable |
| Migrate run_helpers to polaris/delivery/cli/ | 4h | Low | Consolidate test runners |
| Migrate dev-tools.py to polaris/delivery/cli/ | 2h | Low | Evaluate if still needed |
| Migrate benchmark_iterative_loop.py | 2h | Low | Move to evaluation cell |

### P3: Low (Archive/Evaluate)

| Item | Effort | Risk | Action |
|---|---|---|---|
| Archive migration scripts (fix_*, apply_*) | 2h | None | Historical artifacts |
| Evaluate lancedb_store.py | 1h | Low | May be obsolete |
| Clean up test_intent*.py scripts | 1h | None | Merge into proper tests |

## 4. Phase Plan

### Phase 1: Critical Fixes (Week 1)

1. Fix process_launcher.py path constants
2. Migrate director_interface.py to polaris/delivery/cli/pm/director_interface.py
3. Update all 3 polaris importers + 1 test
4. Verify no polaris file imports from old roots

### Phase 2: Script Consolidation (Week 2-3)

1. Delete 15 one-off analysis scripts
2. Migrate conftest.py to tests/conftest.py or polaris/tests/conftest.py
3. Migrate run helpers to polaris/delivery/cli/tools/
4. Consolidate test_intent*.py into proper test suite

### Phase 3: Final Cleanup (Week 4)

1. Archive migration scripts to docs/archive/migration_scripts/
2. Evaluate remaining scripts individually
3. Update AGENTS.md and CLAUDE.md to reflect completed migration
4. Add CI gate to prevent old root imports

## 5. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| process_launcher broken paths cause runtime failure | High | High | Fix immediately in Phase 1 |
| director_interface migration breaks orchestration | Low | High | Update all 3 importers atomically |
| Deleting analysis scripts loses historical data | None | Low | These are one-off scripts with no data |
| Test utilities migration breaks test suite | Low | Medium | Run full test suite after migration |
| scripts/ removal breaks developer workflows | Medium | Low | Communicate new CLI locations |

## 6. Verification Checklist

- [ ] No polaris file imports from director_interface (old root)
- [ ] No polaris file imports from scripts/
- [ ] process_launcher.py paths resolve correctly
- [ ] All tests pass after director_interface migration
- [ ] scripts/ file count less than 10 (only essential utilities)
- [ ] CI gate blocks new old-root imports

## 7. Appendix: Script-by-Script Disposition

| Script | Lines | Action | Target Location |
|---|---|---|---|
| analyze_l2.py | 22 | Delete | - |
| analyze_l2_contract.py | 20 | Delete | - |
| analyze_l2_detail.py | 21 | Delete | - |
| analyze_l3_calls.py | 12 | Delete | - |
| analyze_l3_ctx.py | 22 | Delete | - |
| analyze_l3_event5.py | 18 | Delete | - |
| analyze_l3_full.py | 19 | Delete | - |
| analyze_l3_log.py | 31 | Delete | - |
| analyze_l3_meta.py | 16 | Delete | - |
| analyze_l3_res.py | 7 | Delete | - |
| analyze_l3_retry_ctx.py | 16 | Delete | - |
| analyze_l3_tc.py | 17 | Delete | - |
| view_dag.py | 12 | Delete | - |
| view_integrity.py | 12 | Delete | - |
| verify_false_pos.py | 9 | Delete | - |
| conftest.py | 22 | Migrate | tests/conftest.py |
| conftest_quick.py | 9 | Migrate | tests/conftest_quick.py |
| test_conftest_imports.py | 12 | Migrate | tests/unit/test_conftest_imports.py |
| test_import.py | 1 | Delete | - |
| test_imports.py | 33 | Migrate | tests/unit/test_imports.py |
| test_intent.py | 26 | Merge | tests/unit/kernel/test_intent.py |
| test_intent2.py | 14 | Merge | tests/unit/kernel/test_intent.py |
| test_intent3.py | 21 | Merge | tests/unit/kernel/test_intent.py |
| test_intent4.py | 31 | Merge | tests/unit/kernel/test_intent.py |
| test_intent_final.py | 34 | Merge | tests/unit/kernel/test_intent.py |
| test_script.py | 1 | Delete | - |
| test_storage_layout.py | 33 | Migrate | tests/unit/kernelone/test_storage_layout.py |
| run_all_new_tests.py | 49 | Migrate | polaris/delivery/cli/tools/run_tests.py |
| run_all_tests.py | 62 | Migrate | polaris/delivery/cli/tools/run_tests.py |
| run_collect.py | 21 | Delete | - |
| run_message_bus_tests.py | 27 | Migrate | polaris/delivery/cli/tools/run_tests.py |
| run_message_bus_tests2.py | 27 | Migrate | polaris/delivery/cli/tools/run_tests.py |
| run_message_bus_tests3.py | 28 | Migrate | polaris/delivery/cli/tools/run_tests.py |
| run_msgbus.py | 21 | Migrate | polaris/delivery/cli/tools/run_msgbus.py |
| run_msgbus_subprocess.py | 28 | Migrate | polaris/delivery/cli/tools/run_msgbus.py |
| run_pytest.py | 24 | Migrate | polaris/delivery/cli/tools/run_tests.py |
| run_pytest2.py | 31 | Migrate | polaris/delivery/cli/tools/run_tests.py |
| run_quick_tests.py | 87 | Migrate | polaris/delivery/cli/tools/run_tests.py |
| benchmark_iterative_loop.py | 187 | Migrate | polaris/cells/llm/evaluation/tools/benchmark.py |
| check_cell_imports.py | 210 | Migrate | polaris/delivery/cli/tools/check_imports.py |
| contextos_gate_checker.py | 257 | Migrate | polaris/delivery/cli/tools/contextos_gate.py |
| dev-tools.py | 326 | Evaluate | TBD |
| lancedb_store.py | 111 | Evaluate | TBD |
| add_session_tool.py | 62 | Delete | - |
| audit_markers.py | 23 | Delete | - |
| find_l3_case.py | 13 | Delete | - |
| parse_logs.py | 31 | Delete | - |
| apply_prompt_updates.py | 85 | Archive | docs/archive/migration_scripts/ |
| apply_rw_barrier.py | 41 | Archive | docs/archive/migration_scripts/ |
| decouple_config.py | 64 | Archive | docs/archive/migration_scripts/ |
| fix_circular.py | 28 | Archive | docs/archive/migration_scripts/ |
| fix_imports.py | 26 | Archive | docs/archive/migration_scripts/ |
| fix_model_copy_violations.py | 98 | Archive | docs/archive/migration_scripts/ |
| fix_ollama_404.py | 70 | Archive | docs/archive/migration_scripts/ |
| move_configs_to_central.py | 31 | Archive | docs/archive/migration_scripts/ |
| refactor_config.py | 58 | Archive | docs/archive/migration_scripts/ |
