# ContextOS model preservation — POL-95

Mode: implementation. Owner: e42def29-b6a1-4fd2-9bf4-701fc0a3d747.
Baseline HEAD: 9bb5bd1b4eddca9c7d28495cc1de3bf7f4689f32, dirty main retained.

## Boundary and data flow

`raw input -> shared lexical normalization -> validated data fields -> WorkingStateV2 -> snapshot.to_dict -> private ReceiptStore/export -> UTF-8 JSON -> snapshot.from_mapping -> canonical WorkingStateV2.from_mapping`

Graph catalog and polaris/cells/kernelone/core/cell.yaml own these public models
in kernelone.core, with no dependencies or subgraphs. Its README.agent.md is a
historical declaration that lags the catalog; the generated pack is absent.
Reuse the existing WorkingStateV2 validated restoration path and lower-level pure
normalizer. No authority, graph boundary, effect scope, provider request, budget,
descriptor or admission contract changes. accepted_plan remains a state hint.

## Assumption register

- A1: Pydantic's model-wide trim runs after lexical cleanup and removes incomplete
  quoted tails in StateEntryV2.value and TranscriptEventV2.content. Root and the
  immutable ATTEMPT2_STATE_ENTRY_DEFECT identify this; constructor RED must confirm.
- A2: WorkingStateV2.from_mapping covers every declared working-state field;
  snapshot restoration duplicates only part of that list. Serialized roundtrip
  RED must identify every lost field, including decision links and history.
- A3: TranscriptEventV2's before model validator assigns into the caller mapping;
  its field validator already handles metadata. Deep-copy comparisons must
  prove immutability for model_validate and snapshot restoration.
- A4: Models currently trim outer prose only, while other normalizer callers fold
  prose and optionally remove tags. A pure optional folding switch must retain
  those defaults and model internal prose whitespace.
- A5: ReceiptStore uses an in-memory ContentStore. Private workspace stores plus
  real export/import and UTF-8 disk serialization suffice without native DB access.
- A6: CodeGraph MCP returned unrelated symbols for both exact queries; CLI status
  reports an older engine index. This task uses explicit file reads as a recorded
  fallback and does not re-index.
- A7: Actual 9,117-character private archive replay loses transcript content_ref
  hash/size/mime. Dynamic field differences and an additional serialization RED
  prove this authorized model seam; restore the existing nested wire representation
  into declared model fields using a copied mapping, retaining flat-field precedence.

## Design and pre-mortem

Use a field-specific constrained string with an after validator for proven data
fields, keeping Pydantic validation and ordinary identity/provenance trim. Preserve
complete/incomplete quoted data lexically; trim only external edges, retain internal
prose spacing and tags. Extend the pure normalizer only with a default-preserving
option. Do not parse/repair/serialize strings as a bypass. Remove redundant mutable
metadata preprocessing; retain its field validator. Replace snapshot's second
working-state parser with WorkingStateV2.from_mapping.

Most likely wrong fixes: global trim removal, folding model prose, losing dangling
escapes, weakening identifier/confidence validation, treating hints as approved
plans, using native archive storage, and broadening unrelated snapshot fields.
Each is controlled by focused tests or exact scope/hash checks.

## Acceptance and verification

New test: src/backend/polaris/kernelone/context/tests/test_contextos_model_state_preservation.py.
First run real constructors/from_mapping/serialized roundtrips RED. Then test
quoted Unicode/markup/escapes, ordinary prose, malformed-input rejection, every
working-state collection and provenance, caller immutability, all three real
SessionContinuityEngine input forms, and >5000-character private archive restore.
Replay the two immutable POL-94 input packages as data only; do not rerun their
scripts or alter historical artifacts/tests.

Run existing continuity, host, ContextOS, state/model/persistence, strict protocol
and PM parsing tests before and after; compare failing nodes and signatures.
Use existing .venv/bin/python3.13, PYTHONDONTWRITEBYTECODE=1, pytest cache disabled,
private basetemp/cwd. Run focused Ruff, format, Mypy and diff checks. Global/release
and native fresh isolated delivery gates are explicitly outside this source task.
Source writes must finish before report/review handoff; Root independently checks
source and exact-generation release, then assigns QA. No self-approval.

Dynamic verification also found that a process-wide private runtime-root override
changes two existing capacity tests' expected workspace-local snapshot paths and
can retain shared task claims between bundled runs. Keep the broad before/after
evidence unchanged; verify those controls separately using newly owned tmp_path
workspaces and default workspace-local runtime resolution. No native stores are
used, and these test-environment findings do not authorize runtime/storage edits.

## Authorized writes

Only models_v2.py, optional text_normalization.py, the new test, this blueprint,
the named verification card, and owned implementation evidence. No target/runtime,
native DB, old tests, source acceptance, configuration, services or Bench writes.

## POL-97 corrective boundary — before implementation

Mode: implementation, explicitly activated; same owner. HEAD
4bbe8e9b504c6afa3ce3006b105846d8c9a6c06a; models preimage SHA-256
5e4f867fd83896eda1c5517a46cb97d5e38dd392e2ad00388c72404462c34f67.
Root's negative QA disposition replaces the incomplete POL-96 report condition;
verified namespace_drained release is recorded in ROOT_QA_DISPOSITION.md. Neither
earlier source acceptance nor native execution is approved by this repair.

Checklist: freeze and reproduce; trace consumers; write RED; make one boundary
change; verify GREEN/prior51/wider failures; lint/type/diff; report; independent
Paperclip review. No delegation, provider call, Bench, service or target writes.

### Assumptions and dynamic evidence

- R1: The nested wire content_ref is copied into existing flat fields with
  setdefault. Four private direct calls confirm malformed size/hash/mime/encoding
  reach TranscriptEventV2 validation, then the broad ValidationError catch drops
  metadata and references. Evidence: implementation/repro-before.json.
- R2: TranscriptEventV2 is the canonical schema and already supplies missing
  optional reference defaults. Valid old snapshots do not need the second raw
  constructor. Keep schema and its coercion exactly unchanged; malformed events
  will now fail instead of being reconstructed with str/int coercions.
- R3: Flat fields have per-field precedence, including invalid flat fields.
  Validate effective fields after that precedence; a shadowed nested field is
  not selected evidence. Empty nested mappings remain supported; a supplied
  non-mapping content_ref (including null) is not the supported absent-reference
  representation and must raise ValueError at the wire boundary.
- R4: Private ReceiptStore/export plus UTF-8 disk persistence covers actual
  archive restoration without touching native receipts or reconstructing plans.

CodeGraph MCP found from_mapping but subsequent precise symbol queries returned
unrelated symbols. CLI status confirms an older engine index; recorded fallback
uses targeted reads/searches, without reindexing or altering trust/auth settings.
kernelone.core catalog/cell declare these models; no subgraphs/dependencies.
README.agent.md lags catalog; generated context pack is absent. No graph,
public schema, state-owner, descriptor or effect scope change is needed.

### Consumer trace and error propagation

Public ContextOSSnapshot aliases ContextOSSnapshotV2. SessionContinuityEngine.project
(session_continuity.py:524) restores before prompt/snapshot replacement;
ContextGateway._build_context_impl (gateway.py:360) restores before projection;
RoleSessionContextMemoryService._load_snapshot (context_memory_service.py:49)
restores before query results; StateFirstContextOS pipeline and memory/state
methods (runtime/engine.py:751,799,873,932,952,978,994) restore before consumption;
evaluate_context_os_case and extract_attention_trace (evaluation.py:366,588)
restore before evaluation. These seams do not catch Pydantic ValidationError.
They already propagate malformed working_state/artifact/budget validation.
Explicit failure for supplied invalid transcript references is the same contract.
None/non-dict snapshot input still means absent snapshot; omitted content_ref
still means optional fields default. No consumer-side recovery is required by
the valid-input controls; historical invalid source stays untouched.

### Minimal change and pre-mortem

Keep receipt lookup, copied nested mapping, flat precedence, lexical preservation
and canonical WorkingState restoration. Remove the transcript ValidationError
fallback and pass each effective event directly through canonical model_validate.
Check only the nested reference container shape before decoding; do not add a
reference schema, hash format rule, new coercion or PM plan admission.

Likely wrong fixes: catching errors and returning empty metadata; validating
nested values before flat precedence; mutating caller dictionaries; treating a
missing optional reference as invalid; accepting malformed effective fields;
replacing archive receipts or changing other owners' files. Controls below
exercise those boundaries. Existing invalid event coercion is intentionally
rejected rather than maintaining the lossy constructor as compatibility truth.

### Concrete verification plan

New test_contextos_reference_validation.py must reject all four malformed fields
in nested and flat forms, validate invalid winning flat fields, reject supplied
non-mapping wire containers, and check failure after/before other events without
mutating any input. Controls cover missing/empty/partial nested references,
canonical integer coercion, valid nested/flat per-field precedence and repeated
restoration, provenance/state, real >5000-character receipt export/import plus
private UTF-8 archived JSON, and invalid reference rejection during archive
restoration. Real continuity and StateFirstContextOS consumers must propagate
validation failures. Existing strict protocol tests guard malformed plan admission.

Commands: rtk proxy .venv/bin/python3.13 -B
.superpowers/sdd/2026-10-11-contextos-reference-validation/implementation/run_check.py
{baseline,red,green,prior51,regression,controls,ruff,format,format-check,mypy,diff}.
Each preserves exact inner RTK command, exit code, log/XML and fresh private roots.
Compare wider failing node/signatures before/after and independently classify
historical 17 failures; do not call the component green. Root and independent
review must validate source; unattended fresh isolated L1-L12 remains unproven.

POL-97 authorization supersedes the earlier write list for this corrective task:
models_v2.py, the new reference-validation test, this blueprint, the existing
verification card and own reference-validation/implementation evidence only.

### POL-97 implementation evidence (author verification, review pending)

Valid RED: 23 failed / 10 passed. Initial RED also contained five author test
errors using a nonexistent WorkingState updated_at field; corrected to the real
active_artifacts contract before source edits, preserving both logs. GREEN:
33 passed; prior51: 51 passed unchanged. Focused Ruff/format/format-check,
project mypy and mypy --strict, and scoped diff checks passed. Seven relevant
canonical JSON schemas are byte-structure identical to the frozen preimage.
Dynamic repro-after rejects all four invalid reference fields with unchanged
input; real continuity and memory consumers propagate the failures.

Wide baseline: 17 failed / 1806 passed / 14 skipped / 1 xfailed. Wide regression:
17 failed / 1839 passed / 14 skipped / 1 xfailed. The same 17 failing nodes and
normalized error signatures persist, including the historical independent QA
set; this is not a component-wide PASS. Existing warning: a non-async test has
an asyncio marker. No test assertions, skip or xfail declarations were changed.

Independent author classification:

- Seven legacy model API tests pass _metadata to canonical Pydantic models,
  call an absent TranscriptEventV2.from_mapping, or use dataclasses.replace on
  a non-dataclass. One followup test also passes _metadata. Actual calls show
  canonical metadata retains the followup and extracts its goal, while the
  unsupported _metadata is ignored. Contract migration remains outside scope.
- Four observer failures are a real existing pipeline notification gap. A real
  project call creates a transcript event and commits it, but tracing never
  reaches _notify_projection_lifecycle_deltas; the observer receives no event.
- One route-priority failure depends on shared adaptive state. Fresh private
  ordering is patch,archive; running the earlier 40-outcome hardening control
  first makes the unchanged route test fail. route-order-trace.json records
  inherited weights. This ordering/isolation issue remains unresolved.
- RefTracker uses RLock while its test asserts threading.Lock. The failed test
  is unchanged; deciding the concurrency/test contract is outside scope.
- The graph test's relative path fails from private cwd but passes from root.
  Two capacity tests expect workspace-local context files while a process-wide
  private runtime override redirects them. The default-root private controls
  pass all 23 cases; no product runtime settings were changed.

An initial author audit script omitted RefTracker's required store argument;
fixed in own evidence tooling and rerun successfully, retaining its failed log.
No product fix was based on that author-tool error. Detailed node-by-node
classification, commands/exits, source hashes/diff, fixtures and risks are in
reference-validation/implementation/REPORT.json and REPORT.md. Native projects,
final provider requests, UI/WS and L1-L12 delivery were not executed or qualified.
