# Polaris Stage B Test Plan (Functional)

## Purpose
Stage B validates real functional behavior across Polaris workflows. These tests exercise the loops end‑to‑end with controlled inputs and (optionally) real tools/models.

## Execution Guard
Default Stage B tests run by default (mocked models, controlled inputs).
Real Ollama integration tests are opt-in:
- Set `POLARIS_OLLAMA_MODEL` to a locally installed model (e.g. `qwen2.5-coder:latest`).
- Optional: `POLARIS_OLLAMA_TIMEOUT` to cap runtime (seconds).

## Current Functional Tests

### Director Flow (Mocked Model, Real Tool Plan)
- File: `tests/functional/test_director_flow.py`
- Behavior:
  - Builds a temp workspace with docs + code
  - Uses required_evidence to trigger repo_read tooling
  - Runs Director iteration end‑to‑end
  - Verifies `DIRECTOR_RESULT.json`, `events.jsonl`, evidence output, and file changes

### PM Loop (Mocked Model)
- File: `tests/functional/test_pm_loop.py`
- Behavior:
  - Runs PM loop once with a fake Ollama response
  - Verifies `pm_tasks.json` and `PM_REPORT.md` outputs

### PM Loop (Real Ollama Smoke Test)
- File: `tests/functional/test_ollama_integration.py`
- Behavior:
  - Runs PM loop once with a real Ollama model
  - Verifies `pm_tasks.json` includes tasks, target_files, and acceptance
  - Skips automatically when `POLARIS_OLLAMA_MODEL` is not set or the model is not installed

## Future Stage B Extensions
- QA toolchain execution (ruff/mypy/pytest)
- Gap review vs docs/code inventory
- Dashboard live controls
- Port conflict behavior with live processes
