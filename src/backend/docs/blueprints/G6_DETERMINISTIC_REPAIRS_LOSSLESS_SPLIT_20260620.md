# Blueprint: Lossless split of `deterministic_repairs.py` (G6)

**Status:** Blueprint ready (read-only analysis complete). Execution = zero-behavior-change package split.
**Target:** `polaris/cells/roles/adapters/internal/director/deterministic_repairs.py` — **2752 lines, 73 functions + 29 constants = 102 top-level symbols** (0 real module classes; `DeclaredPythonModuleSmokeTests` is generated code inside an f-string — do NOT move it as a symbol).
**Source audit:** gap-audit 2026-06-20 (G6); full per-symbol map in task output `wf`/agent result for this blueprint.

## 1. Public surface (facade MUST preserve byte-identically)
- **Exactly ONE external importer:** `internal/director/execute_method.py`, single deferred block at **lines 2858-2949** = `from .deterministic_repairs import (...)` of **90 symbols** (65 funcs + 25 consts), each `X as X`.
- No other module imports `deterministic_repairs` directly; no attribute-style `deterministic_repairs.X` anywhere. Tests reach symbols through the `execute_method` namespace (e.g. `test_director_adapter_pure.py`, `test_director_realtime_file_events.py`).
- ⇒ Preserving the 90-symbol re-export from the `deterministic_repairs` module path is necessary AND sufficient.

## 2. Approach
- Convert file → package `deterministic_repairs/__init__.py` (the facade). `from .deterministic_repairs import name` resolves identically for a package. `git mv` to preserve blame on the facade.
- Facade re-exports all symbols with the `X as X` idiom (silences F401 / no-implicit-reexport). Re-export all **102** for full `dir()` parity (90 load-bearing + 12 currently-internal).

## 3. Submodule grouping (by language/concern; carve verbatim)
- `_common.py` — ALL 29 constants + cross-group helpers: `_dedupe_paths`, `_path_inside_workspace`, `_dependency_root_name`, `_package_declared_in_manifest`, the `_parse_*` error-path family, `_find_nearby_declared_target_source(+_candidates)`, `_filter_satisfied_declared_target_missing_errors`. (Centralized so NO language submodule imports another.)
- `python_repairs.py` — python unittest/smoke/symbol-stub/unresolved-import repairs.
- `javascript_repairs.py` — JS frontend + node-test-script.
- `typescript_repairs.py` — reexport + return-object-semicolon + escaped-newline + relative-import-case clusters.
- `zod_repairs.py` — zod inferred-type/class-collision.
- `typeorm_repairs.py` — typeorm model normalization.
- `npm_repairs.py` — runtime-dependency + npm-test-script.
- `generic_repairs.py` — patch-residue, scaffold markers, declared-target repairs, prompt-block, AND the top orchestrator `_apply_deterministic_materialization_quality_repairs` (imports each language submodule's entry point — the only cross-group fan-in; no back-edges ⇒ no cycle).

## 4. Critical risk — preserve the circular-import dance EXACTLY
- `deterministic_repairs` imports `from . import execute_method as _em` (top, line 25); `execute_method` imports `from .deterministic_repairs import (...)` at the BOTTOM (line 2858, deferred for cycle safety).
- In the package, the 2 submodules needing it (`generic_repairs.py`: `_apply_deterministic_missing_declared_target_repair` → `_em.scan_workspace_artifact_quality`; `_apply_deterministic_pre_materialization_declared_target_repairs` → `_em._declared_target_file_quality_errors`) must do `from .. import execute_method as _em` and reference `_em.<attr>` **at call time only** (never at submodule import time, never bind to a local name) — required so test monkeypatch on the `execute_method` namespace still works (module docstring contract).
- No module-level side effects (all consts are pure `re.compile`/dict/float). No real `deterministic_repairs → quality_gate` import edge (routes through `_em`).
- Submodule sibling imports become two-dot: `from ..task_scope_paths import ...` (6 names, up to 19 callers — esp. `_normalize_declared_task_path`), `from ..execution_tools import DirectorToolExecutor` (15 callers), `from ..helpers import has_successful_write_tool` (3).
- f-string-embedded generated code (`_build_python_unittest_smoke_content` 221-262, `_build_substantive_node_test_script` 859-925, `_build_javascript_frontend_smoke_test_content` 283-333): move the triple-quoted literal VERBATIM (the `{{`/`}}` and `\\n` are load-bearing; don't let tooling reformat).

## 5. Verification gate (proves zero behavior change)
- `python -c "import polaris...deterministic_repairs as m; print(len([n for n in dir(m) if not n.startswith('__')]))"` ≥ 90 (ideally 102).
- `ruff check` + `mypy --strict` on the director package green (watch F401 on `X as X`).
- `pytest polaris/cells/roles/adapters/tests/test_director_adapter_pure.py polaris/cells/roles/adapters/tests/test_director_realtime_file_events.py polaris/tests/unit/kernelone/quality/test_artifact_quality.py -q` then the full director/quality suite (197+) green.
