# Director task write guidance and final-request parity

Status: implementation; local and live acceptance pending.

## Evidence and cause

Exact ContextOS snapshot `887c66eb636cb0d36d3946c0`, Factory
`factory_9aa169804636`: final native write_file schema permits only
`tests/verify.test.ts` and `README.md`. The action prompt requires those files
plus `package.json` and `index.html`. An unbound diagnostic response emitted all
four; no tools were executed. This proves a scope/prompt conflict, not that it
caused the 660-second physical timeout.

The claim projection unions old targets and CE-owned paths before message
construction; it also unions read/legacy scope with explicit write scope. Original
PM TASK-3 declares three targets; index.html belongs to TASK-2. The first addition
of index.html is not proven, but retention of all four is reproduced at claim.
The adapter's coverage text and kernel's mutation-target extraction then treat
inventory as current write obligations. The existing current_task_write_boundary
helper also uses inventory and is correctly omitted by ContextOS plane isolation.
Do not expose it, broaden schemas, change models, or execute archived candidates.

## Minimal architecture

```
admitted execution envelope + declared inventory
  KernelOne pure write-guidance projection (no grant, I/O, or second SSoT)
  Director actionable targets + reference-only targets
  ContextOS safe projection + single-batch contract hint
  final provider request consistency audit
  existing authorization / DEO / effect receipts / verifier / bounded repair
```

Reuse existing scope matching and existing final-request error gate. Preserve
PM/CE payloads and hashes. Guidance is derived, never capability authority.
No new Cell, public execution API, state owner, effect, or realtime rail.

## Invariants

- Current mandatory writes derive from effective allowed_write_paths, not scope
  prose, project inventory, sibling evidence, or missing files alone.
- Absent envelope preserves ungoverned helper compatibility; a present malformed
  or explicit-empty envelope never silently falls back to inventory.
- Scope guidance contains paths and interpretation only, no token/lease/run IDs.
- Current guidance must survive safe ContextOS projection and appear in the final
  messages; pinned tool paths must cover current required writes without enlarging
  envelope authorization.
- Explicit required-write targets outside scope are contract errors, not dropped
  obligations. Reference inventory may be outside current task scope.
- One kernel turn remains at most one batch; existing Director outer quality and
  semantic repair remain bounded. stream=True is not a multi-turn switch.

## Verification

1. RED/GREEN: four inventory targets, two authorized writes; adapter prompt and
   kernel hint require only the two. Input contracts unchanged.
2. Explicit empty/malformed scope, directory/glob scope, unsafe paths and explicit
   required-write conflicts.
3. ContextOS keeps safe guidance and still omits raw control-plane helper.
4. Final request reports error on missing/divergent guidance or pinned tool scope.
5. Focused owner tests, strict types/lint, relevant cascade; original same-run
   Director-only retry after source freshness and drained-attempt checks.
6. Fresh isolated Bench remains separate from same-run recovery.

Target project manual edits: prohibited.

## Local verification checkpoint

- Original regressions RED3; pre-envelope claim/physical-schema regressions RED2;
  missing/divergent/unprojected final guidance RED5.
- Mixed read/write capability regression RED1; explicit empty-scope regression
  RED2. All 31 focused cases subsequently pass.
- Ruff passes; Mypy passes for eight modified production files.
- Expanded owner first run: 1015 pass, six fail from missing split-test helpers
  and an unwritable synthetic workspace. PDB confirmed the actual audit failure;
  unchanged fixture semantics restored, canonical evidence assertion strengthened.
  All affected groups rechecked: 223 pass, two historical SWIG deprecation warnings.
- Full cascade: 9/9 pass. Full repository suite not run.
- Package scripts read-only check: test already targets dist/tests/verify.test.js;
  do not rewrite a satisfied sibling obligation.
- No paid call, service restart or target edit during this implementation.
  Next is an owned isolated same-run Director-only recovery, not fresh proof.
- Live pre-dispatch snapshot `689900fd07297c9d04d6ef58` confirms target/scope2
  and safe guidance in messages. Structured fact propagation omitted the field;
  audit stopped before physical dispatch. Added the field to request_facts and
  a real request-preparer RED/GREEN regression;125 tests pass. This closes an
  actual receiver boundary, not a prose workaround. New live acceptance pending.
- Full-repo test suite and fresh Bench remain unverified.

## Fresh r03 retry boundary (2026-10-02)

`factory_7244a26d3ae3`, physical request `2236f46591e275373dc67730`:
the original final messages contained the exact safe task_write_guidance line.
After a rejected read_file on an edit_file-only surface, the existing mutation
retry builder retained role identity and the last user message but discarded this
system projection. A read-only replay of the exact snapshot proved guidance
present before and absent after narrowing. The next two attempts failed
qualification with task_write_guidance_not_projected, without Provider transport.

Narrow repair: retain only existing standalone safe guidance lines from system
messages (ContextOS repr or canonical JSON rendering). Never derive permission
from this text; the existing final audit still compares it with admitted typed
envelope scope. Do not copy arbitrary system history, lease/token data, raw tool
receipts, user/tool imitation, or broaden schemas. Multiple pins remain subject
to the existing current-envelope audit; input messages and tools stay unchanged.

Verification: real-snapshot before/after replay; two render variants; tool-output
imitation exclusion; existing retry and guidance audits. The initial broad role
prompt versus narrow physical tool surface remains a separately tracked residual,
not claimed closed by retaining guidance.
