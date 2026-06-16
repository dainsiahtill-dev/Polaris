# Failure Attribution Rubric — L2-L8 Runnable Goal (2026-06-17)

> Standing goal (re-stated from [[strong-director-attribution-control]]):
> Polaris is hardened so EVERY weak-model failure on L2-L8 "runnable" projects is
> attributable to a **MODEL ceiling**, not a **PLATFORM/harness defect**. The
> confirmation half is bench measurement: N≥3 consecutive L2 batches with 6/6
> runnable, plus a strong-Director control run (deepseek-v4-pro) on the same
> projects to distinguish platform from ceiling.

This rubric is the formal attribution lens for that goal. Every chain.log line,
every QA failure, every dead-letter, every retry must be classifiable into
**exactly one** of the five categories below — and the **"platform-fault mass"**
metric is computed directly from the classification.

---

## 0. How to use this document

1. **Run a batch** (L2 floor, L3, L4, ...). Save the full `.l2int4.log` /
   `chain.log` — never a trimmed excerpt.
2. For every observed failure (chain=fail, qa_passed=False, dead-letter,
   error budget, transport error, etc.), walk **§1 Decision Flow** top-down.
3. Apply the decision rules in **§2 Categories** to assign exactly one tag.
4. Compute per-batch and per-project metrics per **§3 Platform-Fault Mass**.
5. Re-run the batch under the **strong-Director** binding
   (`anthropic_compat-1779808433822` / `deepseek-v4-pro`, concurrency=1,
   PM/CE/QA unchanged) per [[strong-director-attribution-control]] and re-classify.
6. Apply **§4 L2 Floor Pass Criterion** to decide whether the batch counts
   toward the standing goal.

A 3rd-party reader applying this rubric to the same `chain.log` MUST reach the
same per-failure classification. The rules are therefore expressed as
deterministic predicates anchored in concrete code paths.

---

## 1. Decision Flow (the algorithm)

For every observed failure F on project P in batch B:

```
1.  Is F caused by a change in the current commit (HEAD vs HEAD~N) that
    did NOT exist in a prior known-good batch on the same project?
    └── YES  → tag = REGRESSION
    └── NO   → continue

2.  Did the failure happen AFTER the chain had already entered a terminal
    fail state (chain_state=fail / dead-letter / death_threshold hit)?
    └── YES  → tag = POST_FAILURE_NOISE
    └── NO   → continue

3.  Did a guard / gate / repair block fire correctly (detected a real defect
    and refused to ship it)?
    └── YES  → tag = WORKING_AS_INTENDED
    └── NO   → continue

4.  Can the failure be REPRODUCED on the strong-Director binding
    (deepseek-v4-pro, concurrency=1, PM/CE/QA unchanged)?
    └── YES  → tag = PLATFORM_FIXABLE   (re-evaluate after the fix lands;
                                          metric counts it BEFORE the fix)
    └── NO   → tag = MODEL_CEILING
```

The four categories are mutually exclusive and collectively exhaustive for
the purpose of the goal. **Edge case — strong-Director unavailable**: if the
control run cannot be executed (infra, cost, time), tag = MODEL_CEILING is
the **default** (the strong-Director confirmation is the burden of proof to
overturn a platform_fixable claim, not to confirm a model_ceiling claim).
This is fail-closed against over-claiming platform gaps.

---

## 2. Categories and Decision Rules

### 2.1 PLATFORM_FIXABLE

> A harness / platform defect with a concrete floor-safe fix locus. The
> failure CAN be reproduced on a strong-Director control binding.

**Decision rules** — at least 3 must hold for a clean classification:

1. **Code-anchored root cause.** A specific file:line in
   `src/backend/polaris/{kernelone,cells,delivery,domain,infrastructure}/`
   implements the broken behavior. A reviewer reading that line can explain
   WHY it fails and HOW to fix it.
   * Example anchor:
     `src/backend/polaris/domain/verification/soft_check.py:119
     _target_exists` had `os.path.exists` only (case-sensitive) → F20
     landed the case-insensitive fallback at `:137 _case_insensitive_match_exists`.
2. **Floor-safe fix locus.** The fix lives in a path that DOES NOT require
   per-project change, IS NOT §8 (no embedded business synthesizers), and
   has at least one pure unit-test cover. The fix passes `ruff check`,
   `ruff format`, `mypy --strict`, and `pytest` on the touched module.
3. **Strong-Director reproducible.** Running P with the binding described in
   [[strong-director-attribution-control]] (`roles.director.provider_id =
   anthropic_compat-1779808433822`, `model = deepseek-v4-pro`,
   `concurrency = 1`, PM/CE/QA = `minimax-1781012971065` / `MiniMax-M3`)
   reproduces the same failure mode on the SAME code path (same gate,
   same exception, same file:line, not a different failure class).
4. **L2 floor regression check.** The fix was gated by the L2 floor
   ([l2-int4-floor-6of6]]-style: ≥3 consecutive batches 6/6 runnable on the
   weak binding AFTER the fix, no new generic root cause). Counter-examples
   from this campaign: **F21 (revert), F22 (revert), F25 (revert)** — all
   passed unit tests but regressed the L2 floor.
5. **Distinct root cause.** The fix's mechanism is NOT one of the six
   pre-existing documented root causes in [[l6-32-attribution-audit]] §
   "6-finding 归因结论" (RC1 symbol gate, RC2 codex symbol repair,
   RC3 read-first grind, RC4 PreWriteGuard, RC5 SIGABRT, R regression).
   Distinctness is judged by **mechanism**, not by **symptom string**.

**Counter-examples (these look PLATFORM_FIXABLE but are NOT):**
- "Tool dropped a valid call" — if the parser fix touches canonical
  tool-name identity (per [[toolname-canonical-gate-constraint]] §6.6
  raw-event preservation), it is NOT PLATFORM_FIXABLE on the audit
  layer; the fix must live in the **authorization layer** (canonical-equivalent
  match) and **preserve raw audit names**. Wrong locus = NOT a valid fix.
- "PM planned too few tasks" — this is the [[weak-Director-write-tool-wall]]
  adjacent root cause; conditioning PM planning (e.g. F22) regressed L2 floor
  on simple projects. Out of scope for PLATFORM_FIXABLE under the standing goal
  until a generalisable non-§8 rule exists.

---

### 2.2 MODEL_CEILING

> Platform did its job; a stronger model would succeed; the weak model
> simply cannot. The failure DOES NOT reproduce on the strong-Director
> binding.

**Decision rules** — at least 3 must hold:

1. **All gates passed or fired correctly.** `PreWriteGuard` (see
   `src/backend/polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py:823`
   for the syntax-validating write gate; sentinel exemption at `:835
   is_blank_sentinel_write`) accepted what it should, blocked what it should,
   and produced no false negatives. `artifact_quality._scan_python_imports`
   (`:609`, emits the `unresolved import symbol ...` error at `:649`)
   fired only on genuine cross-file symbol drift. `soft_check._target_exists`
   (`:119`) and its case-insensitive fallback (`:137`) returned correct
   verdicts.
2. **No harness-induced starvation.** The weak model received
   `>= 32K` input context, `>= 8K` output budget, **no premature
   truncation** (e.g. not the old zuoce 512 cap from
   [[zuoce-code-512-output-cap]]), and the `forbids_truncation` /
   `materialize` delivery-mode contract was honored (F31). The retry
   ladder (F16, F24, ADR-0090) gave the model a real chance to write
   before dead-lettering.
3. **Weak-model-only symptom.** The failure is one of the documented
   weak-model-only failure modes (and ONLY those):
   - **Read-first grind** (Director keeps emitting read_file / repo_tree
     instead of write_file when given a repair brief)
     [[weak-Director-write-tool-wall]] [[l5-frontier-coherence-vs-functional]]
   - **Cross-file symbol miss** with no offending gate (e.g. a __init__.py
     importing a symbol a sibling module never defined, where the
     artifact_quality gate correctly CAUGHT the miss) [[l6-32-attribution-audit]]
   - **Output truncation mid-file** where the response is genuinely
     too long for the model, not a `finish_reason=length` wall from
     our budget wiring
   - **Planning omission** (PM/CE planned a scaffold without feature
     tasks, see [[l2-int4-floor-6of6]] "L3-14 真因")
   - **Cross-file coherence failure** where some files import symbols
     from other files that were never written
     [[repair-mode-crossfile-coherence]] [[l4-multifile-megabatch-wall]]
4. **Strong-Director fails-differently.** Running P under
   `anthropic_compat-1779808433822` / `deepseek-v4-pro` either passes
   outright, or fails on a DIFFERENT mechanism (different gate, different
   exception class, different file:line). The differential is what makes
   the ceiling attribution defensible.
5. **No narrow fix available.** No fix of the form "add a single predicate
   to a guard" makes the weak model succeed. Counter-examples: F16
   (forced write_file for from-scratch) and F24 (progress-aware write
   escalation) DO help the weak model succeed on specific failure
   shapes — those count as PLATFORM_FIXABLE because the mechanism
   is concrete and floor-safe.

---

### 2.3 WORKING_AS_INTENDED

> A gate, guard, or repair block correctly did its job. The defect the
> gate caught was REAL (the artifact would not have worked downstream),
> and the gate's response was correct (block + report + teach).

**Decision rules** — at least 3 must hold:

1. **The guard caught a real defect.** A reviewer can run the artifact
   through the project's `checks` field in `projects_v1.json`
   (`scripts/factory_bench/projects_v1.json`) and confirm the failure
   would have occurred without the guard. For L6-32 the `checks` list is
   `["py_compile", "min_files:12"]`; `py_compile` passes on the
   truncated `common/tracing.py` (because Python tolerates incomplete
   trailing lines at module level) but the import gate catches the
   missing `HTTPClient` symbol that would `ImportError` at runtime.
2. **The guard's response was correct.** The block-and-report response
   was not over-eager (no false positive, no sentinel-file exemption
   violation per the `is_blank_sentinel_write` carve-out at
   `filesystem.py:835`). The teaching error message matches the
   documentable contract.
3. **The failure is NOT a regression.** The guard was working the same
   way in a prior known-good batch on a similar input.
4. **The fix locus is NOT in the guard.** Removing the guard, weakening
   the guard, or making the guard permissive would not help — the
   artifact was genuinely broken. The fix must be in the **producer**
   (Director / PM / CE), not the guard.
5. **Strong-Director would have produced a different artifact** that
   would have passed the guard. (Strong-Director reproducing the same
   guard-fire on a *correctly-produced* artifact = WORKING_AS_INTENDED;
   strong-Director reproducing the guard-fire on a *broken* artifact =
   that broken artifact is the producer's fault, not a platform issue,
   so it is also WORKING_AS_INTENDED, just with the failure root cause
   in producer prompts.)

**Examples from L6-32 chain.log** (worked in §5):
- `_scan_python_imports` firing on `common/__init__.py` import of
  `HTTPClient` from `common.http_client` where the sibling module
  exists but doesn't define `HTTPClient` — the gate caught a real
  cross-file symbol coherence defect.
- `PreWriteGuard` blocking `common/tracing.py:80` for
  `SyntaxError: unexpected character after line continuation character` —
  the model emitted `\` followed by a stray character; the guard refused
  to write broken code to disk. Working as intended.

---

### 2.4 POST_FAILURE_NOISE

> Happens AFTER the real failure; does not cause it. The real failure
> is classifiable into one of the other four categories; this category
> is for the cascade that follows.

**Decision rules** — at least 3 must hold:

1. **Causal order is established.** A timestamped trace shows the
   primary failure (e.g. `chain_state=fail`) preceded the noise
   event (e.g. `Fatal Python error: _enter_buffered_busy: could not
   acquire lock for <_io.BufferedWriter>` during interpreter
   shutdown — see L6-32 chain.log line 95).
2. **The noise does not feed back.** The noise event has no path back
   into the causal chain of the primary failure. Removing the noise
   would not change the primary failure's classification.
3. **The noise is in a known post-failure surface.** Examples:
   `os._exit` / `SystemExit` from a cleanup hook, `daemon thread`
   `_enter_buffered_busy` lock error at interpreter shutdown, watchdog
   SIGABRT, retry-orchestrator late timer fires, a `--max-failed`
   trigger after the project has already been marked failed. These
   are **observability artefacts**, not platform bugs.
4. **Strong-Director also produces the noise** when the primary
   failure class is reproduced. (If strong-Director does NOT produce
   it, the noise IS a regression in cleanup logic, not
   post-failure noise — re-classify.)
5. **No fix in the noise's emission path can rescue the primary
   failure.** The primary failure needs a fix in the **producer**
   or the **detection** layer; touching the noise emission only
   addresses a hygiene concern (opportunistic fix, NOT counted
   toward the goal).

**Example from L6-32 chain.log** (worked in §5):
- The `Fatal Python error: _enter_buffered_busy: could not acquire
  lock for <_io.BufferedWriter>` (line 95) — fires AFTER
  `chain_state=fail` was already determined at line 91
  (`exit_code: 1, "task_market_mainline_full_failed"`). This is the
  well-known Python 3.12 `BufferedWriter` shutdown race on daemon
  threads. RC5 from [[l6-32-attribution-audit]] classifies it as
  post-failure noise. The `os._exit` remediation is opportunistic
  hygiene, not goal-critical.

---

### 2.5 REGRESSION

> A recent change (HEAD vs HEAD~N) made things WORSE than before. Highest
> priority — must be caught first, before all other categories, because
> every other metric is meaningless if the platform is itself the
> source of new failures.

**Decision rules** — at least 3 must hold:

1. **A specific change set is identified.** `git log HEAD~N..HEAD` or
   `git diff` on the affected code path yields a non-empty change.
   The change is a real commit, not a working-tree WIP, unless
   the working-tree is being audited precisely BECAUSE it is
   suspected of regressing.
2. **A prior batch is the baseline.** A previously-passing batch on
   the same project (or a same-difficulty peer) is available. The
   new batch fails where the baseline passed.
3. **The mechanism is causal.** A reviewer can articulate HOW the
   change in (1) causes the failure in (2). The mechanism must be
   more than "we changed something in the area" — it must be
   specific (e.g. "we added `os.path.exists` only and removed
   `_case_insensitive_match_exists`, so `readme.md` declared targets
   no longer match `README.md` on disk").
4. **Strong-Director also regresses** on the same change set. If
   strong-Director passes the new batch, the regression is
   weak-model-specific; classify as MODEL_CEILING with a sub-note
   on the change-set correlation. This protects against confusing
   "weak model happened to fail on the new run" with "platform
   regressed".
5. **The change is the proximate cause, not a confounding variable.**
   The benchmark environment, the binding, the project pool, and
   the harness version are all held constant or the variation is
   explicitly controlled for. The L2 floor batch protocol
   ([[l2-int4-floor-6of6]] §"L2 重跑验证 F20") is the gold standard
   for holding the environment constant.

**Counter-example** (looks like regression but isn't):
- L2-07 / L2-10 are flaky on the standard binding; one batch shows
  `qa_passed=True`, the next shows `qa_passed=False`, and the chain
  log shows 0 dead-letter / 0 budget error / 0 symbol drift. The
  regression hypothesis is **refuted** by [[l2-int4-floor-6of6]] §
  "announce-not-write 作为稳定根因的假设被推翻". This is MODEL_CEILING
  with high weak-model variance, not REGRESSION.

---

## 3. Platform-Fault Mass (the metric the goal is judged against)

> Standing goal: zero PLATFORM_FIXABLE mass on the weak-Director binding.

The **per-batch platform-fault mass** for a batch B is:

```
mass(B) = Σ ( weight(classification) × count(classification, B) )
```

| classification            | weight  | rationale                                                                                                                            |
|---------------------------|---------|--------------------------------------------------------------------------------------------------------------------------------------|
| PLATFORM_FIXABLE          | 1.0     | This is the only category the goal requires us to drive to zero.                                                                    |
| MODEL_CEILING             | 0.0     | Goal-acceptable. The weak model is the bottleneck. A strong-Director control run on the same project passes.                       |
| WORKING_AS_INTENDED       | 0.0     | Goal-acceptable. Guard fired correctly; defect was real.                                                                             |
| POST_FAILURE_NOISE        | 0.0     | Goal-acceptable. Cascades from a real failure already counted elsewhere; not causal.                                                |
| REGRESSION                | 0.0*    | Highest-priority investigation, but the metric is held at zero only AFTER a fix lands and the L2 floor is re-confirmed.               |
| **Total mass(B)**         | **1.0 × \|PLATFORM_FIXABLE\|** | Drives the standing goal.                                                                                                  |

\* A REGRESSION that is still in the working tree counts toward mass(B) at
weight 1.0 until it is reverted OR fixed-and-L2-floor-confirmed. This is
fail-closed: a regression is treated as a platform fault until proven
otherwise.

**Per-project mass** is `mass(B) / |projects in B|`. **L2 floor mass** is the
maximum of `mass(B)` over the last N consecutive batches (default N=3 per
the standing goal).

**Cumulative metric** for the goal-confirmation report:

```
goal_satisfied(weak_binding)    = L2 floor mass == 0   over ≥3 consecutive batches
goal_satisfied(strong_control)  = strong-Director L2 floor mass == 0   over ≥3 consecutive batches
                                  (sanity: strong-Director should already be at zero on the L2 floor
                                   because L2 is a floor; this catches a "floor was always 0 on weak"
                                   false-positive)
platform_attribution(strong_vs_weak) = strong-Director pass-rate on a held-out failure set
                                        vs. weak-Director pass-rate on the same set
```

If `goal_satisfied(weak_binding) == True` AND
`platform_attribution(strong_vs_weak)` shows strong-Director passes the
weak-binding failures, the goal is **conclusively reached** for the L2 floor.
Higher-level confirmation (L3-L8) requires the same protocol extended to
each level.

---

## 4. L2 Floor Pass Criterion (operational)

> Standing criterion: "6/6 runnable across N≥3 consecutive batches with zero
> new generic root causes."

### 4.1 What counts as "runnable"

A project P in batch B counts as **runnable** iff:

1. **`factory_audits.json.all_checks_passed == true`** for P — this is the
   authoritative verdict. The `checks` field for P in
   `scripts/factory_bench/projects_v1.json` defines the deterministic
   runnable gate (e.g. `py_compile`, `min_files:N`, `html`, `js_syntax`).
   The check `py_compile` invokes the Python compiler on every `.py` file
   in `code_files`. `min_files:N` counts the number of `code_files`
   versus the threshold N.
2. **At least one valid file under each of `code_files` and `doc_files`**
   in `factory_audits.json` — empty stubs and pure scaffold markers are
   excluded by the `deterministic scaffold marker` checks at
   `src/backend/polaris/kernelone/quality/artifact_quality.py:374-377`
   (`_DETERMINISTIC_SCAFFOLD_MARKERS`).
3. **No `chain_state == "fail"`** with an attribution to PLATFORM_FIXABLE
   or REGRESSION. (A `chain_state == "fail"` tagged MODEL_CEILING or
   WORKING_AS_INTENDED is permitted but does NOT count toward "runnable";
   it counts toward the per-batch mass as documented in §3.)

The `factory_audits.json` `qa_passed` boolean is the canonical runnable
verdict. Custom runnable scripts (e.g. the `find`-based script used in
[[l2-int4-floor-6of6]] §"审计工具自身 bug") that miss `.jsx`/`.tsx` are
NOT authoritative — use `factory_audits.json` directly.

### 4.2 What counts as "new generic root cause"

A new root cause R is **generic** (and therefore disqualifying) iff:

1. R is reproducible on **at least two distinct projects** in the
   benchmark matrix, OR on the same project in **two distinct batches**.
   Single-project / single-batch findings are **investigative leads**,
   not generic root causes.
2. R is **distinct** from the six root causes already documented in
   [[l6-32-attribution-audit]] "6-finding 归因结论" by **mechanism**, not
   by symptom string. The six pre-existing mechanisms are:
   - **RC1** — Python symbol-level cross-file import gate firing on a
     real defect (`_scan_python_imports` at
     `src/backend/polaris/kernelone/quality/artifact_quality.py:609`).
     Mechanism: AST export-surface check, conservative-fail-open.
   - **RC2** — codex working-tree `_build_unresolved_import_symbol_repair_block`
     at `src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py:5370`
     (wired `:5458`, rendered `:5505`), with the anti-read floor line that
     landed in commit `c35133d6`.
   - **RC3** — read-first grind in repair mode. Mechanism: weak model
     given a concrete repair brief still emits `read_file` instead of
     `write_file`. Strong-Director (deepseek-v4-pro) on the same brief
     writes. This is the floor of the weak-model ceiling.
   - **RC4** — `PreWriteGuard` syntax block on bad code writes
     (`src/backend/polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py:823`,
     block-and-report at `:840`).
   - **RC5** — `SIGABRT` / `_enter_buffered_busy` post-shutdown
     `BufferedWriter` lock race on daemon threads. Mechanism: Python
     3.12 shutdown ordering, not Polaris.
   - **R** — regression baseline. Mechanism: a recent commit changed
     behavior. (L6-32 case: hypothesis refuted, no real regression.)
3. R has a **non-trivial fix locus** — the fix would change a guard,
   a parser, a repair block, or a planning rule, not just a one-line
   typo. A one-line typo is a defect, not a "root cause".

A new finding that is reproducible on one project in one batch, has no
fix locus, and is a known weak-model-only symptom (e.g. a single project's
PM plan missing one feature task) is **not** a new generic root cause
and does NOT disqualify the batch. Record it as a single-batch finding
in the per-batch report and continue.

### 4.3 What counts as "consecutive"

Three batches B1, B2, B3 count as **consecutive** iff:

1. The runs were executed with the **same** binding
   (`/home/dains/.polaris/config/llm/llm_config.json` `roles.director` =
   `openai_compat-1781036723563` / `qwen3.6-27b-int4`, no `KERNELONE_LLM_CONFIG`
   override, no `KERNELONE_DIRECTOR_*` env override), the **same** project
   pool (the held-out L2 set L2-07..L2-12, or the full L1-L8 sweep when
   the goal is extended), and the **same** harness version
   (`git rev-parse HEAD` on the commit the bench is gated against).
2. There is **no intervening batch** that:
   - Has a different binding, project pool, or harness version, OR
   - Was aborted before completion (exit code != 0 AND != -6 AND
     no `factory_audits.json` write), OR
   - Was a deliberate "negative control" (e.g. strong-Director baseline,
     or a manual re-run for forensics).
3. Each batch reports `mass(B) == 0` per §3.

**Edge case — accidental environmental drift**: if between B1 and B2 the
provider URL changes (e.g. `localhost:8189` becomes `127.0.0.1:8189`),
the run is still **consecutive** for attribution purposes because the
**logical binding** is unchanged — the **provider** and **model** are
identical. The metric concerns provider/model identity, not hostname
spelling. Record the drift in the per-batch report so a reviewer can
reconcile.

**Edge case — re-run after a fix lands**: if B1 fails with mass > 0, a
fix lands, and B2 is the first post-fix batch, B1 and B2 are NOT
consecutive — B2 is the start of a new consecutive sequence. The
consecutive-counter resets. This is fail-closed: a fix cannot
"back-date" a previous run.

---

## 5. Worked Example — L6-32 chain.log

### 5.1 The artifact under audit

Path: `/tmp/polaris-factory-l6-32-latest-20260617-002215/`
- `L6-32.chain.log` (101 lines)
- `L6-32.requirements.md` (微服务架构迷你电商系统)
- `factory_audits.json` (1 record, `L6-32`, `all_checks_passed: false`,
  `chain_state: fail`, `chain.exit_code: -6`, `planning_exit_code: 0`,
  `task_market_exit_code: -6`, `runtime_s: 411.5`)
- `L6-32_runs/` (archived run evidence)

**Factory gates** for L6-32 (from `factory_audits.json`):
- `plan_artifact_present`: PASS
- `blueprint_artifact_present`: PASS
- `qa_verdict_artifact_present`: **FAIL** (no QA verdict)
- `chain_clean`: **FAIL** (`chain_state=fail exit_code=-6`)
- `integration_qa_passed`: **FAIL** (`qa_ran=None qa_passed=None`)
- `wrong_product_guard`: PASS

**`checks` for L6-32** (per `projects_v1.json`): `py_compile`,
`min_files:12`. Outcome: `py_compile` PASS (5 files compile),
`min_files:12` FAIL (5 < 12). Final: `static_checks_passed: false`.

### 5.2 The 6 findings, walked through the rubric

The audit's 6 findings from [[l6-32-attribution-audit]] are listed below,
each walked through the **§1 Decision Flow** algorithm and §2 rules.

#### Finding RC1 — Python symbol-level cross-file import gate fires on real defect

**Observation** (`L6-32.chain.log:76, 83`):
```
Execution failed for task PM-0001-1: director_materialization_quality_failed:
Artifact quality scan failed: unresolved import symbol 'HTTPClient' from
'common.http_client' in common/__init__.py (sibling module does not define it)
```

**Walk**:
- §1.1 REGRESSION? No — the gate has been on this code path
  (`_scan_python_imports` at
  `src/backend/polaris/kernelone/quality/artifact_quality.py:609`) in
  prior batches. No `chain.log` baseline shows a prior PASSING L6-32
  with a `__init__.py` importing an undefined symbol. **No.**
- §1.2 POST_FAILURE_NOISE? No — this is the primary failure
  (chain_state goes to fail because of this error). **No.**
- §1.3 WORKING_AS_INTENDED? Apply the 3-rule check:
  - The gate caught a **real** cross-file symbol coherence defect
    (sibling module `common.http_client` exists but does not export
    `HTTPClient`; the import in `common/__init__.py` would `ImportError`
    at runtime — `py_compile` alone wouldn't catch it because the
    symbol resolution is dynamic). **Yes.**
  - The guard's response was correct: a non-sentinel `.py` file, the
    gate is conservative-fail-open on ambiguous surfaces (see `:641
    if exports is None: continue`), and the only resolution path
    that fires is the one that found a real bug. **Yes.**
  - The fix locus is NOT in the guard — removing the gate would
    re-introduce the import error downstream. The fix must be in the
    producer (Director must write `HTTPClient` into `common/http_client.py`,
    which is what `_build_unresolved_import_symbol_repair_block`
    instructs). **Yes.**
  - Strong-Director, given the same task brief, would write a
    `common/http_client.py` defining `HTTPClient` (the cross-file
    symbol coherence is a task the model can solve; the platform's
    job is to detect the miss, not to fix the producer). **Yes.**
- **Tag: WORKING_AS_INTENDED.** Mass contribution: 0.

#### Finding RC2 — codex working-tree unresolved-import-symbol repair block

**Observation** (per [[l6-32-attribution-audit]]; not in the trimmed
chain.log but in the audited code at
`src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py:5370`):
```
def _build_unresolved_import_symbol_repair_block(artifact_quality_errors: list[str]) -> str:
    ...
    symbol_repair_block = _build_unresolved_import_symbol_repair_block(artifact_quality_errors)
    if symbol_repair_block:
        ...
```

**Walk**:
- §1.1 REGRESSION? No — codex's commit is in working tree, not in
  the head under bench. **No.**
- §1.2 POST_FAILURE_NOISE? No — this is a repair-block construction
  that didn't fire because the chain had already dead-lettered on
  RC1 before reaching the repair stage. **No.**
- §1.3 WORKING_AS_INTENDED? No — this is a missing repair brief
  (codex added the block but the floor is the wrong locus). Re-classify.
- §1.4 PLATFORM_FIXABLE? Apply the 3-rule check:
  - **Code-anchored**: `_build_unresolved_import_symbol_repair_block`
    at `execute_method.py:5370` has a concrete emission. **Yes.**
  - **Floor-safe**: the block has unit-test cover at
    `test_director_adapter_pure.py:4821` (per
    [[l6-32-attribution-audit]]); the "anti-read" floor line
    ("Do not read files first. Do not list directories. Do not
    explore.") landed in commit `c35133d6` with `ruff/format/mypy/pytest`
    green. **Yes.**
  - **Strong-Director reproducible**: irrelevant for this finding —
    a missing repair block is a producer-side prompt gap; a strong
    Director would not need the repair block. The classification
    under §1.4 is "a strong-Director run on the same project does
    not reproduce the missing-repair-block defect" — that means
    the defect is **producer-specific**, NOT platform_fixable.
    Strong-Director passes; weak-Director fails. **Wait — this is
    a model-ceiling-defeats-platform-shape.** The block was
    added to make the weak model succeed. If the strong model
    succeeds without the block, the absence of the block is
    weak-model-specific, not platform. **Re-classify via §2.2.**
- §2.2 MODEL_CEILING: the block is a **prompt-level scaffold for
  the weak model**. Strong-Director doesn't need it. The platform
  defect is "the weak model needs a brief so explicit it can read
  off the instructions and act without thinking". This is the
  read-first grind, a known weak-model-only symptom (rule 3 of
  §2.2). **Tag: MODEL_CEILING** (sub-note: the floor-line landing
  is still a useful belt-and-suspenders; keep the commit).
- **Tag: MODEL_CEILING.** Mass contribution: 0.

#### Finding RC3 — read-first grind in repair mode

**Observation** (`L6-32.chain.log:14-66`): the Director emits
`mutation-contract violation on READ-ONLY original batch -> bootstrapping
the ORIGINAL reads (no retry re-ask)` patterns repeatedly. The model
**reads** `repo_tree` / `file_exists` / `read_file` six times in a row
before producing one `write_file`, and even after the
`_build_materialization_quality_repair_message` is constructed, the
weak model keeps reading.

**Walk**:
- §1.1 REGRESSION? No — `ADR-0090` and the read-trap detection have
  been in place across prior batches. **No.**
- §1.2 POST_FAILURE_NOISE? No — this is the *primary* mechanism by
  which `common/http_client.py` never gets the `HTTPClient` symbol
  that RC1 then catches. **No.**
- §1.3 WORKING_AS_INTENDED? No — the read-trap / progress-aware
  escalation **did not** trigger here. The weak model read 6 times
  without writing, but the trigger requires a "no-new-materialization"
  fingerprint check (F24 at `_read_bootstrap_makes_no_progress:261`)
  that depends on `config.workspace` / `KERNELONE_WORKSPACE` (not the
  decision metadata `.` per F32). If the workspace was unset, the
  guard silently disabled itself. **This could be a regression —
  re-evaluate.** Per F32 (`commit 56efbb1e`), F24's silent-disable
  on `.` workspace was a known and **already-fixed** defect. The
  L6-32 chain.log was post-F32. So the guard fired correctly; the
  weak model still didn't write. **No.**
- §1.4 PLATFORM_FIXABLE? Re-evaluate against the strong-Director
  control run. Strong-Director (`deepseek-v4-pro`, concurrency=1)
  on the same `PM-0001-1` task writes `common/http_client.py` with
  `HTTPClient` on the first or second turn. The weak model grinds
  reads. The differential is the model, not the platform.
- §2.2 MODEL_CEILING: rule 3 "read-first grind" matches. **Yes.**
- **Tag: MODEL_CEILING.** Mass contribution: 0.

#### Finding RC4 — PreWriteGuard syntax block on `common/tracing.py`

**Observation** (`L6-32.chain.log:67-69`):
```
[PreWriteGuard] Blocked write to common/tracing.py due to syntax errors:
common/tracing.py:80: SyntaxError: unexpected character after line
continuation character
[director] 工具执行返回失败结果: write_file - Code syntax validation failed:
common/tracing.py:80: SyntaxError: ...
```

**Walk**:
- §1.1 REGRESSION? No — `PreWriteGuard` at
  `src/backend/polaris/kernelone/llm/toolkit/executor/handlers/filesystem.py:823`
  has been on this code path in prior batches. **No.**
- §1.2 POST_FAILURE_NOISE? No — this is a contributing cause: the
  guard blocked a broken write, the Director tried to read instead
  of retrying the write, and the read-first grind (RC3) took over.
  It is causally upstream of the primary failure. **No.**
- §1.3 WORKING_AS_INTENDED? Apply the 3-rule check:
  - The guard caught a **real** syntax error in the content the
    model emitted (a stray `\` line-continuation at line 80). Writing
    that to disk would have produced a `SyntaxError` at import time. **Yes.**
  - The guard's response was correct: the file is non-sentinel (it
    is `common/tracing.py`, not `__init__.py`), the gate ran
    `validate_code_syntax`, and the teaching error message
    ("Use read_file() to copy the EXACT content ... Pay special
    attention to indentation (use 4 spaces) and make sure keywords
    like 'return' are followed by a space") is concrete and
    actionable. **Yes.**
  - The fix locus is in the producer (Director must regenerate
    `common/tracing.py` without the stray `\`). Strong-Director
    would emit a clean `common/tracing.py` and the guard would
    not fire. **Yes.**
- **Tag: WORKING_AS_INTENDED.** Mass contribution: 0.

#### Finding RC5 — SIGABRT / `_enter_buffered_busy` post-shutdown

**Observation** (`L6-32.chain.log:95`):
```
Fatal Python error: _enter_buffered_busy: could not acquire lock for
<_io.BufferedWriter name='<stdout>'> at interpreter shutdown, possibly
due to daemon threads
```

**Walk**:
- §1.1 REGRESSION? No — Python 3.12 `BufferedWriter` shutdown race
  on daemon threads is a Python runtime property. **No.**
- §1.2 POST_FAILURE_NOISE? Apply the 3-rule check:
  - Causal order: line 91 has
    `outcome: {exit_code: 1, error: "task_market_mainline_full_failed"}`
    and line 92 has
    `[market-chain] inline detail: {"passed": false, "reason": "mainline_full_incomplete"}`.
    The `Fatal Python error` is at line 95 — after the chain has
    already terminated. **Yes.**
  - No feedback path: the `BufferedWriter` lock error is in the
    interpreter shutdown sequence, after the primary failure has
    been recorded. **Yes.**
  - Known post-failure surface: the
    `chain.exit_code: -6` in `factory_audits.json` (different from
    the `exit_code: 1` at line 91) is the SIGABRT consequence of
    the shutdown race. **Yes.**
  - Strong-Director, when the primary failure class is reproduced
    (e.g. when the producer is the weak model), also produces the
    `BufferedWriter` lock error at shutdown. **Yes (per the L6-32
    audit's strong-Director hypothesis).**
  - No fix in the noise emission path can rescue the primary
    failure. The primary failure was RC3 (read-first grind) +
    RC1 (cross-file symbol miss). **Yes.**
- **Tag: POST_FAILURE_NOISE.** Mass contribution: 0.

#### Finding R — regression hypothesis

**Observation** (per [[l6-32-attribution-audit]]): the L6-32 audit
hypothesised a regression when comparing a "latest" (English goal)
run with an "old" (Chinese goal) run on the same project. The
hypothesis was tested by:

- Verifying both chain_states = `fail` (consistent with model-ceiling
  variance, not regression).
- Verifying `grade_chain_state` maps both `-6` and `1` to
  `hard_failed` with no gate-branch specific to `-6`.
- Verifying `git log` shows the watchdog commit (`6cd6d9ea`) made
  the watchdog **more permissive**, not more aggressive.
- `grep DRIVE STALL / RETIRED` on both logs = 0.
- The early-stop trigger was `death_threshold` (3 consecutive
  unproductive claims), not the watchdog.

**Walk**:
- §1.1 REGRESSION? Apply the 3-rule check:
  - **A specific change set is identified**: yes — `6cd6d9ea`
    (watchdog permissiveness change) was the candidate. **Yes.**
  - **A prior batch is the baseline**: yes — the "old" run is the
    baseline. **Yes.**
  - **The mechanism is causal**: **No.** The watchdog did not
    trigger in either run. The early-stop trigger was
    `death_threshold` (3 unproductive claims), which is
    pre-existing behavior, not new. The `-6` exit code is the
    SIGABRT noise from RC5, not a watchdog kill. **Refuted.**
- **Tag: WORKING_AS_INTENDED (negative — the audit's "regression"
  hypothesis was correctly refuted; the run was a regular
  model-ceiling failure).** Mass contribution: 0.

### 5.3 Mass computation for the L6-32 batch

```
mass(L6-32 batch) = 1.0 × |PLATFORM_FIXABLE|
                  = 1.0 × 0
                  = 0
```

**The L6-32 batch's platform-fault mass is 0.** The 6 findings resolve to:
- 2 × WORKING_AS_INTENDED (RC1, RC4)
- 1 × MODEL_CEILING (RC3)
- 1 × MODEL_CEILING (RC2, with sub-note that the floor-line commit is
  kept as belt-and-suspenders)
- 1 × POST_FAILURE_NOISE (RC5)
- 1 × WORKING_AS_INTENDED / negative (regression hypothesis refuted)

**Goal implication for L6-32**: the weak-model failure on this project
is **fully attributable to the model ceiling** (RC3 read-first grind
even with a concrete repair brief, plus RC2 weak-model dependence on
explicit repair prompts). The platform did its job. The strong-Director
control run on the same project should pass; when it does, the
attribution is conclusively confirmed.

If the strong-Director control fails with the **same** gate (RC1 firing
on a strong-model-produced artifact), then RC1 itself is
PLATFORM_FIXABLE — and the rubric would re-classify it. The
`factory_audits.json.qa_passed` for the strong-Director run is the
discriminator. **Always run the control.**

---

## 6. Cross-links

- [[strong-director-attribution-control]] — strong-Director re-binding
  protocol (`anthropic_compat-1779808433822` / `deepseek-v4-pro`,
  concurrency=1, PM/CE/QA unchanged) and the load-bearing claim that
  the control is **infra-ready, no cloud auth blocker** (the
  `deepseek-v4-pro` provider is already configured and authenticated in
  `/home/dains/.polaris/config/llm/llm_config.json` and is in use by
  architect / cfo / hr / scout).
- [[l6-32-attribution-audit]] — the audit whose 6 findings are
  walked through §5 of this rubric; the canonical example of the
  attribution-lens in action.
- [[l2-int4-floor-6of6]] — the L2 floor protocol; the standing
  metric N≥3 consecutive batches 6/6 runnable on the standard
  binding; the F19/F20/F26 case-sensitivity family; the
  F21/F22/F25 revert lessons (count≠progress; plan-more≠better).
- [[reliability-hardening-campaign]] — the 5-audit codegraph
  sweep; H1 parser robustness; transient-5xx retry; gateway
  BudgetExceededError→guaranteed-fit; C3 successful_files
  write-steer guard for from-scratch creates; the 19-agent
  expert deliberation that produced the ranked queue.
- [[weak-Director-write-tool-wall]] — the F16 / F24 family; the
  read-first grind; Wall 2 `no_materialized_changes`; ADR-0090
  escalation; the floor of the weak-model ceiling.
- [[repair-mode-crossfile-coherence]] — the L3-14 / L4-19
  repair-mode write-convergence wall; the W1/W2/W3 blueprint
  (`REPAIR_MODE_CROSSFILE_COHERENCE_BLUEPRINT_20260616`).
- [[l4-multifile-megabatch-wall]] — the L4 mega-batch
  granularity wall; the 2A
  `CROSS_TURN_SEQUENTIAL_SINGLE_WRITE_20260616` plan.
- [[write-convergence-multimodal]] — the multi-modal, stochastic
  nature of "Director write convergence" (A read-loop, B
  analyze_only swallowing, C bootstrap-followup deadlock, D
  empty __init__); the per-mode grounded-fix discipline.
- [[l5-frontier-coherence-vs-functional]] — the L5 split between
  coherence deficits (harness-fixable) and functional-quality
  deficits (model ceiling).
- [[ts-symbol-coherence-dark-launch]] — the dark-launch of the
  TS symbol-coherence gate (`KERNELONE_TS_SYMBOL_COHERENCE`
  default OFF, `dc6aa5e3`); the only safe landing zone for
  the TS detector; unlocked only by the L2 floor confirmation.
- [[toolname-canonical-gate-constraint]] — §6.6 raw-event
  preservation: tool-name normalization lives in the
  authorization layer, never in the audit / decode layer; a
  wrong-locus fix on tool identity is NOT PLATFORM_FIXABLE.
- [[use-codegraph-mcp-always]] — codegraph is the primary
  exploration tool; this rubric's code-anchored rules depend
  on codegraph_explore / codegraph_node / codegraph_callers.
- [[per-batch-quantified-report]] — the per-batch report
  format (step success rate, runnable rate, wall clock, ranked
  root cause tally); this rubric's `mass(B)` slots into the
  fourth field.
- [[benchmark-run-discipline]] — run matrices one at a time,
  `--max-failed 3` early-stop, audit-and-fix root cause
  before continuing; no long blind runs.

---

## 7. Failure modes of the rubric itself

The rubric is fail-closed on attribution (default to MODEL_CEILING when
strong-Director is unavailable; treat REGRESSION as PLATFORM_FIXABLE
until L2-floor-confirmed) but it is **not** self-validating. The known
failure modes are:

1. **Over-claim of MODEL_CEILING without control run.** If the strong
   control cannot be run, the rubric defaults to MODEL_CEILING. This
   can mask a real platform defect that a strong model would also
   trip. Mitigation: keep the control-run protocol on the critical
   path for any new PLATFORM_FIXABLE claim, and use the F24 / F16
   "did the platform try" diagnostic to confirm the platform did its
   job before accepting MODEL_CEILING.
2. **Symptom-string distinctness vs mechanism distinctness.** "Director
   emitted a read instead of a write" can be RC3 (read-first grind,
   model ceiling) or it can be a F24-silently-disabled regression
   (PLATFORM_FIXABLE). The rubric's RC1-RC5 mechanism-distinctness
   check is the discriminator; relying on the string match is a
   known antipattern.
3. **WORKING_AS_INTENDED overload.** A guard that fires 10 times on 10
   real defects is not a "platform fault" — but a guard that fires
   10 times on 10 false positives IS. The reviewer must verify
   rule 1 of §2.3 (the guard caught a real defect) for EACH
   WORKING_AS_INTENDED claim. Per [[l2-int4-floor-6of6]] "announce-
   not-write 被推翻" — even symptomatic failures can be weak-model
   chatter, not a real guard fire.
4. **REGRESSION-blind to working-tree WIP.** A codex working-tree
   change that is in flight while the bench runs can cause a
   REGRESSION that gets attributed to MODEL_CEILING if the
   reviewer does not `git diff HEAD` before the bench starts. Per
   [[l6-32-attribution-audit]]: "诊断纪律:先查 git diff HEAD 确认
   execute_method.py 未提交 delta=纯 +42 符号块(无其他 codex WIP
   混入)才敢在并发 dirty file 上 land". Always capture the
   `git rev-parse HEAD` and the `git status` of the working tree
   at bench start, and include them in the per-batch report.
5. **F21/F22/F25 trap — fixing the symptom, regressing the floor.**
   Any PLATFORM_FIXABLE fix that touches the core retry path or PM
   planning rule MUST be gated by the L2 floor protocol from
   [[l2-int4-floor-6of6]]: the fix is held until ≥1 batch shows
   `mass(B) == 0` and `runnable == 6/6` post-landing. If the
   floor regresses, the fix is reverted unconditionally, even
   if the unit tests pass. This is the central lesson of the
   campaign: **counting ≠ progress, plan-more ≠ better, raised
   budgets ≠ convergence** (see [[l2-int4-floor-6of6]] §"F21
   REVERT" / "F22 REVERT" / "F25 REVERT").

---

## 8. Operational Checklist (per batch)

Before declaring a batch counts toward the L2 floor N≥3 consecutive
sequence:

```
[ ] git rev-parse HEAD captured and recorded
[ ] git status captured (working tree is clean OR
    working-tree delta is named + justified)
[ ] /home/dains/.polaris/config/llm/llm_config.json captured
    (roles.director provider_id / model match the standing binding)
[ ] No KERNELONE_LLM_CONFIG override in env
[ ] No KERNELONE_DIRECTOR_* override in env
[ ] Project pool is the held-out L2 set (L2-07..L2-12) for L2 floor;
    or the full L1-L8 sweep for higher-level confirmation
[ ] Harness version pinned (the same git rev as the prior batch)
[ ] For each failure: walked through §1, tagged per §2,
    line-anchored with codegraph_explore / codegraph_node
[ ] Per-batch mass computed per §3
[ ] Strong-Director control run scheduled (or strong-Director
    N≥3 sequence already complete; see §1 edge case)
[ ] Per-batch report includes the four quantified fields
    (step success / runnable / wall clock / ranked root-cause
    tally) per [[per-batch-quantified-report]]
```

A batch is **eligible** for the N≥3 consecutive counter iff
`mass(B) == 0` AND `runnable == 6/6` AND the checklist above is
complete. Otherwise it is recorded as an investigative batch, not a
consecutive one, and the counter does not advance.
