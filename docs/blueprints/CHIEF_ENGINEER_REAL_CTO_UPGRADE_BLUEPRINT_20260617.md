# Chief Engineer — Real CTO Upgrade Blueprint

Date: 2026-06-17
Status: Tier-1 + Tier-2 (ADR, handoff-enforcement, Tech Radar, Post-Mortem) LANDED — 8 governance capabilities, gate-verified

## Tier-2 increment #4 — Post-Mortem / Incident Review (2026-06-17)

Closes the failure-learning loop (Risk Register anticipates failure; the
post-mortem log learns from it). A blameless incident-review record:
incident / severity (sev1-4) / timeline / root_cause / impact / action_items
/ status, mirroring the hardened ledger pattern under `runtime/post_mortems/*`.

- `internal/post_mortem.py` — `PostMortemLog` (traversal guard, uuid nonce,
  tolerant loader, atomic UTF-8) + `summarize()` with an `open_action_items`
  count (action items on non-closed incidents).
- Contracts: `IncidentSeverity`, `PostMortemStatus`, `PostMortemRecordV1`,
  register/list/update commands+queries, event.
- Service: `register_post_mortem` / `list_post_mortems` /
  `update_post_mortem_status` / `summarize_post_mortems`.
- HTTP: `POST`/`GET /chief-engineer/post-mortems`, `POST .../{id}/status`.
- Frontend: 3 typed service fns + a "Post-Mortems" column in the governance panel.
- Governance: `runtime/post_mortems/*` in `cell.yaml` + catalog (gate clean).

## Lifecycle coverage (8 governance capabilities)

plan (blueprint) → anticipate (Risk Register) → gate (Quality Gate) →
enforce (Handoff Decision) → execute → learn (Post-Mortem); plus ongoing
governance: Tech-Debt Ledger, ADR Decision Log, Tech Radar / stack policy,
and Rollback linkage. Remaining (advisory/observability, genuinely deferred):
build-vs-buy evaluator, perf/capacity budget, realtime dashboard.

## Tier-2 increment #3 — Tech Radar / stack policy (2026-06-17)

A 技术总监 owns the tech radar (approved/trial/hold/deprecated libraries).

- `internal/tech_radar.py` — `TechRadarLedger` (mirrors the hardened risks
  pattern: traversal guard, uuid nonce, tolerant loader, atomic UTF-8) +
  `check_stack_policy(libraries)` which flags any requested library whose
  latest ring is `hold`/`deprecated` (case-insensitive, latest-decided wins).
- Contracts: `TechRadarRing` (adopt/trial/hold/deprecated), `TechRadarEntryV1`,
  `StackPolicyViolationV1`, register/list/update commands+queries, event.
- Service: `register_tech_radar` / `list_tech_radar` / `update_tech_radar_ring`
  / `summarize_tech_radar` / `check_stack_policy`.
- HTTP: `POST`/`GET /chief-engineer/tech-radar`, `POST .../{id}/ring`,
  `POST /chief-engineer/stack-policy/check`.
- Frontend: 4 typed service fns + a "Tech Radar" column in the governance panel.
- Governance: `runtime/tech_radar/*` added to `cell.yaml` + catalog (gate clean).

## Tier-2 increment #2 — Director-handoff gate enforcement (2026-06-17)

Closes the quality-gate loop: the gate now *blocks* handoff, not just records.

- `internal/handoff.py` — single source of truth (`build_handoff_decision`,
  `handoff_enforcement_enabled`), imported by both `service` and
  `ce_consumer` (no public↔internal cycle). A handoff is blocked when the
  deterministic quality gate has blockers OR the workspace Risk Register has
  open critical/blocker risks for the task. Fail-closed: malformed → blocked.
- Contract `HandoffDecisionV1` (allowed / blocker_count / warning_count /
  open_blocker_risk_count / blockers / reason).
- Service: `evaluate_handoff_decision`, `evaluate_handoff_decision_for_blueprint`
  (fail-closed None on missing), `assert_handoff_ready` (raises
  `ChiefEngineerBlueprintErrorV1` code `handoff_blocked`).
- **CE-consumer enforcement** (`ce_consumer._claim_and_process_one`): the
  decision is always surfaced on the ack metadata; when
  `KERNELONE_CE_HANDOFF_ENFORCEMENT` is opted in (default OFF), a blocked
  decision requeues the task to `pending_design` instead of acking it to the
  Director. Default-OFF keeps live pipeline behavior unchanged (proven: the
  8 existing ce_consumer tests still pass).
- HTTP: `GET /chief-engineer/handoff-decision?blueprint_id=` (the
  PM/Director/desktop consultation surface; fail-closed on missing).
- Diagnostics: `_handoff_blockers` now adds `open_blocker_risks` so the
  desktop `can_handoff` honors the gate at the dispatch boundary (read-only,
  defensive — a register read failure never crashes diagnostics).
- Frontend: `getChiefEngineerHandoffDecision` service fn + `HandoffDecision` type.

Rollout: pipeline enforcement is env-gated default-OFF (per the gated-rollout
discipline); the decision/HTTP/diagnostics surfaces are live immediately.

## Tier-2 increment #1 — Architecture Decision Log (2026-06-17)

ADR ownership is the most CTO-defining capability, so it landed next. Key
architectural finding: the existing internal `adr_store.py` is **not** a
decision log — it is a construction-plan **delta-compiler** (create_blueprint
→ propose_adr → compile → apply deltas to `construction_steps`) used inside
the CE→Director pipeline, sharing the `runtime/blueprints/*` key. Surfacing it
naively would round-trip governance blueprints through `BlueprintBase` and
**strip** `target_files`/`governance` — a data-loss clobber. So Tier-2 ships a
**separate, lightweight Architecture Decision Log** (canonical ADR shape:
context / decision / consequences / alternatives / status), stored under
`runtime/adr_log/*`, mirroring the hardened risks/tech-debt pattern:

- `internal/adr_log.py` (`ADRDecisionLog`): traversal-guarded ids, uuid
  nonce, corrupt-tolerant loader, atomic UTF-8 writes, and `supersedes`
  auto-marks the predecessor `superseded`.
- Contracts: `ADRStatus` enum (proposed/accepted/superseded/deprecated/rejected),
  `ADRRecordV1` + register/list/update commands+queries + `ADREventV1`
  (fail-closed enum coercion).
- Service: `register_adr` / `list_adrs` / `update_adr_status` / `summarize_adrs`.
- HTTP: `POST`/`GET /chief-engineer/adrs` + `POST /chief-engineer/adrs/{id}/status`.
- Frontend: 3 typed service fns + a "Decision Log" column in the governance panel.
- Governance: `runtime/adr_log/*` added to `cell.yaml` + catalog (gate `new_issue_count=0`).

The internal `adr_store.py` compiler is intentionally left in place and
unsurfaced (it remains a Director-pipeline implementation detail).

## Landed summary (2026-06-17)

Tier-1 shipped end-to-end and is fully gate-verified:

- **Contracts** (`public/contracts.py`): 5 enums + `RiskRecordV1`,
  `TechDebtRecordV1`, `QualityGateResultV1`, `RollbackLinkV1`,
  `GovernanceSummaryV1`, the register/list/update commands+queries, and
  the audit event types. Enum coercion is **fail-closed** (invalid/empty
  severity or status → `ValueError`, never a silent default).
- **Internal**: `risks.py` (RiskRegister), `tech_debt.py`
  (TechDebtLedger), `quality_gate.py` (pure deterministic evaluator),
  `rollback_link.py`. Storage is atomic (temp + replace), UTF-8, and
  **path-traversal-hardened** — `risk_id`/`debt_id` are validated as bare
  safe tokens before any filesystem access (defense-in-depth at the
  storage boundary, on top of the router's own path normalization). Ids
  carry a uuid suffix so they cannot collide within a microsecond.
  Loaders tolerate corrupt/invalid enum values on disk (coerce to a safe
  default rather than crashing `list()`).
- **Service** (`public/service.py`): `register_risk` / `list_risks` /
  `update_risk_status`, the tech-debt trio, `summarize_*`,
  `build_blueprint_governance`, `attach_governance_to_blueprint`
  (mutates the payload in place + recomputes `handoff_ready` from the
  gate), and `get_blueprint_governance` (the read API for the
  PM/Director/QA loop — recomputes fresh so resolving a blocker risk
  flips the gate without regenerating the blueprint).
  `generate_task_blueprint` now attaches governance on every generate.
- **HTTP** (`delivery/http/v2/chief_engineer.py`): 6 auth-gated routes
  (`POST`/`GET /chief-engineer/risks`, `POST /chief-engineer/risks/{id}/status`,
  and the tech-debt mirror). Invalid severity/status → 400; missing
  record → 404; UTF-8 round-trips; traversal ids → 4xx (never processed).
- **Frontend** (`services/chiefEngineerService.ts` +
  `components/chief-engineer/ChiefEngineerGovernancePanel.tsx`): 6 typed
  service functions (strict TS, no `any`) and a read-only governance
  panel mounted in the WorkbenchPanel.
- **Governance**: `cell.yaml` + catalog `cells.yaml` declare the new
  `runtime/risks/*` and `runtime/tech_debt/*` state owners / effects
  (catalog gate `new_issue_count == 0`).

Verification: backend 333 tests pass (ruff + mypy clean); frontend
typecheck + lint clean, 49 tests pass. A 4-agent adversarial review
(workflow) surfaced — and this landing fixed — a path-traversal blocker,
an enum fail-closed inconsistency, and an id-collision hardening gap.

## 0. Why this blueprint exists

`chief_engineer.blueprint` (工部尚书) currently does one thing well:
**produce a per-task handoff blueprint** (target files, acceptance criteria,
execution checklist, dependencies, risks). It is, functionally, a Tech
Lead deliverable, not the full surface of a 技术总监 / CTO.

Industry expectations for that role — confirmed by every CTO handbook
worth reading (Will Larson, Camille Fournier, ex-Google SRE, ThoughtWorks
Tech Director canon) — include at minimum:

| Capability                  | Industry Name                | Current Polaris State         |
|-----------------------------|------------------------------|--------------------------------|
| Per-task plan handoff       | Engineering plan             | ✅ `generate_task_blueprint`   |
| Architecture decisions log  | ADR / RFC                    | ⚠️ Internal `adr_store.py` only |
| Risk register               | Risk register                | ❌ Flat list per task          |
| Tech-debt ledger            | Tech debt register           | ❌ Not present                 |
| Hard-block / soft-block gate| Build / No-build gate        | ⚠️ `contract_completeness` boolean only |
| Post-build review sign-off  | Code review / acceptance     | ⚠️ In Director pool, not surfaced |
| Rollback plan               | Rollback / disaster recovery | ⚠️ `rollback_guard.py` exists, not bound |
| Stack / library policy      | Tech radar                   | ⚠️ `RoleLibraryPolicy` exists, not enforced per task |
| Performance / capacity budget | SLO / capacity             | ❌ Not present                 |
| Post-mortem synthesis       | Incident review              | ❌ Not present                 |
| Cross-task dependency policy| Dependency governance        | ⚠️ `validate_dependency_dag` only |

The Tier-1 slice of this blueprint lands the four highest-leverage
missing capabilities and wires them into the existing public surface so
PM, Director, and QA can consume them without breaking changes.

## 1. Tier-1 scope (this blueprint's implementation target)

### 1.1 Risk Register (real, not a flat list)

Public contracts (additive, no breakage):

- `RegisterRiskCommandV1(task_id, title, severity, owner, mitigation, links)` → result
- `ListRisksQueryV1(task_id?, severity?, status?)` → result
- `UpdateRiskStatusCommandV1(risk_id, status, note)` → result
- `RiskRecordV1` dataclass: `risk_id, task_id, title, severity, owner,
  mitigation, status, detected_at, links, supersedes, history`
- `RiskSeverity` enum: `low | medium | high | critical | blocker`
- `RiskStatus` enum: `open | mitigating | accepted | resolved | reverted`
- `RiskEventV1(risk_id, action, actor, at, note)` event type

Persistence: `runtime/risks/{risk_id}.json`, atomic JSON via the
existing `resolve_logical_path` pattern. No new store class.

### 1.2 Technical Debt Ledger

Public contracts (additive):

- `RegisterTechDebtCommandV1(title, description, severity, surface, owner, evidence)`
- `ListTechDebtQueryV1(severity?, surface?, status?)` → result
- `UpdateTechDebtStatusCommandV1(debt_id, status, note)` → result
- `TechDebtRecordV1`: `debt_id, title, description, severity, surface,
  owner, evidence, status, registered_at, history`
- `TechDebtSeverity`: `trivial | minor | major | severe | fatal`
- `TechDebtStatus`: `registered | acknowledged | scheduled | paid | wontfix`
- `TechDebtEventV1` event type

Persistence: `runtime/tech_debt/{debt_id}.json`.

### 1.3 Quality Gate with explicit blocking conditions

Replace the boolean `contract_completeness.handoff_ready` with a
structured gate. The boolean is kept for backward compat as a derived
field (`handoff_ready = blocker_count == 0`).

Public contracts (additive):

- `QualityGateResultV1`: `passed: bool, blocker_count, warning_count,
  info_count, blockers[], warnings[], info[], evaluated_at`
- The existing `TaskBlueprintResultV1.recommendations` tuple is
  augmented with a derived `gate: QualityGateResultV1` field
  (additive; downstream deserializers ignore unknown fields).

The gate is computed by `evaluate_quality_gate(blueprint_payload)`,
purely deterministic, no LLM:

- `blocker` = missing `target_files` OR missing `acceptance_criteria` OR
  `target_files` includes a path already locked by another in-flight
  task (per `task_board`)
- `warning` = missing `execution_checklist` OR empty `dependencies` when
  task has more than 3 acceptance criteria OR any risk with severity
  `critical` or `blocker` whose status is `open`
- `info` = `recommendations` shorter than 2 entries

### 1.4 Rollback linkage inside the blueprint

Augment the blueprint with a `rollback` sub-document:

```yaml
rollback:
  enabled: true
  strategy: "git_revert"     # git_revert | manifest_restore | file_snapshot
  marker_path: "runtime/state/blueprints/{blueprint_id}.stash"
  preconditions: ["no_risks_open_with_severity_blocker"]
```

Generated deterministically from the task's `target_files` and the
workspace's `.git` presence. Existing `rollback_guard.create_rollback_guard`
is wired in; no new module.

Public contract: `RollbackLinkV1(enabled, strategy, marker_path, preconditions)`.

The blueprint now carries a single `governance` field that aggregates
`risk_register_summary`, `tech_debt_linked`, `quality_gate`, `rollback`.
Old consumers that ignore unknown fields are unaffected.

## 2. New / changed files

| File                                                                       | Change                                        |
|----------------------------------------------------------------------------|------------------------------------------------|
| `src/backend/polaris/cells/chief_engineer/blueprint/public/contracts.py`   | + `RiskRecordV1`, `TechDebtRecordV1`, `QualityGateResultV1`, `RollbackLinkV1`, `RegisterRiskCommandV1`, `ListRisksQueryV1`, `UpdateRiskStatusCommandV1`, `RegisterTechDebtCommandV1`, `ListTechDebtQueryV1`, `UpdateTechDebtStatusCommandV1`, `RiskEventV1`, `TechDebtEventV1` |
| `src/backend/polaris/cells/chief_engineer/blueprint/public/service.py`     | + `register_risk`, `list_risks`, `update_risk_status`, `register_tech_debt`, `list_tech_debt`, `update_tech_debt_status`, `evaluate_quality_gate`, `build_rollback_link`, `attach_governance_to_blueprint` |
| `src/backend/polaris/cells/chief_engineer/blueprint/internal/risks.py`     | NEW — risk register storage + helpers          |
| `src/backend/polaris/cells/chief_engineer/blueprint/internal/tech_debt.py` | NEW — tech debt ledger storage + helpers       |
| `src/backend/polaris/cells/chief_engineer/blueprint/internal/quality_gate.py` | NEW — deterministic quality-gate evaluator  |
| `src/backend/polaris/cells/chief_engineer/blueprint/internal/rollback_link.py` | NEW — rollback-link builder (wraps `create_rollback_guard`) |
| `src/backend/polaris/cells/chief_engineer/blueprint/tests/test_*.py` (extend) | + new tests for the above                       |
| `src/backend/polaris/cells/chief_engineer/blueprint/cell.yaml`             | + `state_owners: runtime/risks/*`, `runtime/tech_debt/*` |
| `src/backend/polaris/delivery/http/v2/chief_engineer.py`                   | + 6 routes (register/list/update for risks + tech_debt) + `?include=governance` on existing blueprint endpoints |
| `src/frontend/src/app/services/chiefEngineerService.ts`                    | + 6 service functions + 2 governance types    |
| `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkbenchPanel.tsx` | + 2 panels (Risk Register, Tech Debt) — read-only, tab-based |
| `src/frontend/src/app/components/chief-engineer/ChiefEngineerWorkbenchPanel.test.tsx` | + 2 new tests for the new panels |

## 3. Architecture (text diagram)

```
                 ┌─────────────────────────────────────┐
                 │  Public boundary (contracts.py)     │
                 │  RiskRecordV1 / TechDebtRecordV1    │
                 │  QualityGateResultV1 / RollbackLink │
                 └────────────┬────────────────────────┘
                              │
   ┌──────────────────────────┼──────────────────────────┐
   │                          │                          │
   ▼                          ▼                          ▼
┌────────────┐         ┌────────────┐              ┌────────────┐
│ risks.py   │         │ tech_debt.py│              │ quality_gate│
│ (storage)  │         │ (storage)   │              │ (pure fn)   │
│            │         │             │              │             │
│ runtime/   │         │ runtime/    │              │ evaluates   │
│ risks/*.json│        │ tech_debt/  │              │ blueprint   │
│            │         │ *.json      │              │ payload     │
└────────────┘         └────────────┘              └────────────┘
                                                        │
                                                        ▼
                                              ┌────────────────┐
                                              │ rollback_link  │
                                              │ (wraps         │
                                              │ create_        │
                                              │ rollback_guard)│
                                              └────────────────┘

PM (task intake) ──▶ register_risk / register_tech_debt (per task)
Director (handoff) ──▶ reads blueprint.governance.quality_gate
                     → blocks on blockers
                     → emits warnings
QA (post-build)    ──▶ update_risk_status, update_tech_debt_status
Desktop / API      ──▶ list_*, get_*
```

## 4. Design rules (binding)

1. **No business-project code** — Polaris §8. Risk and debt titles
   belong to the user's task domain; the cell must not synthesize
   project-specific names. The title is provided by the caller.
2. **§6.6 canonical-gate iron rule** — tool names and event names are
   preserved verbatim. New event names follow `risk_registered`,
   `tech_debt_registered`, `quality_gate_evaluated` (lowercase, snake).
3. **UTF-8** — all JSON I/O uses `encoding="utf-8"`.
4. **Type hints required** — all new functions, dataclass fields, and
   return types are fully typed. No `# type: ignore`.
5. **Fail-closed** — malformed input raises `ChiefEngineerBlueprintErrorV1`.
6. **Deterministic quality gate** — `evaluate_quality_gate` must be pure;
   same blueprint → same gate. No clock drift unless passed via
   `evaluated_at` override.
7. **Backward-compat** — `TaskBlueprintResultV1` gains a `governance`
   field; existing `summary / recommendations / risks` stay. Old
   deserializers ignore the new field.
8. **Atomic writes** — `risks.py` and `tech_debt.py` use temp-file +
   replace, same as `BlueprintPersistence.save`.

## 5. Verification

### 5.1 Unit tests

- `test_risks.py` — register / list / update round-trip; severity
  validation; status transitions; supersede chain; multi-task isolation.
- `test_tech_debt.py` — register / list / update round-trip; severity
  validation; surface filter; history accumulation.
- `test_quality_gate.py` — empty/missing-target → blocker;
  large-task-no-deps → warning; risk-with-blocker-status → warning.
- `test_rollback_link.py` — git workspace → `git_revert` strategy;
  non-git workspace → `file_snapshot`; blocker-risk preconditions.
- `test_contracts.py` — frozen-dataclass invariant; new V1 dataclasses
  reject empty `task_id`; `__post_init__` invariants.

### 5.2 Integration tests

- `test_ce_consumer_integration.py` — extend to assert governance
  summary appears in the consumer's emitted payload.
- HTTP route tests — 6 new routes; auth required; workspace override;
  UTF-8 round-trip on Chinese titles; 4xx on invalid severity.

### 5.3 Quality gates (must all pass)

1. `ruff check <paths> --fix && ruff format <paths>`
2. `mypy <paths>` — `Success: no issues found`
3. `pytest <tests> -q` — 100% green

## 6. Out of scope for Tier-1

- Build-vs-buy evaluator — Tier-2
- Post-mortem synthesis — Tier-2
- Performance / capacity budget — Tier-2
- Architecture constraint guard (per-ADR enforcement) — Tier-2
- Stack / library policy enforcement — Tier-2
- Dashboard realtime Risk / Tech Debt panels — Tier-2 (Tier-1 ships
  read-only frontend tab; live-update is Tier-2)
- Director pool integration of quality gate — Tier-2 (Director pool
  already has phases; Tier-1 only persists the gate, Director
  consumption is wired in Tier-2)
- Resident / autonomy integration — Tier-2

## 7. Rollout

- Default OFF for any new env flag (none needed in Tier-1; the cell
  surface is purely additive). New commands are available immediately.
- Smoke command: `python -m polaris.cells.chief_engineer.blueprint.tests.smoke_governance`
  (new) — registers one risk, one tech-debt, generates one blueprint,
  evaluates the gate, asserts handoff-ready=false on missing acceptance.

## 8. Risks & Boundaries

- The new contracts add 12 new public symbols. Old consumers that
  deserialize the blueprint JSON by `dict.get` patterns must be
  tolerant of new keys. Audit any such callers (search hits at the
  time of writing are within the CE cell only).
- `quality_gate.py` is pure; no LLM cost. This is deliberate — gate
  evaluation must not require a model to stay deterministic and
  fail-closed.
- `evaluate_quality_gate` is a Tier-1 baseline. A real 技术总监 would
  also consult a static analyzer or test coverage report; that is
  Tier-2 and requires a new subgraph.
- The Risk Register does not deduplicate on `title`. Real-world
  registers usually key on `(task_id, title)`. This is an intentional
  Tier-1 simplification; the `supersedes` field carries forward
  resolution. Caller-side dedup is documented.

## 9. Self-check

- Reuse > new: Tier-1 reuses `BlueprintPersistence`'s JSON pattern,
  `create_rollback_guard`, `resolve_logical_path`, the `V1` contract
  naming, and the existing audit-event emission. No new Cell, no new
  KernelOne module, no new subgraph.
- Single source of truth: governance is computed at blueprint
  generation; risk / tech-debt are stored separately and linked.
- No silent caps: list endpoints return all matches; no pagination
  in Tier-1 (intentional — workspaces are bounded by task count,
  not data volume).
- Fail-closed: malformed input → `ChiefEngineerBlueprintErrorV1`.
- UTF-8: explicit `encoding="utf-8"` on every `open()` and JSON I/O.
- §8 honored: no project-specific titles, paths, or templates in the
  cell surface.

## 10. Future optimization (Tier-2 and beyond)

1. Director-side gate enforcement (consume `governance.quality_gate`
   in the `SPEC_REVIEW` / `QUALITY_REVIEW` phases; emit
   `director_quality_gate_failed` event when blockers > 0).
2. Dashboard realtime — pipe risk / debt changes through the same
   `runtime/events/*.jsonl` journal that `chief_engineer_preflight` uses.
3. Resident / autonomy — let the resident role triage open risks
   nightly and propose mitigations.
4. ADR / RFC — surface the existing `adr_store.py` as a public cell
   contract (was Tier-0 already, kept internal until governance wires
   it in Tier-2).
5. Cross-workspace rollup — `list_risks` and `list_tech_debt` can
   accept a workspace-list argument and emit a rollup; useful for a
   CTO overview view.
6. Stack / library policy enforcement — read `RoleLibraryPolicy`
   during quality-gate evaluation and surface a `forbidden_library`
   blocker when a target file imports a forbidden module.

## 11. Adjudication sources

- `docs/AGENT_ARCHITECTURE_STANDARD.md` (Cell reuse rule, §2.1)
- `src/backend/AGENTS.md` §8 (no business code)
- §6.6 (raw tool-name preservation) — applies by analogy to event names
- `CLAUDE.md` (3 quality gates, UTF-8, fail-closed)
