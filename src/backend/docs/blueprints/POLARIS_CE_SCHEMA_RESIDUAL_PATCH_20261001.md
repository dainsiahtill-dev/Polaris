# CE schema residual patch — 2026-10-01

Status: implementation in progress; no fresh acceptance.
Owner: `factory.pipeline`, existing CE schema-repair orchestration. No new state owner/public API/effect port.
Master plan: unattended development P06, retained candidate and diagnosis-scoped repair.

## Dynamic evidence

Fresh L1-01 `factory_8edf12521449` failed before Director in 582.2s. PM succeeded.
Four physical CE calls occurred; the stage's logical `llm_calls=3` is not the physical call count.
The first rejected native candidate remains in `events/chief_engineer.llm.events.jsonl`.
Exact offline validation reports additional root fields and missing required members, including
an array-element object. Current missing-member patch allows object-only required errors;
mixed errors force complete reconstruction. Three subsequent returned responses did not yield
a validated portfolio. Provider completion is not schema/CE approval.
Raw stream bytes are not available; Provider-versus-assembly origin remains unproven.

## Bounded design

Existing claim/lease/deadline/final-request authority
  -> retained decoded candidate (untrusted draft)
  -> exact schema diagnostics
  -> immutable candidate hash + typed diagnostic edit schema
  -> same CE result-submission tool
  -> pure server-side draft composition
  -> full original schema validation
  -> existing CE semantic/PM authority validation
  -> existing blueprint/handoff publication.

Only two edits: add one currently missing required member, remove one currently
forbidden additional member. Array indices refer to existing elements only; no insert,
append, replace, reorder, deletion of declared members, or free-form paths.
Every operation and path is a schema `const`, every operation is required exactly once,
and every added value uses the original property schema. Echo the immutable base hash.
Unsupported diagnostic classes or ambiguous schema constructs retain strict reconstruction.
Limit planning to 16 edits and path depth 16; this is an optimization, never a success fallback.

The model explicitly adjudicates each forbidden draft-member removal. Do not silently erase
fields or relocate them by guessed semantics. Preserve other subtrees byte-semantically.
The draft candidate is not a target artifact and not authority. Preserve physical patch provenance
separately from the composed candidate; never relabel composed data as raw Provider output.
No target hand edits, QA changes, larger retry count, transport allowance, or deadline extension.
Keep the existing required-object merge-patch fast path unchanged.

## Assumptions / pre-mortem

- A1: native candidate is retained: proved by exact event; visible output0 is not candidate0.
- A2: existing repair claim and result-tool contract can carry a typed partial payload: existing required-path tests.
- A3: full schema success is insufficient: existing downstream semantic/authority checks must still run.
- Main risks: trusting arbitrary patch paths; mutable candidate drift; hidden sibling overwrite;
  replacing physical evidence with composed content; declaring local tests as project completion.
- Countermeasures: schema-derived const paths, frozen serialized plan, hash check, no replacement
  operations, unchanged full schema and downstream gates, explicit composition hashes/provenance.

## Verification

1. Existing Factory flow with mixed required/additional errors must select exact patch and preserve
   original valid completion contract. Observe RED before implementation, then GREEN.
2. Pure planner/composer: required members inside arrays, stale candidate hash, forged paths,
   overwrites, reordered/missing operations, unsupported type errors, invalid added values,
   and caller mutation all tested. No target or Provider effects in these tests.
3. Replay retained native candidate: exact diagnostic locations only; no fabricated missing semantics.
4. Existing CE handoff/schema-repair/lease/deadline suites, Ruff/Mypy, relevant cascade.
5. After code gates, restart only the owned isolated project backend through Supervisor externally;
   retry the same CE run, preserving PM. Later fresh no-intervention proof remains required.
