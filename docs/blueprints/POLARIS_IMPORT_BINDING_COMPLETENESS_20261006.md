# Whole-import binding proof

POL-17; mode=implementation; owner=修复内核与语言适配工程师.
Pattern fix in active `director.runtime`; HEAD baseline 9bb5bd1b4 plus existing dirty tree.
External engineering document; no project delivery or receipt authority.

```text
raw diagnostic / typed canonical issue
    -> public Plan / materialization plan probe
    -> existing executable unique-export binding
    -> complete supported import clause
    -> module-level exact identifier and value-capable export proof
    -> unique candidate / refusal
    -> existing composer (proposal only)
```

`common/misc_ops.py` owns complete import clause parsing. Unsupported retained
parts must invalidate the entire result. Ordinary default/named/alias imports,
including unused bindings and literal `$`, remain supported.
`common/import_text_ops.py` reuses KernelOne's code mask for export evidence.
Uncertain escaped/joiner identifiers are refused. Unique-import routing needs
value-capable declarations; type-aware callers retain their existing mode.
`imports_exports.py` only requests the stricter proof at the unique-import seam.
No parser dependency, new public DTO, registry, state owner, effect, policy,
scheduler, receipt, revalidation, or execution authority change.

Assumptions: partial regex parsing caused E1/E2; unsupported identifier boundaries
caused E3; textual type existence is insufficient for a runtime value (E4).
Verify with public raw/typed Plan and materialization probe before implementation,
then actual scratch TypeScript compiler controls. No Run/effects, service, Bench,
provider call, target edit, API write, or handoff by this employee.

Availability tradeoff: no complete TypeScript grammar promised. Whole-import
type-only/namespace/quoted/escaped syntax can be refused. Value alias exports
require an ordinary module-level declaration; uncertain reexports are refused.
Existing template/regex/division refusal remains. Premortem: masking unsupported
parts can create a partial proof; changing shared type behavior can break other
repair planners; accepting an alias without proving its local value recreates E4.

Verification: original 34 binding tests plus new E1–E4 controls; four frontiers
plus contract1; contract1–7 plus four frontiers and C++ normalization. B1/B6 remain
separate pre-existing failures. Ruff/mypy/format compare baseline without broad
formatting. Reports/checkpoints only in the authorized binding-completeness-fix
directory. Rollback is limited to this task's delta; preserve pre-existing dirty.
