# roles.runtime lazy-init field type boundaries

Date: 2026-11-09. Task: ROLES-RUNTIME-LAZY-INIT-TYPE-BOUNDS. Classification: pattern.

## Problem and evidence

Lazy-initialised dependency fields were assigned `None` in `__init__` without a type
annotation, so mypy inferred the field type as the literal `None`. Two distinct
consequences followed, both reported as mypy errors on pristine HEAD:

- `sequential_engine.py`: `self._llm_caller = None` / `self._tool_gateway = None`
  were inferred as `None`. Every later use guarded by `if not self._llm_caller:`
  or `if not callable(self._llm_caller):` was narrowed to "always true", so the
  live LLM-calling bodies (line 759, line 808) were flagged `unreachable`.
- `agent_runtime_base.py`: `self._backend_tool_runtime = None` (line 747) was
  inferred as `None` while its siblings (`_storage`, `_memory`, `_toolbox`,
  `_message_queue`, `_state`) all carry `: X | None`. The assignment at line 929
  was flagged as `Incompatible types in assignment`, and the double-checked
  fast-path at line 919/922 was flagged `unreachable` once the field stopped
  being `None`-typed.

`mypy polaris/cells/roles/runtime/internal/` reproduced all five errors before the
change. No runtime behavior depends on these annotations; they only restore the
intended `Any`/`Any | None` shape of the lazily-injected dependencies.

## Architecture and responsibilities

Owner remains `roles.runtime`. This is a type-boundary correction inside the
internal execution engine; no public contract, graph, state owner, effect,
descriptor, or cell boundary changes. The lazily-injected dependencies are still
supplied by the same setters (`SequentialEngine.set_dependencies`, the
`_get_backend_tool_runtime` lock-protected constructor); only their declared
static shape is corrected so mypy models the live code paths.

## Assumptions and pre-mortem

- The dependencies are genuinely `Any` (setters already declare `llm_caller: Any`
  and `tool_gateway: Any`), so `Any` is the faithful static type, not a weakening.
- Reading `_backend_tool_runtime` into a local before the fast-path `is not None`
  check is behavior-identical under the GIL (single reference read) and only
  prevents mypy from narrowing the field across the double-checked lock.
- Risk zone: `agent_runtime_base` is a base class consumed by many roles; the
  edit must not change the lock-protected construction order or the fail-closed
  `return None` branches.

## Complexity and rollback

Constant-size annotation additions and one local read: O(1) time and space, no
new allocation on the hot path. Roll back by reverting the three field
annotations and the single local read; no data, persistence, or wire format
changes.

## Verification plan

- `mypy` on `sequential_engine.py` + `agent_runtime_base.py`: five errors to zero.
- `ruff check` + `ruff format --check` on both files.
- Direct covering tests: `test_sequential_engine.py`,
  `test_agent_runtime_base_backoff.py`, `test_backend_tool_allowlist.py`,
  `test_kernelone_runtime_executor.py`.
- Subclass regression: `test_hr_advisor.py`, `test_cfo_advisor.py`,
  `test_agent_bus_proxy_peek.py`.
- Import sanity of both modules.

## Results

- RED: five mypy errors reproduced on pristine HEAD (2 `unreachable` in
  sequential_engine, `unreachable` x2 + `assignment` in agent_runtime_base).
- GREEN: mypy clean on both files; ruff check/format pass; import OK.
- Regression: 88 passed / 1 skipped across the direct and subclass suites above.
- Limitations: no new behavior is introduced; this is a static-shape correction.
  Full repository pytest and native execution are out of scope for this increment.
