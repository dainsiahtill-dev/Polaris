# Paperclip Repair Frontier Implementation Plan

> **For agentic workers:** Use installed Superpowers debugging/TDD/verification skills. The user explicitly selected Paperclip execution with root supervision; do not stop for repeat approvals, create branches/worktrees or commit.

**Goal:** Make an existing registered unique-export import planner reachable through the real public materialization plan-probe path without granting new writes or fabricating semantic exports.

**Architecture:** Reuse director.runtime registry and its public generic Plan/Run interfaces. Correct the runtime-owned diagnostic selection, retain coverage versus planning separation and same owner policy. Root assigns implementation to one Paperclip employee and accepts independently.

**Tech Stack:** Python3.12.3, existing registry/public queries, pytest, Ruff, mypy; Paperclip external only.

**Spec:** `docs/blueprints/POLARIS_PAPERCLIP_REPAIR_FRONTIER_20261006.md`.

## Global Constraints

- Sole writer: repair engineer. Source scope: `src/backend/polaris/cells/director/runtime/internal/repair_kernel/registry/_rules_typescript.py`; `src/backend/polaris/cells/director/runtime/public/service/_execution.py` only when independently demonstrated necessary.
- New test scope: `src/backend/polaris/cells/director/runtime/tests/test_materialization_unique_export_routing.py`.
- Engineering reports/checkpoints only under `.superpowers/sdd/2026-10-06-paperclip-repair-frontier/repair/`.
- Preserve earlier contracts.py/diagnostics.py/test_diagnostic_identity_frontier.py changes; no existing-test edits, new executable source_tool or extra public facade.
- No generated target edits, stubs, fake receipts, owner widening, weak QA, source installs, services, branches or commits.
- Shadow means no writes/effects. A coverage match alone is not a usable patch; unrelated errors cannot become covered_plannable through fallback.
- Existing model `gpt-6.1-sol`, effort high; single issue bounded1800s engineering budget, checkpoint each phase, do not extend any Polaris provider/Bench deadline.
- All shell RTK and UTF-8. Interpreter `/home/dains/.pyenv/versions/3.12.3/bin/python`.

## Task1: Existing planner routing, RED/GREEN

- [ ] Read assigned plan and runtime Cell/public contracts. CodeGraph first; if exact source absent, use explicit RTK fallback. Generated context.pack is currently absent, use actual cell.yaml/README/graph, do not fabricate a pack.
- [ ] Dynamically build generic authored base_files with one TS exporter and one incorrect compiled-path import. Compare public coverage, `PlanDirectorRepairCommandV1(mode=shadow)` with the registered source_tool, and `QueryDirectorRepairMaterializationPlanProbeV1(step_id=materialization.typescript_compiler)`.
- [ ] Add real regression tests that fail before code changes: typed/raw unresolved_relative_import routes to existing unique-export planner when structurally unique; assert only importer patch.
- [ ] Add ambiguity/missing symbol/unrelated diagnostic/unknown tool/unchanged input negative tests; shadow cannot authorize effects.
- [ ] Fix smallest registry selection gap. Preserve existing raw-term compatibility; no unrelated broad matching. Change `_execution.py` only if a concrete negative fallback case remains, and preserve truthful covered_plannable status.
- [ ] Repeat focused tests +91 diagnostic baseline + contract suite; list every broad failure with exact name, including known six. Ruff/format/type on touched files.
- [ ] Checkpoint dynamic baseline and test exits early; final REPORT.md and structured result include sources, real patch/coverage counts, unresolved risks. Do not mark whole r11, Bench, TS5110 repair, owner handoff or verifier closure complete.

Example generic inputs (tests must assert behavior, not source strings):

```python
files = {
    "src/math.ts": "export function add(a: number, b: number) { return a + b; }\n",
    "tests/verify.test.ts": 'import { add } from "../dist/math.js";\nconsole.log(add(1, 2));\n',
}
diagnostic = "unresolved relative import '../dist/math.js' in tests/verify.test.ts"
# Call real public coverage + explicit Plan + materialization plan-probe.
# The intended repair must be source-proven; no empty test or synthetic export.
```

Run:

```bash
rtk proxy env PYTHONPATH=/home/dains/Documents/polaris/src/backend rtk proxy /home/dains/.pyenv/versions/3.12.3/bin/python -m pytest -q src/backend/polaris/cells/director/runtime/tests/test_materialization_unique_export_routing.py src/backend/polaris/cells/director/runtime/tests/test_diagnostic_identity_frontier.py src/backend/polaris/cells/director/runtime/tests/test_repair_kernel_contract_1.py
```

## Independent investigations queued next

Runtime/ledger employee: read-only public owner/claim/handoff path, exact r11 TASK3 test ownership versus TASK1 repair claim; produce dynamic reproduction and precise candidate files. Process employee: read-only trace direct tsc/npm-test collector versus existing NativeValidationGroup/broker receipt; produce safe closed-staging migration design and negative tests. Neither edits Factory until root issues an exact nonoverlapping scope.

Root independently re-runs tests and inspects full diff before enabling another writer. Existing primary workspace serialization is preserved; do not claim three concurrent writers.

## Execution ruling after user boundary update

User delegated Paperclip fixes to its project team and instructed root not to touch Paperclip code, only advance Polaris. The initial Paperclip POL-6 dispatch was blocked before model invocation by historical writer compatibility. Root therefore implements this already-approved narrow Polaris bucket directly; Paperclip code/data/locks are not modified or bypassed. Paperclip issue/run success is not claimed.

Independent review dynamically found that structured coverage alone still gives0patches: existing `typescript_syntax/imports_exports.py::_parse_unresolved_relative_import_errors` ignores canonical path+specifier and reads raw text only. Root grades this as an input-contract gap in the requested typed/raw planning closure, not a deferred success. Extend this bucket to that existing helper only; canonical typed fields take precedence over stale raw provenance. Add structured real-plan, stale raw-owner and malformed-specifier RED/GREEN. No public contract, source_tool, fallback, execution policy or ownership expansion.
