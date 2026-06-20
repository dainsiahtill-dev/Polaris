# polaris/cells/roles/runtime/public/service.py — class RoleRuntimeService + ~30 module-level helpers (2901 lines)

kind=god-class-split effort=large

# HUB-01 / G4 Decomposition Blueprint — roles.runtime.public.service

## Target
`polaris/cells/roles/runtime/public/service.py` — 2901 lines. `class RoleRuntimeService(IRoleRuntime)` (~1798-line class, ~36 logical methods) plus ~30 module-level helpers fusing task / session / aggregate-chat / CLI / cognitive-runtime / strategy concerns. Dominant coupling hub: 39 non-test importers + 19 test importers; re-exported widely through `public/__init__.py` (PEP 562 lazy facade).

This is the LAST big module of an in-progress lossless campaign. Already extracted siblings: `aggregate_chat.py`, `capability_commands.py`, `cli_runner.py`, `context_adapter.py`, `persistence.py`, `contracts/`. The established, proven idiom is **extract-to-sibling-then-leave `from ...sibling import X as X` re-export** for module-level functions, and (new here) **mixin composition** for class methods so attribute identity is preserved.

## Why behavior-preserving is hard here
1. Tests patch `RoleRuntimeService` at IMPORT SITES (`console_host.RoleRuntimeService`, `director_cli.RoleRuntimeService`, `role_runtime_chat.RoleRuntimeService`) and patch a bound method directly: `monkeypatch.setattr(RoleRuntimeService, 'stream_chat_turn', ...)`. => the class name and every method must remain real class attributes. Mixins are safe; class-level free-function aliases are not.
2. Two `__all__` lists (service.py 133, `public/__init__.py` 158) plus ~110 cross-module re-exports must keep resolving.
3. Lazy in-body imports break real cycles (control_plane, factory.cognitive_runtime, kernelone.cognitive.middleware, chief_engineer.blueprint, qa.audit_verdict, textual_console) — must stay inside function bodies after relocation.
4. No load-bearing `importlib`+`contextlib.suppress` dance exists (the `import importlib as importlib` line is a pure re-export). The one load-bearing soft-fail is the broad `except (...)` in `_emit_cognitive_runtime_shadow_artifacts`.

## Decomposition map (sibling modules to create)
| New sibling | Contents | Re-integration |
|---|---|---|
| `result_mapping.py` | 9 contract-mapping helpers | `from ...result_mapping import X as X` |
| `cognitive_strategy.py` | 13 pure cognitive/strategy helpers | `X as X` re-export |
| `context_gateway_wiring.py` | 3 §8 gateway helpers (keep lazy cross-cell imports) | `X as X` re-export |
| `strategy_resolution.py` | `_StrategyResolutionMixin` (6 strategy methods) | add to `RoleRuntimeService` bases |
| `aggregate_execution.py` | `_AggregateExecutionMixin` (6 aggregate methods) | add to bases |
| `cognitive_runtime_methods.py` | `_CognitiveRuntimeMixin` (emit/preflight, optionally prepare_*) | add to bases |

Final `RoleRuntimeService` retains: ctor + `_get_kernel` + kernel cache, domain-resolution classmethods, task/session exec (`execute_role_task`, `execute_role_session`, `stream_chat_turn`, `create_transaction_controller`, `get_runtime_status`), CLI delegators, persist/history delegators. Class declaration becomes `class RoleRuntimeService(_StrategyResolutionMixin, _AggregateExecutionMixin, _CognitiveRuntimeMixin, IRoleRuntime)` with `IRoleRuntime` LAST.

## Ordered atomic-green steps
See plan_steps. STEP 0 writes characterization tests first (coverage_gaps). STEPS 1-3 move pure module-level helpers (lowest risk, re-export idiom). STEPS 4-6 move method clusters via mixins (preserves attribute patching). STEPS 7-8 tidy + verify barrels. Run the full 38-file guard suite + ruff + mypy after EACH step.

## Frozen public surface
- `class RoleRuntimeService` (name, `__init__()` no-arg, all method signatures).
- Module functions: `aggregate_chat_completions`, `query_role_runtime_status`, `query_aggregate_role_plan`, `audit_aggregate_runtime_integrations`, `execute_role_session_command`, `execute_role_task_command`, `stream_role_session_command`, `create_role_cli_parser`, `run_tui`, `reset_role_runtime_service`.
- Re-exported names: `create_worker_pool`, `create_protocol_fsm`, `create_protocol_bus`, `registry`, `AgentMessage`, `MessageType`, `RoleAgent`, `ProtocolFSM`, `ProtocolType`, + the full `__all__`.
- `_DEFAULT_ROLE_RUNTIME_SERVICE` singleton identity; module `__getattr__` + `_SESSION_PUBLIC_EXPORTS` + `_load_session_public_symbol` lazy facade.
- Both `__all__` lists.

## §8 business code (FLAG ONLY — relocate verbatim, do not delete/refactor semantics)
`_read_blueprint_status_for_context` (chief_engineer.blueprint.public), `_read_qa_verdict_for_context` (qa.audit_verdict.public), `_build_context_gateway_config_for_role`, and the role/intent vocab + magic thresholds in `_build_cognitive_strategy_override`. All cross-cell calls already go through PUBLIC contracts (ACGA-compliant).

## Critical files for implementation
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/runtime/public/service.py
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/runtime/public/__init__.py
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/runtime/public/aggregate_chat.py (holds `_SESSION_PUBLIC_EXPORTS`/`_load_session_public_symbol` the module `__getattr__` depends on)
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/runtime/tests/test_aggregate_role_plan.py