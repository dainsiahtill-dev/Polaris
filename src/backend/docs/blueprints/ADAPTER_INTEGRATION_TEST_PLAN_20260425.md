# Adapter Integration Test Plan (Squad BB)

**Date**: 2026-04-25
**Scope**: `polaris/cells/roles/adapters/` heavy I/O adapter modules
**Status**: Unit tests delivered; integration test plan documented

---

## 1. Summary

This document records the mock-isolated unit test coverage delivered for the
`polaris/cells/roles/adapters/` adapter modules, and identifies the remaining
I/O-bound integration surfaces that require integration-level testing.

### 1.1 Modules Covered by New Unit Tests

| Test File | Target Module | Tests | Coverage Focus |
|-----------|---------------|-------|----------------|
| `test_director_helpers_pure.py` | `director/helpers.py` | 67 | Config resolution, error detection, content preview, tool extraction, task coercion |
| `test_director_state_tracking_pure.py` | `director/state_tracking.py` | 21 | QA state derivation, task description sanitization, taskboard reference building |
| `test_director_state_utils_pure.py` | `director/state_utils.py` | 37 | Output requirements, domain tokens, projection slug/requirement composition |
| `test_director_execution_backend_pure.py` | `director_execution_backend.py` | 37 | Normalization helpers, backend resolution precedence, dataclass behavior |
| `test_base_adapter_pure.py` | `base.py` | 22 | Task ID coercion, kernel validation/retry resolution, trace type mapping |

**Total new tests**: 184
**Adapter tests cumulative total**: 443 (was 259)

### 1.2 Pre-Existing Unit Tests (Squad AM)

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_director_adapter_pure.py` | 28 | Strategy selection, intelligent correction, message building, metadata, backend |
| `test_director_adapter_unit.py` | 19 | Base adapter retry budget, validation, task trace seq |
| `test_pm_adapter_pure.py` | 67 | Message building, JSON extraction, task normalization, complexity analysis |
| `test_qa_adapter_pure.py` | 49 | Review parsing, merging, finalization, semantic equivalence, regression detection |
| `test_director_patch_executor_pure.py` | 28 | Timeout resolution, tool argument normalization, markdown block extraction |

---

## 2. Integration Surfaces Requiring Integration Tests

The following I/O-bound paths cannot be adequately tested with mock-isolated
unit tests and require integration tests with real (or Dockerized/testcontainer)
external dependencies.

### 2.1 LLM Invocation Layer

**Files**: `pm_adapter.py`, `qa_adapter.py`, `director/adapter.py`

| Method | I/O Surface | Integration Strategy |
|--------|-------------|---------------------|
| `PMAdapter._run_pm_stage` | `generate_role_response` | Mock LLM provider or VCR-recorded responses |
| `QAAdapter.execute` | `generate_role_response` | VCR-recorded LLM responses with deterministic prompts |
| `DirectorAdapter._call_role_llm` | `generate_role_response` | Stub LLM provider returning canned tool-call responses |
| `DirectorAdapter._execute_sequential` | Engine integration | Requires full director runtime + mock LLM |
| `DirectorAdapter._execute_hybrid` | Engine integration | Requires full director runtime + mock LLM |

**Recommended approach**:
- Use `pytest-vcr` or similar to record/replay LLM HTTP traffic
- Create a `MockLLMProvider` that returns deterministic responses based on prompt hashes
- Test the full `execute()` flow with mocked provider, real task board (SQLite in-memory), and temp workspace

### 2.2 Task Board / TaskRuntimeService

**Files**: `base.py`, `pm_adapter.py`, `qa_adapter.py`, `director/adapter.py`

| Method | I/O Surface | Integration Strategy |
|--------|-------------|---------------------|
| `BaseRoleAdapter.task_runtime` | `TaskRuntimeService` lazy init | In-memory SQLite task board |
| `BaseRoleAdapter._board_task_exists` | `task_exists` | In-memory SQLite task board |
| `BaseRoleAdapter._update_board_task` | `update_task` | In-memory SQLite task board |
| `PMAdapter._create_board_tasks` | `create`, `update_task` | In-memory SQLite task board |
| `DirectorAdapter.execute` | Task board reads/writes | In-memory SQLite task board + mock LLM |

**Recommended approach**:
- Initialize `TaskRuntimeService` with a temporary workspace and in-memory SQLite
- Use `tmp_path` fixture for workspace isolation
- Verify task board state transitions after adapter execution

### 2.3 Filesystem Operations

**Files**: `pm_adapter.py`, `qa_adapter.py`, `base.py`, `director/state_tracking.py`

| Method | I/O Surface | Integration Strategy |
|--------|-------------|---------------------|
| `PMAdapter._write_plan_artifact` | `write_text_atomic` | `tmp_path` workspace |
| `QAAdapter._write_qa_report` | `write_text_atomic` | `tmp_path` workspace |
| `BaseRoleAdapter._append_runtime_stage_signals` | `resolve_signal_path`, `write_text_atomic` | `tmp_path` workspace |
| `DirectorStateTracker.collect_workspace_code_files` | `rglob`, `path.stat`, `read_bytes` | `tmp_path` workspace with seeded files |
| `DirectorStateTracker.append_debug_event` | `mkdir`, `open_text_log_append` | `tmp_path` workspace |

**Recommended approach**:
- All filesystem tests use `tmp_path` pytest fixture
- Seed workspace with known file tree before test
- Assert on file contents after operation

### 2.4 Subprocess / Command Execution

**Files**: `qa_adapter.py`, `base.py`

| Method | I/O Surface | Integration Strategy |
|--------|-------------|---------------------|
| `QAAdapter._verify_test_execution` | `subprocess.run` (pytest) | Requires real Python environment with test files |
| `BaseRoleAdapter._count_file_changes` | `CommandExecutionService` (git diff) | Requires git repo with staged changes |

**Recommended approach**:
- For QA test execution: create a temp workspace with a minimal pytest suite, run it, assert on results
- For git diff: initialize a git repo in `tmp_path`, make commits, create changes, assert stats

### 2.5 Projection Backend Integration

**Files**: `director_execution_backend.py`

| Method | I/O Surface | Integration Strategy |
|--------|-------------|---------------------|
| `DirectorProjectionBackendRunner.execute` | `FactoryProjectionLabService`, `ProjectionChangeAnalysisService` | Requires factory pipeline Cell to be available |
| `DirectorProjectionBackendRunner._run_projection_generate` | Full projection lab service | Integration test with mock factory services |
| `DirectorProjectionBackendRunner._run_projection_refresh_mapping` | Back-mapping service | Integration test with mock factory services |
| `DirectorProjectionBackendRunner._run_projection_reproject` | Reproject service | Integration test with mock factory services |

**Recommended approach**:
- Mock `FactoryProjectionLabService` and `ProjectionChangeAnalysisService` at the module level
- Verify correct command objects are passed to services
- Test error paths (missing scenario_id, missing experiment_id)

---

## 3. Proposed Integration Test File Structure

```
polaris/cells/roles/adapters/tests/integration/
  test_pm_adapter_integration.py      # PMAdapter.execute with mock LLM + task board
  test_qa_adapter_integration.py      # QAAdapter.execute with mock LLM + filesystem
  test_director_adapter_integration.py # DirectorAdapter.execute with mock LLM + task board
  test_projection_backend_integration.py # DirectorProjectionBackendRunner with mock factory
  test_state_tracker_integration.py   # DirectorStateTracker filesystem operations
```

---

## 4. Test Data Fixtures Needed

### 4.1 LLM Response Fixtures

```python
# fixtures/llm_responses.py
PM_PLAN_RESPONSE = {
    "content": '{"tasks": [{"subject": "Implement login", "priority": "high"}]}',
    "tool_calls": [],
    "error": "",
}

DIRECTOR_PATCH_RESPONSE = {
    "content": "",
    "tool_calls": [
        {"tool": "write_file", "success": True, "result": {"file": "main.py"}}
    ],
    "error": "",
}

QA_REVIEW_RESPONSE = {
    "content": '{"verdict": "PASS", "score": 95, "critical_issues": []}',
    "tool_calls": [],
    "error": "",
}
```

### 4.2 Workspace Fixtures

```python
# fixtures/workspaces.py
MINIMAL_PYTHON_PROJECT = {
    "main.py": "def main(): pass\n",
    "tests/test_main.py": "def test_main(): pass\n",
    "pyproject.toml": "[project]\nname = 'demo'\n",
}
```

---

## 5. Risks and Dependencies

1. **LLM non-determinism**: Integration tests must use recorded responses or mock providers; never hit real LLM APIs in CI
2. **Task board schema drift**: `TaskRuntimeService` schema changes will break integration tests; keep schema validation layer
3. **Factory pipeline availability**: Projection backend tests depend on factory Cell being importable; may need `pytest.importorskip`
4. **Git dependency**: `_count_file_changes` tests require git CLI; skip on environments without git
5. **Cross-platform paths**: Windows path separators in assertions; use `Path.as_posix()` for comparisons

---

## 6. Deliverables Checklist

- [x] Analyze adapter code structure and identify pure vs I/O-bound logic
- [x] Write mock-isolated unit tests for `director/helpers.py`
- [x] Write mock-isolated unit tests for `director/state_tracking.py`
- [x] Write mock-isolated unit tests for `director/state_utils.py`
- [x] Write mock-isolated unit tests for `director_execution_backend.py`
- [x] Write mock-isolated unit tests for `base.py` static methods
- [x] Pass `ruff check --fix`
- [x] Pass `ruff format`
- [x] Pass `mypy`
- [x] Pass `pytest -q` (all 443 adapter tests green)
- [ ] Integration test implementation (deferred to future sprint)
