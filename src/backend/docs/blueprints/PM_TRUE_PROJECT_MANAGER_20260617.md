# Making the PM Role a TRUE Project Manager

Date: 2026-06-17
Goal (/goal): perfect the PM (ProjectManager / 尚书令) role — its code, architecture,
assets, and unique functions — from the perspective of what a real senior project
manager actually does. Investigate real-PM professional capabilities first, then
implement them. Must fully use codegraph + superpowers. (Ultracode.)

Owner cells: `orchestration.pm_planning`, `orchestration.pm_dispatch`, plus PM
delivery assets under `delivery/cli/pm/`. Meta-tool only (§8: no target-project code).

## 1. Where the PM stands today (codegraph-grounded, workflow wksxljkbu)

PM is a strong FIRST-IN-CHAIN decomposer and contract author:
- LLM-driven brief→tasks with a quality-retry loop (`pm_planning/pipeline.py:564
  run_pm_planning_iteration`, `pm_adapter.py:117`), producing the canonical
  `pm_tasks.contract.json` (schema v2, `delivery/cli/pm/config.py:47`).
- THREE genuinely PM-grade assets already present and good:
  - requirements traceability matrix (`delivery/cli/pm/requirements_tracker.py`),
  - document version control + change-impact (`delivery/cli/pm/document_manager.py`),
  - measurability-enforced acceptance gating (`pm_planning/internal/task_quality_gate.py:2128`).

But against real senior-PM doctrine it is missing the **governance + quantitative**
half of the discipline:
- The PM persona is the **thinnest of all five roles** (`prompt_templates.py:156-169`
  = persona stub + 3-bullet Focus). The profession YAML declares
  `requires_milestones=true` / `requires_risk_assessment=true` with **no enforcing
  tool or gate**.
- No RAID log, no risk scoring, estimates exist as never-populated fields, the DAG
  is validated for cycles only (no critical path / schedule), re-planning is just an
  LLM quality-retry + fixed `director_iterations`, `pm.report.md` is an append-only
  verdict log (no %-complete / RAG / ETA), no stakeholder-clarification tool
  (whitelist is read-only), prioritization is a raw 1–5 int, no milestones / decision
  log / change-control as first-class data.
- Two structural debts: dual unwired iteration engines (`PmOrchestrator.run_iteration`
  has 0 callers; live path is the procedural `orchestration_engine.py:561 run_once`),
  and a **confirmed §8 violation** — `task_quality_gate.py` embeds hardcoded
  game/card3d business-domain tables + `_append_missing_*_domain_tasks` synthesizers
  (lines ~147-238 / 2252-2278 / 2358-2394) that inject target-project answers into the
  platform.

## 2. Reuse decision (§7 no-reinvent)

Polaris already has a workspace-scoped **Risk Register** in the Chief-Engineer
blueprint cell (`chief_engineer/blueprint/internal/risks.py`,
`public/contracts.py`): `RiskSeverity` (low/medium/high/critical/blocker),
`RiskStatus` (open/mitigating/accepted/resolved/reverted), `RiskRecordV1`, atomic
per-entry JSON under `runtime/risks/`, register/list/update_status/summarize, wired
into handoff-blocking governance. **codex is actively editing those files.**

The PM RAID log follows the same storage pattern as `risks.py` and aligns its
severity/status **vocabulary** with the CE Risk Register — and it adds the **A/I/D**
entry types (Assumptions, Issues, Dependencies) plus a **probability** axis that the
CE register does not model.

**Boundary correction (2026-06-17, overturns the earlier "import CE enums" plan).**
A deeper codegraph + governance-gate analysis (workflow `w6nh16mmt`, boundary expert)
showed that importing the CE enums is *not* the right reuse mechanism here:
- `orchestration.pm_planning/cell.yaml` `depends_on` does **not** declare
  `chief_engineer.blueprint`. The catalog governance gate
  (`run_catalog_governance_gate.py`) has a HIGH rule
  `declared_cell_dependencies_match_imports` that walks **all**
  `polaris/cells/**/*.py` — **including `tests/`** — scoping each file by *path
  structure*, not `owned_paths`. So a `from polaris.cells.chief_engineer...` import in
  **either** the internal module **or the test** would emit a new HIGH issue, and the
  in-tree `test_catalog_governance_gate` (`new_issue_count == 0`) would go red.
- PM is **upstream** of CE in the role chain (PM→Architect→CE→Director→QA), so a
  `pm_planning → chief_engineer.blueprint` edge inverts the dependency direction and
  couples PM-green to codex's churning CE WIP.

Therefore the RAID register uses **PM-owned enums whose values are byte-identical to
the CE vocabulary** (`RaidSeverity` = low/medium/high/critical/blocker; `RaidStatus`
= open/mitigating/accepted/resolved/reverted), importing **nothing** from
`chief_engineer` anywhere. Vocabulary parity is guaranteed by a **literal-parity
test** (hardcoded expected maps, no CE import). §7 is satisfied at the vocabulary
level: §7 protects existing *capabilities* (the Risk Register store + lifecycle
engine), not a 5-member string enum that the RAID log materially extends. The only
cross-package import is `resolve_logical_path` from `polaris.kernelone.storage`
(declared via `storage.layout`, and a kernel import creates no cell edge).
Verified: governance gate `new_issue_count == 0`, zero issue records referencing
`pm_planning`/`raid`.

## 3. Ranked plan (high-value × missing × §8-clean × floor-safe)

1. **§8 cleanup + generic Definition-of-Ready gate** — refactor
   `task_quality_gate.py`: delete the game/card3d domain tables + detectors +
   `_append_missing_*_domain_tasks`; replace with a generic DoR ruleset (measurable
   acceptance + concrete scope + estimate present + dependencies declared + risk
   assessed). **Bench-gated**: the deleted branches fire only on game/card3d
   contracts (e.g. L3-16 Tetris), so removal must be re-verified against the
   game-bench + L2-floor before the default flips. Blueprint-first.
2. **RAID register + risk scoring** — ← **LANDED** (`raid_register.py`, 48 tests).
   `pm_planning/internal/raid_register.py`: `RaidCategory` (risk/assumption/issue/
   dependency), PM-owned `RaidSeverity`/`RaidStatus` (CE-value-aligned, no CE import —
   see the boundary correction in §2), PM-native `RaidProbability` (rare..almost_certain),
   a pure `probability × impact` 5×5 `compute_risk_score`/`compute_risk_band`,
   `RaidRecordV1` (with derived `risk_score`/`risk_band` in `to_dict`), and a
   `RaidRegister` store (register/update_status/load/list/summarize) with atomic
   temp+`os.replace` writes, traversal-guarded ids, and fail-closed loads, under a
   PM-owned `runtime/pm/raid/` path. Shipped + unit-tested `KERNELONE_PM_RISK_GATE`
   predicate (env-read, default-OFF) is wired to **nothing** this increment. Purely
   additive / floor-safe; governance gate `new_issue_count == 0`. Designed-not-wired
   future hooks: planning-prompt open-RAID summary + a DoR rule blocking handoff on
   `open_critical_or_blocker > 0`, both behind the default-off gate.
3. **Dependency graph → critical-path / level-order schedule** — extend
   `pm_planning/internal/dependency_validator.py` with `compute_schedule(tasks)`
   (CPM: forward/backward pass, earliest/latest start, slack, critical path,
   makespan, topological levels). Pure algorithm, no I/O, no business code,
   deterministic, fully unit-tested. **Purely additive** (nothing on the live path
   calls it yet → zero floor risk); later consumed by prioritization + status ETA
   behind `KERNELONE_PM_CRITICAL_PATH` default-off.  ← **THIS INCREMENT**
4. **Project status rollup** — ← **PURE CORE LANDED** (`status_rollup.py`, 51 tests).
   `pm_planning/internal/status_rollup.py`: a pure, deterministic, clock-free,
   §8-clean core — `compute_status_rollup(...)` builds a frozen `PmStatusRollup`
   (%-complete, RAG health, ETA, `remaining_effort`, makespan + critical path read
   verbatim from #3's `Schedule` (§7), open-RAID from #2's `summarize()`, requirements
   coverage as a 0..1 ratio with `None` = untracked) from **plain-data inputs** — plus
   a byte-stable `render_status_markdown`. Fail-closed/total (never raises; ETA never
   fabricated — `None` unless velocity is finite > 0). Imports only stdlib +
   `.dependency_validator`; no delivery import, no new cross-cell edge; governance gate
   `new_issue_count == 0`; wired to nothing (floor-safe/additive). **Deferred delivery
   glue** (designed, not built): a `delivery/cli/pm` `build_pm_status_rollup(workspace,
   tasks)` gathers live inputs (TaskBoard counts, `requirements_tracker.get_coverage_report()`
   — dividing its 0..100 percent by 100 — `RaidRegister.summarize()`, `compute_schedule()`),
   stamps the UTC clock into `generated_at`, calls the core, and writes `pm.status.md` +
   `pm.status.json`.

Later: WSJF prioritization (consumes #3 critical-path criticality), milestones
registry, durable decision log, change-request register, failure-driven replan,
stakeholder-clarification tool, consolidate the unwired orchestrators, restore the
PMRole RoleBase interface.

## 4. Increment #3 design — `compute_schedule`

Add to `dependency_validator.py` (same cell, same pure-function style):

- `_task_weight(task) -> float`: estimate from `estimated_effort`/`estimated_hours`/
  `effort`/`estimate`; accepts a positive number or a size class
  (xs/s/m/l/xl/xxl → 0.5/1/2/4/8/16); default 1.0 (unit weight) when absent. No
  business semantics.
- `@dataclass(frozen=True) Schedule`: `order`, `levels`, `earliest_start`,
  `earliest_finish`, `latest_start`, `latest_finish`, `slack`, `critical_path`,
  `makespan`, `weights`, plus `to_dict()`.
- `compute_schedule(tasks) -> Schedule`: Kahn topo order (reusing the existing
  adjacency/in_degree construction); on an incomplete order raise the existing
  `DependencyCycleError` (single cycle-detection vocabulary). Forward pass for
  earliest start/finish + topological level; backward pass for latest start/finish;
  slack = latest_start − earliest_start (tolerance 1e-9); critical_path = slack≈0
  tasks in topological order; makespan = max earliest_finish. External deps (refs not
  in the task set) are skipped exactly like `validate_dependency_dag`.

Data flow (when later wired, gated): `compute_schedule` →
`get_ready_tasks_for_director` (critical-path tasks sort first) and the status
rollup ETA. This increment lands the algorithm + tests only; no live-path wiring.

## 5. Constraints honored

§8 (pure algorithm, no project literals); floor-safe (additive — no live-path call
yet; future wiring gated default-off, re-verified against L2-floor per
`l2-int4-floor-6of6`); §7 reuse (RAID reuses CE enums, no duplicate vocabulary, no
edits to codex's CE files); cells never depend on delivery; UTF-8 explicit;
blueprint-first; ruff/format/mypy/pytest before landing; code-review + verify
superpowers on the diff.
