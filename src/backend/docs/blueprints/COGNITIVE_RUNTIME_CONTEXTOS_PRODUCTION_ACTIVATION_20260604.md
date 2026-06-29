# Cognitive Runtime / Context OS Production Activation Audit - 2026-06-04

## Scope

This audit covers the production paths that decide whether Polaris actually uses
the Cognitive Runtime, Context OS, and role-runtime orchestration when it creates
target-project code.

Relevant production boundaries:

- `src/backend/polaris/cells/roles/adapters/internal/director/adapter.py`
- `src/backend/polaris/cells/roles/runtime/public/service.py`
- `src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py`
- `src/backend/polaris/cells/roles/runtime/internal/session_orchestrator.py`
- `src/backend/polaris/cells/roles/kernel/internal/development_workflow_runtime.py`
- `src/backend/polaris/delivery/cli/pm/backend.py`
- `src/backend/polaris/delivery/cli/pm/orchestration/doc_rendering.py`
- `src/backend/polaris/delivery/http/routers/role_runtime_chat.py`

## Findings

1. Director code generation still had a direct dialogue path.

   `DirectorAdapter._invoke_role_dialogue()` called `generate_role_response(...)`
   directly with `enable_cognitive=False`. That avoided duplicate cognitive
   middleware, but it also bypassed the canonical
   `RoleRuntimeService -> RoleExecutionKernel -> ContextGateway` path. As a
   result, Context OS projection, role strategy metadata, repo intelligence, and
   role-runtime Cognitive Runtime receipts were not guaranteed to participate in
   Director materialization.

2. Role runtime has the correct Context OS path, but Director was not using it.

   `RoleRuntimeService.execute_role_session()` builds an
   `ExecuteRoleSessionCommandV1`, calls `RoleExecutionKernel`, and emits Cognitive
   Runtime shadow artifacts. `RoleContextGateway` then projects context through
   `StateFirstContextOS` and records context routing metadata. This path was real
   but not the first Director generation path.

3. Development handoff could create fake materialization.

   `RoleSessionOrchestrator` can enter `HANDOFF_DEVELOPMENT` and instantiate
   `DevelopmentWorkflowRuntime`. Its previous default `_execute_patch()` wrote
   the natural-language intent to `.polaris/development_patch.md`. If that path
   ran in production, Polaris could report a tool write while producing no useful
   target-project code.

4. Non-Director workflow adapters also bypassed `RoleRuntimeService`.

   PM, Architect, Chief Engineer, and QA adapters each had their own role LLM
   path. PM was especially risky because it called the lower-level provider
   runtime directly: real LLM invocation could succeed while the role-runtime
   session, Context OS snapshot projection, strategy receipts, and cognitive
   runtime receipts never participated in the production workflow. Architect,
   Chief Engineer, and QA used direct dialogue calls, which were better than
   provider bypass but still did not make the adapter boundary itself
   runtime-first.

5. Director execution code-generation bridge still used direct dialogue.

   `director.execution.internal.code_generation_engine` described its
   `_invoke_director_role_response()` path as a canonical Director role runtime,
   but the implementation called `generate_role_response(...)` directly. That
   meant the lower-level proposal/apply bridge could still create target-project
   code without first entering `RoleRuntimeService.execute_role_session()`.

6. Director adapter fallback was too broad.

   The Director adapter caught `RuntimeError` from the role runtime and then
   fell back to legacy dialogue/direct-provider code. That could hide a real
   runtime execution failure behind an older successful path.

7. Projection lab PM normalization bypassed Context OS and leaked Polaris deps.

   `factory.pipeline.projection_lab` is part of the `projection_generate` target
   project creation path. Its PM requirement-normalization step directly called
   provider runtime, so Context OS was bypassed. The generated project renderer
   also imported `polaris.kernelone.utils.time_utils` into target-project code,
   making the generated project non-portable outside the Polaris repository.

8. PM planning backends bypassed or weakened `RoleRuntimeService`.

   `orchestration.pm_planning.internal.pipeline_ports.CellPmInvokePort` and the
   PM CLI backend had lower-level `codex` / `ollama` direct invoke branches.
   This meant task-contract generation could reach a configured LLM while
   bypassing role runtime sessions, Context OS projection, and provider policy
   evidence.

9. PM CLI, PM docs, HTTP chat, and diagnostic probe entrypoints also exposed
   legacy bypasses.

   The PM CLI generic backend, Architect-authored PM document rendering,
   non-streaming HTTP role chat, non-streaming PM chat, and `agentic_eval`
   role probe all reached lower-level dialogue/provider paths. These were not
   all code materialization paths, but they made runtime evidence ambiguous and
   could prove a configured LLM was reachable without proving Context OS and
   Cognitive Runtime participated.

10. Role-runtime Cognitive Runtime artifacts were shadow-only.

   `RoleRuntimeService._emit_cognitive_runtime_shadow_artifacts()` wrote
   receipts/handoff packs as a best-effort side effect. It returned no evidence,
   used default feature-flag resolution instead of the command context/metadata,
   and only logged failures. A production caller could therefore claim
   `role_runtime_required=True` while losing Cognitive Runtime evidence.

11. PM compatibility wrapper could still re-inject direct provider assumptions.

   `delivery/cli/loop-pm.py` still imported `invoke_ollama` and monkeypatched PM
   backend internals after the canonical PM backend moved to RoleRuntimeService.
   That made old direct-provider assumptions available to tests and future
   callers.

12. Frontend runtime projection compatibility hid legacy status provenance.

   `runtime/projectionCompat.ts` converted legacy flat and nested websocket
   status payloads into `RuntimeProjectionPayload` and stamped them with the
   current frontend time. Without explicit provenance, the UI could treat
   compatibility-derived legacy state as canonical backend runtime projection
   evidence.

13. The term `legacy` appears in several compatibility layers.

   Not every `legacy` string is an active legacy executor. WebSocket v1 channels,
   old HTTP aliases, config migration fields, frontend compatibility provenance,
   and task id mappings still carry compatibility labels. Production role
   adapters no longer retain an executable legacy dialogue fallback.

14. Context override mixed control-plane fields into prompt-visible data plane.

   `RoleContextGateway._process_context_override()` flattened the full
   `context_override` dict into a system message. That meant fields such as
   `context_os_snapshot`, provider policy, runtime-required flags, and internal
   TransactionKernel payloads could enter LLM messages instead of remaining
   control-plane/audit state.

15. Cognitive Runtime `MAINLINE` did not influence the current turn before LLM.

   `resolve_cognitive_runtime_mode()` documented `MAINLINE` as influencing
   runtime decisions, but `RoleRuntimeService` only used the mode after the LLM
   turn while recording receipts/handoff packs. This made `MAINLINE` materially
   equivalent to shadow for current-turn prompt context.

16. HTTP streaming chat still used the dialogue compatibility facade.

   `/v2/role/{role}/chat/stream`, `/v2/pm/chat/stream`, and
   `/v2/roles/sessions/{session_id}/messages/stream` imported
   `generate_role_response_streaming(...)`. The facade currently delegates into
   RoleRuntimeService, but keeping HTTP production routers wired to the dialogue
   cell made runtime enforcement ambiguous and left an easy reintroduction point
   for non-runtime LLM calls.

17. PromptBuilder retained unreceipted fail-open fallbacks.

   Chunk assembly and Tri-Axis composition failures could fall back to direct
   string joining or template-mode recursion. Those fallbacks produced prompts
   without the final request receipt used by Context OS/runtime audit.

18. `llm.dialogue` public role facade still invoked the internal dialogue path.

   `LlmDialogueService.invoke_role_dialogue()` called
   `generate_role_response(...)`, which directly constructed
   `RoleExecutionKernel` and therefore bypassed the session-level
   `RoleRuntimeService` boundary. Even though HTTP role chat had already moved
   to RoleRuntime, this public facade remained an executable old entrypoint for
   future callers.

## Changes

1. Director generation is runtime-first.

   `DirectorAdapter._invoke_role_dialogue()` now calls
   `RoleRuntimeService.execute_role_session()` first with:

   - role `director`
   - domain `code`
   - `stream=False`
   - original task/run context
   - metadata flags `role_runtime_required=True`,
     `cognitive_runtime_required=True`, and `context_os_expected=True`

   The normalized response preserves runtime metadata, artifacts, tool calls, and
   execution stats. Legacy dialogue fallback is no longer a production default:
   runtime boundary construction failures now fail closed.

2. Development handoff is fail-closed.

   `DevelopmentWorkflowRuntime._execute_patch()` no longer writes a natural
   language intent to `.polaris/development_patch.md`. It only executes:

   - native file tool calls (`write_file`, `edit_file`, `append_to_file`,
     `delete_file`)
   - `PATCH_FILE` / `FILE` protocol operations parsed through the existing
     KernelOne toolkit

   Plain intent now returns `development_handoff_requires_concrete_patch`, so the
   handoff cannot fake a code change.

3. Workflow role adapters are runtime-first.

   PM, Architect, Chief Engineer, and QA now call
   `roles.adapters.internal.runtime_dialogue.invoke_role_runtime_first()`. The
   helper constructs an `ExecuteRoleSessionCommandV1` and calls
   `RoleRuntimeService.execute_role_session()` with `stream=False`, role/task/run
   context, and metadata flags `role_runtime_required=True`,
   `cognitive_runtime_required=True`, and `context_os_expected=True`.

   The helper has no executable legacy dialogue fallback. Runtime boundary
   construction failures fail closed, and runtime execution failures are not
   hidden by any older dialogue/provider path.

4. Director execution code-generation bridge is runtime-first.

   `CodeGenerationEngine._invoke_director_role_response()` now constructs an
   `ExecuteRoleSessionCommandV1` with `role=director`, `domain=code`,
   `stream=False`, and code-generation metadata, then calls
   `RoleRuntimeService.execute_role_session()` directly. No legacy dialogue
   fallback is used on this bridge.

5. Director adapter runtime and boundary failures are no longer hidden by fallback.

   `DirectorAdapter._invoke_role_dialogue()` now fails closed for runtime
   execution failures and runtime-boundary construction failures. The old
   direct-provider/dialogue fallback has been removed from this production
   adapter.

6. Projection lab uses PM role runtime and emits portable target code.

   `FactoryProjectionLabService._normalize_requirement()` now constructs a PM
   `ExecuteRoleSessionCommandV1` with `domain=document`, `stream=False`, and
   `context_os_expected=True`, then calls `RoleRuntimeService.execute_role_session()`.
   The JSON CLI and resource HTTP renderers now use target-project-local
   standard-library time helpers instead of importing Polaris modules.

7. PM planning backends are runtime-first.

   `CellPmInvokePort.invoke()` now uses PM `ExecuteRoleSessionCommandV1` with
   `domain=document`, `stream=False`, and `context_os_expected=True` for all PM
   planning backends. Explicit `codex` / `ollama` backend selections are
   represented as RoleRuntime provider allowlists, not direct process/provider
   invokes.

8. PM CLI, PM docs, non-streaming HTTP chat, and agentic-eval probe are
   runtime-first.

   - `invoke_pm_backend(...)` now calls PM
     `RoleRuntimeService.execute_role_session()` for `generic`, `codex`, and
     `ollama`; explicit `codex` / `ollama` selections become provider allowlist
     policy in the RoleRuntime command.
   - PM document rendering now asks Architect through
     `ExecuteRoleSessionCommandV1(domain=document, stream=False)`.
   - `/v2/pm/chat` and `/v2/role/{role}/chat` now share
     `role_runtime_chat.execute_role_chat_nonstreaming()`.
   - `agentic_eval` role probes now use `RoleRuntimeService` instead of direct
     dialogue.
   - Frozen zero-consumer ChiefEngineer/Director LLM-tools compatibility files
     now fail closed instead of invoking providers directly.

9. Cognitive Runtime evidence is now a required RoleRuntime contract on
   production role-runtime paths.

   `RoleRuntimeService.execute_role_task()` and `execute_role_session()` now
   return `metadata.cognitive_runtime_evidence` after recording a runtime
   receipt and, when a session exists, exporting a handoff pack. If
   `cognitive_runtime_required=True`, disabled Cognitive Runtime or failed
   receipt/handoff writes raise instead of being swallowed as warnings. PM,
   PM planning, Director codegen, PM docs, projection lab, audit LLM, role chat,
   runtime dialogue, and Director adapter commands now set
   `cognitive_runtime_required=True` alongside `role_runtime_required=True`.

10. PM and Worker legacy wrappers were hardened.

   `delivery/cli/loop-pm.py` no longer imports `invoke_ollama` or monkeypatches
   PM backend internals. Worker progress markers now include a UTF-8 content hash
   in addition to size/mtime, preventing same-size rapid rewrites from being
   treated as completed work. Worker legacy `_invoke_ollama` and
   `_invoke_generation_response` wrappers now fail closed locally.

11. Runtime projection compatibility is now observable.

   `RuntimeProjectionPayload` now includes optional `projection_source` and
   `provenance`. Canonical, empty, partial, merged, legacy flat, and legacy
   nested projections are explicitly labeled. `toCanonicalProjection()` marks
   migration conversions as `transformed=true`, records the migration reason,
   and lists the migration fields that triggered the conversion. The same
   provenance is mirrored in `snapshot_derived` for migration consumers.

12. ContextGateway now separates control-plane from prompt-visible context.

   `_process_context_override()` now drops known control-plane keys and internal
   `_transaction_kernel_*` keys before creating a prompt-visible system message.
   Safe explicit data-plane context remains injectable, but Context OS snapshots,
   provider policy, runtime-required flags, session/run identifiers, metadata,
   and internal tool forcing payloads no longer leak into LLM messages.

13. Cognitive Runtime `MAINLINE` now runs before the LLM turn.

   `RoleRuntimeService` now prepares task/session requests through an async
   preflight step. In `MAINLINE`, it invokes KernelOne `CognitiveMiddleware`
   before `kernel.run`, fails closed if the cognitive layer is unavailable or
   blocks the turn, and injects a sanitized `cognitive_guidance` context entry
   for the current LLM turn. Final result metadata includes
   `cognitive_runtime_preflight` so audits can distinguish shadow observation
   from mainline application. The default runtime mode is now `mainline`;
   `shadow` remains an explicit grey-release mode, and `off` remains an
   explicit fail-closed escape hatch for required runtime paths.

   The session orchestrator controller path now calls the same async preflight
   before constructing `TransactionKernel`, so orchestrated unattended
   development turns cannot bypass current-turn cognitive guidance.

14. HTTP role/PM/session streaming now enters RoleRuntime directly.

   `role_runtime_chat.execute_role_chat_streaming()` constructs
   `ExecuteRoleSessionCommandV1(stream=True)` and maps RoleRuntime stream events
   to the existing SSE queue format. The role chat, PM chat, and role-session
   streaming routers no longer import or patch `generate_role_response_streaming`.
   During this migration, `role_session.send_message_stream()` also fixed a real
   async bug by awaiting `SessionContinuityEngine.project(...)`.

15. PromptBuilder prompt assembly is now fail-closed.

   Prompt chunk assembly and Tri-Axis composition must succeed and emit their
   request receipt. Failures now surface as `RuntimeError` instead of falling
   back to unreceipted string-joined prompt content.

16. WorkerExecutor success now depends on real artifacts and canonical codegen.

   Director tasking code generation now loads from
   `polaris.cells.director.tasking.internal.code_generation_engine`; the old
   `execution.internal.code_generation_engine` module is a compatibility
   re-export. WorkerExecutor now rejects generated-file receipts that do not
   exist on disk or fail `scan_workspace_artifact_quality(relative_paths=...)`.
   The `file_creation` path delegates to runtime codegen instead of writing
   `Created by Polaris` placeholders, and bootstrap missing-target backfill now
   uses runtime codegen or fails closed.

17. Cognitive Runtime MAINLINE tool governance now affects the current turn.

   `CognitiveMiddleware.process()` now preserves `CognitiveResponse.blocked_tools`.
   `RoleRuntimeService._apply_cognitive_runtime_preflight()` copies those tool
   denials into `metadata.cognitive_tool_policy` and the preflight receipt.
   `RoleExecutionKernel` then filters blocked tools from TransactionKernel native
   tool schemas and rejects blocked tool execution before dispatching to the
   executor. This makes cognitive governance an execution constraint, not just a
   prompt hint or audit note.

18. `llm.dialogue` public role facade now delegates to RoleRuntime.

   `LlmDialogueService.invoke_role_dialogue()` now constructs
   `ExecuteRoleSessionCommandV1` and calls
   `RoleRuntimeService.execute_role_session()` with `role_runtime_required=True`,
   `cognitive_runtime_required=True`, `context_os_expected=True`, and
   `fallback_policy=fail_closed`. Production metadata now uses
   `runtime_fallback_used=False` rather than `legacy_fallback_used=False`, so
   logs do not imply an old executor is still in use.

19. Cognitive Runtime MAINLINE now influences RoleRuntime strategy identity.

   MAINLINE preflight now derives a conservative current-turn strategy override
   from cognitive guidance. Verification-heavy, code-generation, root-cause, or
   uncertain turns expand exploration/read escalation and delay compaction. The
   override is merged into `StrategyRegistry.resolve(...)`, so stream
   fingerprint events and strategy receipts carry a profile hash that reflects
   cognitive governance. `stream_chat_turn()` now runs cognitive preflight before
   emitting the first fingerprint event, instead of resolving strategy before
   the cognitive layer had a chance to act.

## Assumptions

- A1: Director materialization should use `roles.runtime`; production runtime
  boundary failures must fail closed rather than falling back to legacy dialogue.
- A2: A natural-language development handoff is not a patch and must not be
  written as a placeholder file.
- A3: Compatibility labels such as `legacy_subscriptions` are acceptable only
  when they do not route target-project code generation around runtime contracts.
- A4: Workflow adapters must not bypass `RoleRuntimeService` just because they
  can reach a real configured LLM provider.
- A5: Runtime execution failures are not equivalent to runtime boundary
  unavailability and must not trigger legacy fallback.
- A6: Generated target projects must not import Polaris internals just to run
  their own application code or tests.
- A7: PM task-contract generation must use role runtime on its default/generic
  backend, not direct provider runtime.
- A8: PM CLI/document/chat/diagnostic entrypoints must not make role LLM calls
  through provider runtime or direct dialogue when a role-runtime boundary exists.
- A9: Audit LLM local-only provider preference must be enforced through
  RoleRuntime provider allow/block constraints, not provider-runtime direct
  invocation.
- A10: `role_runtime_required=True` must be paired with
  `cognitive_runtime_required=True` on production RoleRuntime paths.
- A11: Compatibility wrappers may retain legacy names only when they cannot
  invoke a provider, call legacy dialogue, or materialize target code outside
  RoleRuntime.
- A12: Frontend compatibility projections must expose their source so UI/runtime
  audits do not confuse legacy status with canonical Runtime/Context OS evidence.
- A13: Context override must not inject control-plane runtime state into LLM
  prompt-visible data plane.
- A14: Cognitive Runtime `MAINLINE` must affect the current RoleRuntime turn
  before LLM invocation or fail closed when it cannot.
- A15: HTTP role/PM/session streaming must not depend on the dialogue
  compatibility facade when a RoleRuntime streaming boundary exists.
- A16: Prompt assembly must fail closed when it cannot produce the receipt used
  by Context OS/runtime audit.
- A17: WorkerExecutor must not treat `files_created` receipts as success unless
  referenced files exist on disk and pass the shared artifact quality gate.
- A18: Director `file_creation` and bootstrap target backfill must not write
  deterministic placeholder code; missing target files must go through runtime
  code generation or fail closed.
- A19: Director tasking code generation must load from
  `polaris.cells.director.tasking.internal.code_generation_engine`; the old
  `execution.internal` path may only re-export compatibility symbols.
- A20: Cognitive Runtime `MAINLINE` tool denials must affect the current turn's
  exposed tool schemas and actual tool execution, not only metadata or prompt
  text.
- A21: Public role dialogue compatibility facades may remain importable only if
  their executable path delegates to RoleRuntime and never emits
  `legacy_fallback_used` production metadata.
- A22: Cognitive Runtime `MAINLINE` strategy guidance must be applied before
  stream strategy fingerprint/receipt creation, not only after the first event.
- A23: Cognitive Runtime `MAINLINE` strategy overrides must be consumed by
  ContextGateway before LLM prompt assembly, not only stored as runtime
  metadata.
- A24: ContextGateway decision hints must reduce the current native LLM tool
  surface in both streaming and non-streaming RoleExecutionKernel paths.
- A25: The narrowed tool surface must also be enforced at direct
  `ToolBatchExecutor` dispatch time so hidden tools cannot be called through a
  stale or compatibility decision branch.
- A26: Context OS production/runtime code must read structured policy subobjects
  (`context_window`, `window_size`, `artifact`, `token_budget`,
  `attention_runtime`, `collection_limits`) instead of deprecated compatibility
  accessors. Compatibility accessors may remain only for external migration and
  explicit compatibility tests.
- A27: RoleExecutionKernel must not expose an environment-variable downgrade
  path from the canonical TransactionKernel execution path back to TurnEngine.
- A28: ExplorationWorkflowRuntime handoff failures must fail closed and surface
  as turn errors instead of successful handoff completions.
- A29: The philosophical `Cognitive Lifeform` / `认知生命体` metaphor must not
  remain in production Python comments, docstrings, or CLI text. Production code
  must use engineering terms such as cognitive capabilities, cognitive pipeline,
  and cognitive middleware.

## Verification

Focused checks run during this audit:

```text
python -m ruff check src/backend/polaris/cells/roles/adapters/internal/director/adapter.py src/backend/polaris/cells/roles/adapters/tests/test_director_adapter_pure.py
python -m mypy src/backend/polaris/cells/roles/adapters/internal/director/adapter.py src/backend/polaris/cells/roles/adapters/tests/test_director_adapter_pure.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/roles/adapters/tests/test_director_adapter_pure.py::TestDirectorRuntimeFallback src/backend/polaris/cells/roles/adapters/tests/test_director_adapter_pure.py::TestDirectorFailureClosure::test_execute_rejects_workspace_diff_without_write_tool_receipt -q
python -m ruff check src/backend/polaris/cells/roles/kernel/internal/development_workflow_runtime.py src/backend/polaris/cells/roles/kernel/internal/tests/test_development_workflow_runtime.py
python -m mypy src/backend/polaris/cells/roles/kernel/internal/development_workflow_runtime.py src/backend/polaris/cells/roles/kernel/internal/tests/test_development_workflow_runtime.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/roles/kernel/internal/tests/test_development_workflow_runtime.py -q
python -m ruff check src/backend/polaris/cells/roles/adapters/internal/runtime_dialogue.py src/backend/polaris/cells/roles/adapters/internal/pm_adapter.py src/backend/polaris/cells/roles/adapters/internal/architect_adapter.py src/backend/polaris/cells/roles/adapters/internal/chief_engineer_adapter.py src/backend/polaris/cells/roles/adapters/internal/qa_adapter.py src/backend/polaris/cells/roles/adapters/tests/test_runtime_dialogue.py src/backend/polaris/tests/test_roles_engine_dialogue.py --fix
python -m ruff format src/backend/polaris/cells/roles/adapters/internal/runtime_dialogue.py src/backend/polaris/cells/roles/adapters/internal/pm_adapter.py src/backend/polaris/cells/roles/adapters/internal/architect_adapter.py src/backend/polaris/cells/roles/adapters/internal/chief_engineer_adapter.py src/backend/polaris/cells/roles/adapters/internal/qa_adapter.py src/backend/polaris/cells/roles/adapters/tests/test_runtime_dialogue.py src/backend/polaris/tests/test_roles_engine_dialogue.py
python -m mypy src/backend/polaris/cells/roles/adapters/internal/runtime_dialogue.py src/backend/polaris/cells/roles/adapters/internal/pm_adapter.py src/backend/polaris/cells/roles/adapters/internal/architect_adapter.py src/backend/polaris/cells/roles/adapters/internal/chief_engineer_adapter.py src/backend/polaris/cells/roles/adapters/internal/qa_adapter.py src/backend/polaris/cells/roles/adapters/tests/test_runtime_dialogue.py src/backend/polaris/tests/test_roles_engine_dialogue.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/roles/adapters/tests/test_runtime_dialogue.py src/backend/polaris/tests/test_roles_engine_dialogue.py src/backend/polaris/cells/roles/adapters/tests/test_pm_adapter_pure.py src/backend/polaris/cells/roles/adapters/tests/test_architect_adapter_pure.py src/backend/polaris/cells/roles/adapters/tests/test_qa_adapter_pure.py src/backend/polaris/cells/roles/adapters/tests/test_director_adapter_pure.py::TestDirectorRuntimeFallback -q
python -m ruff check src/backend/polaris/cells/director/execution/internal/code_generation_engine.py src/backend/polaris/cells/director/execution/tests/test_code_generation.py --fix
python -m ruff format src/backend/polaris/cells/director/execution/internal/code_generation_engine.py src/backend/polaris/cells/director/execution/tests/test_code_generation.py
python -m mypy src/backend/polaris/cells/director/execution/internal/code_generation_engine.py src/backend/polaris/cells/director/execution/tests/test_code_generation.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/director/execution/tests/test_code_generation.py::TestBlockedEntryPoints::test_runtime_codegen_invokes_director_in_proposal_mode -q
python -m ruff check src/backend/polaris/cells/factory/pipeline/internal/projection_lab.py src/backend/polaris/cells/factory/pipeline/internal/json_cli_app_renderer.py src/backend/polaris/cells/factory/pipeline/internal/resource_http_service_renderer.py src/backend/polaris/cells/factory/pipeline/tests/test_projection_lab.py --fix
python -m ruff format src/backend/polaris/cells/factory/pipeline/internal/projection_lab.py src/backend/polaris/cells/factory/pipeline/internal/json_cli_app_renderer.py src/backend/polaris/cells/factory/pipeline/internal/resource_http_service_renderer.py src/backend/polaris/cells/factory/pipeline/tests/test_projection_lab.py
python -m mypy src/backend/polaris/cells/factory/pipeline/internal/projection_lab.py src/backend/polaris/cells/factory/pipeline/internal/json_cli_app_renderer.py src/backend/polaris/cells/factory/pipeline/internal/resource_http_service_renderer.py src/backend/polaris/cells/factory/pipeline/tests/test_projection_lab.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/factory/pipeline/tests/test_projection_lab.py -q
python -m ruff check src/backend/polaris/cells/orchestration/pm_planning/internal/pipeline_ports.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_pipeline_ports.py --fix
python -m ruff format src/backend/polaris/cells/orchestration/pm_planning/internal/pipeline_ports.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_pipeline_ports.py
python -m mypy src/backend/polaris/cells/orchestration/pm_planning/internal/pipeline_ports.py src/backend/polaris/cells/orchestration/pm_planning/tests/test_pipeline_ports.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/orchestration/pm_planning/tests/test_pipeline_ports.py -q
python -m ruff check src/backend/polaris/delivery/cli/pm/backend.py src/backend/polaris/delivery/cli/pm/orchestration/doc_rendering.py src/backend/polaris/delivery/http/routers/role_runtime_chat.py src/backend/polaris/delivery/http/routers/pm_chat.py src/backend/polaris/delivery/http/routers/role_chat.py src/backend/polaris/delivery/cli/agentic_eval.py src/backend/polaris/delivery/cli/pm/chief_engineer_llm_tools.py src/backend/polaris/delivery/cli/director/director_llm_tools.py src/backend/polaris/tests/test_loop_pm_backend_resolution.py src/backend/polaris/tests/test_pm_doc_rendering_role_runtime.py src/backend/polaris/tests/test_role_runtime_chat_router_helper.py src/backend/polaris/tests/test_roles_engine_dialogue.py --fix
python -m ruff format src/backend/polaris/delivery/cli/pm/backend.py src/backend/polaris/delivery/cli/pm/orchestration/doc_rendering.py src/backend/polaris/delivery/http/routers/role_runtime_chat.py src/backend/polaris/delivery/http/routers/pm_chat.py src/backend/polaris/delivery/http/routers/role_chat.py src/backend/polaris/delivery/cli/agentic_eval.py src/backend/polaris/delivery/cli/pm/chief_engineer_llm_tools.py src/backend/polaris/delivery/cli/director/director_llm_tools.py src/backend/polaris/tests/test_loop_pm_backend_resolution.py src/backend/polaris/tests/test_pm_doc_rendering_role_runtime.py src/backend/polaris/tests/test_role_runtime_chat_router_helper.py src/backend/polaris/tests/test_roles_engine_dialogue.py
python -m mypy src/backend/polaris/delivery/cli/pm/backend.py src/backend/polaris/delivery/cli/pm/orchestration/doc_rendering.py src/backend/polaris/delivery/http/routers/role_runtime_chat.py src/backend/polaris/delivery/http/routers/pm_chat.py src/backend/polaris/delivery/http/routers/role_chat.py src/backend/polaris/delivery/cli/agentic_eval.py src/backend/polaris/delivery/cli/pm/chief_engineer_llm_tools.py src/backend/polaris/delivery/cli/director/director_llm_tools.py src/backend/polaris/tests/test_loop_pm_backend_resolution.py src/backend/polaris/tests/test_pm_doc_rendering_role_runtime.py src/backend/polaris/tests/test_role_runtime_chat_router_helper.py src/backend/polaris/tests/test_roles_engine_dialogue.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/tests/test_loop_pm_backend_resolution.py src/backend/polaris/tests/test_pm_doc_rendering_role_runtime.py src/backend/polaris/tests/test_role_runtime_chat_router_helper.py src/backend/polaris/tests/test_roles_engine_dialogue.py -q
$env:PYTHONPATH='src/backend'; pytest src/backend/polaris/tests/test_audit_llm_runtime.py -q
$env:PYTHONPATH='src/backend'; pytest src/backend/polaris/tests/unit/cells/test_audit/test_task_audit_llm_binding.py -q
$env:PYTHONPATH='src/backend'; pytest src/backend/polaris/cells/orchestration/pm_planning/tests/test_pipeline_ports.py src/backend/polaris/tests/test_loop_pm_backend_resolution.py -q
$env:PYTHONPATH='src/backend'; pytest src/backend/polaris/tests/llm/engine/test_executor_resilience_fixes.py::TestProviderTypePolicy -q
$env:PYTHONPATH='src/backend'; pytest src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py::TestRoleRuntimeServiceStrategy::test_build_session_request_copies_provider_policy_to_context_override src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py::TestRoleRuntimeServiceStrategy::test_build_task_request_copies_provider_policy_to_context_override -q
$env:PYTHONPATH='src/backend'; pytest src/backend/polaris/cells/roles/kernel/tests/test_turn_history_persist_parity.py::TestPhase3ContextOSDirectIntegration::test_kernel_build_context_extracts_context_os_snapshot -q
$env:PYTHONPATH='src/backend'; pytest src/backend/polaris/tests/test_roles_engine_dialogue.py::TestAdapterCallsRuntimeBoundary -q
$env:PYTHONPATH=(Resolve-Path src/backend).Path; python -m pytest src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py src/backend/polaris/cells/director/execution/tests/test_code_generation.py src/backend/polaris/tests/test_worker_executor_tech_stack.py -q
python -m ruff check src/backend/polaris/cells/director/tasking/internal/worker_executor.py src/backend/polaris/cells/director/tasking/internal/code_generation_engine.py src/backend/polaris/cells/director/execution/internal/code_generation_engine.py src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py --fix
python -m ruff format src/backend/polaris/cells/director/tasking/internal/worker_executor.py src/backend/polaris/cells/director/tasking/internal/code_generation_engine.py src/backend/polaris/cells/director/execution/internal/code_generation_engine.py src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py
python -m mypy src/backend/polaris/cells/director/tasking/internal/worker_executor.py src/backend/polaris/cells/director/tasking/internal/code_generation_engine.py src/backend/polaris/cells/director/execution/internal/code_generation_engine.py src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py
$env:PYTHONPATH=(Resolve-Path src/backend).Path; python -m pytest src/backend/polaris/kernelone/cognitive/tests/test_middleware.py src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py -q
python -m ruff check src/backend/polaris/kernelone/cognitive/middleware.py src/backend/polaris/kernelone/cognitive/tests/test_middleware.py src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py src/backend/polaris/cells/roles/kernel/internal/kernel/core.py src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py
python -m ruff format src/backend/polaris/kernelone/cognitive/middleware.py src/backend/polaris/kernelone/cognitive/tests/test_middleware.py src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py src/backend/polaris/cells/roles/kernel/internal/kernel/core.py src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py
python -m mypy src/backend/polaris/kernelone/cognitive/middleware.py src/backend/polaris/kernelone/cognitive/tests/test_middleware.py src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py src/backend/polaris/cells/roles/kernel/internal/kernel/core.py src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/tests/test_llm_dialogue_public_service.py src/backend/polaris/tests/test_loop_pm_backend_resolution.py src/backend/polaris/tests/test_pm_doc_rendering_role_runtime.py src/backend/polaris/cells/director/execution/tests/test_code_generation.py -q
python -m ruff check src/backend/polaris/cells/llm/dialogue/public/service.py src/backend/polaris/tests/test_llm_dialogue_public_service.py src/backend/polaris/tests/test_loop_pm_backend_resolution.py src/backend/polaris/tests/test_pm_doc_rendering_role_runtime.py src/backend/polaris/cells/director/execution/tests/test_code_generation.py src/backend/polaris/cells/director/tasking/internal/code_generation_engine.py src/backend/polaris/delivery/cli/pm/backend.py src/backend/polaris/delivery/cli/pm/orchestration/doc_rendering.py src/backend/polaris/delivery/cli/agentic_eval.py
python -m ruff format src/backend/polaris/cells/llm/dialogue/public/service.py src/backend/polaris/tests/test_llm_dialogue_public_service.py src/backend/polaris/tests/test_loop_pm_backend_resolution.py src/backend/polaris/tests/test_pm_doc_rendering_role_runtime.py src/backend/polaris/cells/director/execution/tests/test_code_generation.py src/backend/polaris/cells/director/tasking/internal/code_generation_engine.py src/backend/polaris/delivery/cli/pm/backend.py src/backend/polaris/delivery/cli/pm/orchestration/doc_rendering.py src/backend/polaris/delivery/cli/agentic_eval.py
$env:PYTHONPATH='src/backend'; python -m mypy src/backend/polaris/cells/llm/dialogue/public/service.py src/backend/polaris/tests/test_llm_dialogue_public_service.py src/backend/polaris/delivery/cli/pm/backend.py src/backend/polaris/delivery/cli/pm/orchestration/doc_rendering.py src/backend/polaris/delivery/cli/agentic_eval.py src/backend/polaris/cells/director/tasking/internal/code_generation_engine.py
python -m ruff check src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py --fix
python -m ruff format src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py
$env:PYTHONPATH='src/backend'; python -m mypy src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py -q
python -m ruff check src/backend/polaris/kernelone/context/contracts.py src/backend/polaris/cells/roles/kernel/internal/tool_loop_controller.py src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py src/backend/polaris/cells/roles/kernel/tests/test_tool_loop_controller.py src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py --fix
python -m ruff format src/backend/polaris/kernelone/context/contracts.py src/backend/polaris/cells/roles/kernel/internal/tool_loop_controller.py src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py src/backend/polaris/cells/roles/kernel/tests/test_tool_loop_controller.py src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py
$env:PYTHONPATH='src/backend'; python -m mypy src/backend/polaris/kernelone/context/contracts.py src/backend/polaris/cells/roles/kernel/internal/tool_loop_controller.py src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py src/backend/polaris/cells/roles/kernel/tests/test_tool_loop_controller.py src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/roles/kernel/tests/test_tool_loop_controller.py src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py -q
python -m ruff check src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py src/backend/polaris/cells/roles/kernel/internal/kernel/core.py src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py --fix
python -m ruff format src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py src/backend/polaris/cells/roles/kernel/internal/kernel/core.py src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py
$env:PYTHONPATH='src/backend'; python -m mypy src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py src/backend/polaris/cells/roles/kernel/internal/kernel/core.py src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py -q
python -m ruff check src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py src/backend/polaris/cells/roles/kernel/internal/transaction/stream_orchestrator.py src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py --fix
python -m ruff format src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py src/backend/polaris/cells/roles/kernel/internal/transaction/stream_orchestrator.py src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py
$env:PYTHONPATH='src/backend'; python -m mypy src/backend/polaris/cells/roles/kernel/internal/turn_transaction_controller.py src/backend/polaris/cells/roles/kernel/internal/transaction/stream_orchestrator.py src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py -k "narrowed_tool_names" -q
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py -q
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py -q
python -m ruff check src/backend/polaris/kernelone/context/context_os/runtime/engine.py src/backend/polaris/kernelone/context/context_os/runtime/state.py src/backend/polaris/kernelone/context/context_os/pipeline/stages.py src/backend/polaris/kernelone/context/context_os/pipeline/attention_aware_stages.py src/backend/polaris/kernelone/context/context_os/pipeline/phase_aware_stages.py src/backend/polaris/kernelone/context/context_os/runtime/scheduler.py src/backend/polaris/kernelone/context/context_os/helpers.py src/backend/polaris/kernelone/context/context_os/domain_adapters/generic.py src/backend/polaris/cells/roles/runtime/public/persistence.py src/backend/polaris/kernelone/context/session_continuity.py --fix
python -m ruff format src/backend/polaris/kernelone/context/context_os/runtime/engine.py src/backend/polaris/kernelone/context/context_os/runtime/state.py src/backend/polaris/kernelone/context/context_os/pipeline/stages.py src/backend/polaris/kernelone/context/context_os/pipeline/attention_aware_stages.py src/backend/polaris/kernelone/context/context_os/pipeline/phase_aware_stages.py src/backend/polaris/kernelone/context/context_os/runtime/scheduler.py src/backend/polaris/kernelone/context/context_os/helpers.py src/backend/polaris/kernelone/context/context_os/domain_adapters/generic.py src/backend/polaris/cells/roles/runtime/public/persistence.py src/backend/polaris/kernelone/context/session_continuity.py
$env:PYTHONPATH='src/backend'; python -m mypy src/backend/polaris/kernelone/context/context_os/runtime/engine.py src/backend/polaris/kernelone/context/context_os/runtime/state.py src/backend/polaris/kernelone/context/context_os/pipeline/stages.py src/backend/polaris/kernelone/context/context_os/pipeline/attention_aware_stages.py src/backend/polaris/kernelone/context/context_os/pipeline/phase_aware_stages.py src/backend/polaris/kernelone/context/context_os/runtime/scheduler.py src/backend/polaris/kernelone/context/context_os/helpers.py src/backend/polaris/kernelone/context/context_os/domain_adapters/generic.py src/backend/polaris/cells/roles/runtime/public/persistence.py src/backend/polaris/kernelone/context/session_continuity.py
$env:PYTHONPATH='src/backend'; python -m pytest src/backend/polaris/kernelone/context/tests/test_attention_runtime.py src/backend/polaris/kernelone/context/tests/test_attention_runtime_boundaries.py src/backend/polaris/kernelone/context/tests/test_budget_alignment.py src/backend/polaris/kernelone/context/tests/test_context_os_policies.py -q
```

Results:

- Director runtime-first focused tests: `4 passed`
- Development workflow runtime tests: `16 passed`
- Adapter runtime-dialogue and pure regression tests: `166 passed`
- Director execution code-generation runtime bridge test: `1 passed`
- Projection lab runtime normalization and portable renderer tests: `3 passed`
- PM planning pipeline port and PM CLI backend tests: `70 passed`
- Audit LLM RoleRuntime binding tests: `23 passed`
- Provider policy executor tests: `2 passed`
- RoleRuntime provider-policy request mapping tests: `2 passed`
- Kernel context override preservation test: `1 passed`
- Static production-entrypoint no-direct-legacy guard: `4 passed`
- Runtime dialogue no-legacy-fallback guard:
  `src/backend/polaris/cells/roles/adapters/tests/test_runtime_dialogue.py`
  plus `src/backend/polaris/tests/test_roles_engine_dialogue.py` -> `20 passed`
- Combined runtime-entrypoint regression suite: `291 passed`
- Cognitive Runtime evidence hard-gate tests:
  `src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py`
  `20 passed`
- Adapter/runtime/PM/Director focused hardening suite: `226 passed`
- Frontend projection provenance tests:
  `npm run test -- src/frontend/src/runtime/projectionCompat.test.ts` ->
  `17 passed`
- Frontend projection type/lint checks:
  `npm run typecheck` passed; targeted `npx eslint` passed
- ContextGateway control-plane isolation and Cognitive Runtime MAINLINE preflight:
  `src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py`
  plus `src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_fallback.py`
  -> `38 passed`
- HTTP RoleRuntime streaming migration:
  `src/backend/polaris/tests/test_role_runtime_chat_router_helper.py`
  plus role/PM/session router regression tests -> `79 passed`
- PromptBuilder fail-closed receipt protection:
  `src/backend/polaris/cells/roles/kernel/tests/test_prompt_builder_chunks.py`
  -> `2 passed`
- Director tasking codegen canonical migration and artifact quality gate:
  `src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py`
  plus compatibility codegen and tech-stack regression tests -> `125 passed`
- WorkerExecutor focused quality/bootstrap regression:
  `src/backend/polaris/cells/director/tasking/tests/test_worker_executor.py`
  -> `55 passed`
- Cognitive Runtime MAINLINE tool-policy enforcement:
  `src/backend/polaris/kernelone/cognitive/tests/test_middleware.py`,
  `src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py`,
  and `src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py`
  -> `66 passed`
- Public llm.dialogue RoleRuntime facade and no-legacy metadata regression:
  `src/backend/polaris/tests/test_llm_dialogue_public_service.py`,
  `src/backend/polaris/tests/test_loop_pm_backend_resolution.py`,
  `src/backend/polaris/tests/test_pm_doc_rendering_role_runtime.py`,
  and `src/backend/polaris/cells/director/execution/tests/test_code_generation.py`
  -> `62 passed`
- Cognitive Runtime MAINLINE strategy override/fingerprint ordering:
  `src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py`
  -> `24 passed`
- Cognitive Runtime MAINLINE ContextGateway consumption:
  `src/backend/polaris/cells/roles/kernel/tests/test_tool_loop_controller.py`
  plus `src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py`
  -> `15 passed`
- ContextGateway decision hints drive Kernel tool-surface reduction:
  `src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py`,
  `src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py`,
  and `src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py`
  -> `65 passed`
- Direct TransactionKernel tool dispatch receives narrowed executor allow-list:
  `src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py -k "narrowed_tool_names"`
  -> `2 passed`
- TransactionKernel direct/stream regression suite:
  `src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py`
  -> `42 passed`
- RoleRuntime/ContextGateway/Kernel/TransactionKernel combined regression:
  `src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py`,
  `src/backend/polaris/cells/roles/kernel/tests/test_context_gateway_integration.py`,
  `src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py`,
  and `src/backend/polaris/cells/roles/kernel/tests/test_transaction_kernel_facade.py`
  -> `107 passed`
- Context OS structured policy runtime migration:
  `src/backend/polaris/kernelone/context/context_os/runtime/*`,
  `src/backend/polaris/kernelone/context/context_os/pipeline/*`,
  Context OS helpers/domain adapters, and RoleRuntime persistence -> ruff/mypy clean.
- Context OS attention/runtime/policy regression:
  `src/backend/polaris/kernelone/context/tests/test_attention_runtime.py`,
  `src/backend/polaris/kernelone/context/tests/test_attention_runtime_boundaries.py`,
  `src/backend/polaris/kernelone/context/tests/test_budget_alignment.py`,
  and `src/backend/polaris/kernelone/context/tests/test_context_os_policies.py`
  -> `118 passed`. Remaining deprecation warnings are emitted only by
  `test_context_os_policies.py` compatibility-accessor tests.
- RoleExecutionKernel canonical TransactionKernel and ExplorationWorkflowRuntime
  fail-closed regression:
  `src/backend/polaris/cells/roles/kernel/tests/test_role_kernel_transaction_wiring.py`,
  `src/backend/polaris/cells/roles/kernel/tests/test_integration_transactional_flow.py`,
  and `src/backend/polaris/cells/roles/kernel/tests/test_stream_nonstream_parity_transaction_kernel.py`
  -> `35 passed`.
- RoleExecutionKernel facade, run/stream parity, and stream compatibility
  regression:
  `src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py`,
  `src/backend/polaris/cells/roles/kernel/tests/test_run_stream_parity.py`,
  `src/backend/polaris/cells/roles/kernel/tests/test_stream_parity.py`,
  and `src/backend/polaris/cells/roles/kernel/tests/test_role_kernel_transaction_wiring.py`
  -> `72 passed, 1 skipped`. The remaining warnings are compatibility-facade
  and LLMCaller deprecation warnings plus pytest cache noise.
- Cognitive terminology governance and middleware regression:
  `src/backend/polaris/tests/test_terminology_governance.py` and
  `src/backend/polaris/kernelone/cognitive/tests/test_middleware.py`
  -> `14 passed`.
- RoleRuntime Cognitive Runtime and Kernel tool-surface regression:
  `src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py`
  and `src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py`
  -> `57 passed`.
- Production RoleExecutionKernel fallback scan:
  `LEGACY_FALLBACK|USE_TRANSACTION_KERNEL_PRIMARY|TurnEngine\(|old TurnEngine`
  against `src/backend/polaris/cells/roles/kernel/internal/kernel/core.py`
  -> no matches. Removed environment variables remain only as inert regression
  test inputs.
- Production legacy/fallback keyword scan:
  `allow_legacy_fallback|legacy_dialogue_fallback|legacy_fallback_used`
  -> no production matches; `generate_role_response(` -> compatibility facade
  definition only. codegraph reports no callers for `generate_role_response`
  and test-only callers for `generate_role_response_streaming`.
- Ruff and mypy: clean for touched files

Known residual risk:

- Cognitive Runtime is now a hard receipt/handoff gate for required RoleRuntime
  paths, and `MAINLINE` now runs a pre-LLM guidance/blocking preflight. It still
  does not yet rewrite the persisted role profile. Runtime tool policy is now a
  per-turn reduction layer: explicit cognitive blocked-tools and ContextGateway
  decision hints can remove tools from the current LLM surface, but they never
  grant tools outside the role profile whitelist.
- Speculative execution / stream shadow remains feature-flagged off by default.
- `generate_role_response_streaming(...)` still exists as a compatibility
  façade name, but its implementation constructs
  `ExecuteRoleSessionCommandV1(stream=True)` and calls
  `stream_role_session_command(...)`, which enters `RoleRuntimeService`. HTTP
  production routers no longer import this facade.
- `runtime_dialogue` no longer imports or calls `generate_role_response(...)`
  and no longer exposes a legacy fallback switch; runtime boundary errors are
  fail-closed.
- `RoleExecutionKernel.run` and `run_stream` no longer contain a TurnEngine
  fallback branch. TransactionKernel is the only production execution path; old
  environment switches cannot downgrade it.
- Production Python now keeps the `Cognitive Lifeform` / `认知生命体` metaphor out
  of code comments, docstrings, and CLI text. The new terminology governance
  test scans non-test, non-generated Python files under `src/backend/polaris`.
- Frontend LLM/process stream views still need a fuller source badge audit, but
  runtime status projections now carry provenance and no longer masquerade as
  canonical backend projection facts.
- Director tasking code generation now loads from the canonical tasking module.
  `execution.internal.code_generation_engine` remains only as a compatibility
  re-export, so old imports still work but WorkerExecutor no longer falls back
  through that package.
- WorkerExecutor now fails closed when generated-file receipts are missing on
  disk or match shared artifact-quality junk markers; `file_creation` and
  bootstrap missing-target paths no longer synthesize placeholder code.
- Cognitive Runtime `MAINLINE` now affects the current turn's tool surface,
  tool execution for explicit cognitive `blocked_tools`, and stream strategy
  fingerprint/receipt overrides. It also feeds current-turn strategy overrides
  into `ToolLoopController` and `RoleContextGateway`, where they alter the
  ContextOS projection recent-window size and budget-pressure telemetry before
  LLM prompt assembly. `RoleExecutionKernel` now consumes ContextGateway
  `context_decision_hints` in both non-streaming and streaming TransactionKernel
  paths, removing expensive context tools under budget pressure and suppressing
  mutating/exec tools in read-only/audit modes. The resulting narrowed
  `tool_definitions` are now converted into `allowed_tool_names` and passed to
  direct non-streaming and streaming `ToolBatchExecutor` dispatch as an execution
  layer backstop.
- Context OS production/runtime code now reads structured policy subobjects
  instead of deprecated compatibility accessors. Deprecated policy accessors
  remain intentionally available for migration/backward compatibility and are
  covered by explicit compatibility tests.
- `generate_role_response(...)` still exists for internal compatibility tests,
  but current production entrypoints and `LlmDialogueService.invoke_role_dialogue()`
  no longer call it.
- The full `test_turn_history_persist_parity.py` file still has unrelated
  ContextOSSnapshot / ContextOSProjection historical failures under
  `TestPhase6EventSourcingSafeguard`; the provider-policy regression test added
  here passes when run directly.
