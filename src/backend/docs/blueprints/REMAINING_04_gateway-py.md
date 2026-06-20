# gateway.py - RoleContextGateway (34 methods, 1751 lines); hot god-method _build_context_impl L446-778 (333 lines). Path: polaris/cells/roles/kernel/internal/context_gateway/gateway.py

kind=god-class-split effort=large

# G8 Decomposition Blueprint: RoleContextGateway / `_build_context_impl`

Target file: `polaris/cells/roles/kernel/internal/context_gateway/gateway.py` (1751 lines, class `RoleContextGateway`, 34 methods). Cell: `roles.kernel`. ACGA layer: kernel-internal (delivery/runtime reach it only via `public/service.py` and the package barrel).

## 1. Current State
A single 1751-line module holding: module-level pure helpers (L56-254), `ContextGatewayConfig` dataclass (L257-282), `DuplicateStateOwnerError` (L285-290), and the god class `RoleContextGateway` (L293-1747). The class mixes seven concerns: (a) collaborator wiring + budget computation; (b) the public async entrypoint; (c) the 333-line orchestration god method `_build_context_impl` (L446-778); (d) the 155-line `_build_projection_dict` (L1179-1333); (e) the 140-line embedded-prompt `_get_blueprint_step` (L1467-1607); (f) per-run telemetry emitters; (g) a swarm of pure strategy/window/budget helpers and signal data-source readers.

### Hot god method `_build_context_impl` (L446-778)
Linear pipeline with no sub-structure. Phases: input resolution -> projection -> override/fallback merge -> telemetry-audit -> 3-stage budget enforcement -> system_prompt prepend -> final budget guard (raises) -> emit telemetry -> assemble `ContextResult`. The 3-stage compression (L606-627), the system-prompt token reservation/prepend symmetry (L473-486 reserve, L631-634 prepend), and the final per-message diagnostic `BudgetExceededError` (L636-666) are all load-bearing and documented against live production incidents.

## 2. Public Surface (must stay byte-identical)
- **Class** `RoleContextGateway`, ctor `(profile, workspace="", config=None)`.
- **Public methods**: `async build_context(request, *, system_prompt=None) -> ContextResult`; `record_projection_outcome(*, success, tokens_used=0) -> dict`; `build_system_context(base_prompt, appendix=None) -> str`.
- **Three re-export barrels** (keep all names): package `internal/context_gateway/__init__.py`; `public/service.py`; `public/__init__.py` lazy map.
- **Monkeypatch string targets** (must keep `RoleContextGateway` resolvable on the `context_gateway` package object): `patch("polaris.cells.roles.kernel.internal.context_gateway.RoleContextGateway")` (test_llm_caller.py x3); `monkeypatch.setattr(context_gateway_module, "RoleContextGateway", _Gateway)` (capability_profile test). => If the class is moved out of `gateway.py`, `__init__.py` must re-import it so the package attribute still exists.
- **Module-level callables** `render_blueprint_overview` / `render_verdict_history`: imported from `gateway` by their tests and used as provider fallbacks; keep importable from `gateway`.
- **Config callback protocol**: `ContextGatewayConfig.blueprint_overview_provider` / `verdict_history_provider: Callable[[str,str], Any|None]`; built in `roles/runtime/public/service.py` L807 and via `ContextGatewayConfigFactory` (`kernel/core.py` L116).
- **Duck-typed `ContextRequest` getattrs**: `context_override, context_os_snapshot, strategy_receipt, strategy_override, focus, history, message, task_id, run_id, events_path`.
- **Test reach-ins freezing internal method names** (must remain delegating shims): `_process_context_override`(8), `_emit_prefix_drift_observation`(13), `_emit_context_build_observation`(6), `_apply_compression`(7), `_extract_tool_messages_from_history`(4), `_process_tool_messages_for_fallback`(3), `_messages_from_projection`(3), `_compute_enforcement_budget`(3), `_process_history`(2), `_format_context_os_snapshot`(2), `_build_projection_dict`(2), `_get_verdict_history`/`_get_blueprint_overview`/`_emergency_fallback`(1); attrs `_context_os`, `_projection_engine`, `_compression_engine`, `_enforcement_budget_tokens`.
- **`build_context` callers**: `llm_caller/caller.py` L312; `kernel/core.py` L910/1182; `kernel/turn_engine.py` L670/884; `turn_engine/engine.py` L644/935; `context/engine/public/service.py` L333; `openai_compat_provider.py` L472; `kimi_provider.py` L435.
- **`record_projection_outcome` callers**: `turn_engine/engine.py` (5 sites) + `kernel/core.py` (5 sites); asserted in `test_role_kernel_transaction_wiring.py`.

## 3. Plan (extract-to-sibling-then-leave-shim; suite green after each)
0. Characterization tests for the uncovered `_build_context_impl` branches (see Sec.6).
1. Extract module-level pure helpers -> sibling helpers module; re-import names into `gateway.py`. Keep `render_*` importable from gateway.
2. Extract telemetry trio -> `GatewayTelemetry` collaborator; leave delegating methods.
3. Extract signal data-source readers + `_estimate_signal_budget_pressure` -> `SignalSourceProvider`; preserve lazy imports + try/except; keep delegating shims for tested ones.
4. Extract `_get_blueprint_step` -> `blueprint_step_card.py` free function; keep static delegating method. Do NOT edit embedded prompt strings.
5. Extract context_override + history-fallback processing -> `ContextOverrideProcessor`; keep delegating shims.
6. Extract strategy/window/budget pure helpers -> `BudgetWindowResolver`; keep `_compute_enforcement_budget` shim.
7. Extract `_build_projection_dict` body -> `ProjectionDictBuilder`; thin delegating shim; preserve lazy imports.
8. Decompose `_build_context_impl` into same-class private sub-steps: `_resolve_assembly_inputs`, `_run_projection`, `_enforce_budget`, `_emit_assembly_telemetry`, `_assemble_result`. Statement order MUST stay identical; suite after each sub-step.
9. Final cleanup; leave the 5 backward-compat shims; run suite + mypy + ruff.

## 4. Risks
- Hot path on every role turn; budget math + statement order are load-bearing (documented live incidents).
- Monkeypatch fragility: class must stay a package-level attribute.
- Lazy/in-method imports are load-bearing for circular-import + cross-cell (`TaskRuntimeService` lazy + try/except per ACGA direction).
- 13 distinct test reach-ins freeze internal names => keep delegating shims.
- **Sec.8 violation (flag, do NOT delete)**: `_get_blueprint_step` embeds large project-specific construction-protocol prompts; `_CONTROL_PLANE_CONTEXT_KEYS` encodes app-specific knobs. Governance follow-up only.
- `get_prefix_drift_observer()` and the role-keyed `ProjectionEngine` are process-global stateful singletons; do not relocate their construction.

## 5. Test Guard
In-package: test_context_build_emission, test_prefix_drift_emission, test_blueprint_overview_render, test_role_signals, test_role_signal_freshness, test_repo_identity. Cell: test_context_gateway_fallback, test_context_gateway_integration, test_context_budget_clamp, test_transcript_leak_guard, test_turn_history_persist_parity, test_service_integration, test_role_kernel_transaction_wiring. Cross-cell: tests/contextos/test_context_overflow_guard, tests/test_llm_caller, roles/runtime/tests/test_context_gateway_asset_provider_wiring, testing/test_testing_infrastructure.

## 6. Coverage Gaps (write characterization tests FIRST)
3-stage compression ordering + guaranteed-fit last resort (L606-627); final BudgetExceededError diagnostic (L636-666); context_override vs system_prompt insert ordering; full 20-key `ContextResult.metadata` contract; history_tool_fallback branch + truncation marker; multi-turn freshness circuit-breaker; `_get_blueprint_step` skeleton/fill/fix-round string assembly.