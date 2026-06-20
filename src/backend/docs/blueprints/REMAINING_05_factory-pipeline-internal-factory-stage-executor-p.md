# polaris/cells/factory/pipeline/internal/factory_stage_executor.py :: OrchestrationStageExecutor (1945 lines, 62 methods)

kind=god-class-split effort=large

# Blueprint: Decompose `OrchestrationStageExecutor` god-class (G8 part)

Target: `polaris/cells/factory/pipeline/internal/factory_stage_executor.py`
Class: `OrchestrationStageExecutor` — 1945 lines, 62 methods. Behavior-preserving, atomic-green decomposition.

## 1. Current state
One class implementing the `FactoryStageExecutor` Protocol via a handler-map `execute()` over 5 stages (docs_generation, pm_planning, chief_engineer_review, director_dispatch, quality_gate). 62 methods mix artifact filesystem I/O + mirroring, delivery-target validation, PM/doc text shaping (with embedded Chinese prompt business strings), task-field accessors, taskboard/director evidence statics, an npm/node workspace-quality subprocess engine, the 5 async stage executors, and orchestration-service/run-wait coordination (load-bearing lazy imports + duck-typed reach into `orchestration._active_runs`).

ctor: `__init__(self, workspace: Path)` → `self.workspace`, `self._fs = KernelFileSystem(str(workspace), get_default_adapter())`.

## 2. Public surface (FROZEN)
- Cross-cell contract: `FactoryStageExecutor` Protocol (`factory_run_models.py` L203-207), one method `execute(stage, run, context) -> StageResult`. `OrchestrationStageExecutor` is its production impl. NOT exported from `public/service.py` — internal only.
- Import sites: `factory_run_service.py` L61 import, L117 `__all__` re-export, L167 default executor. Both module paths (direct + `factory_run_service` re-export) must keep returning the same class object.
- Test coupling: subclassing overriding `_build_orchestration_service` / `_run_workspace_quality_command`; `monkeypatch.setattr(executor, ...)` on `_wait_run_completion`, `_ensure_docs_artifacts`, `_artifact_exists`, `_validate_pm_plan_contract`; direct calls to `_artifact_path`, `_collect_declared_delivery_targets`, the 5 `_execute_*` and `_run_pm_planning_deterministic_recovery`.
- Invariant: every monkeypatched/overridden/test-called method stays an instance (or static/class) method with identical name+signature, and all internal call sites route through `self.<name>`.

## 3. Decomposition target (collaborators in sibling modules; executor keeps thin shims)
1. `factory_stage_helpers.py` — pure functions/statics (text shaping, delivery-target normalization, evidence truth-tables, env/bool, command resolution, output trimming).
2. `factory_artifact_store.py` — `ArtifactStore(workspace, fs)` (path/read/write/copy/mirror/audit/exists).
3. `factory_workspace_quality.py` — `WorkspaceQualityRunner` (package.json parsing, npm command building, subprocess exec).
4. `factory_run_completion.py` — `RunCompletionWaiter` (orchestration-service build + run-wait race) preserving lazy imports + `_active_runs` getattr + `contextlib.suppress`.
5. `factory_stage_executor.py` — slim coordinator keeping `OrchestrationStageExecutor`, the 5 async stage executors, and same-named delegating shims for every frozen method.

## 4. Plan (atomic-green)
Order: (0) characterization tests → (1) pure helpers → (2) ArtifactStore → (3) planning text glue → (4) WorkspaceQualityRunner → (5) RunCompletionWaiter → (6) stage executors stay/thin → (7) lint+mypy+full green. Each step is extract-to-sibling-then-leave-delegating-shim, suite green after each.

## 5. Risks
Subclass/monkeypatch bypass if call sites stop routing through `self.<name>`; load-bearing lazy imports must not hoist (import-cycle guard); `_active_runs` getattr and `contextlib.suppress(asyncio.CancelledError)` concurrency path move verbatim; section-8 business code (Chinese PM/architect prompts, npm/node commands, `dispatch_mode='mainline-full'`) flagged — do NOT delete/alter; `_artifact_path` rewrite ordering is test-asserted; line-count drift (HEAD 1945, not 1835).

## 6. Test guard & coverage gaps
Guarded by the 3 listed test files. Write characterization tests for mirror helpers, artifact write/read/audit, package.json parsing, real-subprocess branches of `_run_workspace_quality_command`, the director-evidence statics, and text-shaping edge cases BEFORE extracting them.

## 7. Effort
Large — 5 new modules, ~62 methods rewired as shims, concurrency- and monkeypatch-sensitive, plus ~7 characterization test additions. Strictly behavior-preserving.