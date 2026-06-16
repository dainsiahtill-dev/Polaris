# TS/JS Cross-File Symbol-Coherence Detection (dark-launched)

Date: 2026-06-17
Owner cell: `kernelone.quality` (`artifact_quality.py`)
Status: blueprint + implementation, **dark-launched behind a default-OFF flag**

## 1. Problem

The artifact-quality gate (`scan_workspace_artifact_quality`) has **Python**
cross-file symbol-coherence detection (`_scan_python_imports` →
`_python_module_exports`, ast-exact, fail-open): it flags
`from .sibling import Symbol` where the resolved sibling `.py` exists but never
defines `Symbol` (live L3-16 tetris wall; the dominant L6-32 microservices wall —
`common/__init__` imports `HTTPClient` that `common/http_client.py` never defines).

The **TS/JS** scan (`_scan_typescript_imports`) only checks (a) relative-module
existence and (b) package declaration. It does **not** check that a named import
`import { X } from './sibling'` corresponds to an actual export of the sibling.
So an L4-L8 React/Express/Vue/TS project can ship a broken named import — a
guaranteed runtime `SyntaxError: does not provide an export named 'X'` — and the
gate stays silent. That is a **platform detection asymmetry**: a known failure
mode (symbol drift, proven in Python) is undetected in a second language, so the
failure is mis-attributed instead of caught.

## 2. Why this is dangerous to land naively

`scan_workspace_artifact_quality` runs on **every** materialization, **including
L2** (which contains JS projects, e.g. L2-10 `src/main.js`). The Python detector
is safe because `ast.parse` gives an EXACT export surface. TS/JS has **no stdlib
parser** in this Python process, so we must extract the export surface with
regex. A regex that MISSES a valid export form → **false positive** → a runnable
product is marked FAIL → a NEW platform fault that breaks the L2 floor (6/6) —
the exact opposite of the goal. False positives are catastrophic; false
negatives (missing a real drift) are merely "no worse than today".

Therefore the design rule is hard asymmetry:
- **Generous** in what counts as an export (capture every plausible form).
- **Conservative** in when we check at all (fail open on ANY ambiguity).
- **Dark-launched**: gated behind `KERNELONE_TS_SYMBOL_COHERENCE` (default OFF),
  so the live path is byte-for-byte unchanged until codex bench-validates it ON
  across L2 + L4-L8 TS projects and confirms zero false positives, then flips the
  default. This is floor-safe by construction.

## 3. Design

Mirror the Python trio:

- `_typescript_module_exports(text) -> set[str] | None` — the export surface, or
  `None` (fail-open) when it cannot be safely determined. Returns `None` if the
  module contains ANY of: `export *` / `export * from`, `export =`,
  `module.exports` / `exports.x` / `exports[`, `declare module`/`declare global`,
  destructured `export const { … }` / `export const [ … ]`, or fails a sanity
  parse. Otherwise captures (generously): `export const|let|var NAME`,
  `export (async )?function\*? NAME`, `export (default )?(abstract )?class NAME`,
  `export interface|type|enum|namespace NAME`, `export { A, B as C }` (exported
  name = alias if `as`), `export { A } from '…'` (re-export), and a `default`
  sentinel for `export default`. Strips line/block comments and string literals
  before scanning to avoid matching `export` inside comments/strings.

- `_resolve_typescript_module_file(root, importer, specifier) -> Path | None` —
  the resolved single sibling file (first hit of `_relative_import_candidates`),
  or `None`. If it resolves to a directory `index.*` barrel, the exports fn will
  almost always see `export * from` and fail open — safe.

- `_TS_NAMED_IMPORT_RE` — captures `import [type] [Default,] { names } from
  '<specifier>'`. We check ONLY plain named imports of relative specifiers:
  - skip `import type { … }` (type-only — ambient/declaration-merging risk),
  - skip inline `{ type X }` names,
  - skip default-only / namespace (`* as NS`) imports (no named symbol),
  - strip `as alias` → check the ORIGINAL imported name,
  - resolve specifier → file → surface; if surface is `None` → skip,
  - flag each named import absent from the surface AND not `default`.

Error string mirrors Python for parser reuse symmetry:
`Artifact quality scan failed: unresolved import symbol {name!r} from
{specifier!r} in {relative_path} (sibling module does not define it)` — the SAME
`_UNRESOLVED_IMPORT_SYMBOL_ERROR_RE` (execute_method.py:2157) already routes this
to `_build_unresolved_import_symbol_repair_block`, so the repair guidance is free.

## 4. Data flow

`_scan_typescript_imports` (already iterates relative imports for existence) →
when flag ON and a relative specifier resolves → additionally run the named-import
symbol check → append symbol errors. `_scan_file:325` already calls it; no new
wiring.

## 5. Fail-open test matrix (false-positive guards — all must yield ZERO errors)

barrel `export *`; re-export `export { X } from './y'`; default import;
namespace `import * as NS`; type-only `import type`; inline `{ type T }`;
CommonJS `module.exports` / `exports.x`; `export =`; destructured export;
`declare module`; `.d.ts` ambient; `export` inside a comment / string; unresolved
specifier; index-barrel target; class/function/const/interface/type/enum/namespace
exports recognized; `as`-aliased export recognized. Plus the positive case:
`import { Missing } from './sib'` where `sib` is a simple module missing `Missing`
→ exactly one flag.

## 6. Validation protocol before default-ON (codex's bench half)

1. Run one L2 floor bench with `KERNELONE_TS_SYMBOL_COHERENCE=1`; confirm 6/6
   unchanged (zero new FAILs on the JS projects).
2. Run an L4-L8 TS-project bench with the flag ON; confirm no runnable product is
   newly FAILed (zero false positives) and at least one real symbol drift is
   caught.
3. Flip the default to ON in a follow-up commit once both are clean.

## 7. Pre-merge adversarial verification (completed 2026-06-17)

Probed 25 realistic false-positive patterns (multiline imports, trailing commas,
generic types, default+named, decorated classes, aliased re-exports, CRLF,
indented exports, namespace, const enum, declared exports, the realistic L4
React App.tsx pattern). Result: **zero real false positives**. The three
"hits" that looked like false positives were all actually invalid ESM/JS:
`export default class Foo {}` then `import { Foo }` is not valid ESM (the
class's name is not a separate export); `export<newline>id` is not valid JS
(`export` must continue on the same line); `export default { a: 1 }` then
`import { a }` is not valid ESM (properties of a default are not named
exports). The detector correctly catches all three. Positive detection
confirmed: `App.tsx` importing `Card` from `components.tsx` that exports only
`Button` is correctly flagged. Safe to enable once the L2 + L4-L8 bench
validations pass.

## 7. Constraints honored

§8 (generic import/symbol reasoning, no project literals); floor-safe (flag OFF
default → live path unchanged → no L2-floor risk); fix-not-delete; UTF-8 explicit
reads; conservative-fail-open mirrors the accepted Python precedent.
