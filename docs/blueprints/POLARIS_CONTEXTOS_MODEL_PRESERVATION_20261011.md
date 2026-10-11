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
