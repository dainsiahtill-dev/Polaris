# TS-8: Type-erasure cleanup of `Any` annotations across Cell public/contract files (root: polaris/cells/**/public, polaris/kernelone/**/contracts*.py). Priority files: cells/roles/runtime/public/service.py, cells/llm/evaluation/internal/tool_calling_matrix/_contracts.py, cells/runtime/state_owner/internal/pm_contract_store.py, kernelone/tool_execution/contracts.py, plus the Protocol-return-erasure cluster in cells/roles/session/public/contracts.py, cells/roles/kernel/public/contracts.py, kernelone/db/contracts.py, kernelone/llm/provider_contract.py.

kind=typing-fix effort=large

# TS-8 Typing-Fix Blueprint — `Any` erasure on Cell public/contracts

## 0. Scope & ground truth
- Target: `Any` annotations across `polaris/cells/**/public/*.py` and `polaris/kernelone/**/*contract*.py`.
- Inventory (rg, non-test): 92 files carry the `Any` token, ~887 raw occurrences. The actionable subset ("212 across 64") is the real `: Any` params + `-> Any` returns; the rest are `dict[str, Any]`/`Mapping[str, Any]` JSON envelopes that are legitimately dynamic.
- **Gate reality**: root `pyproject.toml [tool.mypy]` is `strict=true, warn_return_any=true, disallow_untyped_defs=true`; mypy excludes `tests/` + `infrastructure/`. **ruff ANN401 (`Any`) is in the GRADUAL-ADOPTION ignore list** (lines 190-192). So `Any` is NOT currently gate-failing. TS-8 is a quality uplift. Invariant per step: `mypy polaris/` error count does not increase AND `pytest -q` stays green.

## 1. Categorization
### (a) MUST stay `Any` — legitimate dynamic
- **PEP 562 lazy-export `__getattr__(name: str) -> Any`** — 19 sites across public `__init__.py`/service barrels. roles/runtime/public/__init__.py:225 even documents it. Narrowing breaks cross-cell lazy attribute resolution.
- **`*args, **kwargs -> Any` passthrough shims** — tool_execution/contracts.py:40 (`_deprecated` wrapper), roles/runtime/public/service.py:354 (`get_role_system_prompt`).
- **`dict[str, Any]` / `Mapping[str, Any]` JSON envelopes** — PM payloads, context/metadata/patch, HP command results (policy/protocol/public/contracts.py ~27 hits are all `dict[str, Any]` HP JSON), tool specs. Genuinely dynamic.
- **Abstract Ports** — kernelone/db/contracts.py:65/84/111 (DBAPI/SQLAlchemy/LanceDB), kernelone/llm/provider_contract.py:203 (dynamic registry). Typing couples kernelone to vendor types; LEAVE (or TYPE_CHECKING-quote only).
- **DEPRECATED `_ToolSpecsProxy` view methods** (tool_execution/contracts.py:134-144) — deprecated 2026-04-16; not worth typing.

### (b) REAL erasure to fix (typed replacement, no behavior change, no `type: ignore`)
- Protocol returns with a concrete impl/DTO: roles/session/public/contracts.py:259/262/265, roles/kernel/public/contracts.py:275, roles/kernel/services/contracts.py:163, kernelone/roles/shared_contracts.py:123/128, kernelone/llm/shared_contracts.py:445/457.
- JSON-coercion helpers: pm_contract_store.py:57 (`write_json_atomic(data: Any)`), read_json_safe, safe_payload_digest; _contracts.py:213 (`_sanitize_json`). Introduce a shared `JsonValue` TypeAlias.

## 2. Plan (atomic-green, extract-to-sibling / type-in-place)
0. Baseline mypy error count + pytest green.
1. Add `JsonValue` TypeAlias in a kernelone sibling module (grep for existing first); export it. No call-site change.
2. pm_contract_store.py: type JSON params/returns to `JsonValue`/`dict[str, JsonValue]`; keep `normalized: dict[str, Any]` (dynamic PM payload). Guard: test_pm_contract_store_kfs.py, test_pm_state_sync.py.
3. _contracts.py `_sanitize_json -> JsonValue`. Leave dataclass `Mapping[str, Any]` fields. Guard: test_tool_calling_matrix_prompt_contract.py.
4. shared_contracts.py format_tools/format_messages — type return only if impl returns a concrete shape (verify via codegraph_callees); else leave + flag.
5. Protocol return cluster — **one method per atomic commit**; verify the concrete impl return via codegraph BEFORE typing. If impl/Protocol signatures diverge with no V1 result DTO (e.g. IRoleSessionService), LEAVE `Any` and log to coverage_gaps.
6. Document SKIP set (Ports, registry, proxy, __getattr__, passthrough).
7. roles/runtime/public/service.py — surgical: only line 1582 `-> Any` if impl return is known; the 73 hits are mostly legit Mapping envelopes.
8. Full `mypy polaris/` + full `pytest -q`; confirm zero new errors, zero `type: ignore`.

## 3. Frozen public surface
PEP 562 `__getattr__ -> Any` (19); contracts/__init__.py re-export barrels + `__all__` (F401 disabled to preserve them); frozen dataclass field annotations + to_dict/from_dict key sets; Protocol method names+param types (only returns may change, only to impl-proven types); deprecation decorator strings; `_TOOL_SPECS`/`_get_validator` module attrs. New TypeAliases on public sub-modules must be re-exported through the barrel + added to `__all__` (additive only).

## 4. Risks
- PEP 562 narrowing trap; Protocol/impl signature divergence (IRoleSessionService returns Conversation but Protocol is command-shaped — don't guess); hot-path churn in service.py with zero gate benefit; `warn_return_any` can convert a silent Any into a NEW mypy error once `-> Concrete` is set — mypy-verify each return change; barrel import cycles → prefer `TYPE_CHECKING`-guarded imports for concrete return types.
- **§8 business-code-in-contracts flags (do NOT touch this pass)**: pm_contract_store.py embeds Director/PM dispatch orchestration; _contracts.py embeds score-weight/refusal-marker (Chinese) business policy + tool-equivalence groups; tool_execution/contracts.py embeds Chinese prompt-teaching `_MISSING_ARG_HINTS`. Behavior-preserving typing pass leaves all of these in place.

## 5. Test guard & coverage gaps
Guards: tool_execution tests (test_contracts_validation_integration, test_tool_spec_registry), pm_contract_store tests, tool_calling_matrix prompt-contract test, roles/session + roles/kernel + roles/runtime contract tests, plus `mypy polaris/` strict as primary static guard.
Gaps needing characterization tests BEFORE typing: return-type of IRoleSessionService methods (runtime_checkable only checks presence); `_sanitize_json` output shapes; format_tools/format_messages return shape; pm_contract_store JsonValue round-trip; db Ports (recommend leave Any).