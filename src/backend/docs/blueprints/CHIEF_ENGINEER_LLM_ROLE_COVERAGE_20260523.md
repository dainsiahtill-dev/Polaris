# Chief Engineer LLM Role Coverage

Date: 2026-05-23

## Finding

The desktop role stack treats PM, Chief Engineer, and Director as separate
layers, but LLM configuration coverage was inconsistent:

- Frontend canonical LLM state initialized PM, Director, QA, and Architect, but
  not Chief Engineer.
- Backend default LLM config initialized PM, Director, QA, and Architect, but
  not Chief Engineer.
- LLM runtime status listed PM, Director, QA, and Architect, but not Chief
  Engineer.
- Visual graph validation already considered Chief Engineer a first-class role,
  creating a mismatch between visual guidance and persisted/default config.

## Root Cause

Chief Engineer was added to role workspaces and visual configuration after the
older LLM config defaults had already been established. Several role lists were
kept as local arrays instead of sharing the same role coverage expectation.

## Fix

- Add `chief_engineer` to backend default LLM role configuration.
- Include `chief_engineer` in LLM runtime status list and role-specific runtime
  status validation.
- Add `chief_engineer` to frontend canonical LLM initial assignments and
  requirements.
- Include `chief_engineer` in visual config assignment validation and summaries.
- Add regression tests for backend default config, runtime status, frontend
  canonical state, and visual config validation.

## Verification

Targeted gates:

- `.venv\\Scripts\\python.exe -m ruff check src/backend/polaris/kernelone/llm/config_store.py src/backend/polaris/delivery/http/routers/llm.py src/backend/polaris/tests/test_llm_phase0_regression.py src/backend/polaris/tests/integration/delivery/routers/test_llm_router.py src/backend/polaris/tests/unit/delivery/http/routers/test_llm_v2.py --fix`
- `.venv\\Scripts\\python.exe -m ruff format src/backend/polaris/kernelone/llm/config_store.py src/backend/polaris/delivery/http/routers/llm.py src/backend/polaris/tests/test_llm_phase0_regression.py src/backend/polaris/tests/integration/delivery/routers/test_llm_router.py src/backend/polaris/tests/unit/delivery/http/routers/test_llm_v2.py`
- `.venv\\Scripts\\python.exe -m mypy src/backend/polaris/kernelone/llm/config_store.py src/backend/polaris/delivery/http/routers/llm.py src/backend/polaris/tests/test_llm_phase0_regression.py src/backend/polaris/tests/integration/delivery/routers/test_llm_router.py src/backend/polaris/tests/unit/delivery/http/routers/test_llm_v2.py`
- `.venv\\Scripts\\python.exe -m pytest src/backend/polaris/tests/test_llm_phase0_regression.py src/backend/polaris/tests/integration/delivery/routers/test_llm_router.py src/backend/polaris/tests/unit/delivery/http/routers/test_llm_v2.py -q`
- `npm run test -- UnifiedLlmDataManagerV2 copySync`
- `npm run typecheck`
- `npm run lint`
