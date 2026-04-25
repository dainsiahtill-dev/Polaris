# README Information Architecture Blueprint

**Date**: 2026-04-25  
**Status**: Active  
**Scope**: `README.md`  
**Owner**: Principal Architect

## 1. Goal

Rewrite the repository root `README.md` so that a new developer can answer the following questions in under five minutes:

1. What is Polaris?
2. What is the current maturity and scope?
3. How is the repository organized?
4. What are the canonical entrypoints?
5. How do I run the desktop app, backend, tests, and quality gates?
6. Where do I read deeper architectural truth after the initial overview?

## 2. Current Problems

The current README is difficult for first-time readers because:

1. It leads with metaphor, philosophy, and moat language instead of concrete orientation.
2. The first practical startup information appears too late.
3. It mixes target-state ambition with current-state facts.
4. It makes strong claims that are not the best on-ramp for evaluating a large alpha codebase.
5. It under-explains canonical versus compatibility entrypoints.

## 3. Target Reader Model

The new README serves three primary readers:

1. Evaluator
   Wants to know what Polaris is and whether it is worth reading further.
2. Contributor
   Wants the repo shape, canonical paths, and startup commands.
3. Maintainer
   Wants the root README to match actual code layout and point to deeper truth sources.

## 4. Content Strategy

The README must:

1. Start with plain-language positioning.
2. State current maturity honestly.
3. Introduce four or five core concepts only.
4. Show the real repository layout.
5. Prefer canonical entrypoints but mention compatibility shims where they still exist.
6. Use engineering terms first, not metaphor-first language.
7. Keep ambition, but subordinate it to clarity.

The README must not:

1. Present the project as fully converged when it is still in architectural migration.
2. Lead with internal metaphors or role mythology.
3. Overload the first page with a long moat catalog.
4. Hide real commands behind conceptual language.

The README should additionally:

1. explain the project's technical advantages in concrete engineering terms
2. separate current strengths from future direction
3. keep ambition visible without presenting roadmap items as already completed facts
4. include a fast architecture visual suitable for GitHub landing-page reading
5. include a 30-minute guided onboarding path
6. provide an English README for external readers

## 5. Planned README Structure

```text
1. Title + one-sentence positioning
2. What Polaris is
3. Current status
4. Core concepts
5. Core advantages
6. Architecture overview diagram
7. Repository layout
8. Quick start
9. 30-minute onboarding path
10. Testing and quality gates
11. Future direction
12. Where to read next
13. Optional support/license footer
```

## 6. Technical Truth Sources

The rewrite should align with:

1. `pyproject.toml`
2. `package.json`
3. `src/backend/AGENTS.md`
4. `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`
5. `src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md`
6. `docs/TERMINOLOGY.md`
7. actual canonical backend entrypoint at `src/backend/polaris/delivery/server.py`
8. compatibility shim at `src/backend/server.py`
9. current workflow definitions in `.github/workflows/*.yml`

## 7. Key Messaging Decisions

### 7.1 Positioning

Use:

- "AI agent governance and runtime platform"
- "transaction-governed runtime"
- "KernelOne as substrate"
- "Cells as bounded capability units"

Avoid as leading message:

- "cognitive lifeform"
- "唐朝官制"
- "全球唯一"
- other unverifiable moat language

### 7.2 Status

The README should explicitly say:

1. the project is alpha
2. architecture convergence is still in progress
3. `src/backend/polaris/` is the canonical backend root
4. some compatibility shims still exist for old entrypoints

### 7.3 Commands

Use commands that correspond to real scripts and files:

1. `npm run setup:dev`
2. `npm run dev`
3. `polaris --host 127.0.0.1 --port 49977`
4. `python src/backend/server.py --host 127.0.0.1 --port 49977` as compatibility fallback
5. `pm --workspace . --start-from pm`
6. `director --workspace . --iterations 1`
7. `npm test`
8. `npm run test:e2e`
9. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py`

### 7.4 Supporting assets

The README package should include:

1. `README.md` in Chinese as the primary in-repo orientation
2. `README.en.md` for GitHub and external evaluators
3. a small SVG architecture overview under `docs/assets/diagrams/`
4. a concise onboarding path that can be followed in about 30 minutes

## 8. Acceptance Criteria

The rewrite is complete when:

1. a new reader can locate canonical backend/frontend entrypoints without scanning the repo
2. the README no longer depends on long metaphor-heavy sections to explain the project
3. the status section distinguishes current facts from long-term ambition
4. the quick-start commands are grounded in actual repo scripts
5. deeper docs are linked as next reads instead of being duplicated
