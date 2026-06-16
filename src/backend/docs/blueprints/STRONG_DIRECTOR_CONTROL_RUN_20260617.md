# Strong-Director Control-Run Binding Blueprint

**Date**: 2026-06-17
**Author**: Polaris hardening campaign
**Status**: DRAFT — pending user sign-off
**Goal**: enable a controlled **strong-Director** measurement arm so every residual
weak-Director failure on L2-L8 "runnable" projects is **provably attributable to
model ceiling, not platform/harness defect**.

> Standing goal reminder (2026-06-17):
> *Polaris is hardened so EVERY weak-model failure on L2-L8 "runnable" projects is
> attributable to a MODEL ceiling, not a PLATFORM/harness defect.*
> The confirmation half is bench measurement: N>=3 consecutive L2 batches with 6/6
> runnable, plus a strong-Director control run (deepseek-v4-pro) on the same
> projects to distinguish platform from ceiling.

---

## 0. Premise: infra-ready, no cloud-authorization blocker

`/home/dains/.polaris/config/llm/llm_config.json` already contains
`anthropic_compat-1779808433822` (`DeepSeekV4-Pro`, `deepseek-v4-pro`,
`base_url=https://api.deepseek.com/anthropic`, `api_key` present) **authenticated
and in production use** by `architect` / `cfo` / `hr` / `scout` plus the PM
secondary binding. It is therefore ready to absorb the Director role with no new
cloud work.

- File: `/home/dains/.polaris/config/llm/llm_config.json` lines 4-12 (provider
  entry), 159-176 (architect/cfo/hr/scout already bound to it).

The `KERNELONE_LLM_CONFIG` env-override is **NOT** consulted by
`llm_config_path()`:

```python
# src/backend/polaris/kernelone/llm/config_store.py:760-762
def llm_config_path(workspace: str, cache_root: str) -> str:
    del workspace, cache_root  # Kept for API compatibility; LLM config is global app data.
    return resolve_global_path("config/llm/llm_config.json")
```

`codegraph_search` for `KERNELONE_LLM_CONFIG` returns **0 hits** in the loader
hot path — only a test import in
`src/backend/polaris/kernelone/llm/tests/test_config_store_normalize.py:8`.
**Conclusion: there is no env-only path. Non-destructive switching MUST go
through file snapshot + atomic rewrite.** The earlier
`strong-director-attribution-control.md` claim "via `KERNELONE_LLM_CONFIG` or
env override" was aspirational; we file-snapshot instead.

---

## 1. Binding sites to change

### 1.1 SSoT (must change)
File: `/home/dains/.polaris/config/llm/llm_config.json`

#### Current director block (lines 126-154)
```json
"director": {
  "provider_id": "openai_compat-1781036723563",
  "model": "qwen3.6-27b-int4",
  "provider_pool": [
    "openai_compat-1781036723563",
    "openai_compat-1781325474837",
    "openai_compat-1781448696751",
    "openai_compat-1781448928833"
  ],
  "concurrency": 4,
  "bindings": [
    {"provider_id": "openai_compat-1781036723563", "model": "qwen3.6-27b-int4"},
    {"provider_id": "openai_compat-1781448928833", "model": "qwen3.6-27b-gpu1"},
    {"provider_id": "openai_compat-1781448696751", "model": "qwen3.6-27b-gpu0"},
    {"provider_id": "openai_compat-1781325474837", "model": "qwen3.6-27b-int4"}
  ]
}
```

#### Target director block (strong-Director)
```json
"director": {
  "provider_id": "anthropic_compat-1779808433822",
  "model": "deepseek-v4-pro",
  "concurrency": 1,
  "bindings": [
    {"provider_id": "anthropic_compat-1779808433822", "model": "deepseek-v4-pro"}
  ]
}
```

#### Fields to change (3)
| Field              | From                              | To                                            |
| ------------------ | --------------------------------- | --------------------------------------------- |
| `provider_id`      | `openai_compat-1781036723563`     | `anthropic_compat-1779808433822`              |
| `model`            | `qwen3.6-27b-int4`                | `deepseek-v4-pro`                             |
| `provider_pool`    | (4-backend pool)                  | **REMOVE** (replaced by single binding)       |
| `concurrency`      | `4`                               | `1` (cloud, serial)                           |
| `bindings`         | 4 qwen backends                   | 1 entry pointing to deepseek-v4-pro           |

#### Fields to LEAVE UNCHANGED (isolation)
| Field           | Value kept                                                   | Reason                                                |
| --------------- | ------------------------------------------------------------ | ----------------------------------------------------- |
| `roles.pm`      | `provider_id=minimax-1781012971065` `model=MiniMax-M3` (lines 106-120) | isolates variable: only Director changes              |
| `roles.chief_engineer` | `minimax-1781012971065` / `MiniMax-M3` (lines 121-125) | same                                                  |
| `roles.qa`      | `minimax-1781012971065` / `MiniMax-M3` (lines 155-158)       | same                                                  |
| `roles.architect` | `anthropic_compat-1779808433822` / `deepseek-v4-pro` (lines 159-163) | already on strong model (do not touch)                |
| `roles.cfo/hr/scout` | `anthropic_compat-1779808433822` / `deepseek-v4-pro` (lines 164-176) | already on strong model (do not touch)                |
| `policies.required_ready_roles` | `["pm","director","qa"]` (lines 210-214) | Director must still pass readiness gate              |
| `policies.test_required_suites` | `["connectivity","response","qualification"]` (lines 183-187) | unchanged                                             |
| `policies.role_requirements.director` | `{min_confidence:0, error_message:"", requires_thinking:false}` (lines 199-203) | unchanged                                             |
| `visual_layout` (lines 216-321)   | unchanged — purely cosmetic UI                               |
| `visual_node_states` (lines 322-622) | unchanged — purely cosmetic UI                           |

### 1.2 Binding sites audit (other than SSoT)

`codegraph_explore` of `provider_id` / `KERNELONE_LLM_CONFIG` / `resolve_provider_for_role` returned the following hot-path readers, all of which read **the SSoT** (no hard-coded Director strings):

| Reader | File:line | Behavior on edit |
| ------ | --------- | ---------------- |
| `load_llm_config` (the SSoT loader) | `src/backend/polaris/kernelone/llm/config_store.py:882-892` | Re-reads `llm_config.json` per call. No cache to bust. |
| `normalize_llm_config` | `src/backend/polaris/kernelone/llm/config_store.py:980-1079` | Re-derives `bindings[0]` → `provider_id` + `model` for the role. Ensures consistency. |
| `_normalize_role_config` | `src/backend/polaris/kernelone/llm/config_store.py:824-879` | If `bindings` empty, falls back to `provider_id`/`model` at role level. |
| `get_config` (LlmControlPlaneService) | `src/backend/polaris/cells/llm/control_plane/public/service.py:182-205` | Returns `provider_id` + `model` for a role. Reads SSoT. |
| Provider registry | `src/backend/polaris/infrastructure/llm/providers/provider_registry.py:53-64` | `anthropic_compat` is already registered at `ProviderManager._register_default_providers` (line 64). No registration needed. |
| `AnthropicCompatProvider` | `src/backend/polaris/infrastructure/llm/providers/anthropic_compat_provider.py:430-475` | `invoke()` reads `config.get("max_tokens")`, `config.get("temperature")`, `config.get("api_key")` from the **per-role provider entry** in SSoT (lines 4-12). All three already present for `anthropic_compat-1779808433822`. |

**Conclusion**: the swap is a single-file change. No environment variables, no
provider-registry hot-paths, no per-call timeout constants, no role-profile YAML,
no `_director_backup` entry, no `provider_pool` fallbacks elsewhere need to be
touched.

### 1.3 What we deliberately DO NOT change
- `_director_backup` role (line 177-180) — fallback only used if primary fails;
  leaving it as `gemma-4-12b-it-Q8_0` is correct (a different weak model would
  not help the attribution either way; if a strong-Director run hits a fatal
  error we want to know, not silently fall back to another weak model).
- `policies.role_requirements` for `director` — `requires_thinking=false` and
  `min_confidence=0` mirror the current setting. Strong model can opt-in
  later (e.g. `requires_thinking=true` to test whether the L6 ceiling is
  reasoning-bandwidth, not API-budget) but that is a second experiment, not
  the attribution run.
- `concurrency` is being lowered to 1 because the **client-side pool/draining
  machinery** documented in `director-multibackend-routing.md` was tuned for
  4-backend local pools and assumes per-backend saturation behaviors (the
  continuous-drain fix `F13`). Running a single cloud Director with
  `concurrency=1` is the cleanest, lowest-risk config for attribution.

---

## 2. Backup / rollback procedure (exact)

### 2.1 Pre-swap snapshot (atomic, fail-closed)

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
sudo -E cp -a /home/dains/.polaris/config/llm/llm_config.json \
           /home/dains/.polaris/config/llm/llm_config.json.pre-strongdirector.${TS}
ls -la /home/dains/.polaris/config/llm/llm_config.json.pre-strongdirector.${TS}
# verify the snapshot byte-equals the source:
diff -q /home/dains/.polaris/config/llm/llm_config.json \
        /home/dains/.polaris/config/llm/llm_config.json.pre-strongdirector.${TS}
# expected: no output (= files identical)
```

The `cp -a` is used (not `mv`) so a partial-write never destroys the source.
The pre-existing `llm_config.json.backup.<unix_ts>` files in the same directory
are the **internal backup created by `save_llm_config` at
`config_store.py:957`**; we ignore them and create an explicit
`.pre-strongdirector.${TS}` so the rollback is unambiguous and survives
intervening `save_llm_config` calls.

### 2.2 Atomic swap (write through temp + rename)

The config store's own write path is `write_json_atomic(path, ...)` at
`config_store.py:975` — but that requires the **whole `save_llm_config` flow**
(validation, schema migration, masking). For a one-shot binding swap we can
either (a) call `save_llm_config(workspace, cache_root, new_payload)` via
Python or (b) write a tiny patcher script. **Recommended: option (a)**, since
it is the same path the UI uses:

```bash
python3 - <<'PY'
import json, sys
from pathlib import Path
from polaris.kernelone.llm.config_store import load_llm_config, save_llm_config

WORKSPACE = "."
CACHE_ROOT = str(Path("~/.polaris/cache").expanduser())

current = load_llm_config(WORKSPACE, CACHE_ROOT)
director = current["roles"]["director"]

# Strong-Director patch
new_director = {
    "provider_id": "anthropic_compat-1779808433822",
    "model": "deepseek-v4-pro",
    "concurrency": 1,
    "bindings": [
        {"provider_id": "anthropic_compat-1779808433822", "model": "deepseek-v4-pro"}
    ],
}
# Defensive: ensure PM/CE/QA are still on MiniMax-M3
for role in ("pm", "chief_engineer", "qa"):
    assert current["roles"][role]["provider_id"] == "minimax-1781012971065", \
        f"isolation guard failed: {role} drifted from MiniMax-M3"

current["roles"]["director"] = new_director
result = save_llm_config(WORKSPACE, CACHE_ROOT, current)

print("director rebind OK; primary =", result["roles"]["director"]["provider_id"])
PY
```

Validation afterwards:
```bash
python3 -c "
import json
c = json.load(open('/home/dains/.polaris/config/llm/llm_config.json', encoding='utf-8'))
d = c['roles']['director']
assert d['provider_id'] == 'anthropic_compat-1779808433822', d
assert d['model'] == 'deepseek-v4-pro', d
assert d.get('concurrency') == 1, d
assert len(d.get('bindings', [])) == 1, d
assert c['roles']['pm']['provider_id'] == 'minimax-1781012971065', c['roles']['pm']
assert c['roles']['chief_engineer']['provider_id'] == 'minimax-1781012971065', c['roles']['chief_engineer']
assert c['roles']['qa']['provider_id'] == 'minimax-1781012971065', c['roles']['qa']
print('binding verification: OK')
"
```

### 2.3 Rollback (single command, single file)

```bash
TS=20260617T013000Z   # whatever timestamp was used in 2.1
sudo -E cp -a /home/dains/.polaris/config/llm/llm_config.json.pre-strongdirector.${TS} \
            /home/dains/.polaris/config/llm/llm_config.json
diff -q /home/dains/.polaris/config/llm/llm_config.json \
        /home/dains/.polaris/config/llm/llm_config.json.pre-strongdirector.${TS}
# expected: no output (= restored exactly)
```

The `_create_config_backup(path, max_backups=5)` call at `config_store.py:957`
will already have created an internal `llm_config.json.backup.<unix_ts>` for the
**swap moment** — that is the alternate rollback path. Use whichever is more
convenient; both restore byte-for-byte the same JSON.

### 2.4 Hard rollback trigger conditions (auto-rollback if any fire)
1. `load_llm_config` raises after the swap → SSoT is corrupt → restore.
2. `validate_llm_config` returns `valid=False` → do not start bench → restore.
3. The first L2 project's PM phase fails with a 401/403/429 from the new
   provider (api_key wrong, account suspended) → stop bench, restore.
4. Any L2 project's Director `invoke()` returns a 5xx that is reproducible on
   3 consecutive turns → stop bench, restore (this is a strong-Director
   runtime failure, not an attribution data point).

---

## 3. Bench command (exact)

### 3.1 L2 control run

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
WS=/tmp/l2-control-strongdirector.${TS}
mkdir -p "${WS}"
python3 /home/dains/Documents/polaris/src/backend/scripts/factory_bench/run_factory_bench.py \
  --levels 2 \
  --work-dir "${WS}" \
  --timeout 1200 \
  --max-failed 10 \
  --director-dispatch-driver task-market \
  --director-workflow-execution-mode parallel
```

Differences from the L2 floor batch 1 baseline (`birc6egs8`):
| Arg                | Floor (weak)           | Control (strong)        | Reason                                                  |
| ------------------ | ---------------------- | ----------------------- | ------------------------------------------------------- |
| `--timeout`        | `900`                  | `1200`                  | deepseek-v4-pro is a **cloud** round-trip; allow buffer |
| `--work-dir`       | `/tmp/l2-floor-batch1` | `/tmp/l2-control-strongdirector.${TS}` | keep control-run logs separate from floor logs         |
| `--max-failed`     | `10`                   | `10`                    | same early-stop; we want every L2 project's data point  |
| `--director-dispatch-driver` | `task-market` | `task-market`           | same driver so harness is identical                     |
| `--director-workflow-execution-mode` | (default `serial`) | `parallel`         | strong model should saturate parallelism to expose ceiling |

Project set: **L2-07, L2-08, L2-09, L2-10, L2-11, L2-12** (all 6 L2 projects; same
set as `birc6egs8` baseline). The 6 IDs are discoverable from
`/home/dains/Documents/polaris/src/backend/scripts/factory_bench/projects_v1.json`
under `projects[].id` where `level==2`:
L2-07 / L2-08 / L2-09 / L2-10 / L2-11 / L2-12.

### 3.2 L6-32 follow-up (single-project deep probe)

After the L2 run is clean, repeat for the L6 frontier project most likely to
exhibit the ceiling:

```bash
WS=/tmp/l6-control-strongdirector.${TS}
mkdir -p "${WS}"
python3 /home/dains/Documents/polaris/src/backend/scripts/factory_bench/run_factory_bench.py \
  --project-ids L6-32 \
  --work-dir "${WS}" \
  --timeout 1800 \
  --max-failed 1 \
  --director-dispatch-driver task-market \
  --director-workflow-execution-mode parallel
```

L6-32 is the held-out L6 frontier (微服务架构迷你电商系统). `--max-failed 1`
because L6 runs are slow and we want one clean data point, not a batch.

### 3.3 Pre/post comparison rubric (the actual attribution math)

For each (project, step) the run emits an audit record in
`factory_audits.json` (written at
`src/backend/scripts/factory_bench/run_factory_bench.py:541-550`). For each
project we capture:

1. **Chain exit code + duration** (`chain.exit_code`, `chain.duration_s`).
2. **Code-file count** (`code_file_count`) — real artifacts on disk.
3. **Director** (`chain_results.director`) — model id used.
4. **QA verdict / QA pass** (`has_qa_verdict`, `chain_results.qa_ran`,
   `chain_results.qa_passed`).
5. **All-checks-passed** flag (`record["all_checks_passed"]`).
6. **Per-step receipts** (truthlog) for any failing step — under
   `runtime_dir` / truthlog JSONL.

The rubric for a given project ID is then:

| Weak-Director result                              | Strong-Director result                    | Attribution                     |
| ------------------------------------------------- | ----------------------------------------- | ------------------------------- |
| `all_checks_passed=False`, step X fails error Y  | `all_checks_passed=True`                 | `model_ceiling`                 |
| `all_checks_passed=False`, step X fails error Y  | `all_checks_passed=False`, same step X    | `platform_fixable` (or `working_as_intended` if a guard) |
| `all_checks_passed=False`, step X fails error Y  | `all_checks_passed=False`, different step | mixed; per-step audit required  |
| Weak `all_checks_passed=True`                     | Strong `all_checks_passed=True`          | not interesting (both pass)     |
| Both time out                                     | inconclusive — extend `--timeout`         | `working_as_intended` retry     |

`working_as_intended` and `post_failure_noise` only apply after the primary
attribution is settled. `regression` is the highest-priority signal: if the
strong-Director run on a project that the weak-Director run passed **now
fails**, the recent change is regressing both harnesses and must be rolled
back before any attribution conclusion.

---

## 4. Known risks and mitigations

| # | Risk                                                              | Source of risk / source of truth                                                  | Mitigation                                                                                                                                                  |
| - | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| R1 | **Backends warming up** — the first 1-3 Director calls may be cold | `AnthropicCompatProvider.invoke()` at `infrastructure/llm/providers/anthropic_compat_provider.py:430` has `retries` from config (none set for deepseek → `int(config.get("retries") or 0) = 0` at line 433) | Use a warmup ping **before** starting the bench: `curl -sS -X POST https://api.deepseek.com/anthropic/v1/messages -H "x-api-key: $DEEPSEEK_KEY" -H "anthropic-version: 2023-06-01" -H "content-type: application/json" -d '{"model":"deepseek-v4-pro","max_tokens":16,"messages":[{"role":"user","content":"ping"}]}'`. If it returns 200, the path is warm. Repeat after any long pause. |
| R2 | **Request-rate limits** (deepseek-v4-pro TPM/RPM)                 | No Polaris-side rate limiter for outbound cloud APIs (`AnthropicCompatProvider` does not call `RateLimitMiddleware`; that is HTTP-level only at `polaris/delivery/http/middleware/rate_limit.py:231`); cloud-side limits are opaque | `concurrency=1` already serializes requests. Add a **client-side sleep** between successive Director turns in `execute_method.py:1354` (cross-turn single-write driver): `time.sleep(0.2)` per turn. If a turn returns 429, the driver must back off **exponentially** (current ADR-0071 floor is `>=1` step per turn; backoff is a non-binding observation, but we must record it). |
| R3 | **Context-window policy**                                          | `anthropic_compat-1779808433822` declares `max_context_tokens=1000000` and `max_output_tokens=384000` (lines 7-8 of SSoT). Director prompts routinely hit 32K-200K with tool receipts. | Verify **both** the model and the gateway agree on the budget. The context gateway reads `budget_plan.model_context_window` per the F5 memory — if Director is now deepseek-v4-pro, the plan MUST use the deepseek window (1M), not the qwen window (32K). If the gateway is still computing against the old window, every long-context Director turn will hit `BudgetExceededError` (already routed to compress, see #46). The fix is **runtime**: when Director binding changes, the budget plan source-of-truth (`F5`/retry-orchestrator) must re-read the new provider's `max_context_tokens`. Verify empirically on a single warmup project. |
| R4 | **Tool-call format differences** — anthropic_compat vs openai_compat | `AnthropicCompatProvider._convert_tools_to_anthropic` (`anthropic_compat_provider.py:180`) and `_convert_tool_choice_to_anthropic` (line 230) emit Anthropic-shaped tool definitions; deepseek-v4-pro is reached via the **Anthropic-compatible** endpoint and accepts native tool_use, but may have model-specific quirks (e.g. `tool_choice` omission, see `test_invoke_omits_tool_choice_for_deepseek_anthropic_endpoint` at `tests/integration/llm/providers/test_anthropic_compat_provider.py:193`). | Two-fold: (a) the **only** model on this endpoint is `deepseek-v4-pro`, and the provider **already** knows to omit `tool_choice` for it — no action required; (b) on first tool_use failure, capture the raw request/response, verify `_extract_output` (line 471) yields non-empty `content` / `tool_use` blocks, then run a `replay_steps.py --steps 1` replay per `velocity-replay-harness.md` to confirm. |
| R5 | **API-key leakage in log**                                         | SSoT has `api_key` in plaintext (line 10). `save_llm_config` masks it via `_restore_masked_sensitive_values` (`config_store.py:944`) but bench logs may still print role-config dumps. | Confirm `KERNELONE_LOG_SENSITIVE=1` is set (default) and grep the bench audit record for `sk-` substrings before publishing. If the audit JSON contains the key, the leak path is in `LlmControlPlaneService.get_config` returning `provider_cfg` (line 203). The key is **already in the file** — no new exposure, but a known design issue. |
| R6 | **Mid-run provider outage**                                       | deepseek.com is a third-party cloud; SLA is opaque. Past qwen backend outages (F15) hung dispatch when workers had no per-call timeout. | Run with `--max-failed 1` for any single project (we already do for L6-32) and `--max-failed 3` for L2. Add a **wall-clock kill** at the bench-process level: launch via `timeout 7200 python3 ...` to bound the worst case. If a run is killed mid-project, restore the binding (Section 2.3) before retrying. |
| R7 | **Schema migration** during swap                                   | `save_llm_config` migrates `schema_version` to 2 (`config_store.py:916-929`). The current file is already schema_version=2 (line 2), so no migration should fire, but verify by reading the post-swap file's `schema_version`. | The diff is identical before/after; the only change is `roles.director`. If `schema_version` is bumped, the audit log will record `config_migration_started` — that is unexpected. If it fires, abort. |
| R8 | **In-flight batch (`birc6egs8`) interference**                     | The L2 floor batch 1 is **running** in the background. If it produces audit records while we swap, the in-flight `chain_results` may capture the **new** Director binding mid-run. | **Do not swap while `birc6egs8` is running.** Wait for it to complete (or terminate it gracefully). The control-run is a separate bench process reading a separate `--work-dir`; the only shared state is the SSoT. If we must run concurrently, the SSoT swap is global — both batches would observe the new binding. Decision: **serialize the two batches**. |

---

## 5. Sign-off requirements

### 5.1 Memory / docs that need user approval before execution

The following existing records (from
`/home/dains/.claude/projects/-home-dains-Documents-polaris/memory/`) document
this plan and must be referenced for user sign-off:

1. **`strong-director-attribution-control.md`** — already says "non-destructive
   switching via `KERNELONE_LLM_CONFIG` or env override". Section 0 of this
   blueprint **contradicts** that claim: `KERNELONE_LLM_CONFIG` is not wired.
   The user must explicitly approve **updating the memory note** to reflect
   "file-snapshot + atomic rewrite" before we run.
2. **`reliability-hardening-campaign.md`** — defines the standing order
   "platform-blame-hunt FIRST, control-run SECOND". The control run is
   therefore gated on the platform-blame-hunt queue (H1, P1 stream
   collection-limit, P1 receipt-eviction, P2 successful_files steer, F6
   telemetry collision) being drained to **zero platform-blame items** OR the
   user explicitly accepting residual platform risk for the attribution round.
3. **`l4-multifile-megabatch-wall.md`** (task #59) and
   **`repair-mode-crossfile-coherence.md`** (task #54) — both are still
   `in_progress` and would distort the strong-Director run if unfixed (L4-L6
   in particular). User must approve running the control while these are open,
   or first land them.
4. **`director-multibackend-routing.md`** — the multi-backend plumbing is
   **bypassed** in the control run (single binding, `concurrency=1`). User
   must approve the deliberate degradation of the runtime load-balancer for
   the duration of the control run; the memory note's continuous-drain
   protection (F13) is still relevant for the floor baseline.

### 5.2 Sign-off deliverables

Before this blueprint can be executed, user must approve:
- [ ] Update `strong-director-attribution-control.md` to remove the
      `KERNELONE_LLM_CONFIG` claim and replace with the file-snapshot recipe.
- [ ] Approve running the control **after** the in-flight `birc6egs8` finishes
      (or, alternatively, approve killing it and re-running from scratch).
- [ ] Approve running the control **with tasks #54 and #59 still open**, OR
      specify which must be closed first.
- [ ] Approve the `--timeout 1200` and `concurrency=1` choices (and the cost
      implication: deepseek-v4-pro is a **metered** cloud provider; cost
      class `METERED` per `ProviderInfo.cost_class` in
      `kernelone/llm/providers/base_provider.py:29`).
- [ ] Approve the per-project `--max-failed 1` choice for L6-32.

### 5.3 Attribution outcomes this run CAN produce

Per the standing attribution lens, this control run can produce **4 of the 5**
outcomes:

| Outcome               | Can the control run produce it?                                                                                  |
| --------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `platform_fixable`    | **YES** — if strong-Director passes a project where weak-Director failed on a step that the harness gates on.    |
| `model_ceiling`       | **YES** — if weak-Director fails and strong-Director **also** fails on a **different** step with a different error pattern (signals the project is beyond current strong model). |
| `working_as_intended` | **YES** — if a guard (e.g. `_target_exists` case-insensitive) trips identically in both runs, the guard is correct. |
| `post_failure_noise`  | **YES** — observed during the run; cannot drive the conclusion, only annotate.                                   |
| `regression`          | **YES** — if a project the weak-Director passed now fails under strong-Director, a recent change broke both.     |

It **cannot** directly produce evidence of "deepseek-v4-pro is itself
sufficient" — that is a stronger claim requiring a **second** strong-Director
run on a wider, held-out set; this blueprint is the **first** step.

---

## 6. Execution checklist (when approved)

```
[ ]  1. birc6egs8 finished (or terminated) — confirm via `tail -n 20 <ws>/factory_audits.json`
[ ]  2. snapshot:  cp -a llm_config.json llm_config.json.pre-strongdirector.${TS}
[ ]  3. deepseek warmup:  curl -sS ... (see R1)
[ ]  4. patch via Python (see 2.2)
[ ]  5. validate (see 2.2 inline assertions)
[ ]  6. launch L2 control run (see 3.1) — log path = ${WS}/factory_audits.json
[ ]  7. monitor:  watch -n 30 'tail -n 5 ${WS}/<pid>.chain.log'
[ ]  8. on completion OR hard-fail (R1-R8), diff weak-Director vs strong-Director factory_audits.json
[ ]  9. on attribution-done, rollback:  cp -a llm_config.json.pre-strongdirector.${TS} llm_config.json
[ ] 10. restore verification:  diff -q llm_config.json llm_config.json.pre-strongdirector.${TS}  # empty=ok
[ ] 11. (optional) L6-32 follow-up (see 3.2) if L2 run is clean
[ ] 12. update memory note `strong-director-attribution-control.md` with measured outcomes
```

---

## 7. File:line citation summary (load-bearing references)

- SSoT director block: `src/backend/polaris/.polaris/config/llm/llm_config.json:126-154` (also shown inline above)
- `llm_config_path` (no env override): `src/backend/polaris/kernelone/llm/config_store.py:760-762`
- `load_llm_config`: `src/backend/polaris/kernelone/llm/config_store.py:882-892`
- `save_llm_config` (with backup + atomic write): `src/backend/polaris/kernelone/llm/config_store.py:895-977`
- `_create_config_backup` (the internal `.backup.<ts>` mechanism): `src/backend/polaris/kernelone/llm/config_store.py:957`
- `_normalize_role_config` (binding-derived provider_id): `src/backend/polaris/kernelone/llm/config_store.py:824-879`
- `AnthropicCompatProvider.invoke`: `src/backend/polaris/infrastructure/llm/providers/anthropic_compat_provider.py:430-475`
- `AnthropicCompatProvider._resolve_max_tokens` (uses config's max_output_tokens, NOT model-specific): `src/backend/polaris/infrastructure/llm/providers/anthropic_compat_provider.py:167`
- ProviderRegistry: `src/backend/polaris/infrastructure/llm/providers/provider_registry.py:53-64`
- Bench CLI: `src/backend/scripts/factory_bench/run_factory_bench.py:431-563`
- Bench projects: `src/backend/scripts/factory_bench/projects_v1.json:projects[]` (6 L2 entries, 6 L6 entries)
- Budget gateway (must read new provider's window per F5): `src/backend/polaris/cells/roles/kernel/internal/context_gateway/gateway.py`

---

**End of blueprint. Awaiting user sign-off per Section 5.2 before any
binding-swap execution.**
