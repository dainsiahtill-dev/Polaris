# Final Provider Attempt Qualification Blueprint

Status: locked; B3.0-B3.1 closed; B3.2-B3.6 pending
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
2. B3.1 now supplies a distinct **pre-cutoff authority carrier** and binds it at
   every Factory-controlled Architect, PM initial/recovery, Chief Engineer,
   Director direct/fanout-child, and QA task-creation seam. `RequestPreparer`
   does not consume that carrier yet, so physical provider dispatch remains
   fail-closed pending B3.2-B3.5.
3. `RequestPreparer` still reads the post-cutoff carrier before building the
   semantic request and deliberately raises
   `factory_role_evidence_cutoff_not_enabled`. Treating that carrier as the
   pre-cutoff input would invert causality and require fabricated ACK fields.
4. `AIExecutor` can accept a runtime-private physical dispatch port and bind it
   around provider invocation.
5. infrastructure HTTP helpers can consume the bound dispatch port immediately
   before a physical attempt.
6. Invoker sync/structured/fallback and stream paths do not yet prove the port
   is propagated through every call.
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
   carry the runtime-private port without payload contamination.
5. **B3.4 Physical-attempt parity**: every provider transport, internal retry,
   and fallback independently calls the gate.
6. **B3.5 Snapshot qualification**: same-workspace readable 24-hex snapshot,
   exact attempt identity, full audit and lifecycle conservation.
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
  calls and one failed attempt record.
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
