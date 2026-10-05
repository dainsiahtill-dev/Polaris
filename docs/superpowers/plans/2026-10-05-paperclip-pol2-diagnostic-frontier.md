# Paperclip POL-2 Diagnostic Frontier Implementation Plan

> **For agentic workers:** Use installed Superpowers systematic-debugging, test-driven-development and verification-before-completion. Execution is through the existing Paperclip repair engineer, main checkout, no branches or commits. Root supervises and independently accepts.

**Goal:** Close the first two confirmed r11 repair-routing input defects: distinct relative-import diagnostics collapse, and located TypeScript errors in JSON compiler configuration lose typed code/path/location.

**Architecture:** Keep normalization and diagnostic identity inside director.runtime's existing repair kernel. Public contracts and generic coverage remain the discovery boundary. This task does not add an executable repair, widen owner scope, change verifier policy or repair target code.

**Tech Stack:** Existing Python 3.12.3, repair-kernel public normalization API, pytest, Ruff and mypy.

**Spec:** Exact r11 public coverage/shadow audit at `.superpowers/sdd/2026-10-04-review-regression-closure/fresh-r11-repair-route-audit.md`; user approved Paperclip executing remaining platform tasks.

## Global Constraints

- mode=implementation; sole writer is Paperclip repair engineer on POL-2.
- Allowed source files only: `src/backend/polaris/cells/director/runtime/internal/repair_kernel/contracts.py`, `.../diagnostics.py`.
- Allowed new test only: `src/backend/polaris/cells/director/runtime/tests/test_diagnostic_identity_frontier.py`.
- Allowed engineering report: `.superpowers/sdd/2026-10-05-paperclip-pol2-diagnostics/REPORT.md`.
- Do not edit registry, planners, callback ports, Factory, QA, PM/CE, product public facade, other tests, generated targets or runtime.
- Preserve explicit supplied diagnostic IDs; preserve legacy IDs when added semantic discriminator is absent; never hash arbitrary mutable metadata wholesale.
- Same physical diagnostic repeated by nested verifier still deduplicates. Different unresolved specifiers from one file must survive normalization as distinct.
- JSON-located TS5110 must retain TypeScript code/path/line/column. Unknown repair coverage must stay honestly uncovered; normalization is not a repair verdict.
- No whole-project regeneration, receipt invention, weak tests, business stubs or config relaxation.
- All shell commands RTK; explicit UTF-8; actual interpreter `/home/dains/.pyenv/versions/3.12.3/bin/python`.

## Review Focus

1. Existing explicit IDs must not be overwritten by new identity logic.
2. Repeated identical imports must remain deduplicated while different specifiers survive.
3. Irrelevant metadata order or telemetry must not change IDs.
4. Normal .ts/.tsx diagnostics must keep their previous typed location handling.
5. Recognizing TS5110 must not claim coverage or authorize generated-file edits.

## Task 1: Dynamic RED, minimal source change, GREEN

- [ ] Read repo/backend rules and runtime Cell metadata; use CodeGraph first.
- [ ] Call the real public normalizer on two distinct unresolved import strings for a generic `tests/verify.test.ts` plus a located `tsconfig.json(4,15): error TS5110` string. Current dynamic baseline must show collision/unknown classification, not merely infer it statically.
- [ ] Add pytest regressions exercising public normalization and real RepairDiagnostic construction: distinct specifiers, duplicate same specifier, explicit IDs, no-discriminator legacy ID, metadata stability, TS5110 JSON location, and unchanged .ts/.tsx cases.
- [ ] Run the new tests and preserve actual RED commands/errors in REPORT.md.
- [ ] Modify only the two allowed sources, with smallest sufficient semantic discriminator and compiler parser correction.
- [ ] Run new tests and existing contract-1 normalization regressions, then all `test_repair_kernel_contract*.py` if the bounded runtime permits. Preserve any collection/baseline failure honestly; do not edit unrelated tests.
- [ ] Run Ruff and mypy on changed files. Existing diagnostics must be identified separately; no suppressions to claim green.
- [ ] Report exact changed files, test counts/exit codes, compatibility limits and unresolved planner/owner/verifier defects. Do not claim r11 completed or all120 green.

Commands (all source edits scoped above):

```bash
rtk proxy env PYTHONPATH=/home/dains/Documents/polaris/src/backend rtk proxy /home/dains/.pyenv/versions/3.12.3/bin/python -m pytest -q src/backend/polaris/cells/director/runtime/tests/test_diagnostic_identity_frontier.py src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract_1.py
rtk proxy ruff check src/backend/polaris/cells/director/runtime/internal/repair_kernel/contracts.py src/backend/polaris/cells/director/runtime/internal/repair_kernel/diagnostics.py src/backend/polaris/cells/director/runtime/tests/test_diagnostic_identity_frontier.py
rtk proxy mypy src/backend/polaris/cells/director/runtime/internal/repair_kernel/contracts.py src/backend/polaris/cells/director/runtime/internal/repair_kernel/diagnostics.py src/backend/polaris/cells/director/runtime/tests/test_diagnostic_identity_frontier.py
```

Root must inspect resulting diff, repeat the dynamic baseline and tests, review old ID compatibility and record actual Paperclip execution settings. Do not automatically run a new paid Bench.
