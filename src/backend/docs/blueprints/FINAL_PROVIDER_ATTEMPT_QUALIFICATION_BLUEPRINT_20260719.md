# Final Provider Attempt Qualification Blueprint

Status: locked; B3.0-B3.3 closed; B3.4 specification CLEAR and implementation active; B3.5-B3.6 pending
Date: 2026-07-19
Scope: `factory.pipeline` + `roles.kernel` + KernelOne LLM effect boundary
Bench: `not_schedulable`

## 1. Objective

Make the exact provider-visible request for every physical Architect, PM,
Chief Engineer, Director, and QA attempt the only dispatch authority. A prepared semantic
request, a messages-only audit, a prior qualified attempt, or `FPR=N/A` must
never authorize a physical provider call.

Normative acceptance remains
`docs/superpowers/plans/2026-07-18-role-final-provider-request-audit-gate.md`.
This blueprint freezes the A009B3 implementation boundary required by that
gate; it does not authorize Bench or change project-business code.

## 2. Classification and assumptions

Classification: `structural`.

The wrong shared assumption is that role context preparation and physical
provider dispatch are one event. They are not: structured-output retries,
provider retries, fallbacks, streams, and Director fanout can create multiple
physical attempts from one logical role turn.

Verified current facts:

1. `FactoryRoleEvidenceBindingV1` is a **post-cutoff proof carrier**: it requires
   a durable cutoff fact, source-head vector, and canonical policy facts. It
   cannot truthfully be constructed by a Factory role seam before the semantic
   request and cutoff ACK exist.
2. B3.1 supplies a distinct **pre-cutoff authority carrier** at every
   Factory-controlled Architect, PM initial/recovery, Chief Engineer, Director
   direct/fanout-child, and QA task-creation seam. B3.2 now consumes that exact
   carrier, resolves the committed post-cutoff proof and freezes the semantic
   request; physical provider dispatch remains fail-closed pending B3.4-B3.5.
3. `RequestPreparer` builds the pre-anchor candidate from the pre-cutoff
   carrier, acquires the durable ACK, resolves the exact post-cutoff proof,
   injects the canonical evidence block and freezes the final semantic request.
   `LLMInvoker` then deliberately raises
   `factory_role_semantic_request_frozen_physical_dispatch_not_enabled` before
   any physical path. No fabricated ACK or carrier inversion remains.
4. `AIExecutor` can accept a runtime-private physical dispatch port and bind it
   around provider invocation.
5. infrastructure HTTP helpers can consume the bound dispatch port immediately
   before a physical attempt.
6. Invoker sync/structured/fallback and stream paths all enforce the B3.2
   zero-transport barrier; B3.3 must propagate the frozen authority through
   every physical call without serializing the runtime-private port.
7. existing cutoff, segmented lifecycle, in-flight drain, and final-attempt gate
   primitives are reused; no second authority is introduced.

## 3. Architecture

```text
Factory strict event chain + immutable stage artifacts
  -> FactoryRoleEvidenceAuthorityPort
     -> Factory-minted role grant
        (stage/claim/role/budget-bound execution_authority_hash)
  -> controlled role task creation seam
     -> FactoryRoleEvidenceAuthorityBindingV1
        (public cross-Cell contract, runtime-private/non-serializable capability)
        -> RequestPreparer pre-anchor semantic candidate
        -> cutoff request through the live port + durable ACK
        -> FactoryRoleEvidenceBindingV1
           (post-cutoff proof carrier derived from that ACK)
        -> canonical policy/slot/anchor block in first system message
        -> recompute messages/input/tokens/digests/audit/AIRequest
        -> one frozen semantic request + runtime-private dispatch port
           -> Invoker sync | structured | stream | fallback
              -> AIExecutor runtime-private port binding
                 -> provider helper before EACH physical attempt
                    -> FinalProviderAttemptGate
                       -> exact wire request freeze
                       -> full-request qualification
                       -> readable same-workspace 24-hex snapshot
                       -> lifecycle started
                       -> physical dispatch
                       -> lifecycle terminal
```

The runtime-private port is capability state. It must not be serialized into
`AIRequest.context`, request metadata, events, snapshots, or provider payloads.

## 4. Ownership and responsibilities

### `factory.pipeline`

- Reconstruct the live cutoff authority only from the current strict Factory
  run and stage claim.
- Mint one unique pre-cutoff grant per logical role task, PM recovery task, and
  Director fanout child through the live Factory authority port. The opaque
  execution-authority hash is canonical over the complete current stage claim,
  role, Factory-owned physical-attempt budget, and a Factory-private random
  grant nonce. The nonce and registry row never enter the public carrier or any
  serialized surface; the hash is the grant identity used across Cells.
- Own the fixed per-grant physical-attempt budget of 32. Count physical
  attempts by execution-authority hash across every semantic freeze, structured
  retry, provider retry, fallback, and stream reconnect; a new freeze cannot
  reset the budget. Caller context, role metadata, provider configuration, and
  RequestPreparer may reduce transport retries but cannot mint or enlarge the
  Factory budget.
- Bound grant creation for each exact stage claim: `docs_generation=1`,
  `pm_planning=2` (initial plus recovery), `chief_engineer_review=1`,
  `director_dispatch=512`, and `quality_gate=1`. Exceeding the bound fails
  before role-task creation. The Director bound matches the existing maximum
  plan-task cardinality and also caps a no-task binding fanout.
- Validate the grant registry row, canonical hash, role, budget, open status,
  current Factory run, and live stage claim before ledger creation, source
  resolution, FactStream append, or ACK. Only after the live claim passes may
  the first cutoff request bind the grant once to its controlled child run id;
  all later freezes must use that same child run id.
- Bind a **pre-cutoff authority carrier**, never a fabricated post-cutoff proof,
  for PM, QA, and non-fanout Director before controlled orchestration tasks are
  created.
- Bind the same pre-cutoff carrier locally for direct Chief Engineer.
- Create an independent pre-cutoff carrier and controlled identity for every
  Director fanout child.
- Mint and bind inside each child task. On timeout/cancellation, cancel pending
  fanout tasks and await them with `return_exceptions=True` before the stage may
  close its authority port or release the stage claim.
- Block recovery/background continuation if the live Factory port cannot be
  reconstructed. Never fall back to an unqualified role call.
- Close the stage-local grant registry before releasing the stage claim. Any
  inherited background task or late retry then fails before ledger/source/fact
  effects; task-creation failure revokes its unused grant immediately.
- Preserve `PM -> Chief Engineer -> Director`; this work cannot create a
  PM-to-Director bypass.

### `roles.kernel`

- Expose the pre-cutoff authority carrier and binder as a narrow public
  cross-Cell contract because Factory must install it without importing
  `roles.kernel.internal`. Runtime-private means non-serializable; it does not
  mean an illegal cross-Cell internal import.
- Consume the Factory-minted pre-cutoff authority carrier without importing
  Factory internals or recomputing Factory authority.
- Build one pre-anchor candidate, acquire the cutoff ACK through its live port,
  derive and validate the existing post-cutoff `FactoryRoleEvidenceBindingV1`,
  inject the canonical evidence block into the role-correct first system
  message, then recompute all derived request projections.
- Carry the physical dispatch port as a typed runtime-private field alongside,
  not inside, the serializable prepared request.
- Propagate the port through sync, structured, fallback, and stream execution.
- Reset ContextVars on success, failure, cancellation, and task completion.

### B3.2 locked semantic-cutoff contract amendment (2026-07-19)

The B3.2 specification slice proved that the locator-only
`FactoryRoleEvidenceCutoffAckV1` cannot, by itself, express the existing
post-cutoff `FactoryRoleEvidenceBindingV1`. The complete cutoff body and
fragment decoder are Factory-internal, and Factory may not import
`roles.kernel.internal`. B3.2 therefore freezes these four decisions before
production implementation:

1. **Public exact-type proof resolution, locator-only ACK retained.**
   `roles.kernel.public.final_request_evidence_cutoff` adds
   `FactoryRoleEvidenceCutoffSourceHeadV1` and
   `FactoryRoleEvidenceCutoffProofV1`. The proof contains exactly the matching
   typed ACK, a canonical binding ref/hash, the ordered typed source-head
   vector and vector hash, and one typed `RoleFinalRequestPolicyFactsV1`; it
   contains no live port, pre-cutoff carrier, Factory-private authority,
   mutable mapping, or raw source payload. `FactoryRoleEvidenceCutoffPort`
   adds `resolve_cutoff_proof(ack)`. The Factory implementation strictly
   re-reads the committed fragmented cutoff by the ACK locator, reconstructs
   the exact body, requires every re-derived ACK field to equal the supplied
   ACK, and only then returns the exact proof type. No caller mapping,
   subclass, sidecar, cache, latest alias, or Factory-internal import may
   satisfy this boundary.

   The canonical binding ref is
   `<authority_stream>@<cutoff_fact_sequence>#<cutoff_fact_id>`. The canonical
   binding hash is SHA-256 over UTF-8 canonical JSON containing the proof
   schema, the complete ACK record, that binding ref, the ordered source-head
   records and their vector hash, and the complete policy-facts record, with
   the binding hash itself excluded. `FactoryRoleEvidenceBindingV1` adds one
   exact typed `cutoff_proof: FactoryRoleEvidenceCutoffProofV1`; its existing
   scalar/head/policy fields remain strict detached projections for current
   consumers. Validation requires every projection to equal the nested proof
   and recomputes the canonical binding hash from that proof. It is built only
   from this exact proof; a constructor-created carrier with an arbitrary
   well-shaped hash cannot pass validation.

2. **Invoker-owned semantic identity.** Add one frozen exact-type
   `FactoryRoleSemanticRequestIdentityV1` carrying `run_id`, `turn_id`,
   `call_id`, and `request_freeze_id`. `LLMInvoker` owns every field:
   `run_id` is its normalized controlled child run, `call_id` is one full
   `uuid4().hex` per logical call, `turn_id` is
   `<run_id>:turn:<non-negative turn_round>`, and `request_freeze_id` is one
   full `uuid4().hex` per request-preparation pass. A role-binding fallback
   preserves run/turn/call but mints a new freeze id. Provider-internal retry
   does not refreeze; B3.4 gives each physical attempt a separate provider
   request id under the same semantic freeze. Context metadata cannot select
   or override these identities. When pre-cutoff Factory authority is present,
   a non-empty controlled child `run_id` is mandatory and absence fails closed
   before candidate construction; only ordinary non-Factory calls may retain
   the Invoker's legacy generated default run id.

3. **Canonical pre-anchor semantic candidate.** Add one frozen exact-type
   `FactoryRoleSemanticCandidateV1`. Its canonical payload contains only:
   schema and the complete semantic identity record; canonical role,
   provider id, model, interaction mode, and capability-profile id; complete
   post-role-identity/pre-evidence messages; the exact complete
   provider-visible `tools` value after ToolSpec normalization and provider
   formatter transformation;
   exact `tool_choice`; exact `response_format`; and the provider-visible
   semantic options `temperature`, `max_tokens`, and `stream`. Nested values
   accept only deterministic JSON scalars, string-key mappings, and ordered
   sequences; non-finite numbers, sets, opaque objects, non-string keys, and
   `repr` fallback are rejected. Construction deep-copies into UTF-8
   canonical JSON, stores no caller-owned container, and computes
   `semantic_candidate_hash` over that exact payload. B3.2 sends an empty
   `candidate_refs` tuple because hints are non-authoritative and the Factory
   resolver independently reconstructs every required source. A changed
   candidate under the same freeze id is a replay conflict, not a new request.

   The capability-profile id has one source and one algorithm:
   `canonical_role_final_request_hash(
   ResolvedActorCapabilityProfile.to_dict())`. That helper hashes the strict
   UTF-8 canonical JSON of the complete resolved record and returns a lowercase
   64-hex value satisfying `^[0-9a-f]{64}$`. The same strict JSON rules above
   apply, including rejection of non-finite or otherwise unsupported values;
   `default=str`, `repr`, and other lossy fallbacks are forbidden. Neither
   `context_metadata.capability_profile_ref.sha256` nor any caller-supplied or
   nullable metadata reference may provide or override this id. The candidate
   and frozen final payload must carry this identical value.

   After evidence injection, add one immutable
   `FactoryRoleFrozenSemanticRequestV1`. It stores only the exact semantic
   identity, pre-anchor candidate hash, canonical binding ref/hash, UTF-8
   canonical final payload JSON, and its SHA-256. The final payload uses the
   same closed field set as the candidate with post-injection messages and also
   binds the candidate hash plus binding ref/hash. `PreparedLLMRequest` carries
   this exact typed value in an optional `factory_semantic_request` field;
   ordinary non-Factory requests keep `None`. The frozen value stores no
   mutable containers and is the B3.3 comparison authority. Mutable projection
   objects may not replace or redefine it; B3.3 must fail if their outbound
   values drift from it. Frozen-final construction must also compare every
   non-message semantic field to the candidate and fail closed on any change;
   only the canonical evidence-block insertion in `messages`, addition of
   candidate/binding commitments, and the exact structural-header transition
   from `FACTORY_ROLE_SEMANTIC_CANDIDATE_SCHEMA` to
   `FACTORY_ROLE_FROZEN_SEMANTIC_REQUEST_SCHEMA` are legal between the two
   payloads. The schema transition is not semantic drift; every shared
   non-message semantic field remains canonical-equal. Validation may not trust
   `create()`: `FactoryRoleFrozenSemanticRequestV1.__post_init__` must parse and
   validate the one canonical role-matching policy-facts block, strip exactly
   that block from the first system message, restore the candidate schema,
   remove only the candidate/binding commitment fields, and recompute the
   candidate canonical hash. A manually constructed frozen value whose stored
   hashes are internally self-consistent but whose reconstructed candidate does
   not match is rejected.

4. **One literal provider-visible block.** After the role marker in the first
   system message, inject exactly these complete-line delimiters with exactly
   one canonical JSON line between them:

   ```text
   polaris.final_request_evidence.v1:begin
   <render_role_final_request_policy_facts(policy_facts)>
   polaris.final_request_evidence.v1:end
   ```

   The order is existing system content, blank line, exact
   `polaris.role_identity.v1:<role>` line, blank line, begin delimiter,
   canonical JSON, end delimiter. Any pre-existing delimiter, policy-facts
   schema token, duplicate block, non-first-system block, or role mismatch
   fails closed. Architect is included in the core-role identity set. After
   injection, messages, input text, context token estimate, context projection
   and result ids, prompt digest, audit, `AIRequest.context.chat_messages`, and
   the final `AIRequest` are recomputed from the post-injection messages and
   deep-frozen. The final `context_projection_id` is exactly the post-injection
   `prompt_digest`; `context_result_id` is exactly
   `build_context_result_id(context_projection_id)`. Pre-injection ids may be
   retained only in explicitly `source_`-prefixed audit fields and never
   projected as final ids. No await or caller-mutable reference may change the
   semantic request after that freeze.

   B3.2 requires this version-locked class-level RED matrix before GREEN;
   role/error variants may be parameterized but no class may be omitted:

   1. `B3.2-RED-01 exact-proof-boundary`: accept only the public exact ACK and
      resolved-proof types; reject mappings, subclasses, private Factory body,
      decoder, or equivalent duck types.
   2. `B3.2-RED-02 ACK-commitment-integrity`: any mismatch in
      run/role/turn/call/freeze/candidate/budget/authority or fact/body/fragment
      locator fails closed.
   3. `B3.2-RED-03 resolved-proof-integrity`: missing or tampered ordered
      source-head vector/hash, policy facts/hash, or cutoff binding prevents a
      post-cutoff binding.
   4. `B3.2-RED-04 authority-separation`: resolved proof and post-cutoff binding
      contain and reach no live cutoff port, pre-cutoff carrier, Factory
      internal body, or decoder.
   5. `B3.2-RED-05 ordinary-noninterference`: without Factory authority,
      request semantics remain unchanged and no Factory evidence claim or
      marker is injected.
   6. `B3.2-RED-06 pre-authority-validation`: malformed or wrong
      role/run/authority-hash/budget carrier fails before `acquire_cutoff`.
   7. `B3.2-RED-07 single-semantic-cutoff`: a legal candidate acquires exactly
      once and binds the exact run/role/turn/call/freeze/budget/authority,
      candidate hash, and empty candidate refs.
   8. `B3.2-RED-08 canonical-first-system-injection`: exactly one canonical
      policy-facts block appears in the first system message immediately after
      the exact role marker.
   9. `B3.2-RED-09 full-recomputation-parity`: post-injection messages,
      input text, context result/token estimate, prompt digest, projection ids,
      `AIRequest.context.chat_messages`, final AIRequest, and final-request
      audit are same-source with no pre-injection stale projection; parameterize
      Architect, PM, Chief Engineer, Director, and QA.
   10. `B3.2-RED-10 protocol-forgery-rejection`: duplicate, wrong-role,
       non-first-system, or caller-forged markers/blocks fail closed.
   11. `B3.2-RED-11 failure-cancellation-atomicity`: cutoff exception or
       malformed ACK/proof produces no prepared request; `CancelledError`
       propagates unchanged and every ContextVar is restored.
   12. `B3.2-RED-12 recursive-serialization-nonleakage`: every serializable
       surface of prepared request/messages/context/options/AIRequest/audit/
       events contains no port, carrier, private body, or decoder.
   13. `B3.2-RED-13 semantic-freeze-replay`: caller-owned messages/options/tools
       are deep-frozen before any later await; external mutation cannot change
       hash or final request, formatter-transformed provider-visible tools are
       the frozen authority, any cutoff-time drift in tools/tool choice/response
       format/temperature/max tokens/stream is rejected, the one legal candidate
       schema to frozen-final schema transition succeeds while any wrong schema
       is rejected, direct-constructor/self-consistent-hash forgery fails
       reconstructed-candidate validation, and one freeze id with another
       candidate hash is rejected.
   14. `B3.2-RED-14 resolved-capability-authority`: the same context capability
       ref with different complete resolved actor capability profiles produces
       different lowercase 64-hex candidate hashes.
   15. `B3.2-RED-15 context-ref-nonauthority`: the same complete resolved actor
       capability profile with only caller/context capability ref changed
       produces the same capability id and candidate hash.
   16. `B3.2-RED-16 strict-capability-canonicalization`: unsupported, opaque,
       non-string-key, or non-finite resolved profile values fail closed before
       cutoff; no `default=str` or `repr` fallback is legal.

B3.2 stops at semantic freeze. It adds no physical dispatch field or provider
hook, performs no physical-attempt qualification or snapshot persistence, and
does not authorize B3.3-B3.5, a real provider call, or Bench.

### KernelOne and infrastructure providers

- KernelOne owns the provider-neutral runtime port contract and binding.
- The existing final-attempt gate owns per-physical-attempt qualification,
  lifecycle, retry-budget enforcement, and snapshot pinning.
- Infrastructure calls the bound port at the last common point before each
  actual provider transport attempt. Internal retry and fallback are separate
  attempts and cannot reuse a prior PASS.
- Provider adapters do not learn Factory semantics.

## 5. Exact qualification contract

Before each physical dispatch, the gate proves from the exact final wire
request:

1. role-correct first system message and role metadata;
2. complete messages after canonical anchor injection;
3. exact native tool schemas;
4. `tool_choice`;
5. `response_format`;
6. ToolSpecRegistry alias and argument-normalization contract;
7. final request tokens, context window, and utilization;
8. the complete role-specific ordered slot set, drawn from the seven-kind
   evidence-policy universe, from the immutable cutoff;
9. one readable 24-hex `context_snapshot_ref` in the same workspace and for the
   same attempt.

Missing, malformed, stale, clipped, cross-role, cross-workspace, messages-only,
or unreadable evidence records a failed attempt before transport. `N/A` is
permitted only when no physical call was expected and never satisfies this
gate.

## 6. Controlled seams

Candidate implementation files, subject to final audit line mapping:

- `polaris/cells/factory/pipeline/internal/factory_run_service.py`
- `polaris/cells/factory/pipeline/internal/factory_stage_executor.py`
- `polaris/cells/roles/kernel/public/final_request_evidence_cutoff.py`
- `polaris/cells/roles/kernel/internal/llm_caller/factory_role_evidence_binding.py`
- `polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py`
- `polaris/cells/roles/kernel/internal/llm_caller/response_types.py`
- `polaris/cells/roles/kernel/internal/llm_caller/invoker.py`
- `polaris/cells/roles/kernel/internal/llm_caller/stream_engine.py`
- only the provider helper/adapters proven to bypass the existing physical
  dispatch ContextVar
- corresponding Cell-owned tests and synchronized graph/context/verify assets

No public Factory contract, Bench schema, target-project code, or second event
store is added unless a RED test proves the existing typed boundary cannot
express the required capability.

## 7. Implementation buckets

1. **B3.0 Pre-cutoff authority contract**: add a distinct, exact-type,
   runtime-private carrier for the live cutoff port and Factory-controlled
   identity. It contains no cutoff fact, source heads, policy facts, snapshot,
   or serialized capability. Keep `FactoryRoleEvidenceBindingV1` as the
   post-cutoff proof carrier.
2. **B3.1 Factory binding seams**: move the pre-cutoff carrier/binder to the
   narrow public cross-Cell cutoff contract; have the live Factory port mint a
   unique role/stage/claim/budget/private-nonce-bound grant, enforce the fixed
   budget and per-stage grant cardinality, and reject forged request authority
   before any persistent effect; bind all role paths, independent fanout child
   identities, and recovery/background fail-closed with ContextVar cleanup and
   cancellation drain. Stage close and cutoff acquisition share one explicit
   linearization protocol: close publishes closed/revoked state before waiting
   for registered acquisitions to drain; each acquisition revalidates that
   state before every persistent append/commit and before returning an ACK.
   Authoritative commit append and ACK publication each execute through a
   condition-protected check-and-act helper shared with close and grant revoke;
   a detached live-check followed by an unlocked commit or ACK return is
   forbidden. The ACK publication helper also closes its acquisition lease in
   the same critical section, with outer cleanup remaining exactly-once.
   After close returns, no acquisition may append a new commit, return an ACK,
   or authorize a provider attempt.
3. **B3.2 Semantic cutoff injection**: replace the deliberate stop, inject the
   canonical block, recompute and freeze the exact semantic request. Closure
   also synchronizes the graph catalog `current_modules`/`public_contracts`,
   Cell/context packs, and verification assets for the existing
   `roles.kernel.public.final_request_evidence_cutoff` module and its new exact
   DTO/query surface; it does not add a Factory public contract.
4. **B3.3 Dispatch propagation**: sync, structured, fallback, and stream paths
   carry one exact runtime-private, propagation-only port without payload
   contamination. The B3.2 public hard stop remains active and the port itself
   also rejects `send` / `open_stream` with
   `factory_role_semantic_request_frozen_physical_dispatch_not_enabled`.
   B3.3 therefore proves propagation with zero outbound transport and
   `FPR=N/A`; it does not attach the transport-capable
   `FinalProviderAttemptGate`.
5. **B3.4 Physical-attempt parity**: every `governed_supported` provider
   transport, internal retry, fallback and reconnect independently calls the
   gate; every `factory_disabled_opaque` mode remains exhaustively inventoried
   with zero outbound. Exact attempt identity, authority-hash budget and
   lifecycle conservation are owned and closed here.
6. **B3.5 Snapshot qualification**: consume and revalidate B3.4 identity and
   lifecycle while adding same-workspace readable 24-hex snapshot, complete
   final-request token/window/evidence/ToolSpec audit and qualification. B3.5
   does not redefine or defer B3.4 conservation.
7. **B3.6 Closure**: focused, cross-layer, full Factory, KernelOne, static,
   independent specification, and independent quality reviews.

Only one bucket is implemented and reviewed at a time.

### B3.1 closure evidence (2026-07-19)

- Factory binds one private grant at every Architect, PM initial/recovery,
  Chief Engineer, Director direct/fanout-child, and QA task-creation seam.
- Five deterministic concurrency regressions cover loader-await close,
  fragment-drain close, close-before-commit, close-before-replay-ACK, and
  revoke-before-commit. The corresponding RED failures were observed before
  the production fixes.
- Main-agent acceptance passed the complete authority suite (`119 passed`),
  the seven-file cross-layer aggregate (`310 passed in 124.03s`), and the
  FactoryRunService integration suite (`83 passed in 184.75s`). Ruff, format,
  scoped mypy, compileall, diff check, strict Verification Card validation, and
  defect-ledger JSON validation passed.
- Independent specification rereview returned `PASS` with no P0/P1/P2; the
  independent code-quality/concurrency rereview returned `APPROVED`.
- `FPR=N/A`: B3.1 creates no physical provider attempt and does not count as a
  full-final-request PASS. Dispatch stays fail-closed until B3.2-B3.5 close.

### B3.2 closure evidence (2026-07-19)

- Public exact ACK-to-proof resolution, Invoker-owned run/turn/call/freeze
  identity, strict resolved-capability identity, canonical first-system
  evidence injection, complete post-injection recomputation, detached proof
  validation, recursive non-leakage and immutable semantic freeze are live.
- Gateway, cutoff acquisition and proof resolution await boundaries revalidate
  one immutable authority snapshot. Deterministic RED barriers proved and then
  closed valid-carrier and valid-port swaps, including the cross-port
  acquire/resolve TOCTOU. Sync, structured and stream paths all stop before
  snapshot/cache/executor/fallback/transport with
  `factory_role_semantic_request_frozen_physical_dispatch_not_enabled`.
- Main-agent acceptance passed the five-file semantic-cutoff aggregate
  (`382 passed`), the affected LLM cluster (`299 passed`), graph/metadata
  governance (`569 passed`), catalog governance (`0` issues and `0`
  mismatches), and the complete Factory Pipeline (`1116 passed in 567.71s`).
  Ruff, format, scoped mypy, compileall, UTF-8 and diff checks passed.
- Broad Factory fixtures were not accepted as fake green: `62` direct-stage
  failures were first reproduced, then every
  Architect/PM/CE/Director/QA stage call and Director fanout call received an
  explicit test-only exact authority. Five
  stage-level missing-port regressions fail before any role/service call, and
  the final fixture rereview returned `PASS` with P0/P1/P2 all zero.
- Independent implementation-specification and correctness/concurrency reviews
  returned `PASS` / `APPROVED`, each with P0/P1/P2 all zero.
- `FPR=N/A`: B3.2 freezes semantic authority but performs no physical provider
  dispatch. It does not count as a complete-context PASS and does not authorize
  Provider or Bench.

### B3.3 locked implementation contract (2026-07-19)

Independent specification audit returned `NEEDS_AMENDMENT` for the prior
one-line propagation description. The following contract is now authoritative
for this bucket:

1. One semantic freeze creates one exact runtime-private propagation port from
   the current exact `FactoryRoleEvidenceAuthorityBindingV1`, the exact
   post-cutoff binding, and the exact `FactoryRoleFrozenSemanticRequestV1`.
   Ordinary requests carry neither semantic freeze nor port. Factory requests
   carry both. A missing, extra, wrong-type, cross-role, cross-run, wrong-hash,
   or wrong-freeze pairing fails closed.
2. The port is a sidecar beside `AIRequest`; it never enters `AIRequest`,
   `context`, request options, cache keys/values, events, snapshots,
   `StageResult`, artifacts, provider wire, JSON, `repr`, or any
   `default=str` projection. Recursive runtime-authority leak checks include
   the port and its live Factory objects. Generic `dataclasses.asdict()` is not
   an authorized serialization path for the prepared runtime bundle.
3. The existing B3.2 public zero-transport barrier remains active. The
   propagation port's sync, async, blocking-async, and stream methods also
   raise
   `factory_role_semantic_request_frozen_physical_dispatch_not_enabled`
   before invoking `send`, `open_stream`, or any SDK. This defense remains
   until B3.4 and B3.5 atomically replace it with the qualified physical gate.
4. Sync primary, response-format fallback, reasoning-truncation retry,
   required-tool native/text retry, retryable-exception fallback, structured
   native/manual paths, role-binding fallback, stream initial/reconnect, and
   provider-internal retry all carry the sidecar by object identity. A
   role-binding fallback or any retry that changes messages, tools,
   `tool_choice`, `response_format`, provider, model, temperature, output
   budget, or stream mode must re-prepare, re-cutoff, re-freeze, and obtain a
   new matching port; it may not reuse the prior semantic authority.
5. Factory structured calls cannot use Instructor's direct OpenAI/Anthropic
   SDK path or its hidden reasks. Until that path accepts the exact port and
   exposes every physical reask, Factory skips it and uses the governed native
   or manual path. Ordinary role-session behavior remains unchanged.
6. Stream binding covers async-generator iteration, response cleanup and
   terminal settlement, not only generator creation. Success, error,
   `CancelledError`, `GeneratorExit` and `aclose()` restore the prior
   ContextVar. Nested calls restore the prior token; concurrent tasks and
   `to_thread` workers remain isolated.
7. B3.3 does not create a process-global coordinator and does not synthesize
   physical-attempt authority. Factory-owned aggregate physical budget,
   run-scoped coordinator injection, per-attempt parity, full context
   qualification and snapshot re-read remain B3.4/B3.5 work.

The transport-capable `FinalProviderAttemptGate` cannot be enabled in B3.3:
its current production construction seam lacks the Factory-owned run-scoped
coordinator; it does not consume `execution_authority_hash` or the aggregate
32-attempt physical budget; and its present equivalence checks do not yet prove
the complete token/window/coverage/alias/readable-snapshot contract. Enabling
it now would authorize I/O before qualification is complete.

#### B3.3 RED matrix

- Invariants (`5`): Factory freeze without port; ordinary request with a port;
  wrong exact port type; freeze/port identity or hash mismatch; every disabled
  dispatch method proves `send` / `open_stream` count is zero.
- Propagation (`10`): sync primary; response-format fallback;
  reasoning-truncation retry; required-tool native retry; required-tool text
  retry; retryable-exception fallback; role-binding fallback with a new freeze
  and new port; structured native; structured Instructor direct-SDK denial;
  structured manual fallback.
- Stream/provider (`3`): initial stream; reconnect; provider-internal retry.
  Reconnect and provider retry observe the same semantic-freeze port in B3.3;
  B3.4 later mints a distinct physical-attempt identity for every actual try.
- Lifecycle/isolation (`4`): success/error reset; cancellation/`aclose` reset;
  nested binding restoration; two-task plus `to_thread` isolation.
- Governance: Factory cache cannot satisfy a governed call; recursive
  non-leakage covers the sidecar; all five roles are parameterized; B3.3
  remains `FPR=N/A`, zero outbound, Provider/Bench forbidden.

#### B3.3 closure evidence (2026-07-19)

- One exact runtime-private propagation port now follows each Factory semantic
  freeze through the private sync dispatch seam, structured/manual fallback,
  every semantic retry, role-binding fallback, initial stream and reconnect.
  Public `call()` remains stopped by the B3.2 zero-transport barrier; the
  private seam test is not represented as public provider authorization.
- Every semantic-changing retry re-prepares, re-cuts off, re-freezes and mints
  a new exact port. Retry context metadata, request context and ContextOS audit
  share the fresh projection digest; prior source ids remain explicit, while
  snake_case and camelCase snapshot/degraded/attempt-receipt references are
  removed before the new attempt is audited.
- Factory Instructor direct-SDK dispatch is denied. Stream ownership is closed
  at roles.kernel, AIExecutor and StreamExecutor; per-`anext` binding, nested
  prior-token restoration, cross-task `aclose`, cancellation and terminal
  cleanup are covered without leaking Factory concrete types into KernelOne.
- Main-agent acceptance passed the complete related set at `333 passed` with
  two existing experimental-stream deprecation warnings. Ruff, format, scoped
  mypy with `--no-incremental`, compileall and diff checks passed. Independent
  specification and quality/security reviews both returned `CLEAR`, with
  P0/P1/P2 all zero.
- `FPR=N/A`: B3.3 performs zero physical provider transport. Provider and
  Bench remain forbidden and Bench remains `not_schedulable`. B3.4
  implementation is the next and only active bucket after independent
  specification and quality/security rereviews both returned `CLEAR` with
  P0/P1/P2 all zero.

### B3.4 locked implementation contract (CLEAR; implementation active)

B3.4 closes physical-attempt admission and conservation only. It does not
qualify final provider context, enable the B3.3 public Factory barrier, call a
real provider, or authorize Bench. `FPR=N/A` remains mandatory until B3.5.

#### Ownership and injection

1. `FactoryRunService` owns exactly one physical-attempt coordinator for each
   active Factory run. The same object is injected into Architect, PM
   initial/recovery, Chief Engineer, every Director fanout child, QA, every
   semantic freeze, retry, fallback and stream reconnect belonging to that
   run. Creating a coordinator per call/freeze/role/stage or using a
   process-global singleton is forbidden.
2. Factory remains the authority for the fixed per-grant budget and
   `execution_authority_hash`. Consumption is aggregated by that hash across
   all semantic freezes and every physical route; a new gate, retry, fallback,
   fanout child or reconnect cannot reset it. Ordinary role sessions use a
   distinct session-scoped coordinator and cannot claim Factory authority.
3. `roles.kernel` owns generic gate/state-machine behavior and may consume
   only an injected KernelOne/public admission-and-drain protocol. Factory
   must not import `roles.kernel.internal`; `roles.kernel` must not import
   Factory internals. The runtime-only authority/coordinator objects remain
   recursively absent from `AIRequest`, provider bodies, events, snapshots,
   artifacts, metadata, JSON and `repr`.
4. `FactoryRoleEvidenceAuthorityPort` registers, closes and revokes each grant
   in that run coordinator. Its exact role binding carries one runtime-only
   `physical_attempt_control_port`; all sidecars for the same grant hold the
   same control-port/coordinator identity.
   The injected object is one run-scoped Factory live-control adapter backed
   by the one run coordinator. It still exposes only the seven locked protocol
   methods. At `reserve`, while holding the Factory authority lock before the
   coordinator lock, it derives and registers the exact wire-aware cutoff view
   from its Factory-owned grant plus the typed reserve command, then delegates
   the atomic reservation. This is the sole live bridge for the late-bound
   `physical_wire_hash`; adding an eighth public registration method, trusting
   a caller mapping, or importing Factory internals from `roles.kernel` is
   forbidden.
5. KernelOne owns only the generic physical dispatch runtime port and immutable
   attempt DTOs. Provider adapters consume that generic port only: they never
   mint identities, calculate Factory budgets or read Factory authority.

The exact public surface added for this bucket is:

- commands: `ReserveFactoryPhysicalAttemptV1`,
  `BeginFactoryPhysicalAttemptStartV1`,
  `CommitFactoryPhysicalAttemptStartV1`,
  `AbortFactoryPhysicalAttemptReservationV1`,
  `MarkFactoryPhysicalAttemptStartAmbiguousV1`,
  `SettleFactoryPhysicalAttemptV1` and
  `FailFactoryPhysicalAttemptTerminalV1`;
- immutable results: `FactoryPhysicalAttemptReservationV1`,
  `FactoryPhysicalAttemptStartPermitV1`,
  `FactoryPhysicalAttemptLeaseV1`,
  `FactoryPhysicalAttemptBudgetStateV1`,
  `ProviderAttemptStartReceiptV1` and
  `ProviderAttemptTerminalReceiptV1`; and
- the exact runtime protocol `FactoryPhysicalAttemptControlPort` with sync
  methods `reserve`, `begin_start`, `commit_started`, `abort_reservation`,
  `mark_start_ambiguous`, `settle` and `terminal_persistence_failed`.

`reserve` returns the reservation; `begin_start` returns the start permit;
`commit_started` accepts the exact start receipt and returns the one-shot
physical lease; every abort/ambiguous/settle/failure operation returns the
updated budget state. `StrictProviderAttemptLifecycleStore.append_start` and
`append_terminal` return the typed start and terminal receipts respectively;
neither a `None` return nor a caller-fabricated mapping is accepted.

Every command/result carries exact schema version plus the identity fields
applicable to its phase. The complete reservation identity is
`factory_run_id`, controlled child `run_id`, `role`, `turn_id`, `call_id`,
`request_freeze_id`, `execution_authority_hash`, `attempt_budget`, provider,
model, semantic hash, physical-wire hash, composite hash, `reservation_id`,
globally unique `provider_request_id` and authority-hash-local monotonic
ordinal. Start permit, physical lease and start/terminal receipts repeat and
exact-match that identity; a lease is one-shot and cannot be substituted
across reservation, request id, run, role, child, freeze, authority hash or
composite hash. Start/terminal receipts additionally bind lifecycle event id,
logical sequence, event hash, phase and durability ACK. Any mismatch enters
the typed ambiguous/terminal-failure path and produces zero new outbound.

The runtime-only control port is added to
`FactoryRoleEvidenceAuthorityBindingV1`. `FrozenFinalProviderAttemptV1` and
strict lifecycle start/terminal facts add exact `execution_authority_hash`,
`attempt_budget` and the authority-hash-local monotonic ordinal; the composite
hash binds all three. Drain results expose reserved, start-persisting,
ambiguous, committed, terminal, in-flight, recovered, terminal failures and
consumed/remaining budget by authority hash.

#### Atomic reservation and state machine

One exact reservation operation replaces the current separated
`mint_attempt_identity()` plus `register()` sequence. In one non-awaiting
linearization section it must:

1. validate exact run, scope, role, controlled child run, live stage claim,
   grant, cutoff/freeze identity, `attempt_budget` and
   `execution_authority_hash`;
2. reject closed, revoked, stale, forged, wrong-role, wrong-child or exhausted
   authority before snapshot, lifecycle append or outbound callback;
3. atomically reserve one unit of capacity using the state-derived invariant:
   `reserved_count = count(RESERVED) + count(START_PERSISTING)`,
   `ambiguous_count = count(START_AMBIGUOUS)`,
   `committed_count = count(distinct provider_request_id whose reservation has
   an authoritative durable start receipt)`. This identity set is the sole
   committed-budget source; overlapping live/recovered state ancestry cannot
   add another unit. Admission is legal only when
   `committed_count + reserved_count + ambiguous_count < attempt_budget`;
   reservation occupies capacity without consuming committed budget yet;
4. mint the authority-hash-local monotonic candidate ordinal and globally
   unique `provider_request_id`; and
5. publish the reservation into the same run/session coordinator's visible
   in-flight set before returning it.

The only legal progression is:

```text
RESERVED
  |-- ABORTED
  `-- START_PERSISTING
        |-- ABORTED                    # definite proof that start did not persist
        |-- START_AMBIGUOUS
        |     |-- ABORTED              # strict replay proves start absent
        |     `-- RECOVERED_START_ABORTING
        |           |-- TERMINAL_ACKED # cancelled recovery terminal; never dispatch
        |           `-- TERMINAL_PERSISTENCE_FAILED
        `-- START_COMMITTED
              |-- TERMINAL_ACKED
              |-- TERMINAL_PERSISTENCE_FAILED
              `-- RECOVERED_START_ABORTING # replay-fenced unmatched durable start only
                    |-- TERMINAL_ACKED      # cancelled recovery terminal; never dispatch
                    `-- TERMINAL_PERSISTENCE_FAILED
```

No transition may be skipped or reordered. Reservation only occupies capacity
and cannot authorize transport. `begin_start` acquires an active authority
lease and, under grant-then-coordinator locks, revalidates the live stage claim,
grant and complete reservation identity before entering `START_PERSISTING`.
Only then may roles.kernel perform the synthetic B3.4 start-persistence seam
outside both locks. An exact durable start receipt commits the reservation and
mints the one-shot `FactoryPhysicalAttemptLeaseV1` required by `send`,
`open_stream`, SDK or CLI. Durable start is the physical-attempt and
budget-consumption linearization point.

Close/revoke publishes closed state and atomically aborts plain `RESERVED`
entries. If close/revoke wins before `begin_start`, the loser writes no start
and sends nothing. If `begin_start` wins, close/revoke waits for that active
lease to resolve to `ABORTED` or a terminal state; it cannot invalidate a
committed attempt before terminal. A definite start-write failure aborts,
consumes no budget and produces zero outbound. An ambiguous fsync outcome must
never ordinary-abort or dispatch: it enters `START_AMBIGUOUS`, conservatively
occupies capacity, freezes new reservations for that authority hash and fails
the run/drain closed until strict recovery.

On restart, the run coordinator is rebuilt from the strict lifecycle ledger
before any reservation or outbound is allowed. Every durable start counts as
committed budget, including terminal/recovered descendants. An unpaired or
uncertain start quarantines the run and its authority hash. Strict replay that
proves the start exists may only transition through
`RECOVERED_START_ABORTING`, append one cancelled terminal and leave the run
failed; it must never redispatch. Strict replay that proves no start exists may
transition to `ABORTED`. If neither proof is possible, quarantine remains.
Structural drain may converge after the cancelled recovery terminal, but that
does not convert the quarantined run into success.

The replay-only edge into `RECOVERED_START_ABORTING` is legal from
`START_AMBIGUOUS` when strict replay proves the durable start, or from an
unmatched `START_COMMITTED` reconstructed from an authoritative durable start
with no authoritative terminal. It is available only behind the restart replay
fence, never during live dispatch. A `START_COMMITTED` with a matching durable
terminal reconstructs directly as `TERMINAL_ACKED`; it cannot be cancelled a
second time.

Restart never reconstructs the private grant nonce or revives a live grant
registry row. Before replay, Factory places the recovered run behind a strict
replay fence that forbids live stage/grant mutation and new admission. Factory
then captures one head vector containing the Factory stage event-chain head,
the committed role-evidence cutoff-stream head, the provider-attempt
lifecycle-stream head and the current run/stage fence identity.

Factory reads every source strictly to that vector and builds a private,
immutable, detached **replay-only grant-view set**. Each element is keyed by
`(execution_authority_hash, request_freeze_id, cutoff_fact_id,
cutoff_sequence, cutoff_event_hash)` and is derived from exactly one committed
`FactoryRoleEvidenceCutoffBodyV1` plus the exact Factory stage
claim/event/persistence facts for that grant. Multiple freeze/cutoff elements
under one `execution_authority_hash` are valid and required; consumed budget,
ordinals and lifecycle descendants are aggregated across the complete set by
authority hash. An exact idempotent re-read of the same cutoff fact identity is
one element. A second distinct fact claiming the same freeze/cutoff identity,
or one identity with different bytes/hash, is a duplicate conflict.

Each cutoff element must bind the same Factory run, role, controlled child run,
`execution_authority_hash`, `attempt_budget`, stage claim/fence and source
identity as the Factory facts. A strict roles.kernel public lifecycle replay
query supplies only the independently verified lifecycle snapshot at its
captured head; lifecycle facts cannot create, reconstruct or self-prove
Factory grant authority. Missing, duplicate or cross-view grant facts,
cross-child identity, budget/hash/freeze drift, duplicate/regressing ordinal or
any lifecycle fact without one exact replay-view element quarantine the run.

After constructing and validating the candidate set, Factory immediately
re-reads all three heads plus the run/stage fence and compare-and-installs the
coordinator only if the complete vector is unchanged. Head/fence drift discards
the candidate and performs zero admission or recovery-terminal append; the
implementation must restart the complete replay from a newly captured vector.
The sole bound is the immutable Factory-owned
`FactoryPhysicalAttemptReplayPolicyV1` with
`schema_version="factory.physical_attempt_replay_policy.v1"`,
`max_full_replays=3` total candidate builds including the initial build, and
`total_deadline_seconds=30.0` measured by a monotonic clock; callers,
environment and provider configuration cannot override it. Exhausting either
bound quarantines with stable code
`factory_physical_attempt_replay_head_unstable` and produces zero coordinator
admission, recovery-terminal append, reservation or outbound. Deterministic
concurrent append barriers and a fake monotonic clock must cover drift and
exhaustion across every capture/read/recheck boundary.
The cancelled recovery terminal uses an expected-previous-lifecycle-head CAS
under the same replay fence; CAS or Factory/cutoff head/fence drift fails closed
without a terminal append. The successful append advances the installed
lifecycle head exactly once.

Replay-only view elements contain no grant nonce, live capability or mutation
method. They can only validate lifecycle identity, reconstruct consumed budget
and authorize the one cancelled recovery terminal required for a proven
durable start. They cannot `reserve`, `begin_start`, mint a lease or dispatch.
The pre-crash grant and the entire recovered `factory_run_id` remain
permanently dead for new authority: no stage in that run may mint a replacement
grant or perform later outbound. Any later outbound belongs to an entirely new
Factory run with a new `factory_run_id`, admission, stage claim/fence, grant and
cutoff; it is not continuation or replay of the recovered run.

Every replayed start/terminal must exact-match one Factory-owned replay-only
grant-view element for factory run, role, controlled child run,
`execution_authority_hash`, `attempt_budget`, authority-local ordinal and
`request_freeze_id`. Unknown view, cross-view identity or incomplete
authority-hash aggregation immediately quarantines the run and performs zero
new reservation/outbound.

A result cannot escape before durable terminal ACK and `TERMINAL_ACKED`.
Terminal persistence failure never returns success and remains visible to
`wait_settled`; cancellation cannot erase it. `wait_settled` is true only when
every reservation is `ABORTED` or `TERMINAL_ACKED`; `START_PERSISTING`,
`START_AMBIGUOUS`, `START_COMMITTED`, `RECOVERED_START_ABORTING` and
`TERMINAL_PERSISTENCE_FAILED` all block or raise. Available capacity is
`attempt_budget - committed_count - reserved_count - ambiguous_count` using
the exact state-derived counts above. `START_PERSISTING` therefore never
releases reserved capacity during fsync. Aborted entries release capacity,
while every committed/recovered start remains consumed after terminal.
429, structured reask, provider retry/fallback, SDK retry, CLI/subprocess
launch and stream reconnect each reserve and consume one independent attempt.

The fixed lock order is grant-authority lock, then run-coordinator lock. The
reverse order is forbidden. Await, synchronous I/O, fsync, snapshot/FactStream
access, storage locks, provider/SDK calls, subprocess launch and callbacks are
all forbidden while holding either lock; storage code cannot call back into the
coordinator while holding its own lock.

Stable fail-closed codes are:
`factory_physical_attempt_control_port_required`,
`factory_physical_attempt_control_port_exact_type_required`,
`factory_physical_attempt_coordinator_scope_mismatch`,
`factory_physical_attempt_factory_run_mismatch`,
`factory_physical_attempt_role_mismatch`,
`factory_physical_attempt_controlled_run_mismatch`,
`factory_physical_attempt_execution_authority_hash_mismatch`,
`factory_physical_attempt_budget_mismatch`,
`factory_physical_attempt_authority_closed`,
`factory_physical_attempt_grant_revoked`,
`factory_physical_attempt_budget_exhausted`,
`factory_physical_attempt_reservation_unknown`,
`factory_physical_attempt_reservation_state_conflict`,
`factory_physical_attempt_start_persistence_failed`,
`factory_physical_attempt_start_commit_ambiguous`,
`factory_physical_attempt_transport_before_start`,
`factory_physical_attempt_duplicate_identity`,
`factory_physical_attempt_terminal_unknown` and
`factory_physical_attempt_transport_hook_missing`,
`factory_physical_attempt_replay_head_unstable`, plus the existing typed
terminal-persistence/drain timeout/scope errors. Tests assert codes, not
free-form messages; every admission error is zero transport.

#### Transport inventory and zero-outbound proof

Every registered provider mode must appear in one static inventory and be
classified exactly once as `governed_supported` or
`factory_disabled_opaque`. Every `governed_supported` mode must prove that one
concrete physical HTTP/async/stream/SDK attempt crosses the injected gate, and
every actual retry/fallback/reconnect on that mode must cross again. This
includes direct MiniMax/Ollama/Gemini sync calls; Kimi/MiniMax/Ollama/OpenAI
async streams; async Ollama/Gemini streams; and version-locked Codex SDK
attempts. Missing capability or hidden retry fails closed;
`legacy_ungoverned`, raw transport fallback and silent SDK retry are forbidden
for Factory.

`factory_disabled_opaque` modes receive no positive physical-attempt coverage:
they remain inventoried, Factory-disabled and prove HTTP/SDK/subprocess
outbound count exactly zero. Codex CLI and Gemini CLI PTY, non-PTY, winpty and
fallback `Popen` branches are in this class until every internal HTTP attempt
has a governed hook. A successful outer subprocess launch, outer `retries=0`
claim or capability declaration cannot promote them to
`governed_supported`.

The inventory covers governed role inference for Architect, PM, Chief
Engineer, every Director child and QA. Health, `list_models` and unrelated
administrative probes are explicitly excluded and cannot satisfy role-attempt
coverage. Opaque CLI/agent SDK execution is disabled for Factory unless it
exposes one governed hook for every internal HTTP attempt. An outer
`retries=0` argv/config claim or patched subprocess sentinel is never
sufficient because neither observes hidden reconnects or ignored config. Only
a version-locked, non-opaque single-request SDK transport with independent
tests proving the exact request boundary may use disabled built-in retries as
sufficient evidence; otherwise every actual HTTP retry must independently
cross the gate. Provider capability self-declaration is not evidence.

B3.4 verification is entirely synthetic: patch `requests`, `aiohttp`, SDK
clients and `subprocess` entry points with counting sentinels and use in-memory
fake callbacks for accepted paths. Real provider and Bench call counts remain
zero. The B3.3 public Factory stop remains unchanged until B3.5 atomically adds
full-context qualification and readable snapshot proof.

B3.4 may exercise only an opaque synthetic/pre-existing pin seam to test its
state machine; that is not snapshot qualification. B3.5 must atomically insert
complete final-request qualification, durable persist, same-workspace 24-hex
re-read and pin validation before strict start/transport can be enabled.

B3.5 also owns the non-authoritative rejection schema
`llm.final_provider_attempt_qualification_rejection.v1`, represented by
`FinalProviderAttemptQualificationRejectionV1`. roles.kernel appends it through
`events.fact_stream.public` to the separate run/session-scoped logical stream
`roles.kernel.final_request_qualification_rejections.<scope-hash>`. It binds
scope/run/role/turn/call/freeze identity and one stable rejection code, but has
no `provider_request_id`, reservation, lifecycle start/terminal or budget
effect. Factory may audit its presence as failure evidence; it can never enter
or satisfy the physical provider-attempt inventory.

#### B3.4 mandatory RED matrix

- 64 concurrent competitors against one budget-32 grant yield exactly 32
  committed starts, transport entries, terminals and consumed units. A
  different freeze/gate/retry/fallback/reconnect under the same hash cannot
  reset the counter; a different grant remains isolated.
- Forged/wrong/stale/closed/revoked authority, wrong run/role/child/freeze and
  caller-selected budget fail before reservation effects or outbound.
- Definite start fsync failure aborts the reservation with zero consumption and
  zero send. Ambiguous start commit never sends and makes drain fail closed.
  Deterministic barriers prove no durable start can be unknown to the
  coordinator and no outbound can precede the exact start receipt.
- Process restart strictly replays starts/terminals and reconstructs per-hash
  budget before admission. An unmatched `START_COMMITTED` and a
  `START_AMBIGUOUS` proven to have a durable start both enter the replay-only
  `RECOVERED_START_ABORTING` edge; recovery may append only one cancelled
  terminal per unmatched start and never redispatch.
- Restart installs a replay fence, captures one Factory-stage/cutoff/lifecycle
  head vector, builds the detached replay-only grant-view set with one element
  per exact freeze/cutoff identity, aggregates all elements by authority hash,
  and exact-matches every lifecycle fact against one element. A second legal
  freeze under the same hash is retained, while duplicate/conflicting cutoff
  identity is rejected. Capture/read/recheck drift at every head or fence
  discards the candidate; three total full replays or the 30-second monotonic
  deadline, whichever occurs first, exhausts with
  `factory_physical_attempt_replay_head_unstable`. Recovery terminal uses
  expected-head CAS. Nonce reconstruction, lifecycle self-proof, old-grant
  revival and any outbound in the recovered `factory_run_id` are forbidden.
- Ambiguous replay that proves a durable start exists transitions through
  recovery with consumed count exactly one; its cancelled terminal ACK never
  refunds that budget unit.
- A mixed recovered run with pre-existing normal terminal pairs plus multiple
  unmatched starts proves total consumed budget equals all durable starts,
  cancelled recovery terminals equal only the unmatched subset, every start
  has exactly one terminal after recovery, a recovered start consumes one unit
  despite overlapping state ancestry, and new transport count is zero.
- Close/revoke after reserve but before `begin_start` aborts with zero start;
  close/revoke after `begin_start` waits for abort/terminal. Cross-run/role/
  child/freeze/attempt lease substitution is rejected before outbound.
- Sync, native async, blocking-worker, stream success/error/cancellation,
  `GeneratorExit`, `__aenter__`, consume and `__aexit__` failures each conserve
  one reservation, one start and one terminal; session/response/subprocess
  ownership has no leak.
- Terminal fsync failure blocks drain and cannot return provider success.
- Revoke/close versus reserve/start is linearized as above; the first publisher
  wins and the loser either performs zero start/send or completes only the
  already-won attempt and terminal.
- Two Factory runs plus one ordinary role session prove independent attempt
  numbers, budgets, in-flight state, terminal failures and drains.
- Static provider-registry coverage classifies every registered mode exactly
  once. Patched sentinels prove every `governed_supported`
  HTTP/stream/SDK route is governed per physical attempt, while every
  `factory_disabled_opaque` CLI/SDK/subprocess route remains disabled with
  outbound count zero.
- A live, non-recovered green completion drain proves the generalized B3.4
  equality:
  `transport callback entries = unique provider_request_id = strict starts =
  strict terminals = consumed budget`. Reserved, aborted and qualification
  rejection paths are zero physical attempts; terminal persistence failure is
  a failed drain, never green conservation.
- A recovered failed run has a separate structural-settlement invariant:
  `total_consumed_budget = |all_authoritative_durable_start_ids|`;
  `all_authoritative_durable_start_ids =
  preexisting_authoritative_terminal_ids disjoint-union
  unmatched_recovery_start_ids`; and
  `cancelled_recovery_terminal_ids = unmatched_recovery_start_ids`. After the
  recovery CAS, all authoritative start ids therefore have exactly one
  terminal, but only the unmatched subset receives a new cancelled terminal.
  New transport entries are exactly zero. Pre-crash transport cannot be
  inferred from a durable start and cannot satisfy green evidence. Structural
  settlement never changes the run from failed/quarantined to successful.
- Ordinary non-Factory no-port behavior remains compatible, consumes no
  Factory DTO/hash/budget (any session budget uses a separate non-Factory
  contract), and no runtime
  authority/coordinator object leaks into serializable state.

## 8. RED matrix

- Pre-cutoff and post-cutoff carriers are distinct exact types; neither accepts
  mappings, and the live port never appears in the post-cutoff proof.
- A Factory seam cannot construct or bind a post-cutoff proof before a matching
  semantic candidate and durable ACK exist.
- PM, direct CE, non-fanout Director, every fanout child, and QA have the correct
  pre-cutoff authority carrier and role identity.
- A barrier-controlled acquisition paused after live request binding cannot
  race stage close: close waits for the acquisition to observe revocation and
  drain; the acquisition returns no ACK and appends no commit after close
  begins, and no later provider gate may accept it.
- Barriers immediately before authoritative commit and ACK publication prove
  both close and per-grant revoke win atomically when published first; the
  losing acquisition returns the stable closed/revoked error with no later
  commit or ACK. The inverse ordering proves commit/ACK may win only while
  holding the shared linearization lock.
- docs-generation `run_type="architect"` is governed too; every physical role
  path is covered even when it is outside the PM -> CE -> Director mainline.
- Architect has its own role policy; it never impersonates PM. Because
  `docs_generation` precedes PM planning, its causal Factory evidence policy is
  exactly the admission-time `pm_raw_intent`, required present. Later-stage
  PM/CE/Director/QA evidence cannot be fabricated or back-projected into it.
- Factory rejects wrong-stage role grants, caller-selected budgets, and cutoff
  requests whose execution-authority hash or budget differs from the minted
  grant.
- PM initial/recovery and Director fanout siblings have distinct opaque grant
  hashes. The same grant may cover multiple semantic freezes for exactly one
  controlled child run, but its 32-attempt physical budget is aggregated by
  grant hash and never resets per freeze.
- Stage grant cardinality is exactly bounded at 1/2/1/512/1 for
  Architect/PM/Chief Engineer/Director/QA; overflow creates no role task and no
  cutoff fact.
- Unknown, closed, revoked, stale-claim, wrong-role, wrong-budget, wrong-hash,
  and wrong-child-run grants fail before ledger creation, source resolution,
  FactStream append, or cutoff ACK.
- Factory imports only the `roles.kernel.public` cutoff contract; no cross-Cell
  internal import is introduced.
- Missing/malformed/wrong-role pre-cutoff authority fails before Factory request
  preparation; an absent or malformed post-cutoff proof fails before dispatch.
- recovery without a reconstructed live port fails closed.
- fanout children have distinct call/freeze/attempt identities and cannot
  inherit another child's authorization.
- the canonical evidence block is in the first system message and derived
  input/token/digest/audit projections are recomputed afterward.
- the runtime port is absent from every serializable request/snapshot/event.
- sync, structured, stream, role-binding fallback, provider fallback, and
  provider-internal retry each reach the physical gate.
- `N` physical attempts yield exactly `N` started/terminal lifecycle pairs and
  `N` independently readable snapshots; no PASS is reused.
- missing tools, wrong `tool_choice`, wrong `response_format`, alias drift,
  stale anchors, missing evidence slots, wrong role/workspace, clipped context,
  messages-only audit, and unreadable snapshot all produce zero transport
  calls and one non-physical
  `FinalProviderAttemptQualificationRejectionV1` audit fact owned by the B3.5
  roles.kernel qualification gate. That fact is keyed by call/freeze plus a
  stable rejection code; it carries no `provider_request_id`, reservation,
  start or terminal lifecycle fact, consumes no attempt budget and cannot
  satisfy provider-attempt inventory.
- exception/cancellation resets both Factory binding and physical dispatch
  ContextVars.
- fanout cancellation awaits every child; after stage authority closes, late
  inherited calls fail closed and no carrier, live port, registry row, or nonce
  appears in `StageResult`, `AIRequest`, context, event, snapshot, artifact,
  provider payload, or JSON.
- ordinary non-Factory role calls remain unchanged and cannot claim Factory
  qualification.
- architecture fences prove no PM-to-Director path and no Bench dependency.

## 9. Verification ladder

1. RED tests for the current cutoff stop and every missing propagation seam.
2. Focused role-binding, request-preparer, Invoker, stream, provider-attempt,
   lifecycle, and snapshot tests.
3. Factory role-evidence and complete Factory Pipeline suites.
4. KernelOne architecture release tests and aggregate release gate.
5. Ruff check/format, scoped mypy, compileall, JSON/YAML parse, catalog
   governance, and `git diff --check`.
6. Independent specification review, then independent code-quality review.

No real role provider call and no Bench run is authorized until B3.0-B3.5 are
green and both reviews clear.
