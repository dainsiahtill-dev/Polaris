# Artifact Lab & Self-Inspection Blueprint — 2026-11-17

Status: Approved for prompt-level rollout
Source research: `几猫国产3A大作_V1.html` (1.4MB single-file Three.js game, fully
code-generated assets, built autonomously by a frontier coding model)

## 1. What was researched

A single-file browser game whose every texture, decal, logo, sky, and material
is *generated code* (GLSL shader library + seeded canvas-2D decal factory —
zero binary assets). The build process was visibly staged: the model developed
each subsystem in an isolated "lab" mode and only then assembled the whole.
The URL surface exposes that laboratory (`?show=mats|enemies|fx|weapon|level|…`,
`?eshow=fight|dead`, `?seed=`, `?dt=`, `#bench`).

## 2. Core methodology observed (evidence in the artifact)

1. **Context-injected module registry.** One shared context object holds
   params, a seeded RNG, an event bus, and a time base; every subsystem is a
   factory that attaches itself (`ctx.render`, `ctx.mats`, `ctx.enemies`, …)
   and exposes a uniform `update(dt)`. No import graph: modules meet only via
   the context and named events. A module crash is caught per-update and
   logged once — the loop never dies.
2. **Per-module lab entry (the key habit).** Every subsystem ships a
   `showcase(ctx)`: it boots *alone* against a stub world (`level={stub:true}`,
   flat ground, silent audio, no gameplay), poses a deterministic camera
   (`{cam, target}`), optionally pre-runs N simulation frames, and can freeze
   (`efreeze`). The agent can therefore *look at one material, one enemy, one
   weapon* in isolation, fix it, and only then integrate.
3. **Staged boot with observable progress and fallback.** Assembly is an
   explicit ordered pipeline with progress labels and per-stage timing logs.
   Each stage has a fallback factory (`dr[name]()`); a failed module degrades
   to a stub instead of aborting the build. Quality tiers hot-apply.
4. **Deterministic harness.** All randomness derives from one seed; fixed
   timestep override; scenario pre-run + freeze for reproducible inspection;
   scriptable camera (`__setShot`) for screenshot/regression flows.
5. **Self-observability of the artifact.** `window.__errors[]` (console.error
   intercepted), `__ready`, `__fatal`, `__stats()`, frame counter. The
   artifact reports its own health to the reviewer.
6. **Built-in performance bench.** `#bench` runs an automated camera
   flythrough and reports mean/p99/median frame times on-screen — the artifact
   self-measures without external tooling.
7. **All-code assets.** Textures/materials are parameterized functions
   (shader graph + canvas painters) driven by the seeded RNG — deterministic,
   inspectable, editable as code.

## 3. Why this matters for Polaris

Polaris Director runs often fail on interactive/visual deliverables because
verification is monolithic ("build + test + one screenshot at the end"). The
researched pattern converts verification into **many small, deterministic,
module-level inspections the agent can perform while building** — exactly the
"lab-then-assemble" loop. It also gives QA *physical health evidence* from the
running artifact instead of trusting source text.

This is a prompt/playbook extraction, not a new runtime dependency: the
platform gains a methodology contract; the artifact conventions are plain
web-platform facts (URL params, window globals) that browser_observe can
already drive.

## 4. Rollout

### 4.1 Director playbook (engineering_roles.py `_PROMPTS["director"]`)

Add the staged-lab playbook:

- For multi-module or interactive/visual deliverables: structure the build as
  modules over a shared context/event bus; assemble in a staged boot where one
  module's failure cannot crash the whole (fallback stubs).
- Give each interactive or visual module an isolated lab entry
  (`?lab=<module>` or equivalent URL param / CLI flag) that boots it alone
  with a stub world and a posed, reproducible state; inspect it there before
  integration.
- Seed all randomness; support a fixed-step mode so a scenario can be
  pre-run and frozen for inspection.
- Expose self-observability from the artifact: a runtime-error capture list,
  a ready flag, and a stats/health hook.
- Only assemble after each module passes its own lab; then run the end-to-end
  smoke.

### 4.2 QA checklist (engineering_roles.py `_PROMPTS["qa"]`)

When the artifact exposes self-observability hooks, physically check them via
browser_observe/execute_command (error list empty, ready flag set, per-module
lab renders) in addition to source and tests.

### 4.3 Explicit non-goals

- No new platform cells, tools, or runtime dependencies.
- The conventions are advisory playbook text; deterministic catalog checks
  remain the frozen quality floor.

## 5. Generalization to all project archetypes (2026-11-17)

The lab methodology is archetype-agnostic; only the lab entry *shape* varies:

| Archetype | Lab entry shape | Stub world | Health hook |
|---|---|---|---|
| Web / interactive page | URL param `?lab=<module>` (+ posed camera) | stub scene/audio/physics | `window.__errors`, `__ready`, `__stats` |
| CLI tool | `lab <module>` subcommand / `--lab <module>` flag | stub I/O + seeded fixtures | `--health`/`--self-check` prints `{ready, errors}` |
| API service | dev-only `GET /__lab/<module>` | stub dependency graph + fixtures | `/__health` returns `{ready, errors}` |
| Library / package | `python -m <pkg>.lab <module>` or `npm run lab -- <module>` | stub deps + fixtures | `self_check()` function |
| Compiled binary (Rust/C/Go) | `lab` subcommand | stub deps + fixtures | `health`/`self-check` subcommand |

Universal contract: shared context/event bus; staged boot with fallback;
per-module lab with stub deps + deterministic fixtures; seeded randomness;
self-observability; self-bench. Assemble only after each module's lab passes.
