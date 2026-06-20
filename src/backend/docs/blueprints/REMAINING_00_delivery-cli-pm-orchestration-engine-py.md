# polaris/delivery/cli/pm/orchestration_engine.py :: run_once (line 177-1180) and _run_dispatch_pipeline_with_workflow (line 1183-2134)

kind=god-function-split effort=large

# Blueprint: G1 god-FUNCTION decomposition of orchestration_engine.py

## Target
`polaris/delivery/cli/pm/orchestration_engine.py` (2161 lines). Two god-functions:
- `run_once(args, iteration=1) -> int` lines 177-1180 (~1004 lines) — sole PM-iteration entry point.
- `_run_dispatch_pipeline_with_workflow(*, ...) -> dict` lines 1183-2134 (~952 lines), with 8 nested closures (1229-1471).

Architecture guard (`tests/test_architecture_guard.py`) caps the file at 900 lines and currently `xfail`s.

## ACGA placement
All new helpers stay inside the SAME cell location `polaris/delivery/cli/pm/orchestration/` (sibling modules), matching the existing extracted set (exit_grading, pm_failure_detail, workflow_timeout, zero_task_fallback). No cross-cell move; direction delivery->application->domain->kernelone is unaffected. `run_once` and `_run_dispatch_pipeline_with_workflow` STAY defined in `orchestration_engine.py` as the canonical names (the latter as a delegating shim for its internals); the heavy bodies move to siblings.

## Public surface that must stay byte-identical
- `run_once(args, iteration=1) -> int`; exit codes 0 (success), 1 (failure), 3 (agents/manual confirmation pending), graded 4/5 from exit_grading, plus `return docs_exit` (199) and the RuntimeError ramdisk guard (214-218).
- Callers: `cli.py:36`, `loop-pm.py:60` (wraps as `_run_once_impl`; at 154-161 monkeypatches `_pm_orchestration_engine_module.invoke_pm_backend` — attr must remain settable and `run_once` must remain in this module), `__init__.py:149` lazy map, `http/v2/pm.py` via PMService.
- Module-attribute monkeypatch targets (resolved through the `orchestration_engine` namespace): `run_pm_planning_iteration`, `submit_pm_workflow_sync`, `wait_for_workflow_completion_sync`, `get_workflow_runtime_status`, `summarize_workflow_tasks`, `resolve_director_dispatch_tasks`, `run_post_dispatch_integration_qa`, `persist_pm_payload`, `emit_event`, `invoke_pm_backend`.
- `_run_dispatch_pipeline_with_workflow` is invoked directly as `mod._run_dispatch_pipeline_with_workflow(**kwargs)` in tests; keyword-only signature must stay byte-identical.
- Re-exported private helpers (81-113) imported by tests must keep resolving from `orchestration_engine`; `__all__` (2150-2160) and the required public imports (pm_dispatch.public, runtime.state_owner.public, pm_planning.public.pipeline) must stay.

## Extraction strategy (extract-to-sibling-then-delegate)
Each step keeps the suite green. The CRITICAL invariant: any extracted code that reads a monkeypatchable global MUST resolve it through the `orchestration_engine` module object at call time (`import polaris.delivery.cli.pm.orchestration_engine as _oe; _oe.submit_pm_workflow_sync(...)`), NOT via a frozen `from ... import name`. Otherwise `monkeypatch.setattr(mod, ...)` in the integration_qa tests is bypassed and they fail. Use a function-local `_oe` import to avoid the import cycle.

See `plan_steps` for the ordered atomic-green sequence: characterization tests first, then dispatch body, closures, zero-task fallback, setup, post-dispatch, traceability, finalize, size-confirm.

## Risks
Monkeypatch-through-namespace (dominant), loop-pm invoke_pm_backend setattr, circular import via the `_oe` back-reference, load-bearing `contextlib.suppress`/`except ImportError`/local lazy imports, hot-path exit-code + env side-effect fidelity, and embedded §8 business code (hardcoded Chinese meta-prompt hint + deterministic requirements-fallback heuristics — flag, do not delete).

## Coverage gaps
Dispatch-result unpack + traceability registration, post-dispatch blocked-policy/stop block, RuntimeError ramdisk guard, final engine-status fan-out, and the duplicate `_merge_engine_config` all need characterization before the corresponding extraction step.

## Critical files for implementation
- polaris/delivery/cli/pm/orchestration_engine.py
- polaris/delivery/cli/pm/orchestration/__init__.py
- polaris/delivery/cli/loop-pm.py
- polaris/tests/test_orchestration_engine_integration_qa.py
- polaris/tests/test_architecture_guard.py