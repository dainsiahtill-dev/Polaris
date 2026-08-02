# Platform Unattended Automation

**Purpose**: Make “无人值守自动化项目开发” an executable platform contract—not an infinite Agent rebench loop.

**Slogan**: 一残差一模块 · 先 effect 后语义 · cascade 后才 bench · 四支柱+N-batch 才推进

---

## 1. Five hard rules

1. Residual → **one** `module_id` (ladder: 观测 → 授权 → 工具 → 上下文 → 链 → 账本; M10 only after effect green).
2. Only `--module Mx`, then cascade.
3. Cascade green before isolated L1-01.
4. Four pillars + N-batch only L1 advance; no L1-02 until L1-01 pillars + streak.
5. Hardening modules seal after stable N-batch; stop thrashing sealed surfaces.

---

## 2. Machine APIs (SSoT for supervisors)

| API | Module | Role |
|-----|--------|------|
| `attribute_residual` / `attribute_factory_audit_record` | `polaris.kernelone.platform_modules.residual_attribution` | residual → one module_id |
| `classify_delivery_status` | same | `DELIVERY_VERIFIED_*` vs delivery/chain fail classes |
| `plan_unattended_step` | `polaris.kernelone.platform_modules.unattended_supervisor` | next phase: module → cascade → L1-01 / stop |
| CLI | `scripts/platform_modules/attribute_factory_audit.py` | JSON pack from `factory_audits.json` |
| Gates | `scripts/platform_modules/run_module_gates.py` | module / cascade / bench |

External Claude/Codex supervisors **must** consume these outputs. They must **not** write success into Run Ledger, ContextOS, or Bench.

### Example

```bash
python src/backend/scripts/platform_modules/attribute_factory_audit.py \
  --audits /tmp/factory-bench-.../factory_audits.json \
  --json-out /tmp/attribution.json \
  --no-module-gate-ok --no-cascade-ok
```

Follow `next_step.commands` only. After module green, re-attribute with `--module-gate-ok`; after cascade green, `--cascade-ok` unlocks one L1-01 command.

### Embedded in factory_audits.json

`run_factory_bench.py` writes (non-authoritative enrichment, does not change pass/fail):

- `platform_residual_attribution.primary.primary_module_id`
- `platform_residual_attribution.primary.delivery_status`
- `platform_residual_attribution.primary.gate_commands`
- `unattended_next_step.phase` / `commands` / `allow_l1_01_bench`

External supervisors **must** consume these fields or the CLI pack; they must **not** invent module_id from chat.

M07 cascade gate includes residual attribution unit tests so cascade red fails closed if attribution contracts break.

---

## 3. Delivery status classes

| Class | Meaning |
|-------|---------|
| `DELIVERY_AND_CHAIN_VERIFIED` | real_run + chain both green |
| `DELIVERY_VERIFIED_CHAIN_CONTROL_PLANE_FAIL` | real_run green; control-plane/boundary/runtime killed chain (often M06) |
| `DELIVERY_VERIFIED_CHAIN_INCOMPLETE` | real_run green; chain incomplete for other reasons |
| `CHAIN_OK_DELIVERY_FAILED` | chain ok but real_run failed |
| `DELIVERY_FAILED` | real_run failed |
| `STATUS_UNKNOWN` | insufficient flags |

Unattended policy: schedule control-plane fixes on `DELIVERY_VERIFIED_CHAIN_CONTROL_PLANE_FAIL` before expanding M10.

---

## 4. M06 authority contract (R181)

- Reconcile on-disk artifacts out of stale `downstream_pending`.
- `completed_verified` boundary may supersede non-completed TaskRuntime rows after settle.
- Multi-pass post-settle recovery re-evaluates director stage authority.
- Do **not** invent success without package+src delivery surface.

---

## 5. M10 invent policy

- Relative module invent stubs set `authoritative=false`, `requires_revalidation=true`, `product_delivery_authority=false`.
- Declared product verify/smoke still needs real generation or incomplete materialization—not invent-as-success.
- Prefer coverage report + archetype expansion over one-off source_tools.

---

## 6. Stop conditions

- `is_model_ceiling=true` → stop platform rule expansion.
- ≥3 residual class shifts under M10 → prevention, not new regex.
- Cascade red → ban L1-01 bench.
- N-batch incomplete → ban L1-02.

---

## 7. Related docs

- `PLATFORM_MODULE_SOLIDIFICATION.md` — freeze pyramid
- `src/backend/AGENTS.md` — Cell / repair_kernel rules
- Registry: `polaris.kernelone.platform_modules.registry`
