# Factory R42B: final-request evidence prompt isolation

Status: closed  
Bench: R42B itself grants no Provider authority; pre-bench must authorize the next run  
Scope: KernelOne final-request evidence prompt projection and Roles Kernel request qualification.

## Observed fact

Fresh isolated L1-04 R41 produced readable, qualified Chief Engineer final-request
snapshots. `context_os_audit.ok` was nevertheless false because the provider-visible
system message contained a canonical `polaris.final_request_evidence.v1` block whose
typed slots and anchors included control-plane identity fields such as
`factory_run_id`, `run_id`, `request_freeze_id`, `cutoff_fact_*`, source-head facts,
and `execution_authority_hash`.

## Root

`RoleFinalRequestPolicyFactsV1` is the strict authoritative cutoff record. Its
binding fields are required for replay, drift detection, qualification, and durable
hash validation. `render_role_final_request_policy_facts()` currently serializes
that full authority record directly into the provider prompt. One object therefore
serves two incompatible planes:

```text
authority/cutoff fact (control plane)
             |
             +-- current full serialization --> provider system prompt (data plane leak)
```

This violates the fixed ContextOS boundary: prompt projection must be read-only and
must not expose control-plane runtime state.

The first prompt-safe projection exposed a second, deeper coupling:
`FactoryRoleFrozenSemanticRequestV1.__post_init__` parsed the provider-visible JSON
back into `RoleFinalRequestPolicyFactsV1` and used it to reconstruct cutoff/run
authority. Provider text was therefore acting as a second authority source. A reduced
prompt schema correctly failed that reconstruction. The repair must remove this
reverse dependency, not restore the leaked fields.

## Target design

```text
RoleFinalRequestPolicyFactsV1 (full typed authority; unchanged)
             |
             +-- to_record()/canonical hash --> durable audit and qualification facts
             |
             +-- typed FactoryRoleEvidenceBindingV1 --> live dispatch equality check
             |
             +-- prompt-safe projector --> provider-visible evidence summary
                                           role, slot state, canonical evidence refs/hashes
                                           no run/cutoff/authority/source-head identity
```

### Prompt-safe allowlist

Top-level:

- prompt projection schema
- role
- ordered slots

Per slot:

- prompt slot schema
- `ref_kind`
- `state`
- `canonical_source_ref`
- `source_fact_schema`
- `source_fact_version`
- ordered item projections

Per item:

- prompt item schema
- `ref_kind`
- `canonical_source_ref`
- `canonical_ref`
- `canonical_hash`
- `source_fact_schema`
- `source_fact_version`

Explicitly excluded:

- `factory_run_id`, `run_id`, `request_freeze_id`
- `cutoff_fact_id`, `cutoff_fact_sequence`, `cutoff_fact_hash`
- `source_fact_id`, `source_fact_sequence`, `source_fact_hash`
- `source_head_sequence`, `source_head_hash`
- `execution_authority_hash`

## Assumption register

1. Provider needs evidence meaning and immutable content references, not control-plane
   attempt identity. Evidence payloads are already injected separately.
2. Frozen-request validation checks the prompt-safe schema only. Exact equality to the
   authoritative projection is checked at the live dispatch sidecar against the
   separately carried typed cutoff proof; Provider text is never parsed into authority.
3. Authoritative `to_record()` and canonical hashes remain unchanged; replay and
   conservation semantics therefore remain byte-stable.
4. Distinct prompt schema names avoid pretending a reduced projection satisfies the
   full authority-record schema.

## Pre-mortem

- Wrong fix: suppress `factory_run_id` in ContextOS audit. Failure: provider still sees
  control-plane state. Rejected.
- Wrong fix: delete binding fields from typed facts. Failure: cutoff replay and attempt
  qualification lose authority. Rejected.
- Risk: qualification or retry code expects old full JSON in the message. Mitigation:
  prompt construction consumes the renderer, frozen validation consumes the prompt
  schema validator, and physical dispatch compares the exact rendered bytes with the
  typed binding; run Roles Kernel cutoff, request-preparer, and physical-attempt
  qualification suites.
- Risk: reduced projection hides missing required evidence. Mitigation: typed facts
  validate required slot presence before projection; prompt keeps slot state and refs.

## Verification plan

1. Unit: renderer output contains ordered prompt schemas, ref kinds, states, canonical
   refs/hashes; no excluded control-plane field names or values.
2. Integration: Factory-bound PM/CE/Director/QA request preparation reports
   `context_os_audit.ok=true`, one terminal evidence block, correct role identity, and
   frozen projections remain equal.
3. Security: tampered/noncanonical/wrong-role evidence blocks remain fail-closed.
   A structurally valid but forged prompt projection must pass data-plane parsing and
   still be rejected when it does not equal the typed live binding.
4. Static: Ruff, format, mypy, compileall.
5. Suites: final-request evidence, Roles Kernel request-fact projection, cutoff,
   final-provider qualification, Factory Pipeline full, architecture, KernelOne release.
6. Only after all gates and snapshot checks pass may pre-bench authorize one fresh
   isolated L1-04 run.

## Exit gates

- Full authoritative record and hashes unchanged.
- Provider-visible evidence block contains no excluded control-plane identity.
- ContextOS prompt isolation passes for all Factory roles.
- Final provider request still contains correct role, required evidence refs, tools,
  `tool_choice`, `response_format`, token/window metrics, and readable 24-hex snapshot.
- No target-project edit, fallback chain, audit relaxation, or extra Provider call.

## Closure evidence

- Evidence/frozen/qualification integration: `321 passed`.
- Roles Kernel full: `2747 passed, 1 warning`.
- Factory Pipeline full: `1237 passed, 2 warnings`.
- KernelOne release: `415 passed, 1 skipped, 2 warnings`, report `ok=true`.
- Architecture: `1411 passed, 8 skipped`.
- Ruff, format, mypy, compileall, and diff check: pass.
- Post-edit CodeGraph review confirms prompt allowlisting and typed-binding dispatch
  equality; no control-plane authority is reconstructed from Provider text.
- Provider requests / Bench runs used for closure: `0 / 0`.
