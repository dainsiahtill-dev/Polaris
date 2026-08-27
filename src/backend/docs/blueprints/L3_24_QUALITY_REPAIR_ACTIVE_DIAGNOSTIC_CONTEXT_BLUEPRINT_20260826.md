# L3-24 Quality Repair Active Diagnostic Context Blueprint

## Problem

Live isolated run `factory_0f9b7dff58b8` reached QA with a C++ compile error at
`invisible_diary/cli.cpp:112`.  The first Director repair produced a real file
effect and closed the analogous `run_encode` error.  Three later repair turns
then edited only `<memory>` includes and were rolled back.  The final provider
requests showed why: raw resolved/rejected compiler transcripts were replayed
beside the current error, and a nested unittest string stored compiler newlines
as literal `\n`.  The prompt therefore promoted include context at line 18
ahead of the actionable error at line 112.

## Invariants

1. The exact current verifier residual is the only actionable diagnostic.
2. A path:line token is a compiler error only when its immediate suffix is an
   error marker; another error elsewhere on the same physical line is not
   sufficient.
3. Resolved and rolled-back compiler diagnostics are compact negative
   constraints.  They never carry executable-looking source windows.
4. Current compiler atoms are subtracted from historical atoms using relative
   path, enclosing function, and normalized message identity.
5. Behavior/test regression guards remain available with bounded verifier
   source context.
6. Tool authorization, candidate transaction, CAS rollback, verifier and
   stagnation policy remain unchanged.

## Design

- Classify primary diagnostic anchors from each matched path:line token's
  immediate suffix and order compiler errors before include/note context.
- Normalize literal `\r\n`, `\n`, and `\r` separators only inside the
  read-only compiler diagnostic parser.
- Canonicalize compiler history into `(relative_path, function_context,
  message)` atoms.
- Render historical compiler atoms as `RESOLVED` or `REJECTED-ONLY` negative
  constraints after subtracting current atoms.
- Preserve non-compiler behavior guards and their verifier source context.

## Evidence and acceptance

- Exact run: `factory_0f9b7dff58b8`, L3-24 r45.
- Final snapshots: `5046307301f663a2e9d8ce32`,
  `23a21ea28fc017ec60cf5f7a`, `100c0dcec4f1b653530f853e`,
  `0bcd4d008364086f0a196252`, `494ed2be5908915b19c68309`.
- Tool outcomes prove one accepted causal edit and three real but rolled-back
  include edits; this is not a tool-normalization failure.
- Unit acceptance: current error anchor leads contextual include anchor;
  literal-newline nested transcripts are atomized; current atoms do not return
  through regression/rejection history; behavior guards remain.
- Live acceptance: a fresh isolated L3-24 run must show the Director addressing
  the current residual rather than repeating the resolved include edit.  Full
  completion still requires authoritative ProjectOutcome, successful
  `quality_gate`, and no failed command receipt.

## Shared-tree regressions closed before live revalidation

Dynamic regression debugging exposed three independent target-selection
failures in the shared Director/Factory refactor.  They are closed without
widening any JobToken or permitting writes to verifier-owned files:

1. Go assertion locations (`*_test.go:line`) were accepted by a compiler
   regex whose column was optional.  Compiler ownership now requires the real
   Go `path:line:column:` shape; assertion locations remain verifier evidence.
2. A test primary anchor unconditionally deferred the repair even when TAP
   import/title analysis had already found an explicit task-owned production
   source.  Deferral now remains fail-closed only when no such in-scope causal
   source exists.
3. Rotating a single repair target removed the original causal target forever.
   Rotation now explores alternatives and then revisits the first target.

The broad local acceptance set is `201 passed`; focused Ruff and Mypy gates are
green.  One pre-existing aggregate-test import cleanup remains outside this
change and was not auto-fixed during the concurrent file-splitting refactor.
