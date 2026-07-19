---
status: accepted
date: 2026-07-19
---

# ADR-0098: Exact Final Request Qualification Per Physical Provider Attempt

## Context

Polaris already reconstructs immutable Factory role-evidence sources, exposes a
typed cutoff port, persists segmented provider-attempt lifecycle facts, and has
a provider-neutral physical dispatch capability. Production role calls remain
disabled because these pieces are not composed at the live Architect, PM,
Chief Engineer, Director, and QA seams.

A logical role invocation is not a physical provider attempt. Structured-output
retries, provider retries, fallbacks, streaming transports, and Director fanout
can create multiple provider calls. Qualifying only the prepared messages or
only the first attempt would leave later network effects unaudited.

## Decision

### 1. Physical attempt is the authorization unit

Every actual provider transport attempt independently freezes, persists, and
qualifies its exact final wire request immediately before I/O. No qualification
receipt can authorize a retry, fallback, sibling fanout child, or later attempt.

### 2. Factory owns causal role evidence; roles.kernel owns composition

Factory reconstructs and binds a **pre-cutoff runtime-private authority
carrier** at controlled role task creation seams. That carrier holds the live
cutoff capability and controlled Factory identity; it holds no cutoff ACK,
source-head vector, or policy facts. `roles.kernel` consumes it, builds the
semantic candidate, acquires the durable ACK, and only then derives the existing
post-cutoff `FactoryRoleEvidenceBindingV1` proof carrier. It performs canonical
cutoff injection and recomputes the final semantic request. KernelOne owns the
provider-neutral dispatch capability; infrastructure owns the final transport
binding.

Because Factory is a separate Cell, the pre-cutoff carrier and binder are a
narrow `roles.kernel.public` cross-Cell contract. They remain runtime-private
because they have no record projection and must never be serialized. Keeping
that carrier only under `roles.kernel.internal` would force Factory to violate
the public/internal fence.

The live Factory port mints a unique grant for each logical role task, PM
recovery task, and Director fanout child. Its opaque execution-authority hash
binds the strict current stage claim, role, a fixed Factory-owned budget of 32
physical attempts, and a Factory-private random grant nonce. The private nonce
and registry row never cross the Cell boundary; the existing runtime-only
carrier transports only the opaque hash, role, budget, and live port.
The bound reserves 30 modeled attempts (two structured/logical branches, five
transport attempts including the current four-retry 429 path, and three route
or fallback heads) and rounds up to 32. It is a safety ceiling, not a target;
provider policy may use fewer attempts.

The stage-local registry bounds grants at 1/2/1/512/1 for
Architect/PM/Chief Engineer/Director/QA. The cutoff authority validates the
registry row, canonical hash, role, budget, open status, current run, and live
stage claim before any ledger/source/fact effect. After that validation, the
first request binds the grant once to the controlled child run id; later
semantic freezes must use the same child run. Physical-attempt accounting is
keyed by the grant hash across all freezes, retries, fallbacks, and stream
reconnects, so refreezing cannot reset the budget. Caller context, role
metadata, RequestPreparer, and provider retry configuration cannot mint or
enlarge this authority.

The pre-cutoff authority carrier and post-cutoff proof carrier are distinct
exact types. Reusing the post-cutoff carrier as Factory input is forbidden
because doing so requires evidence for a semantic request that does not yet
exist.

The cutoff ACK remains locator-only. A second exact method on the same public
cutoff port, `resolve_cutoff_proof(ack)`, strictly re-reads the committed
fragmented cutoff and returns a typed `FactoryRoleEvidenceCutoffProofV1`.
That proof carries the matching ACK, canonical binding ref/hash, ordered
source-head vector/hash, and typed role policy facts; it carries no live port,
pre-cutoff carrier, raw source payload, or Factory-private type. Factory builds
the public proof but never imports `roles.kernel.internal`; roles.kernel builds
its private `FactoryRoleEvidenceBindingV1` only from that exact public proof.
The binding ref is
`<authority_stream>@<cutoff_fact_sequence>#<cutoff_fact_id>`, and its hash
binds the complete ACK, ref, ordered heads/vector hash, and policy-facts record
in UTF-8 canonical JSON. The private carrier retains that exact typed proof;
its existing fields are detached projections that must equal the proof, and
validation recomputes the hash from the proof instead of trusting a
constructor-supplied digest.

No layer creates a second Factory evidence store or imports another Cell's
internal implementation.

### 3. Runtime capability is not request data

The physical dispatch port is passed as typed runtime-private capability state.
It is forbidden in provider payloads, `AIRequest.context`, metadata summaries,
events, snapshots, and persisted artifacts.

### 4. Canonical evidence is provider-visible

The immutable policy/slot/anchor block is injected into the role-correct first
system message after cutoff ACK. All messages, input text, token estimates,
digests, audit fields, and the `AIRequest` are recomputed after injection and
then frozen once.

The semantic identity is an exact frozen value owned by `LLMInvoker`: normalized
controlled `run_id`, `<run_id>:turn:<turn_round>`, one full `uuid4().hex`
logical `call_id`, and one full `uuid4().hex` `request_freeze_id` per
preparation pass. A role-binding fallback keeps run/turn/call and mints a new
freeze id; a provider-internal retry keeps the semantic freeze and receives a
new physical provider-request identity later at the dispatch gate. Caller
context cannot override these values. A Factory-bound call without a non-empty
controlled child `run_id` fails before candidate construction; the legacy
generated default run id remains legal only for ordinary non-Factory calls.

The pre-anchor candidate is deterministic UTF-8 canonical JSON over the exact
identity, role/provider/model/interaction/capability identity, complete
post-role-identity messages, exact complete provider-visible tools after
ToolSpec normalization and provider formatting, `tool_choice`, `response_format`,
and provider-visible `temperature`, `max_tokens`, and `stream`. It retains no
caller-owned container and rejects non-JSON or non-finite values instead of
using `repr`. Factory source selection is independent of caller hints, so B3.2
uses an empty `candidate_refs` tuple.

`capability_profile_id` is exactly
`canonical_role_final_request_hash(ResolvedActorCapabilityProfile.to_dict())`:
the lowercase 64-hex SHA-256 (`^[0-9a-f]{64}$`) of strict UTF-8 canonical JSON
for the complete resolved record. Candidate and frozen-final payloads use that
same value. The nullable
`context_metadata.capability_profile_ref.sha256`, caller metadata, `default=str`,
and `repr` are not legal sources or fallbacks for this identity.

After injection, roles.kernel creates one immutable
`FactoryRoleFrozenSemanticRequestV1` containing the identity, candidate hash,
binding ref/hash, canonical final payload JSON, and final semantic hash. It is
the authority against which later dispatch propagation checks mutable request
projections. `PreparedLLMRequest` carries it only for Factory-qualified calls;
ordinary calls carry `None`. This adds no physical dispatch capability.
Frozen-final construction rejects drift in every non-message semantic field;
only evidence-block insertion into messages and candidate/binding commitments
may differ from the pre-anchor candidate. Its `schema_version` changes only
from `FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA` to
`FACTORY_ROLE_FROZEN_SEMANTIC_REQUEST_SCHEMA`; this exact structural-header
transition is legal and every shared semantic field remains canonical-equal.
Frozen validation reconstructs the candidate by validating and stripping the
single canonical role-matching evidence block, restoring the candidate schema,
and removing only the candidate/binding commitments; it recomputes the
candidate hash rather than trusting a constructor or self-consistent supplied
hashes.

The only evidence framing is one complete-line block in the first system
message, immediately after the canonical role marker:

```text
polaris.final_request_evidence.v1:begin
<canonical RoleFinalRequestPolicyFactsV1 JSON>
polaris.final_request_evidence.v1:end
```

Pre-existing, duplicate, wrong-role, non-first-system, or malformed framing is
rejected. Architect is a first-class core role. All derived request projections
are recomputed after this insertion. The final `context_projection_id` equals
the post-injection `prompt_digest`, and `context_result_id` equals
`build_context_result_id(context_projection_id)`; any pre-injection ids are
source-only audit fields. B3.2 then freezes the semantic request and does not
yet add or authorize physical dispatch.

### 5. Full request or no dispatch

Qualification covers role identity, complete messages, exact tools,
`tool_choice`, `response_format`, normalization/aliases, final token/window
metrics, seven evidence slots, and a readable same-workspace 24-hex snapshot.
Missing, stale, malformed, clipped, cross-role, cross-workspace, messages-only,
or unreadable evidence is a failed attempt with zero provider I/O.

### 6. Controlled inheritance only

PM, QA, and non-fanout Director bindings are installed before their controlled
tasks are created; direct Chief Engineer is bound locally; every Director
fanout child receives an independent identity and binding. Recovery/background
work without a reconstructed live Factory port fails closed. ContextVars are
reset on all terminal paths.

## Consequences

### Positive

- The audit unit matches the real network effect.
- Hidden retries and fallbacks cannot escape qualification.
- ContextOS can expose one exact, readable final request per attempt.
- Fanout evidence cannot silently cross child boundaries.
- Factory completion can require physical evidence without treating `N/A` as
  PASS.

### Cost

- Every invocation path and provider transport seam requires parity tests.
- Physical retries persist separate snapshots and lifecycle facts.
- Recovery must reconstruct live authority instead of replaying a serialized
  capability.

## Invariants

1. Physical provider calls equal qualified started attempts.
2. Each started attempt has one readable same-workspace snapshot and one
   terminal lifecycle fact.
3. A prior PASS never authorizes another attempt.
4. Runtime-private ports never enter serialized request data.
5. A Factory role call without current live evidence authority produces zero
   provider I/O.
6. Director fanout children cannot share call/freeze/attempt identity.
7. Architect, PM, Chief Engineer, Director, and QA first system identities cannot cross.
8. `FPR=N/A` never satisfies Factory physical-attempt qualification.
9. This mechanism introduces no Bench dependency and no PM-to-Director bypass.
10. Pre-cutoff authority contains no fabricated ACK data; post-cutoff proof is
    constructed only from the matching durable ACK.
11. Factory never imports `roles.kernel.internal`; the public runtime-private
    carrier is the only cross-Cell binding seam.
12. A forged role, physical-attempt budget, or execution-authority hash produces
    no cutoff ACK and no provider I/O.
13. `docs_generation` uses the canonical `architect` role with required
    admission-time `pm_raw_intent`; it cannot impersonate PM or require evidence
    from later stages that do not yet exist.
14. PM initial/recovery and Director fanout siblings receive distinct grant
    hashes; one grant binds exactly one controlled child run.
15. The 32-attempt budget is aggregated by grant hash across semantic freezes;
    freeze, retry, fallback, and stream boundaries cannot reset it.
16. Per-stage grant cardinality is bounded at 1/2/1/512/1, and overflow creates
    no role task, ledger, source read, cutoff fact, or provider I/O.
17. Fanout cancellation is drained before stage authority closes; after close,
    inherited background calls fail before persistent effects.
18. No carrier, live port, private grant nonce, or registry row may appear in a
    `StageResult`, `AIRequest`, context, event, snapshot, artifact, provider
    payload, or JSON projection.

## Related decisions

- ADR-0094: runtime effect sinks and reliable facts
- ADR-0097: execution fact authority and commit barrier
- `FINAL_PROVIDER_ATTEMPT_QUALIFICATION_BLUEPRINT_20260719.md`
