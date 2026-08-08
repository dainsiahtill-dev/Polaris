# Platform Module Solidification

**Purpose**: Stop the R116–R153 infinite linear defect loop. Complete subsystems become **sealed modules** with fixed invariants and single-module gates. Agents change sealed modules only under explicit unfreeze + re-gate.

**Slogan**: 完善的固化 · 单模块可测 · 级联可测 · Bench 只验收四支柱

---

## 1. Why this exists

Recent 40+ defect rounds (R116–R153) repeatedly rediscovered the same classes of platform bugs:

| Class | Examples | Lesson |
|-------|----------|--------|
| Observation kills execution | R153 keepalive → cancel → authority_closed | Observation module ≠ execution kill switch |
| Context final-role | R152 sibling pin as final system | Final request assembly is its own module |
| Tool batch partial apply | R151 DEO serial sibling continue after fail | Mutation batch is sealed policy |
| Grant/lease races | authority_closed mid multi-task | Physical attempt + lease modules |
| Measure vs repair | bench_gates "fixing" workspace | Four pillars are measure-only |

Without freeze boundaries, every fix re-proves the entire L1–L12 surface. That is why projects cannot be completed end-to-end: residual attribution keeps bouncing between modules.

Reference systems (Codex CLI / long-horizon agent runners) separate:

1. **Observation** (reconnect, wall-clock only)
2. **Authority / capability** (grants, leases)
3. **Effects** (tools, DEO)
4. **Settlement / ledger**
5. **Eval harness** (bench as measure, not product)

Polaris must do the same at the platform module layer.

---

## 2. Test pyramid

```
        ┌─────────────────────┐
        │  bench (L1-01+)     │  four pillars + N-batch
        │  --mode bench       │  only after cascade green
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────┐
        │  cascade            │  sealed + hardening, dependency order
        │  --mode cascade     │  fail-closed stop on first module
        └──────────┬──────────┘
                   │
        ┌──────────▼──────────┐
        │  module             │  full functional suite for ONE module
        │  --module M0x_*     │  no live LLM required for sealed set
        └─────────────────────┘
```

### Commands

```bash
# List freeze registry
python src/backend/scripts/platform_modules/run_module_gates.py --list

# Single module (example: event wait observation — R153 sealed)
python src/backend/scripts/platform_modules/run_module_gates.py --module M01_event_wait

# All sealed modules only
python src/backend/scripts/platform_modules/run_module_gates.py --mode sealed

# Cascade sealed + hardening
python src/backend/scripts/platform_modules/run_module_gates.py --mode cascade \
  --json-out /tmp/platform-module-cascade.json

# Full isolated L1-01 bench (long; do not use main ports 49977/5173)
python src/backend/scripts/platform_modules/run_module_gates.py --mode bench \
  --work-dir /tmp/factory-bench-module-gate-l1-01 \
  --timeout 5400
```

---

## 3. Module status

| Status | Meaning | Change policy |
|--------|---------|---------------|
| `sealed` | Invariants + tests proven; freeze | Need unfreeze note in defect + re-pass module gate + cascade |
| `hardening` | Has suite but residual risk | Prefer finish sealing over new open surface |
| `open` | Active design / still measure-only | Free to evolve under Cell rules |

### Current sealed (R151–R153)

| ID | Name | Sealed by |
|----|------|-----------|
| `M01_event_wait` | Event wait / runtime observation | R153 |
| `M03_tool_batch_deo` | Tool batch DEO serial sibling abort | R151 |
| `M04_final_request_context` | Final request current_user_final | R152 |

### Hardening next

| ID | Name | Residual risk |
|----|------|---------------|
| `M02_physical_attempt_authority` | Grant/close/fence | Premature close under cancel |
| `M05_stage_lease_heartbeat` | Lease renew | Fence without renew |
| `M06_director_multi_task` | Multi-task fanout | TASK-N authority mid-stage |
| `M07_factory_stage_chain` | PM→CE→Director | Stage handoff |
| `M08_run_ledger_tool_lifecycle` | Ledger missing vs failed | tool_lifecycle_failed attribution |
| `M09_four_pillars_gates` | Bench measure | open until L1-01 four pillars green |

---

## 4. Freeze rules (mandatory for agents)

1. **Do not modify sealed `owner_paths` without**:
   - Opening a defect that names the module_id
   - Re-running `--module <id>` green
   - Re-running `--mode sealed` green
2. **Do not claim four pillars pass** unless `--mode cascade` is green in the same session evidence pack.
3. **Bench is measure-only** for target projects; never repair workspace from `bench_gates.py`.
4. **One residual → one module**. If residual spans modules, fix the **lowest cascade dependency** first (observation before authority before tools before context).
5. **Sealing promotion**: when a hardening module has N-batch no new root cause for its invariants, flip `status=sealed` and set `sealed_by_defect`.

---

## 5. Registry SSoT

- Code: `polaris.kernelone.platform_modules.registry`
- Runner: `src/backend/scripts/platform_modules/run_module_gates.py`
- Tests: `polaris.kernelone.platform_modules.tests.test_registry`

Do not hand-copy module lists into CLAUDE.md. Query the registry.

---

## 6. Relationship to Cell architecture

This solidification layer **does not** replace Cell public surfaces. It freezes **cross-cutting runtime contracts** that factory_bench repeatedly rediscovered:

- Observation (bench client)
- Authority (factory physical attempt)
- Effects (roles kernel tool batch / DEO)
- Context (final request audit)
- Chain (factory stages)
- Ledger (control plane)
- Measure (bench gates)

Cell rules in `src/backend/AGENTS.md` still apply for all production code placement.

---

## 7. Expert consensus (R153 retrospective)

**Observation expert**: keepalive drop is expected under long LLM/tool load; reconnect-until-deadline is the only safe observation policy.

**Authority expert**: `factory_physical_attempt_authority_closed` after cancel is correct; the bug was **cancelling on observation failure**, not the close itself.

**Effects expert**: DEO serial sibling abort (R151) must stay sealed; partial batches poison later tasks.

**Context expert**: trailing system pins break `current_user_final` (R152); pin placement is sealed policy.

**Bench expert**: outer 5400s budget is wall-clock only; never label a 13-minute keepalive cancel as “timeout after 5400s” without kind discrimination (R153 cancel reason fix).

**Product expert**: four pillars + N-batch remain the only L1 advancement criteria; module gates are **acceleration**, not a replacement for true-run.

---

## 8. Immediate operating procedure

After any platform fix:

1. Identify module_id (or add `open` record).
2. `run_module_gates.py --module <id>`
3. If sealed set touched: `--mode sealed`
4. If multi-module: `--mode cascade`
5. Only then isolated L1-01 `--mode bench` or `run_factory_bench.py`
6. Update `defect_latest.json` with module_id + gate evidence

---

## 9. Unattended automation (machine residual → next step)

For **无人值守** loops, do **not** hand-pick modules from free text. Use:

| Surface | Path |
|---------|------|
| Attribution API | `polaris.kernelone.platform_modules.residual_attribution` |
| Completion/model terminal authority | `polaris.cells.orchestration.workflow_runtime.public` |
| CLI | `scripts/platform_modules/attribute_factory_audit.py` |
| Embedded in audits | `factory_audits.json` → non-terminal `platform_residual_attribution` |
| Doctrine | `PLATFORM_UNATTENDED_AUTOMATION.md` |

Hard rules (same as freeze rule 4): one residual → one `module_id`; module gate → cascade → one isolated L1-01; four pillars + N-batch before L1-02.
