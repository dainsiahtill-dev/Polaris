# Roles Kernel Cell

## Current engineering boundary (2026-10-27)

New engineering runs are owned by `roles.runtime.public.engineering_service.EngineeringRunService`.
The legacy Factory / `execute_role_session` / `RoleExecutionKernel` write admission is retired.
The legacy contracts below describe frozen records or explicit technical tools; they are not
planning, hash, portfolio or turn-batch requirements for the current engineering loop.

For ContextOS SLM integration, start at `public/slm_binding.py` (`build_slm_binding`).
This Cell owns the concrete `TransactionConfig`, `CognitiveGateway` and coprocessor.
KernelOne consumes the config/gateway protocols in
`polaris.kernelone.context.context_os.summarizers.contracts` and the narrow binding port in
`slm_config`; `bootstrap.assembly.ensure_minimal_kernelone_bindings` installs the producer lazily.
An explicit configuration reaches the actual gateway unchanged. Warm reuse requires matching
SLM settings, and producer contract errors propagate to the host.

Verification: `polaris/cells/roles/kernel/tests/test_slm_binding.py`,
`polaris/tests/unit/bootstrap/test_slm_binding_assembly.py`, and the KernelOne summarizer tests.
The scoped experiments use a declared synthetic SLM transport and do not establish model quality,
real endpoint availability, or unattended project delivery.

## Legacy and optional technical surfaces

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
- `RoleStructuredOutputContractV1`
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
- caller-owned structured result schemas must enter through
  `RoleStructuredOutputContractV1`; the reserved provider result tool is
  normalized into validated JSON before TransactionKernel tool decoding and
  must never enter authorization, Tool Lifecycle, effect receipts, or mutation
  ports
- final-request audit and B3.5 qualification must classify that exact reserved
  result tool as a caller-owned, non-executable provider protocol; every
  executable or unknown tool remains subject to exact `ToolSpecRegistry`
  provenance, alias, schema, and hash validation
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
- Director keeps policy-permitted read/search/edit/write/command tools by
  default. An explicit legacy single-batch contract remains caller-owned.
- A continuing Director turn records its exact workspace/run/task/session and
  source hashes in the canonical turn fact. Completion re-reads strict-integrity
  fact locators; transcripts and caller-projected success claims are observations.
- Successful mutations do not finish a task. Earlier command verification is
  invalid after a later mutation or source-hash change; CE-required commands must
  succeed for the current source before TaskBoundary can complete.
- CE `required_write_targets` defines materialization obligations separately
  from authorized `target_files`. Explicit empty CE write obligations permit a
  verification-only task; absent/None retains legacy behavior. Neither projection
  grants tool, command or path permission.
- The turn ledger captures prepared invocation batches before dispatch, including
  bootstrap reads, denied members, superseded writes and deferred repairs.
  Decision replacement cannot erase earlier invocation inputs. Durable execution
  joins each actual receipt to that inventory by its original call ID and records
  the original batch inventory for typed rehydration. Native arguments remain
  observations; normalized arguments alone enter permission and effect dispatch.
- Native history obtains arguments from the exact sealed invocation joined to
  its receipt by batch/call/tool/status. Missing arguments and receipt drift
  fail closed. Duplicate IDs, omitted committed rows and result/effect drift also
  fail closed; started events never replace the full committed call inventory.
  Physical command failures retain bounded diagnostic feedback and
  their original failed status.

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `public/final_request_evidence_cutoff.py`
- `public/structured_output_contracts.py`
- `internal/kernel.py`
- `internal/turn_engine/`
- `internal/prompt_builder.py`
- `internal/output_parser.py`
- `internal/quality_checker.py`
- `internal/llm_caller/`
- `internal/llm_caller/factory_dispatch_propagation.py`
- `internal/structured_output_transport.py`
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
- `tests/test_structured_output_transport.py`
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
