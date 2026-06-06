# Aggregate Role Lobes Blueprint - 2026-06-06

## Problem

Polaris already has PM, Architect, Chief Engineer, Director, and QA role
profiles, plus KernelOne, ContextOS, Akashic memory, Cognitive Runtime receipts,
and transactional role execution. The missing platform contract is a stable way
to describe these role combinations as one aggregate model-like runtime without
pretending that virtual lobes are current standalone role profiles.

## Current Fact

- Current canonical role profiles remain the five entries in
  `polaris/cells/roles/profile/internal/config/core_roles.yaml`.
- `roles.runtime` is the public role execution/query facade.
- KernelOne owns platform capabilities such as ContextOS, Akashic memory,
  cognitive governance, tool validation, and rollback.
- Cognitive Runtime receipt/handoff evidence is already integrated into
  role-runtime execution paths.

## Decision

Add `roles.runtime` aggregate contracts:

- `BuildAggregateRolePlanQueryV1`
- `AuditAggregateRuntimeIntegrationsQueryV1`
- `AggregateRolePlanResultV1`
- `AggregateRoleLobeV1`
- `AggregateCognitiveLedgerEntryV1`
- `AggregateTakeoverDirectiveV1`
- `AggregateRuntimeIntegrationV1`
- `AggregateRuntimeEntrypointCheckV1`
- `AggregateRuntimeAuditResultV1`
- `AggregateChatCompletionsCommandV1`
- `AggregateChatCompletionsResultV1`
- `AggregateChatChoiceV1`
- `AggregateChatMessageV1`

The query returns deterministic functional lobes:

- Architect + QA constraint boundary generator
- Chief Engineer + virtual adversarial critic self-heal loop
- Director + ContextOS/Akashic hippocampus controller
- Director + QA tool commit guard
- PM + Director + QA runtime task allocator

The result is a plan, not execution. It does not call LLMs, execute tools, or
write state. Virtual lobe ids are explicitly marked as not current role profiles.
Each lobe declares compute tier, handoff keys, and takeover triggers. The plan
also emits a deterministic Cognitive Ledger plus an aggregate compute policy so
the single-model wrapper can pass structured state between internal lobes instead
of relying on noisy natural-language role chatter.

Failure signals and evidence are now explicit contract inputs.
`failure_signals` on `BuildAggregateRolePlanQueryV1` /
`AggregateChatCompletionsCommandV1` activate a planned
`AggregateTakeoverDirectiveV1`, selecting the internal lobe that should take over
next. `failure_evidence` carries structured compiler/typecheck/apply/localization
evidence, and the plan records whether the takeover directive has all required
evidence keys. Execution modes pass that evidence matrix through
`aggregate_runtime_context` instead of relying on natural-language failure prose.

Add a plan-only `chat_completions` wrapper on `RoleRuntimeService`. The external
shape is `chat.completion`, while the assistant content is a deterministic JSON
projection of the aggregate lobe plan. This gives future `/v1/chat/completions`
delivery a stable single-model surface without claiming that multi-role execution
or provider routing has already converged.

Add controlled execution modes:

- `single_turn` selects one concrete current role from the aggregate lobe plan,
  preferring the lobe activated by a takeover directive, and executes that role
  through `roles.runtime.stream_chat_turn`.
- `lobe_chain` executes a bounded sequence of concrete current roles selected
  from the aggregate lobe plan. Each lobe turn passes the previous turn output as
  structured handoff metadata/context instead of relying on natural-language
  agent chatter.

Both modes keep Strategy Profile/Fingerprint, ContextOS preflight, Turn Ledger,
Cognitive Runtime receipt/handoff emission, session continuity, repo
intelligence, and tool normalization on the normal production path. Virtual lobes
remain planning constructs and are never executed as role profiles.

Every executed aggregate lobe turn receives a model-visible JSON envelope with
schema `polaris.aggregate_lobe_turn.v1` as the role `user_message`. The envelope
contains the original objective, selected concrete role, lobe directive,
failure signals/evidence, takeover evidence status, prior structured handoffs,
Akashic recall summary, and the expected response contract. The original user
objective is preserved inside the envelope, but it is not sent as raw cross-role
chat. This keeps the aggregate runtime closer to a structured internal model bus
than to a transcript relay.

When the selected lobe is the hippocampus controller, or when failure signals
such as `localization_uncertain`, `degraded_signal`, `empty_repo_map`, or
`long_session` indicate memory fallback, aggregate runtime now builds an
`akashic_recall_pack` by calling the existing ContextOS `MemoryManager`. The pack
contains the recall query, current facts, projection count, injection allowance
summary, and candidate projections. The pack is supplementary: recalled memories
can guide context, but never override current failure evidence or graph facts.

Every executed lobe also receives a ContextOS attention/budget pack generated
from existing ContextOS runtime components: `PhaseAwareBudgetPlanner`,
`AttentionScorer(use_embeddings=False)`, and `PredictiveCompressor`. The pack
records the phase chosen for the lobe, phase budget allocation, deterministic
attention scores for objective/failure/handoff/lobe candidates, and predictive
compression guidance. This makes ContextOS Attention / Phase-aware Budgeting /
Predictive Compression a runtime input to aggregate role turns instead of a
standalone capability claim.

Aggregate execution also snapshots `TaskMarketProjection.get_dashboard_summary()`
into `task_market_projection_pack`. The snapshot is carried in the same
structured context, metadata, and lobe-turn envelope as failure evidence and
handoffs. This gives PM/Director/QA lobe execution a state-machine projection of
queue depth, in-progress work, dead letters, worker load, and active work items.

Aggregate execution now injects a Context governance pack that combines generated
descriptor pack summaries, generated context pack summaries, a live
`context.engine.build_context_window` ContextPack, a Verify Pack budget summary,
and `context.engine.search_gateway` retrieval candidates. This wires Descriptor /
Context Pack / Verify Pack and graph-constrained semantic retrieval into each
executed lobe turn without regenerating or overwriting pack files.

Aggregate execution also calls `KnowledgeDistillerService.retrieve_knowledge`
before role execution and `KnowledgeDistillerService.distill_session` after each
executed lobe result. The pre-turn pack surfaces reusable distilled lessons; the
post-turn distillation records success/error/stagnation patterns from the lobe
result into the workspace knowledge store. This turns Cognitive Knowledge
Distiller into a runtime feedback path rather than a planned bridge.

Expose both modes through `POST /v1/chat/completions` in
`polaris.delivery.http.routers.aggregate_chat`. The delivery route delegates to
the `roles.runtime` public boundary.

Add an aggregate runtime integration audit. The audit maps the 16 Polaris-unique
technology families into `wired`, `available`, or `planned_bridge` statuses and
returns production entrypoints, triggers, evidence keys, runtime effects, and
benefits. It also performs dynamic entrypoint checks: Python modules/methods must
resolve through import/attribute checks, graph cell ids must be present in
`docs/graph/catalog/cells.yaml`, the OpenAI-shaped route must exist in
`aggregate_chat.py`, generated packs must be present under current Cell assets,
and runtime output paths must be anchored in an existing workspace. The four
priority pillars are required to be `wired` with verified entrypoints:

- Strategy Profile + Role Overlay + Fingerprint
- Turn Transaction Kernel / Turn Ledger
- ContextOS Projection / Context Plane isolation
- Cognitive Runtime Receipt / Handoff Pack

As of this blueprint, the runtime audit reports all 16 integrations as wired,
with zero available-only integrations, zero planned bridges, and zero missing
production entrypoints in the Polaris workspace.

## Verification Plan

- `ruff check src/backend/polaris/cells/roles/runtime/public/contracts.py src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/public/__init__.py src/backend/polaris/cells/roles/runtime/tests/test_aggregate_role_plan.py --fix`
- `ruff format src/backend/polaris/cells/roles/runtime/public/contracts.py src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/public/__init__.py src/backend/polaris/cells/roles/runtime/tests/test_aggregate_role_plan.py`
- `mypy src/backend/polaris/cells/roles/runtime/public/contracts.py src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/tests/test_aggregate_role_plan.py`
- `pytest src/backend/polaris/cells/roles/runtime/tests/test_aggregate_role_plan.py src/backend/polaris/delivery/http/routers/test_aggregate_chat.py -v`
- `mypy src/backend/polaris/delivery/http/routers/aggregate_chat.py src/backend/polaris/delivery/http/routers/test_aggregate_chat.py`
- `pytest src/backend/polaris/cells/roles/runtime/tests/test_stream_chat_turn_audit.py src/backend/polaris/cells/roles/runtime/tests/test_role_runtime_strategy.py -v`

The aggregate role plan test suite includes a production-path materialization
case: `test_aggregate_lobe_chain_materializes_all_unique_technology_evidence`.
It executes a bounded `lobe_chain`, asserts that all 16 Polaris-unique
technology ids are present in the execution metadata, verifies ContextOS /
TaskMarket / Context governance / distilled-knowledge packs in the structured
role context, and confirms that Knowledge Distiller writes a UTF-8 JSONL
knowledge unit through `resolve_runtime_path`.

The HTTP route test suite also includes
`test_v1_chat_completions_lobe_chain_materializes_runtime_evidence_via_service`.
That test posts to `/v1/chat/completions` and lets the route call the default
`RoleRuntimeService`; only the final model streaming method is stubbed. This
proves the OpenAI-shaped delivery boundary can trigger the same stateful
aggregate runtime materialization path.

For repeatable operator-facing evidence, `polaris.delivery.cli.aggregate_audit`
and the unified `python -m polaris.delivery.cli aggregate-audit` command build a
UTF-8 JSON audit package. The default `plan_only` mode requires no external LLM
and records the 16 integration statuses, dynamic entrypoint checks, aggregate
plan, and full-chain environment preflight. Stateful `single_turn` and
`lobe_chain` modes are available when a real role runtime/provider is configured.

## Current Limit

`AggregateChatCompletionsCommandV1.execution_mode` accepts `plan_only`,
`single_turn`, and `lobe_chain`. `plan_only` is side-effect free. `single_turn`
is stateful and executes exactly one concrete role turn through the existing
streamed role runtime. `lobe_chain` is stateful and executes a bounded lobe
sequence with structured handoff metadata; full autonomous PR production remains
a future effect contract.

The compute policy currently recommends local self-heal first after compiler,
typecheck, apply, or localization failures, while cloud critique remains the
priority for architecture ambiguity, graph-boundary violations, and high
blast-radius decisions. This is a current planning policy, not a provider routing
implementation.
