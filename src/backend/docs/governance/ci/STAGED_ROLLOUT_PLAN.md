# Governance Gates - Staged Rollout Plan

Owner: Squad F (CI Governance)
Created: 2026-05-07
Workflow: `.github/workflows/governance-gates.yml`
Authority: `src/backend/docs/governance/ci/pipeline.template.yaml`

This document records the 5-stage rollout plan for ACGA 2.0 governance
gates into GitHub Actions. Stage 1 is live (audit-only, non-blocking);
Stages 2-5 are designed in the workflow file but gated behind
`workflow_dispatch.inputs.stage` so PR / push events do not run them.

## Why staged rollout

- Polaris runs ~28k pytest collected items and 15+ governance gates.
- Bringing every gate online at once would block PRs en masse against
  pre-existing debt that was never gated before.
- ACGA 2.0 fail-closed-but-graceful philosophy: surface debt first,
  baseline it, then ratchet down.

## Stage map

| Stage | Goal                       | Default                | Blocking | Gates                                                                                                   |
|-------|----------------------------|------------------------|----------|---------------------------------------------------------------------------------------------------------|
| 1     | Audit / observability      | enabled (PR + push)    | no       | catalog_governance_audit, directory_hygiene_gate, (utf8 audit deferred)                                 |
| 2     | Fail on new violations     | manual dispatch only   | yes      | catalog_governance_fail_on_new, manifest_catalog_reconciliation_gate, cell_internal_fence_gate          |
| 3     | Release quality            | manual dispatch only   | yes      | kernelone_release_gate (collect+tests), delivery_cli_hygiene_gate, opencode_convergence_gate            |
| 4     | Structural governance      | manual dispatch only   | yes      | structural_bug_governance_gate, contextos_governance_gate, tool_calling_canonical_gate                  |
| 5     | Hard-fail (clean domains)  | manual dispatch only   | yes      | catalog_governance_hard_fail, migration_preflight_gate                                                  |

## Stage 1 - Live (2026-05-07)

Triggers: every `pull_request` against `main`, every `push` to `main`,
plus manual `workflow_dispatch` with `stage=1`.

Gates running:

1. `catalog_governance_audit`
   - Command: `python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only --report workspace/meta/governance_reports/catalog_governance_gate.audit.json`
   - Local dry-run 2026-05-07: exit_code=0, issue_count=0.
   - `continue-on-error: true` -> never blocks PR.

2. `directory_hygiene_gate`
   - Command: `python docs/governance/ci/scripts/check_directory_hygiene.py --workspace .`
   - Local dry-run 2026-05-07: exit_code=0, "No directory hygiene violations".
   - `continue-on-error: true`.

3. `explicit_utf8_text_io_gate` - **DEFERRED to Wave 2**
   - `pipeline.template.yaml` calls `run_kernelone_release_gate.py --mode utf8-audit`
     but the script today only supports `(collect|tests|all)`.
   - The workflow contains the wired step under `if: false` for visibility;
     once the gate script grows the `utf8-audit` mode (or we point to a
     dedicated UTF-8 scanner), flip the guard.

Outputs: JSON reports uploaded as `governance-stage1-reports` artifact,
30-day retention.

## Stage 2 - Designed, disabled

Activation procedure:
1. Confirm baselines exist and are up to date:
   - `tests/architecture/allowlists/catalog_governance_gate.baseline.json`
   - `tests/architecture/allowlists/manifest_catalog_mismatches.baseline.jsonl`
   - `tests/architecture/allowlists/cell_internal_fence.baseline.json`
2. Refresh baselines locally with the `--mode audit-only` runs and
   commit them.
3. Edit `governance-gates.yml` `stage2-fail-on-new.if:` -> change to
   include `pull_request` trigger:
   `if: github.event_name == 'pull_request' || (github.event_name == 'workflow_dispatch' && (github.event.inputs.stage == '2' || github.event.inputs.stage == 'all'))`
4. Tag merge with verification card per `AGENTS.md §8.6`.

Risk if activated prematurely: PRs that touch any cell with depends_on
drift will fail until baseline absorbs current state.

Rollback: revert the `if:` change; reports already exist as artifacts.

## Stage 3 - Designed, disabled

Heaviest stage: full `pytest` suite for KernelOne fences and OpenCode
convergence; collect-only stability check.

Pre-activation checklist:
- KernelOne suite green locally end-to-end.
- `opencode_convergence_gate.json` baseline merged.
- CI runner cost budget reviewed (long pytest suite).

## Stage 4 - Designed, disabled

Includes `polaris.delivery.cli agentic-eval` which requires runtime
provider bindings. Activation requires CI secrets for the runtime
binding (LLM provider keys via `KERNELONE_*` env vars), which is **not**
in scope for this wave - left disabled by design.

Pre-activation checklist:
- Runtime binding secrets configured in repo secrets.
- `tool_calling_canonical_gate.json` baseline reviewed.
- Verify `polaris/tests/contextos/` is fully green.

## Stage 5 - Designed, disabled

Hard-fail mode is reserved for **cleaned domains only**. Catalog
governance is currently at issue_count=0 (per AGENTS.md §15.4 #2),
so this stage is technically activatable, but we prefer to land
Stage 2-4 first to ensure no regression vector slips through audit-only.

## Risk register

| Risk                                          | Mitigation                                                |
|-----------------------------------------------|-----------------------------------------------------------|
| Stage 1 false positive blocks PRs             | `continue-on-error: true` everywhere in Stage 1           |
| Future gate script changes break command flag | Local dry-run before each wave; pin script version via SHA |
| Artifact disk pressure                        | 30-day retention, artifact paths scoped to JSON only      |
| Workflow forks duplicating ci.yml work        | This workflow is dedicated to governance gates only;       |
|                                               | `ci.yml` already runs subset under `governance` job - that |
|                                               | path is preserved unchanged this wave                      |

## Rollback for Stage 1

If Stage 1 starts producing noise users find disruptive:
1. Set `if: github.event_name == 'workflow_dispatch'` on `stage1-audit`.
2. PR / push triggers go silent; manual dispatch still works.
3. No source code changes required, just a single-line workflow edit.

## Cross-references

- `AGENTS.md §15.4` (governance gap log; updated 2026-05-07 to mark
  Stage 1 entry into automation).
- `CLAUDE.md §6.4` and `GEMINI.md §6.4` mirror the same.
- `pipeline.template.yaml` is the source-of-truth command list; this
  workflow is its Actions-binding.
