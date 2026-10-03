# Release KFS boundary repair

Evidence: actual release test test_business_runtime_direct_write_is_baselined_and_non_regressive
rejects direct writes in Director candidate rollback guard, candidate_compile_gate
and syntax_gate. Do not alter guard thresholds or add baseline exemptions.

Reuse KernelOne filesystem public boundary/adapter registry for candidate scratch
and rollback writes. Keep UTF-8, workspace/path policy, before/after CAS, isolation
and error semantics. No direct infrastructure import from KernelOne. No target
manual edits, Provider, Bench or service action. Literal test-owned scratch is
allowed. RED gate plus behavioral tests must precede implementation; retain all
rollback/compiler/syntax assertions. Root independently reviews and retests.

Local implementation: registered KFS workspace APIs now handle rollback read/
write/remove and UTF-8 candidate overlays. Baseline compilation no longer runs
on live source. Independent review found shared baseline/candidate scratch can
mask regression; actual Go TestMain mutation reproduced RED, separate initial
copies fixed GREEN. Four live source hashes and directory sets remain unchanged.
Related442 tests PASS in35.92s (external execution report); root independently
verified37 integration/boundary tests and real Go regression1 PASS. Full release
final rerun pending. Scratch is not hostile-code sandbox proof; symlink/external
dependency containment and optimistic CAS remain separate unclosed obligations.

Allowed changes: only the three reported source files and their related test files.
If existing KFS cannot safely express a needed operation, report that exact contract
gap rather than fabricating success or weakening permission.
