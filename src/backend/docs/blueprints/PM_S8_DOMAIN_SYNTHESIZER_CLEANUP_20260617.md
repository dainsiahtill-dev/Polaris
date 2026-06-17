# PM §8 Domain-Synthesizer Cleanup Blueprint (2026-06-17)

Status: Stage 1 landed (isolation gate). Stages 2-4 pending bench + explicit sign-off.

Authority chain: `src/backend/AGENTS.md` → `docs/AGENT_ARCHITECTURE_STANDARD.md` →
this blueprint (per `CLAUDE.md §4.1` two-phase execution model). This document is the
`Blueprint & Architecture` artifact that precedes the staged removal of the
project-specific code documented below.

---

## 1. Problem statement (the CLAUDE.md §8 violation)

`CLAUDE.md §8` is an iron rule: Polaris is a meta-tool platform and MUST NOT contain
project/business code. The PM quality gate at

    src/backend/polaris/cells/orchestration/pm_planning/internal/task_quality_gate.py

embeds ~300 lines of hardcoded **game** and **card3d** (3D multiplayer card game)
project-domain knowledge. During PM contract quality gating it detects "this looks like
the game/card3d benchmark project" and then **injects project-specific standard-answer
tasks** (engine/world/combat/.../tests for game; client3d/table/.../tests for card3d) with
hardcoded target file paths (`src/engine/game-loop.ts`, `src/client/three-scene.ts`, ...).

This is a **false-hardening signal**: the platform is partly hand-feeding the answer to a
specific benchmark family, which inflates that benchmark and contaminates the
model-ceiling-vs-platform attribution the broader effort depends on.

This increment does **not** delete that code. It **isolates** the entire §8 behavior behind
one default-PRESERVE switch so it is explicit, A/B-testable, and removable in a later
bench-verified increment — without moving the current bench baseline by even one byte.

---

## 2. §8 violation extent (exact map)

All line ranges are pre-edit (HEAD) references inside `task_quality_gate.py`.

### 2.1 Data tables / regexes / maps (inert until a detector calls them)

Lines ~146-475:

- `_GAME_PM_MIN_TASKS`, `_GAME_PM_REQUIRED_DOMAINS`, `_CARD3D_PM_REQUIRED_DOMAINS`
- `_GAME_PM_DOMAIN_SCOPE_PATHS`, `_CARD3D_PM_DOMAIN_SCOPE_PATHS`
- `_CARD3D_PM_TEST_TARGET_FILES`, `_CARD3D_PM_DOMAIN_TARGET_FILES`
- `_CARD3D_PM_DOMAIN_SCOPE_ALIASES`, `_CARD3D_PM_DIRECTORY_SCOPE_DOMAINS`,
  `_CARD3D_PM_DETECTION_CORE_DOMAINS`
- `_GAME_PM_DOMAIN_TITLES`, `_CARD3D_PM_DOMAIN_TITLES`
- Hint regexes: `_GAME_PM_HINT_RE`, `_CARD3D_PM_CORE_HINT_RE`, `_CARD3D_PM_STACK_HINT_RE`,
  `_GAME_PM_FRAGILE_ACCEPTANCE_RE`, `_GAME_PM_FORBIDDEN_DEPENDENCY_POLICY_RE`,
  `_GAME_PM_OFF_DOMAIN_CORE_RE`
- Workspace signal patterns: `_GAME_PM_WORKSPACE_SIGNAL_PATTERNS`,
  `_CARD3D_PM_WORKSPACE_SIGNAL_PATTERNS`
- Contract-string builder `build_card3d_pm_required_domain_contract` (def ~238) and the
  literal game/card3d hard-contract strings in
  `polaris/cells/orchestration/pm_planning/pipeline.py` (`_build_domain_retry_guidance`)
  and `internal/pipeline_ports.py`.

### 2.2 Detectors (THE chokepoint — every branch flows through one of these)

- `should_apply_card3d_pm_domain_contract` (def ~275) — prompt-injection path
  (consumed by `internal/pipeline_ports.py` to bolt the card3d hard contract onto the PM
  system prompt).
- `_is_card3d_pm_contract` (def ~1323) — gate-time card3d classification.
- `_is_game_pm_contract` (def ~1356) — gate-time game classification (defers to card3d).

### 2.3 Synthesizers (guarded by the detectors)

- `_append_missing_card3d_domain_tasks` (def ~1670, body `if not _is_card3d_pm_contract: return 0`)
- `_append_missing_game_domain_tasks` (def ~1754, body `if not _is_game_pm_contract: return 0`)

### 2.4 Detector call sites (every game/card3d behavior branch)

`1357` (`_is_game_pm_contract` defers to card3d), `1434`/`1439`
(`_attach_workspace_game_context_if_needed`), `1677`/`1761` (synthesizer guards),
`2091`/`2092` (`is_card3d_contract` / `is_game_contract` in `evaluate_pm_task_quality`),
`2360`/`2371` (`autofix_pm_contract_for_quality`).

Transitive: the §8 critical-issue strings (`"card3d PM decomposition ..."`,
`"game PM decomposition ..."`, lines ~2254/2258/2262/2270/2274/2278) are emitted **only**
under `if is_card3d_contract:` / `if is_game_contract:` (detector-gated). Those strings are
the trigger for `pipeline.py::_build_domain_retry_guidance`, which is the only place that
reads `_GAME_PM_DOMAIN_SCOPE_PATHS` / `build_card3d_pm_required_domain_contract()` for the
retry prompt. Therefore the retry-guidance path is **transitively** gated by the three
detectors — no bypass.

---

## 3. Chokepoint completeness argument (no bypass)

Claim: gating the three detectors disables 100% of the game/card3d behavior.

- All game/card3d **behavioral** call sites (1357, 1434, 1439, 1677, 1761, 2091, 2092, 2360,
  2371) read a detector result before touching any `_GAME_PM_*` / `_CARD3D_PM_*` table.
- Both **synthesizers** early-return `0` when their detector is False.
- The **prompt-injection** path (`pipeline_ports.py`) only calls
  `build_card3d_pm_required_domain_contract()` when `should_apply_card3d_pm_domain_contract`
  returned True.
- The **retry-guidance** path (`pipeline.py`) only fires when the §8 critical-issue strings
  are present, which only happens under the detector-gated branches.

There is no code path that reaches a `_GAME_PM_*` / `_CARD3D_PM_*` table without first
passing through one of the three detectors. Helper predicates (`_path_matches_game_domain`,
`_card3d_domains_for_task`, etc.) read the tables but are themselves only invoked from inside
detector-gated branches. Hence the three-guard set is complete.

---

## 4. The floor-safe isolation gate (Stage 1 — landed this increment)

### 4.1 Switch

Module-private predicate `_domain_contracts_enabled()` reads the generic env var
`KERNELONE_PM_DOMAIN_CONTRACTS`:

- Unset → `True` (enabled / preserve).
- Any value except an explicit disable token → `True`.
- Explicit disable token `0` / `false` / `no` / `off` (case- and whitespace-insensitive)
  → `False`.

It mirrors the existing fail-closed env-read idiom in the cell (`_domain_text_hints_enabled`)
but inverts the default so the §8-off path must be requested deliberately. The gate itself
adds **no** new business/domain literal — the env name is generic; the disable-token set is
generic boolean vocabulary.

### 4.2 Guards

Four functions get one guard line at the very top:

```python
if not _domain_contracts_enabled():
    return False
```

The three detectors — `should_apply_card3d_pm_domain_contract`, `_is_card3d_pm_contract`,
`_is_game_pm_contract` — plus `_attach_workspace_game_context_if_needed`. The last one is
not itself a classifier (the context key it would set is only ever read by the now-guarded
`_is_card3d_pm_contract`, so it is *inert* on the disable path even without its own guard),
but guarding it makes the disabled path **byte-clean of every game/card3d side effect**
(no workspace planning-hint read, no inert context key, no `game_context_attached` stat
bump) rather than merely injection-free — a cleaner A/B baseline for Stage 2.

### 4.3 Default-identity proof (byte-for-byte with HEAD)

With the env unset, `_domain_contracts_enabled()` returns `True`, so `not True` is `False`
and each guard is skipped — control falls into the original, unchanged body. The guard can
only alter behavior when an explicit disable token is set, which never happens at default.
Therefore at default the three detectors return exactly what HEAD returns, every downstream
branch sees identical detector results, and the produced contracts/critical-issues/synthesized
tasks are byte-identical. The L2-floor and game benches cannot move. Proven by the new test
`test_task_quality_gate_domain_isolation.py` (default-preserve cases) plus the unchanged-green
full `pm_planning` suite (the existing game/card3d tests rely on default-on behavior).

### 4.4 Clean §8 path (disable)

`KERNELONE_PM_DOMAIN_CONTRACTS=0` makes all three detectors return `False`, which:
disables the prompt injection, the gate-time classification, both synthesizers, the
policy-removal/sanitizer branches, and the retry guidance. The platform stops injecting
project-specific answers. This is the lane for the game-bench A/B, the eventual default-flip,
and full deletion.

---

## 5. Staged removal plan

1. **Stage 1 — Isolation gate (this increment, LANDED).** Default-preserve switch +
   three guards + isolation tests + this blueprint. No behavior change at default. No §8
   code deleted.
2. **Stage 2 — Game-bench A/B (codex lane).** Run the game/card3d bench with the env set vs
   unset, and the L2-floor with the env unset (must stay green = default identity). Quantify
   how much of the game/card3d bench score is carried by the §8 injection vs the model. This
   is the measurement that exposes the false-hardening signal.
3. **Stage 3 — Default-flip (requires explicit sign-off).** Only after Stage 2 quantifies the
   delta, flip the default to disabled (clean §8) behind explicit human sign-off. The flip is
   a deliberate attribution decision, not a refactor.
4. **Stage 4 — Deletion.** With the default flipped and benches re-baselined, delete the
   project-specific tables / detectors / synthesizers and their call sites, **keeping the
   generic deterministic quality checks** (prompt-leak detection, action-signal, scope-path
   validity, acceptance-anchor/measurability, dependency-DAG, docs-stage rules — none of which
   are project-specific). The gate predicate `_domain_contracts_enabled()` and the env var are
   removed in this stage once they have no remaining call sites.

---

## 6. Bench-verification protocol (codex lane)

- **Game-bench (A/B):** identical project set, two runs — `KERNELONE_PM_DOMAIN_CONTRACTS`
  unset (A, current) vs `=0` (B, clean). Report per-project qa_passed / runnable rate and the
  A−B delta. A large positive delta is the size of the false-hardening signal.
- **L2-floor (identity guard):** run the L2 held-out set with the env unset and confirm it is
  byte-for-byte the current floor (the gate is a no-op at default). Any movement here is a
  regression in the isolation, not in attribution.
- **Lane:** codex runs the benches; this increment is collision-clear with codex's working
  tree (`task_quality_gate.py` was not touched by codex commit `60a4787d`).

---

## 7. Risks & boundaries

- **False-hardening / attribution risk:** the §8 injection can mask the model-ceiling-vs-
  platform boundary by hand-feeding answers to a specific bench family. The default-flip
  therefore needs explicit sign-off plus the Stage 2 bench delta — never an unattended flip.
- **Floor-safety:** the only risk to the floor is a default-path divergence; §4.3 proves
  there is none, and the test + full-suite green enforce it.
- **Scope discipline:** Stage 4 must delete only the project-specific tables/detectors/
  synthesizers and keep the generic deterministic checks; over-deletion would remove legitimate
  platform quality gating.
- **No new §8 literal:** the isolation gate introduces a generic env name and a generic
  boolean token set only; it does not add any project/business literal.

---

## 8. Testing

- New: `polaris/cells/orchestration/pm_planning/tests/test_task_quality_gate_domain_isolation.py`
  — default-preserve identity, disabled clean path (detectors False + zero synthesis), and the
  env truthiness table.
- Regression: full `pm_planning` suite stays green at default (the existing game/card3d tests
  exercise default-on behavior and must pass unchanged).
- Gates: `ruff check --fix`, `ruff format`, `mypy` (Success), the new test, the full
  `pm_planning` suite, and catalog governance `new_issue_count == 0`.
