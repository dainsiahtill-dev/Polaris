# Roles Kernel Cell

## Purpose

Provide the shared execution kernel for role prompt construction, output
parsing, quality checks, retry policy, runtime-level event emission, and the
typed semantic final-request evidence cutoff shared with Factory role flows.

## Kind

`capability`

## Public Inputs

- `BuildRolePromptCommandV1`
- `ParseRoleOutputCommandV1`
- `CheckRoleQualityCommandV1`
- `ExecuteRoleKernelTurnCommandV1`
- `ClassifyKernelErrorQueryV1`
- `ResolveRetryPolicyQueryV1`
- `FactoryRoleEvidenceCutoffRequestV1`
- `FactoryRoleEvidenceCutoffPort.acquire_cutoff`
- `FactoryRoleEvidenceCutoffPort.resolve_cutoff_proof`

## Public Outputs

- `RoleKernelResultV1`
- `RoleKernelPromptBuiltEventV1`
- `RoleKernelParsedOutputEventV1`
- `RoleKernelQualityCheckedEventV1`
- `FactoryRoleEvidenceCutoffAckV1`
- `FactoryRoleEvidenceCutoffSourceHeadV1`
- `FactoryRoleEvidenceCutoffProofV1`
- `FactoryRoleSemanticRequestIdentityV1`
- `FactoryRoleSemanticCandidateV1`
- `FactoryRoleFrozenSemanticRequestV1`

## Depends On

- `llm.provider_runtime`
- `policy.permission`
- `policy.workspace_guard`
- `audit.evidence`
- `finops.budget_guard`

## State Ownership

- None

## Effects Allowed

- `fs.read:workspace/**`
- `ws.outbound:runtime/*`
- `llm.invoke:roles/*`
- `process.spawn:roles/*`

## Invariants

- kernel logic must stay free of session ownership semantics
- adapter selection belongs outside the kernel boundary
- kernel turn execution must not import `roles.adapters`; role-specific schema
  decisions must arrive through caller-supplied public command payloads backed by
  `roles.profile` or runtime-owned public contracts
- kernel turn execution must not import `llm.dialogue`; prompt-driven role
  dialogue remains a compatibility owner Cell and production role turns enter
  through `roles.runtime` plus kernel/provider/control-plane contracts
- kernel turn execution must not import `roles.runtime`; `roles.runtime` is the
  composition/lifecycle caller of the kernel, not a dependency of it
- runtime events must be emitted explicitly
- assistant turn handling must separate raw parser input from sanitized transcript output
- `FactoryRoleEvidenceCutoffAckV1` is locator-only; only
  `FactoryRoleEvidenceCutoffPort.resolve_cutoff_proof` may reconstruct the
  detached committed proof used for semantic request injection
- B3.2 freezes the provider-visible semantic request after evidence injection;
  B3.3 propagates its exact runtime-private port through private sync,
  structured/manual, retry/fallback, and stream/reconnect seams while retaining
  the public zero-transport barrier
- semantic request identity, pre-evidence candidate, and post-evidence frozen
  request are immutable result/value DTOs; none is a command or query
- B3.4-B3.5 must independently budget, conserve, snapshot, and qualify every Architect, PM initial/recovery,
  Chief Engineer, Director direct/fanout, QA, retry, fallback, structured, and
  stream physical attempt before provider I/O
- runtime-only authority ports must never enter provider payloads, snapshots,
  events, or generated context assets

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `public/final_request_evidence_cutoff.py`
- `internal/kernel.py`
- `internal/turn_engine/`
- `internal/prompt_builder.py`
- `internal/output_parser.py`
- `internal/quality_checker.py`
- `internal/llm_caller/`
- `internal/llm_caller/factory_dispatch_propagation.py`
- `internal/retry_policy_engine.py`
- `internal/error_category.py`
- `generated/verify.pack.json`
- `generated/context.pack.json` (canonical context pack; legacy root pack is retired)

## Verification

- `tests/test_prompt_builder_retry.py`
- `tests/test_output_parser_patch_file.py`
- `tests/test_quality_checker_director_tool_calls.py`
- `tests/test_llm_caller_helpers.py`
- `tests/test_llm_invoker_decomposition_characterization.py`
- `tests/test_llm_invoker_role_binding_fallback.py`
- `tests/test_llm_invoker_final_request_receipt.py`
- `tests/test_llm_caller_capability_profile.py`
- `tests/test_role_kernel_write_budget.py`
- `tests/test_turn_engine_semantic_stages.py`
- `tests/test_turn_engine_policy_convergence.py`
- `tests/test_kernel_stream_tool_loop.py`
- `polaris/cells/roles/kernel/tests/test_final_request_evidence_cutoff.py`
- `polaris/cells/factory/pipeline/tests/test_factory_role_evidence_authority.py`
- `polaris/cells/roles/kernel/tests/test_factory_role_evidence_binding.py`
- `polaris/cells/roles/kernel/tests/test_role_turn_request_fact_projection.py`
- `polaris/cells/roles/kernel/tests/test_llm_caller_components.py`
- `polaris/cells/roles/kernel/tests/test_llm_invoker_decomposition_characterization.py`
- `polaris/cells/roles/kernel/tests/test_llm_invoker_role_binding_fallback.py`
- `polaris/kernelone/llm/engine/tests/test_executor.py`
- `polaris/kernelone/llm/engine/stream/tests/test_executor.py`

## Metadata Authority

- `generated/context.pack.json` is the only roles.kernel context-pack authority.
- `cell.yaml`, `docs/graph/catalog/cells.yaml`, this README, and
  `generated/verify.pack.json` must project the same B3.2 public module, proof
  DTOs, proof-resolution query, and focused tests.
- Metadata closure is not provider authorization. Until B3.4-B3.5 close,
  physical provider qualification and complete final-request snapshots remain
  guarded.
