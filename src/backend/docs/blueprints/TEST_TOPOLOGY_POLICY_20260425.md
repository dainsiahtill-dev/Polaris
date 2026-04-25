# Test Topology Policy

**Date**: 2026-04-25
**Status**: Proposed
**Scope**: `src/backend` (all test directories)
**Authority**: `AGENTS.md` > This document

---

## 1. Current State Assessment

### 1.1 Test File Inventory (2026-04-25)

| Location | Test Files | Purpose |
|----------|-----------|---------|
| `<repo>/tests/` | 82 | Legacy pre-migration tests (partially broken) |
| `src/backend/tests/` | 356 | Backend centralized: integration, architecture, governance, regression |
| `src/backend/polaris/tests/` | 262 | Cross-system integration, new unit test tree |
| `src/backend/polaris/cells/*/tests/` | 316 | Cell-local contract and behavior tests |
| `src/backend/polaris/kernelone/*/tests/` | 280 | KernelOne module-local tests |
| `src/backend/polaris/delivery/*/tests/` | 24 | Delivery layer collocated tests |
| `src/backend/polaris/infrastructure/*/tests/` | 3 | Infrastructure adapter tests |
| `src/backend/polaris/domain/*/tests/` | 1 | Domain model tests |
| **Total** | **~1,324** | |

### 1.2 Subdirectory Breakdown: `src/backend/tests/`

| Subdirectory | Files | Purpose |
|-------------|-------|---------|
| `tests/` (flat root) | 251 | Mixed: cross-cell regression, smoke, service-level integration |
| `tests/architecture/` | 4 | Architecture governance (catalog, layout, release gates) |
| `tests/architecture/governance/` | 10 | Cell governance fitness rules (boundary, semantic, shim) |
| `tests/integration/delivery/routers/` | 15 | HTTP router integration tests (FastAPI TestClient) |
| `tests/integration/llm/providers/` | 6 | LLM provider integration tests |
| `tests/integration/llm/` | 1 | Provider registry integration |
| `tests/benchmark/` | 1 | Performance latency baseline |
| `tests/agent_stress/` | 5 | Agent stress probes (excluded from default collection) |
| `tests/unit/infrastructure/accel/` | 9 | Infrastructure acceleration unit tests |
| `tests/e2e/` | 2 | CLI end-to-end smoke tests |

### 1.3 Subdirectory Breakdown: `src/backend/polaris/tests/`

| Subdirectory | Files | Purpose |
|-------------|-------|---------|
| `polaris/tests/` (flat root) | 17 | Cross-system integration (error chain, parsers, critical path) |
| `polaris/tests/contextos/` | 5 | ContextOS-specific integration tests |
| `polaris/tests/llm/engine/` | 1 | LLM engine resilience |
| `polaris/tests/orchestration/` | 1 | Workflow engine integration |
| `polaris/tests/performance/` | 1 | Tool performance benchmarks |
| `polaris/tests/unit/` | ~160+ | Growing unit test tree mirroring `polaris/` source structure |

### 1.4 Current Configuration (`pytest.ini`)

- **Import mode**: `importlib` (required to resolve duplicate `test_*.py` names across locations)
- **Markers**: `slow`, `integration`, `unit`, `contract`
- **Async mode**: `asyncio_mode = auto`
- **Excluded from collection**: `tests/agent_stress/`, benchmark fixture workspaces, `scripts/`
- **Conftest hierarchy**: Root conftest at `tests/conftest.py` provides singleton resets, mock LLM infrastructure, env vars, and KernelOne FS adapter injection

### 1.5 Repo-Root `tests/` Status

The `<repo>/tests/` directory (82 files) is **legacy**. Its `conftest.py` explicitly documents this with an extensive `collect_ignore` list of tests that reference removed top-level modules (`app`, `core`, `api`, `domain`, `pm`, `application`). Subdirectories include `anthropomorphic/`, `audit/`, `electron/`, `functional/`, `integration/`, `llm_stress/`, `refactor/`, `unit/`.

Per `CLAUDE.md` section 2/3: "root tests/ are legacy; new tests live in src/backend/tests."

---

## 2. Identified Problems

### 2.1 Five Misplaced Test Files

The following test files exist directly inside source directories without a `tests/` parent:

1. `polaris/cells/roles/kernel/internal/services/test_context_assembler.py` (19.9 KB)
2. `polaris/cells/roles/kernel/internal/services/test_contracts.py` (19.7 KB)
3. `polaris/cells/roles/kernel/internal/services/test_tool_executor.py` (24.8 KB)
4. `polaris/cells/roles/kernel/internal/testing/test_testing_infrastructure.py` (20.9 KB)
5. `polaris/cells/llm/provider_config/internal/test_context.py` (3.9 KB)

**Issue**: These are test files mixed into production source directories. They should reside under a `tests/` subdirectory adjacent to the source they test.

### 2.2 Duplicate Test File Names Across Locations

The following test file names exist in both `tests/` (centralized) and `polaris/cells/*/tests/` (collocated):

- `test_prompt_builder_retry.py` -- `tests/` (1.0 KB) vs `polaris/cells/roles/kernel/tests/` (7.6 KB)
- `test_quality_checker_director_tool_calls.py` -- `tests/` (3.3 KB) vs `polaris/cells/roles/kernel/tests/` (10.5 KB)
- `test_role_kernel_write_budget.py` -- `tests/` (2.1 KB) vs `polaris/cells/roles/kernel/tests/` (7.3 KB)
- `test_output_parser_patch_file.py` -- `tests/` (likely in centralized flat) vs `polaris/cells/roles/kernel/tests/` (8.7 KB)

**Issue**: The centralized versions are generally smaller (likely earlier/simpler versions), while the collocated versions are more comprehensive. This creates confusion about which is authoritative and risks running redundant or contradictory tests. The `importlib` import mode prevents module-name collisions at the Python level, but semantic overlap persists.

### 2.3 Unbounded Flat Test Root

`tests/` has 251 flat top-level test files with no organizational structure. This makes it impossible to determine:
- Which Cell or layer a test belongs to
- Whether a test is unit, integration, or contract level
- Which tests to run after modifying a specific module

### 2.4 Dual Unit Test Trees

Unit tests exist in two separate trees:
- `polaris/tests/unit/` -- New tree mirroring `polaris/` source structure (~160+ files)
- `polaris/*/tests/` -- Collocated with source modules (~624 files)

Both contain legitimate unit-level tests. The `polaris/tests/unit/` tree appears to be a newer initiative that mirrors module paths (e.g., `polaris/tests/unit/kernelone/test_fs_encoding.py`), while collocated tests predate this pattern.

### 2.5 Missing Conftest Files

Several test directories lack `conftest.py`:
- `polaris/tests/` (no root conftest; relies on `tests/conftest.py` up the tree)
- Many `polaris/cells/*/tests/` directories
- `tests/e2e/`
- `tests/unit/`

This means test isolation and fixture sharing are inconsistent.

---

## 3. Canonical Test Topology (Target State)

### 3.1 Four Tiers of Testing

All tests MUST belong to exactly one of the following tiers:

| Tier | Marker | Location | Scope | Runs When |
|------|--------|----------|-------|-----------|
| **T1: Unit** | `@pytest.mark.unit` | Collocated: `polaris/<layer>/<module>/tests/` | Single function/class, no I/O, no network | Every commit |
| **T2: Contract** | `@pytest.mark.contract` | Collocated: `polaris/cells/<cell>/tests/` or `polaris/cells/<cell>/public/tests/` | Validates public contract schemas, types, serialization | Every commit |
| **T3: Integration** | `@pytest.mark.integration` | Centralized: `tests/integration/<layer>/` | Cross-cell, cross-layer, HTTP routers, provider chains | PR gate |
| **T4: Architecture** | (no marker, runs via CI gate) | Centralized: `tests/architecture/` | Governance fitness rules, boundary enforcement, catalog audits | PR gate + scheduled |

Supporting tiers (specialized):

| Tier | Marker | Location | Scope | Runs When |
|------|--------|----------|-------|-----------|
| **T5: Benchmark** | `@pytest.mark.slow` | Centralized: `tests/benchmark/` | Performance latency baselines | Scheduled / manual |
| **T6: E2E** | `@pytest.mark.slow` | Centralized: `tests/e2e/` | Full CLI smoke, server bootstrap | Scheduled / manual |
| **T7: Agent Stress** | (excluded) | Centralized: `tests/agent_stress/` | Infrastructure-only, special setup | Manual |

### 3.2 Placement Rules

#### Rule 1: Unit and Contract Tests are Collocated

Unit and contract tests MUST live adjacent to the source they test, inside a `tests/` directory within the module or Cell boundary.

```
polaris/cells/<cell_group>/<cell_name>/
    public/
        contracts.py
        tests/                          # Contract tests for public API
            test_public_contracts.py
    internal/
        service.py
        tests/                          # Unit tests for internal logic
            test_service.py
    tests/                              # Cell-level integration tests
        test_cell_behavior.py
```

```
polaris/kernelone/<subsystem>/
    module.py
    tests/
        test_module.py
```

**Rationale**: Collocated tests are discoverable by proximity. When a developer modifies `service.py`, they immediately see the adjacent `tests/` directory. This aligns with the Cell-First principle: tests belong to the Cell they verify.

#### Rule 2: Cross-Cell Integration Tests are Centralized

Tests that exercise interactions between two or more Cells, or between a Cell and external infrastructure (HTTP, WebSocket, database), MUST live in the centralized `tests/` tree under a layer-mirroring structure.

```
tests/
    integration/
        delivery/
            routers/
                test_<router_name>.py
        llm/
            providers/
                test_<provider>_provider.py
        orchestration/
            test_<workflow>.py
        cells/
            test_<cell_a>_<cell_b>_interaction.py
```

#### Rule 3: Architecture and Governance Tests are Centralized

Tests that enforce structural invariants (import boundaries, catalog presence, graph alignment, cell manifests) MUST live in `tests/architecture/`.

```
tests/
    architecture/
        governance/
            test_<fitness_rule>.py
        test_<structural_invariant>.py
```

#### Rule 4: No Test Files in Production Source Directories

Test files (`test_*.py`) MUST NOT exist directly inside production source directories (i.e., directories that are not named `tests/`). If a module needs collocated tests, create a `tests/` subdirectory.

**Violation examples** (current state):
- `polaris/cells/roles/kernel/internal/services/test_*.py` -- should be `polaris/cells/roles/kernel/internal/services/tests/test_*.py`
- `polaris/cells/llm/provider_config/internal/test_context.py` -- should be `polaris/cells/llm/provider_config/internal/tests/test_context.py`

#### Rule 5: No Flat Top-Level Dump

New test files MUST NOT be added directly to `tests/` root. They must go into an appropriate subdirectory (`tests/integration/`, `tests/architecture/`, etc.) or be collocated with source under `polaris/`.

Existing flat tests in `tests/` are in migration-debt status and should be progressively relocated.

### 3.3 Naming Conventions

| Convention | Pattern | Example |
|-----------|---------|---------|
| Test file | `test_<module_or_feature>.py` | `test_turn_transaction_controller.py` |
| Test class | `Test<ClassName>` | `TestTurnTransactionController` |
| Test function | `test_<behavior_description>` | `test_single_decision_per_turn` |
| Contract test file | `test_public_contracts.py` or `test_<contract_name>_contracts.py` | `test_public_contracts.py` |
| Architecture test | `test_<fitness_rule_slug>.py` | `test_cell_kernelone_boundary.py` |
| Conftest | `conftest.py` | `conftest.py` |
| Fixture module | `fixtures/` or `test_data/` | `tests/fixtures/mock_workspace/` |

### 3.4 Conftest Hierarchy

```
src/backend/
    tests/
        conftest.py              # Root: singleton resets, mock infra, env vars, FS adapter
        integration/
            conftest.py          # Integration: workspace fixtures, polaris_workspace, mock app
            delivery/
                routers/
                    conftest.py  # Router: FastAPI TestClient factory
            llm/
                providers/
                    conftest.py  # Provider: mock LLM fixtures
        architecture/
            governance/
                conftest.py      # Governance: catalog/graph loading helpers
        benchmark/
            conftest.py          # Benchmark: latency threshold fixtures, timer context managers
    polaris/
        cells/<cell>/tests/
            conftest.py          # Cell-local: mock dependencies for that cell
        kernelone/<subsystem>/tests/
            conftest.py          # Subsystem-local fixtures
```

**Rule**: Each `conftest.py` MUST only provide fixtures relevant to its directory scope. Cross-cutting concerns (singleton resets, env vars, FS adapter) belong in the root `tests/conftest.py` only.

---

## 4. Relationship Between Test Types

### 4.1 Unit Tests vs. Contract Tests

| Aspect | Unit Test | Contract Test |
|--------|-----------|---------------|
| **What it validates** | Internal logic correctness | Public API shape, types, serialization |
| **Accesses** | Internal functions, classes | Only `public/` exports |
| **Mocks** | External dependencies | Minimal (tests the contract itself) |
| **Location** | `<module>/tests/` or `<module>/internal/tests/` | `<cell>/public/tests/` or `<cell>/tests/` |
| **Failure meaning** | Implementation bug | Breaking change in public contract |

### 4.2 Unit Tests vs. Integration Tests

| Aspect | Unit Test | Integration Test |
|--------|-----------|-----------------|
| **What it validates** | Single unit in isolation | Cross-boundary interaction |
| **I/O** | None (mocked) | May use temp files, in-memory DB, mock HTTP |
| **Cell boundary** | Stays within one Cell | Crosses Cell boundaries |
| **Speed** | < 100ms per test | < 5s per test |
| **Location** | Collocated with source | Centralized `tests/integration/` |

### 4.3 Architecture Tests vs. All Others

Architecture tests do NOT test runtime behavior. They validate **structural properties** of the codebase:
- Import boundaries (Cell A does not import Cell B's `internal/`)
- Catalog completeness (every Cell directory has a `cell.yaml`)
- Graph alignment (declared dependencies match actual imports)
- Governance fitness rules

Architecture tests typically use `ast`, `pathlib`, `importlib`, or subprocess calls to governance scripts. They should NEVER instantiate application objects or mock runtime behavior.

---

## 5. Coverage Expectations Per Layer

### 5.1 Minimum Coverage Targets (Progressive)

| Layer | Current (~23.3%) | Phase 1 Target | Phase 2 Target | Phase 3 Target |
|-------|-----------------|----------------|----------------|----------------|
| `polaris/domain/` | Low | 80% | 90% | 95% |
| `polaris/bootstrap/` | Low | 60% | 75% | 85% |
| `polaris/kernelone/` | Low | 40% | 60% | 75% |
| `polaris/cells/` | Low | 35% | 55% | 70% |
| `polaris/delivery/` | Very Low | 30% | 50% | 65% |
| `polaris/infrastructure/` | Low | 40% | 60% | 75% |
| `polaris/application/` | Very Low | 50% | 70% | 85% |

### 5.2 Coverage Rules

1. **Domain layer** has the highest coverage requirement because it contains business rules with zero I/O -- pure functions that are trivially testable.
2. **New code** must ship with tests that cover all non-trivial branches. "Non-trivial" means any branch that handles an error, makes a decision, or transforms data.
3. **Public contracts** must have 100% test coverage for their schema/type definitions.
4. **Coverage measurement** uses `pytest --cov=polaris --cov-report=term-missing` from `src/backend/`.

---

## 6. Migration Plan for Existing Debt

### 6.1 Repo-Root `tests/` (Priority: Low, Freeze)

- **Action**: Freeze. No new tests added here.
- **Existing tests**: Leave as-is. They are progressively excluded via `collect_ignore`.
- **Long-term**: When all tests are migrated or confirmed dead, remove the directory.

### 6.2 Flat Tests in `tests/` Root (Priority: Medium)

- **Action**: Freeze new additions to `tests/` root. All 251 flat files are migration debt.
- **Progressive relocation**: When a flat test is touched for any reason, relocate it:
  - If it tests a single Cell's internals: move to `polaris/cells/<cell>/tests/`
  - If it tests cross-cell interaction: move to `tests/integration/<layer>/`
  - If it tests architecture/governance: move to `tests/architecture/`
- **Naming**: Do not rename during relocation unless there is a name conflict.

### 6.3 Misplaced Test Files (Priority: High)

The five test files in production source directories (section 2.1) should be relocated to a `tests/` subdirectory within their parent module at the next opportunity.

### 6.4 Duplicate Test Files (Priority: Medium)

For each duplicate pair (section 2.2):
1. Compare the two versions
2. The collocated version (typically more comprehensive) is authoritative
3. Merge any unique test cases from the centralized version into the collocated version
4. Delete the centralized duplicate

### 6.5 `polaris/tests/unit/` Consolidation (Priority: Low, Monitor)

The `polaris/tests/unit/` tree is a newer pattern that mirrors source structure. This is an acceptable intermediate state. Long-term, these tests should be progressively moved to collocated positions as Cells stabilize. However, this is lower priority than fixing misplaced files and deduplication.

---

## 7. Pytest Invocation Patterns

### 7.1 Developer Workflow (Minimum Gate)

```bash
# Run unit + contract tests for a specific Cell
pytest polaris/cells/roles/kernel/tests/ -q -m "unit or contract"

# Run all tests related to a specific module
pytest polaris/kernelone/context/tests/ -q
```

### 7.2 PR Gate (CI)

```bash
# All tests except slow and agent_stress
pytest tests/ polaris/ -q -m "not slow" --ignore=tests/agent_stress

# Architecture fitness only
pytest tests/architecture/ -q
```

### 7.3 Integration Gate

```bash
pytest tests/integration/ -q -m integration
```

### 7.4 Full Suite

```bash
pytest -q --cov=polaris --cov-report=term-missing
```

---

## 8. Rules Summary

1. **Unit and contract tests** are collocated in `<module>/tests/` adjacent to source.
2. **Integration tests** (cross-cell, HTTP, provider) go in `tests/integration/<layer>/`.
3. **Architecture tests** go in `tests/architecture/`.
4. **No test files** in production source directories (must be under a `tests/` subdirectory).
5. **No new flat tests** in `tests/` root; use appropriate subdirectories.
6. **Repo-root `tests/`** is frozen; no new tests added.
7. **Duplicates** are resolved by keeping the collocated (more comprehensive) version.
8. **Every test file** should use one of the defined markers: `unit`, `contract`, `integration`, `slow`.
9. **Conftest scope** must be minimal: root conftest for cross-cutting, local conftest for local fixtures.
10. **Import mode** remains `importlib` until all name conflicts are resolved.

---

## 9. Decision Log

| Decision | Rationale |
|----------|-----------|
| Collocate unit/contract tests | Aligns with Cell-First principle (AGENTS.md section 4.2); tests are discoverable by proximity |
| Centralize integration tests | Cross-cell tests have no natural "home" Cell; centralization prevents arbitrary placement |
| Separate architecture from integration | Architecture tests validate structure, not behavior; they use different tools (AST, pathlib) and run at different cadence |
| Freeze repo-root tests/ | Per existing conftest documentation: "root tests/ are legacy; new tests live in src/backend/tests" |
| Keep importlib mode | Required until duplicate test names across collocated and centralized locations are resolved |
| Progressive migration | Moving 251+ flat tests at once would create unrevieable diffs; relocate on touch |

---

## Appendix A: Test File Distribution by Cell (Top 10)

Based on `polaris/cells/*/tests/` collocated test count:

| Cell Path | Test Files |
|----------|-----------|
| `cells/roles/kernel/` (all depths) | ~80+ |
| `cells/roles/engine/` | ~10 |
| `cells/roles/runtime/` | ~8 |
| `cells/roles/session/` | ~4 |
| `cells/roles/profile/` | ~4 |
| `cells/orchestration/pm_dispatch/` | ~5 |
| `cells/llm/evaluation/` | ~6 |
| `cells/director/execution/` | ~2 |
| `cells/director/tasking/` | ~2 |
| `cells/context/catalog/` | ~4 |

The `roles.kernel` Cell has the highest test density, consistent with its role as the transaction execution core.

## Appendix B: Conftest Files (Current)

| Path | Size | Scope |
|------|------|-------|
| `tests/conftest.py` | 13.0 KB | Root: singleton resets, mock infra, env vars |
| `tests/integration/conftest.py` | 10.8 KB | Integration: workspace/mock fixtures |
| `tests/integration/delivery/routers/conftest.py` | 1.6 KB | Router TestClient |
| `tests/integration/llm/providers/conftest.py` | 9.4 KB | Provider mocks |
| `tests/benchmark/conftest.py` | 10.6 KB | Latency thresholds, timers |
| `tests/architecture/governance/conftest.py` | 1.6 KB | Catalog/graph helpers |
| `polaris/tests/orchestration/conftest.py` | 2.6 KB | Workflow fixtures |
| `polaris/tests/performance/conftest.py` | 825 B | Perf fixtures |
| `polaris/cells/roles/kernel/tests/conftest.py` | 13.2 KB | Kernel Cell fixtures |
| `polaris/cells/context/catalog/tests/conftest.py` | 678 B | Catalog fixtures |
| `polaris/kernelone/context/tests/conftest.py` | 12.3 KB | Context subsystem |
| `polaris/kernelone/benchmark/conftest.py` | 6.7 KB | Benchmark fixtures |
| `polaris/kernelone/fs/tests/conftest.py` | 1.1 KB | FS fixtures |
