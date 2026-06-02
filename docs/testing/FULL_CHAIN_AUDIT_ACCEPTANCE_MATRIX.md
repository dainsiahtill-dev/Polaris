# Full-Chain Audit Acceptance Matrix

This matrix maps the eight active audit requirements to concrete gates, evidence files, and resume commands.
It is intentionally evidence-first: a requirement is not complete unless the listed command has passed and the
listed artifact is present in the test output or runtime workspace.

## Phase Commands

Use `docs/testing/PHASE_REUSE_MATRIX_COMMANDS.md` as the command source of truth.

Required acceptance runs:

- Full cold chain: validates Court/Architect through QA on a fresh generated workspace.
- Resume from PM: validates PM, Chief Engineer, Director, and QA without rerunning Court.
- Resume from Director: validates Director code changes, diff visibility, policy evidence, and QA without rerunning PM.

## Requirements

| # | Requirement | Current Gate | Required Evidence | Required Command |
|---|---|---|---|---|
| 1 | Seed cannot mask runtime contribution | `full-chain-audit.spec.ts` writes `project.scenario.json`, `scenario.seed-definition.metrics.json`, `complexity.metrics.json`, `seed.file-snapshot.json`, and `round-XX.runtime-contribution.json`; Director runs with file changes must show non-zero runtime contribution and `complexity_contribution_breakdown` ratios. | `seed_metrics`, `runtime_contribution`, `complexity_contribution_breakdown`, `round-XX.runtime-contribution.json`, plus final audit JSON. | Full cold chain and resume from Director. |
| 2 | PM contract quality is strict | `auditPmContract()` rejects too few game tasks, generic paths outside workspace, missing scope, missing execution steps, missing executable/file acceptance, and missing game domains. | PM contract artifact, `pm_quality_history`, and PM phase PASS in final audit JSON. | Full cold chain and resume from PM. |
| 3 | Director code view opens latest diff | `DirectorCodePanel` defaults to the latest event, and `role-workspace-deep-tabs.spec.ts` / `full-chain-audit.spec.ts` assert latest diff or explicit summary expansion. | `director-code` screenshots and `Director code change view` assertion in full-chain; `director-tab-code.review.jpg` in role sweep. | Resume from Director plus role sweep. |
| 4 | Role pages prove core function, not just visibility | `role-workspace-deep-tabs.spec.ts` asserts PM documents, PM requirements matrix, PM history, PM workbench, Chief Engineer blueprint/handoff, Director terminal, Director strategy compare, Director debug, and workbench controls. | Role sweep screenshots (`*.review.jpg`) and one passing role sweep test. | `npm run test:e2e -- src/backend/polaris/tests/electron/role-workspace-deep-tabs.spec.ts`. |
| 5 | QA gate exposes evidence grade | Integration QA result must use `reason=integration_qa_passed` and `evidence_grade=real_command_passed`; weaker grades remain visible in audit JSON. | `qa_gate` in final audit JSON and `integration_qa.result.json`. | Full cold chain and resume from Director. |
| 6 | Director policy is structured and enforced | `director_policy_gate.py` validates AGENTS forbidden paths, workspace scope, and package scripts/dependencies diff; tool write, command redirect, direct write, and apply patch attach `director_policy`. Full-chain audit requires `policy_evidence_count > 0` when Director contributes file changes. | Tool execution test receipts, `director_tool_audit.policy_evidence_count`, and final audit JSON. | Focused policy pytest plus full cold chain or resume from Director. |
| 7 | Phase reuse matrix is fixed | `PHASE_REUSE_MATRIX_COMMANDS.md` defines cold chain, resume from PM, and resume from Director. Full-chain audit records skipped phases in `issues_fixed` with `root_cause=resume_strategy`. | Phase command doc and final audit JSON `issues_fixed`. | Resume from PM and resume from Director. |
| 8 | LLM settings persistence and close behavior are dedicated E2E | `settings-persistence-window-close.spec.ts` verifies provider deletion readback/reopen, role binding cleanup, `close_to_tray=true` hide behavior, and `close_to_tray=false` app exit. | Settings E2E attachments: `llm-config-saved-readback`, settings POST bodies, and three passing settings tests. | `npm run test:e2e -- src/backend/polaris/tests/electron/settings-persistence-window-close.spec.ts`. |

## Focused Verification Commands

### Role Functional Sweep

```powershell
npm run test:e2e -- src/backend/polaris/tests/electron/role-workspace-deep-tabs.spec.ts
```

### Settings Persistence And Window Close

```powershell
npm run test:e2e -- src/backend/polaris/tests/electron/settings-persistence-window-close.spec.ts
```

### Director Policy Gate

```powershell
python -m pytest src/backend/polaris/tests/domain/verification/test_director_policy_gate.py src/backend/polaris/kernelone/llm/toolkit/tests/test_tools_execution.py -q
```

## Completion Rule

The full objective is incomplete until:

1. The focused tests above pass on the current worktree.
2. A full cold-chain run passes on a fresh game workspace.
3. A resume-from-PM run passes on that same workspace.
4. A resume-from-Director run passes on that same workspace and records Director runtime contribution plus `director_policy` evidence.

Do not treat seed complexity, UI visibility, or a generic PASS label as sufficient acceptance evidence.
