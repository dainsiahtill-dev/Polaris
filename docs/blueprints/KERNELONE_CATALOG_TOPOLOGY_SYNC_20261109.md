# KernelOne catalog topology synchronization

Task: K1-GHOST-CATALOG-20261109. Baseline: main 6a75e53f7082c015568c0727c0242ee35ee78710.

## Diagnosis and provenance

The existing top-level admission suite fails two checks: catalog-only paths and exact catalog/disk set equality. Independent pre-edit run: 2 failed, 6 passed in 1.30s. Exact output and diagnosis were saved before these documents and before the catalog edit:

- `/home/dains/.polaris/bench_runs/qwen38-all120-20261105/k1-ghost-catalog-red-20261109.txt`
- `/home/dains/.polaris/bench_runs/qwen38-all120-20261105/k1-ghost-catalog-diagnosis-20261109.json`

Commit `d46e5bff1db685b0e7b2d6c1725099db694feaad` (2026-06-07) intentionally removed agent, dialogue, distributed, learning, observability, performance and reasoning as unused aspirational subsystems. Commit `11dbb8f93c469069fd00a4399f99abdbb9f4d2e2` (same day) intentionally removed tool_creation and ws; tool creation was never wired, and delivery/ws superseded the unused ws substrate.

Catalog creation `45b7fc15f8d74c12a67623c75082e5f5469d1677` (2026-08-14) nevertheless registered those nine paths. Its tracked tree already contained exactly the same 47 top-level directories as current HEAD. Eight stale rows say tests-only shell and ws says empty subtree. This proves incorrect admission of retired paths. Residual local empty/cache directories are a plausible explanation, but their historical filesystem presence is not proven. Retain the original generated_at value 2026-08-10 as provenance.

## Ownership and data flow

```text
Git-tracked KernelOne topology (47 directories)
    compared with kernelone-foundation catalog entries
    by unchanged test_kernelone_toplevel_admission.py
    consumed by existing KernelOne release gate
```

The catalog owns technical top-level admission metadata. Tests own its structural, policy, ghost-path and exact-set checks. No runtime owner, public contract, package or side-effect path changes.

## Minimal implementation

Remove exactly the nine stale catalog entries. Preserve all remaining 47 entries in order and value, every non-entry metadata/policy field, and the admission test file byte-for-byte. Do not create placeholder directories, restore deleted modules, register a new subtree or weaken the gate. The existing regression already detects this class of mismatch; no new test is needed.

Pre-mortem: removing a real capability would hide missing tracked files; retaining an orphan or changing policy would hide the defect. Mitigation: compare both the catalog-introduction Git tree and current HEAD, check current absolute Python references, and assert equality of every retained entry plus all non-entry metadata before writing.

## Verification and limits

Run the unchanged eight-test admission suite and the existing verification-card schema checker scoped to the new card. Check the catalog edit consists only of the nine row removals and run git diff --check. No separate consumer/schema gate for kernelone_capabilities.yaml was found; its existing admission suite validates YAML parsing, required fields, policy and directory equality.

The parent performs independent diff review and reruns the full KernelOne release gate. Focused admission success does not establish fresh model, workload or all-120 acceptance. No services, generated targets or model/network calls are authorized. No commit is part of this task.

Recorded component verification: unchanged admission suite passed all 8 tests in 1.20s; scoped verification-card --strict passed 1/1; Draft202012 schema validation including formats passed. The admission test text matches HEAD exactly. The root independently reviewed the exact deletion and completed the full release rerun: all 12 stages passed, with 373 tests passed and 2 skipped. Evidence: `/home/dains/.polaris/bench_runs/qwen38-all120-20261105/root-kernelone-release-20261109-r2.json`; the first failing report is retained separately.
