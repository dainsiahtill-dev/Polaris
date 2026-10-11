# AST-backed import binding proof

Status: approved implementation direction under the user's standing instruction to execute and independently accept the total plan; not implemented or qualified by this document. Execution: Paperclip employees; Root assigns and accepts. No branches, worktrees, commits, Paperclip changes, or generated-target edits.

## Intent and evidence

Repair an unresolved import only when the same owned importer has real module-level import statements and a unique candidate proves every retained binding. This is a correction of existing `director.runtime` planning, not a new execution authority or a TypeScript language service.

POL-21 independently reproduced three failures through actual public Plan/materialization queries and isolated TypeScript 5.9.3:

- QA-SCOPE: a named class/function expression supplies a false module-level value proof, including valid modules with an outer type-only name. The resulting import use fails TS2693.
- QA-MULTI: only the first real import from a specifier is inspected/repaired; another remains unresolved, including when all required symbols are actually available.
- QA-NONCODE: an import-looking line inside a multiline comment/template is selected ahead of the real import. Only non-code changes; the real importer still fails TS2307.

Existing tests being green did not detect these failures. B1/B6 are separately corrected expectations for explicit `repair_not_planned`; their correction is not acceptance of the above defects.

## Decision and alternatives

Use the already declared Tree-sitter language-pack dependency and existing KernelOne binding adapters to derive immutable AST facts. Regex patching alone is rejected for this path: it repeatedly confuses statement selection, expression scope, and value/type identity. Executing Node/tsc inside public planning is also rejected: it introduces process effects into a read-only query. Real compiler execution remains a separate controlled verifier/private engineering fixture.

Root read-only availability probe confirmed installed `tree-sitter 0.25.2` and `tree-sitter-language-pack 1.8.1`. Parsing the type-shadowed expression returned a `program` with `interface_declaration`, `lexical_declaration`, and `export_statement`, and no module-level class declaration. Initial `named_children` access failed on the actual Rust binding; the existing KernelOne `_ts_root_node`, `_ts_node_children`, and `_ts_node_kind` adapters worked. Do not invent a property-only binding API.

These are availability/causal component observations, not physical sandbox qualification. A Python audit hook observed no prohibited write/spawn/network attempt during the successful probe; that is not a security proof for arbitrary native extension behavior.

## Boundary and data flow

```text
raw/typed diagnostic + immutable authored base_files
  existing canonical normalization and runtime binding
  per-source AST facts: real import spans, all bindings, module values/types
  unique candidate proof for every real matching statement
  source-hash-bound rewrite of proven specifier spans only
  existing RepairPlan / composer / public projection
  existing policy-gated Director tools / receipts / revalidation
```

New private implementation: `typescript_syntax/module_binding_proof.py`. No new public facade/source_tool, state owner, execution path, or registry rule. Cross-Cell consumers continue to use generic public Plan/Run. Reuse existing KernelOne parse/ABI capabilities; do not add a second generic AST framework, mutate KernelOne, or install dependencies.

## Required invariants

1. Only actual module-root import statements count. Comments, strings, templates, dynamic imports, namespace bodies, and named expressions cannot create import or module-value facts.
2. Gather every real statement with the exact diagnostic specifier. Each retained binding must be completely parsed and supported. An unsupported statement for that specifier refuses the combined proposal. Never ignore a second statement or unsupported tail.
3. A candidate must prove the complete union of required runtime exports. A class/function expression's private name is not a module binding. Type/interface-only declarations and type-only exports cannot prove a runtime value. Local export aliases/default references require a genuine module-level local value. Preserve supported direct/default/alias positive cases.
4. Preserve established conservative refusal cases from original tests, including unsupported type-only/quoted/namespace/escaped forms and uncertain reexports/default expressions. Do not weaken old assertions to claim wider grammar coverage. Availability improvements require separate evidence and authorization.
5. AST spans are UTF-8 bytes; public patch text is Python Unicode. Explicitly convert and validate spans before rewriting; never use byte offsets as character indexes. Test non-ASCII text before and between imports.
6. Change every proven matching specifier, and nothing else: retain import clauses, quotes, whitespace, comments, unrelated paths, and semantic source bytes. Apply nonoverlapping validated replacements in descending order and preserve the existing hash/composer contracts.
7. Missing grammar, unsupported binding ABI, ERROR/missing nodes, ambiguous candidate, malformed source, or unverifiable spans produce no safe proposal. No network/install/Node subprocess, fake success, or fallback to the unsafe regex path.
8. Facts are derived, immutable, and local to planning. No global mutable parser shared across concurrent tasks; no persistent source cache, project truth, receipt, or approval in the fact model.
9. Preserve typed canonical importer precedence, original PM/CE evidence, ownership scopes, executable binding, and every existing effect/receipt/revalidation boundary.

## Assumptions and pre-mortem

- A1: parser available. Verified on the Root host; the protected employee environment still needs its own read-only check. If unavailable, preserve evidence and refuse, not downgrade authority.
- A2: existing ABI adapters handle the deployed binding. Verified for root/children/kind only; implementer must verify byte-span/error/missing-node access rather than assume it.
- A3: AST syntactic facts are sufficient for the supported direct/local subset, not all semantic TypeScript. Reexports, merged declarations, ambient syntax, destructuring, TSX and complex default expressions must be explicitly supported with proof or refused.
- A4: the existing unique-export repair is the only cutover target. Other shared type-aware consumers remain unchanged unless this exact proof boundary requires an authorized callsite adjustment.

Most likely incorrect fixes: traversing all descendants instead of root declarations; stripping unsupported imports then claiming completeness; fixing only one matching span; converting byte positions incorrectly; trusting AST error recovery; accidentally widening type-only or existing conservative behavior; returning no proposal for every positive.

## Acceptance

Real public raw/typed RED before implementation for the three POL-21 signatures, then GREEN with exact complete patch-content assertions. Existing 112 binding tests retain their assertions. New private parser cases pin module scope, multiple statements, non-code, Unicode spans and parser absence/error. A positive multi-import proposal must compile under the preserved fixture contract and contain no real unresolved specifier. Negative fixtures stay input/hash unchanged with zero tools/receipts.

Rerun the full selected contract/frontier suite and relevant existing TypeScript callers; record exact selectors/counts/exits, lint/type deltas, and pre-existing format debt. Another Paperclip employee independently reviews new fixtures and compiler evidence after the writer's verified exit. Full repository/release/fresh isolated Bench evidence remains a separate gate; component acceptance never means `COMPLETED_VERIFIED`.
