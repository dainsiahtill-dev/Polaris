# polaris/delivery/http/v2/director.py

kind=lossless-module-split effort=large

## G8 (part) — Lossless module split: polaris/delivery/http/v2/director.py

### Goal
Split a 2414-line function-collection-behind-a-router into a thin router + cohesive helper modules, BEHAVIOR-PRESERVING, suite green after every step. Delivery layer; stays inside delivery/http/v2 (same cell boundary, ACGA 2.0 respected).

### Frozen public surface (must stay byte-identical / attribute-resolvable on `polaris.delivery.http.v2.director`)
- `router` (APIRouter prefix=/director, tags=[Director v2]) — wired in v2/__init__.py L9/L21; all 19 routes unchanged.
- Monkeypatch string targets consumed at call time: RuntimeProjectionService, select_task_rows_from_projection, build_llm_status, build_workflow_task_rows, BlueprintPersistence, resolve_artifact_path, get_task_market_service, get_global_emitter, get_global_token_budget, get_orchestration_service, ensure_required_roles_ready, Path, logger, _runtime_task_rows_for_workspace, _build_director_diagnostics_for_request, _append_debug, _runtime_backed_task_rows.
- Direct test imports: _merge_director_status (shim), _append_debug, _runtime_backed_task_rows, list_tasks (called directly), model classes DirectorDiagnostics{LLM,Status,Task,Worker}Section + DirectorDiagnosticsResponse + DirectorStatusResponse (re-exported from director_models).

### Pattern (precedent: director_models.py already extracted + re-exported)
extract-to-sibling-module-then-leave-delegating-re-export. New modules: director_helpers.py (pure leaves), director_task_rows.py (row assembly + blueprint payload IO), director_diagnostics.py (diagnostics + workers + execute-gates), director_support.py (workspace/snapshot/response/_append_debug). director.py becomes: imports + re-export barrel (`from .director_X import name1, name2, ...`) + _merge_director_status shim + router + 19 handlers.

### Critical correctness rule
Tests patch director.<X> and then drive full routes. Any patchable symbol referenced inside a MOVED helper must be dereferenced through the director module object at call time (`from . import director as _d; _d.X(...)`), NOT by the helper module's own bare global, or the patch is bypassed. Resolve cross-helper patchable calls (e.g. _runtime_backed_task_rows -> _runtime_task_rows_for_workspace) the same way. Use a lazy `from . import director` to avoid the circular import (director imports the helpers' names; helpers reference the director module object, dereferenced only at call time after import completes).

### Steps
0. Characterization tests for transitively-covered helpers (see coverage gaps).
1. Move pure leaves -> director_helpers.py; re-import by name. Green.
2. Move row-assembly + blueprint-IO -> director_task_rows.py with director-namespace resolution. Green (esp. the patch-_runtime_task_rows_for_workspace / build_workflow_task_rows / get_task_market_service / BlueprintPersistence / resolve_artifact_path / Path tests).
3. Move diagnostics cluster -> director_diagnostics.py (build_llm_status, RuntimeProjectionService, ensure_required_roles_ready resolved via director). Re-import _build_director_diagnostics_for_request. Green.
4. Move workspace/snapshot/response + _append_debug -> director_support.py. Re-import _append_debug, _projected_task_response. Green.
5. director.py is now thin router + barrel + shim. Full suite + ruff + mypy.
6. Lossless assertion: import director, assert every frozen name present; grep all test string targets resolve.

### Risks
- Monkeypatch indirection (highest) — see rule above.
- Circular import — lazy `from . import director`, dereference at call time.
- Hot path GET /status, GET /tasks?source=local — keep cheap; no importlib per call.
- list_tasks/route fns must remain module attributes (they stay in director.py).
- Section 8 business code in handlers (Phase 6 unified-orchestration compat /run L2275, /integration-qa L2198, Chinese docstrings) — FLAG only, do NOT delete; stays in handlers.
- Preserve all lazy in-handler imports verbatim.

### Effort: large (4 new modules, ~75 helpers, dense monkeypatch contract).