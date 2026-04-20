# Polaris Stage A Test Plan (Unit/Contract)

## Purpose
Stage A verifies Polaris's core logic, contracts, and safety rules at the unit/contract level without invoking external models or long‑running side effects.

## Scope
In‑scope:
- Pure logic and contract validation (serialization, parsing, merging, limits)
- Tooling orchestration helpers and safety filters
- IO helpers (workspace resolution, JSONL emission, RAMDisk routing)
- Prompt template integrity and required placeholders
- Evidence/trajectory packaging

Out‑of‑scope (Stage B):
- Real Codex/Ollama execution
- Long‑running npm/dev servers
- Port killing / process management
- End‑to‑end flows with real toolchain

## How to run
- Full suite: `python -m pytest -q`
- Single file: `python -m pytest -q tests/<test_file.py>`

## Test Matrix

Component: IO / Workspace
Tests: `tests/test_io_utils_core.py`
Coverage: workspace detection, RAMDisk routing, JSONL event+dialogue emission, stop flags

Component: Policy & Overrides
Tests: `tests/test_policy_merge.py`, `tests/test_director_policy_runtime.py`
Coverage: CLI/env/file overrides, sanitization, source map, policy → state projection, context caps

Component: Tooling Plan / Budget / Ranking
Tests: `tests/test_director_tooling.py`
Coverage: tool CLI args, plan normalization, RG hit ranking heuristics

Component: Exec Utilities
Tests: `tests/test_director_exec_utils.py`
Coverage: npm command filter, tools.py command normalization, patch risk scoring

Component: Evidence & Trajectory
Tests: `tests/test_director_evidence_trajectory.py`
Coverage: evidence summary format, evidence package, trajectory payload

Component: PM / Director Helpers
Tests: `tests/test_loop_pm_utils.py`, `tests/test_loop_director_required_evidence.py`, `tests/test_plan_act_context.py`
Coverage: PM task normalization, required evidence planning, Plan/Act parsing, context caps

Component: Prompt System
Tests: `tests/test_prompt_loader.py`, `tests/test_prompt_templates.py`
Coverage: profile fallback, template rendering, required templates, Plan/Act prompt enforcement

Component: Shared Utilities
Tests: `tests/test_decision_utils.py`, `tests/test_shared_utils.py`
Coverage: decision parsing, ANSI stripping, safe truncate, rate‑limit parsing

Component: Tools CLI (Repo IO)
Tests: `tests/test_tools_repo_io.py`
Coverage: repo_read_* slices and repo_rg hit detection

Component: Ports
Coverage: port policy recommendations and summary formatting (mocked)

Component: External Adapters (Safe Unit Coverage)
Tests: `tests/test_codex_utils.py`, `tests/test_ollama_utils.py`, `tests/test_lancedb_store.py`
Coverage: codex command building, ollama output cleaning, lancedb record shaping

## Test Design Principles
- No external processes or networks (all mocks/stubs)
- Deterministic outputs (no time‑dependent assertions where possible)
- Small fixtures in temp dirs under repo root

## Current Status
- Latest run: `python -m pytest -q`
- Result: 53 passed

## Known Gaps (Intentional, Stage B)
- Real toolchain execution (ruff/mypy/pytest)
- End‑to‑end Director run with actual model
- Gap review vs docs/code inventory realism checks
- Dashboard live process control
