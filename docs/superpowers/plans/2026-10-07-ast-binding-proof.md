# AST-backed binding proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task within the explicitly assigned Paperclip employee. Root handles independent review through Paperclip. User's existing instruction overrides worktree/commit/repeated-approval defaults: same workspace, mutually exclusive writers, no commits.

**Goal:** Close QA-SCOPE, QA-MULTI and QA-NONCODE without inventing symbols, suppressing diagnostics, or repeating completed PM/CE work.

**Architecture:** Reuse the installed Tree-sitter grammar and KernelOne ABI adapters for immutable per-source facts. Only the existing unique-import/value-proof path consumes them; composer, authorization, receipts and public contracts remain unchanged.

**Tech Stack:** Existing Python 3.13 project venv, Tree-sitter 0.25.2/language pack 1.8.1 verified on host, deployed KernelOne adapters, existing pytest/Ruff/mypy and private TypeScript compiler controls.

**Spec:** `docs/blueprints/POLARIS_AST_BINDING_PROOF_20261007.md`.

## Global Constraints

- Product edits only: new `typescript_syntax/module_binding_proof.py`; existing `typescript_syntax/imports_exports.py` and `typescript_syntax/common/import_text_ops.py`; existing binding-frontier test and new AST proof test.
- No modification of `misc_ops.py`, normalizer, old contract tests, KernelOne, registry, public DTO, Factory/QA, Paperclip, target projects, configurations, dependencies, or original assertions.
- Existing unsupported syntax remains conservative; AST absence/error cannot trigger regex fallback.
- All shell segments RTK; text I/O UTF-8. No provider/service/Bench calls or other agents inside this implementation task.
- Reports/checkpoints only the assigned engineering directory and managed scratch; no API write/status/upload/self-handoff while employee is alive.

## Review Focus

- Multiple statements on one line or across newlines: all real matching imports inspected and rewritten, or no proposal.
- Nested named expressions with a type-shadowed name: no false module-level value/alias/default proof.
- Import-like text in non-code before a valid or invalid real import: non-code bytes remain untouched.
- Unicode before/between source spans: byte offsets converted correctly; exact symbol identity unchanged.
- Absent grammar/ABI/error-recovery node or unsupported statement: no unsafe effect/proposal; supported controls remain usable.

### Task 1: Preserve real public counterexamples

**Files:** Extend `src/backend/polaris/cells/director/runtime/tests/test_unique_export_binding_frontier.py` only by new tests. Read POL-21 `probe.py` and compiler-proposal evidence; preserve all 112 existing assertions.

**Interface:** Existing `PlanDirectorRepairCommandV1` and `QueryDirectorRepairMaterializationPlanProbeV1`, source tool `deterministic_typescript_unique_export_import_repair`.

- [ ] Add raw/typed tests for valid type-shadowed named class/function expression candidates, a valid two-import positive, missing/unsupported second imports, and multiline comment/template lookalikes. Assert both planning verdict and exact composed content, not only `planned=true`.
- [ ] Use the real public route, for example the following negative fixture:

```python
files = {
    "tests/acceptance.ts": 'import { Petal } from "../dist/absent.js";\nconsole.log(Petal);\n',
    "src/fresh.ts": 'interface Hidden { height: number }\nconst Wrapper = (\nclass Hidden {}\n); export { Hidden as Petal };\n',
}
diagnostics = normalize_director_repair_diagnostics(
    ("unresolved relative import '../dist/absent.js' in tests/acceptance.ts",)
)
result = plan_director_repair(PlanDirectorRepairCommandV1(
    source_tool="deterministic_typescript_unique_export_import_repair",
    base_files=files, diagnostics=diagnostics, mode="shadow",
))
assert result.planned is False
assert result.composition_summary.to_dict()["patch_count"] == 0
```

- [ ] Run new tests against current source and save actual assertion failures before product edits. Retain input hashes and denied-effect audit results.

### Task 2: One private AST fact boundary

**Create:** `src/backend/polaris/cells/director/runtime/internal/repair_kernel/typescript_syntax/module_binding_proof.py`.

**Test:** `src/backend/polaris/cells/director/runtime/tests/test_typescript_ast_binding_proof.py`.

**Interface:** Define immutable `ImportBindingFact(imported: str, local: str, type_only: bool)`, `ImportStatementFact(specifier: str, bindings: tuple[ImportBindingFact, ...], specifier_span: tuple[int, int], supported: bool)` and `ModuleBindingFacts(imports: tuple[ImportStatementFact, ...], runtime_exports: frozenset[str])`; `parse_module_binding_facts(source: str) -> ModuleBindingFacts | None`. Spans are Python character indexes after validated UTF-8 conversion. None means the source cannot be safely proved, not an empty successful module.

- [ ] Write facts tests for root versus expression scopes and real imports versus non-code. Add one genuine provider/ABI characterization control, plus explicit parser-missing/error handling tests; do not substitute fake ASTs for all normal fixtures.
- [ ] Reuse existing grammar/ABI APIs. Validate actual error/missing-node/span access; operate only on module-root statements and genuine declaration children. Do not derive module names from arbitrary descendants.
- [ ] Derive direct runtime values and verified local aliases/default references. Unsupported forms cannot advertise an export; an unsupported import must remain represented so its matching specifier causes refusal.
- [ ] Convert byte spans using strict UTF-8 prefixes, verify exact original quote/specifier boundaries, and include the non-ASCII offset test:

```python
source = '// 汉字\nimport { Petal } from "../dist/absent.js";\n'
facts = parse_module_binding_facts(source)
assert facts is not None
assert len(facts.imports) == 1
start, end = facts.imports[0].specifier_span
assert source[start:end] == "../dist/absent.js"
```

- [ ] Run facts tests and preserve known availability refusals. Never install/download grammars or run Node inside planning. Missing grammar/unsupported API is a recorded refusal, not a success fallback.

### Task 3: Cut over only the existing unique-import proof

**Modify:** `typescript_syntax/imports_exports.py`; `typescript_syntax/common/import_text_ops.py` only for the `require_value=True` proof/callsite.

**Consumes:** Task 2 `parse_module_binding_facts`; existing canonical diagnostics/base_files/hash/composer APIs.

**Produces:** Existing RepairPlan/public projection, no new public functions or bindings.

- [ ] Gather every real import fact whose specifier equals the diagnostic. Any unsupported required statement refuses. The candidate must prove the union of every retained imported runtime name, including default and unused bindings.
- [ ] Keep candidate uniqueness and owner/path logic. Replace only the validated specifier spans, all of them, preserving surrounding source. Equivalent replacement mechanics:

```python
updated = source
for start, end in sorted(proven_spans, reverse=True):
    assert source[start:end] == old_specifier
    updated = updated[:start] + replacement_specifier + updated[end:]
```

- [ ] Validate bounds/nonoverlap before composing, bind original source hash, and retain typed canonical path precedence. No global text replacement or first-match branch on this path.
- [ ] Preserve unrelated shared/type-aware callers and their defaults. Public proposals remain read-only; policy-gated tools own all future effects.
- [ ] Rerun Task 1 raw/typed public tests and all original binding tests. No weakened old assertion for a wider AST availability claim.

### Task 4: Independent language and regression evidence

**Files:** Product tests from Tasks 1–2; engineering reports only.

- [ ] Run actual owned private TS5.9.3 fixtures with private cwd, `types: []`, empty private typeRoots and unchanged source hashes. Compile exact composed positive multi-import content; negative expression/type-shadow proposals must no longer be produced. Non-code text stays byte-for-byte unchanged.
- [ ] Run full selected contract1–7 + all frontier files + the new AST test + C++ normalization; discover and run existing TypeScript callers of changed helpers. Record every selector, count, exit, baseline failure, skip and unrun global/release gate.
- [ ] Run lint/type/diff checks and compare existing production format debt. All new files lint/format clean. No blanket formatting or configurations changes.
- [ ] Save a small report JSON with evidence paths/hashes rather than duplicating multi-megabyte full catalogs per case. Full native public evidence may stay in separate immutable files.
- [ ] Verify complete Git additions and changes against exact scope; preserve all other dirty assets and original assertions. Capture terminal report then naturally exit, no API handoff. Root verifies process/custody release and assigns a distinct reviewer before acceptance or next module.

## Self-review / evidence separation

All three POL-21 failure signatures and the five review-focus classes have explicit owners/tests above. There is one private fact model, one unique-import cutover and no new state/effect authority. New internal interface names are defined before consumers. Parser provider availability, syntax facts, public proposal safety, private compiler result, actual authorized effect receipt and fresh Bench completion remain distinct evidence levels.
