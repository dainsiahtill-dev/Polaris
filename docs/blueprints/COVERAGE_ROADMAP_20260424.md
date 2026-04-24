# Coverage Improvement Roadmap

> Generated: 2026-04-24
> Based on: `pytest --cov=src/backend/polaris` baseline and CLAUDE.md architecture snapshot

## 1. Current State

### 1.1 Overall Metrics

| Metric | Value | Source |
|--------|-------|--------|
| Total Python files in `polaris/` | 2,732 | CLAUDE.md 2026-04-24 |
| Tests collected | 13,511 | `pytest --collect-only -q` |
| Test collection errors | 62 | Known import/fixture issues |
| Current coverage | ~16-23.3% | Variable by run scope |
| 0% coverage modules | 390 | delivery: 155, cells: 103, kernelone: 103 |

### 1.2 Coverage by Directory (from `pytest --cov=src/backend/polaris`)

| Directory | Files | Coverage Estimate | Priority |
|-----------|-------|-------------------|----------|
| `polaris/bootstrap/` | 16 | Low | Medium |
| `polaris/delivery/` | 242 | Low (155 at 0%) | **High** |
| `polaris/application/` | 4 | Low | Medium |
| `polaris/domain/` | 44 | Low | Medium |
| `polaris/kernelone/` | 1,068 | Low (103 at 0%) | **High** |
| `polaris/infrastructure/` | 155 | Low (20 at 0%) | Medium |
| `polaris/cells/` | 1,167 | Low (103 at 0%) | **High** |
| `polaris/tests/` | 29 | N/A (test files) | Low |
| `polaris/config/` | 5 | N/A | Low |

### 1.3 Zero-Coverage Hotspots

The following directories have modules with **0% coverage** and should be the first targets:

- `polaris/delivery/http/routers/` - 155 files at 0% (highest ROI for contract tests)
- `polaris/cells/roles/kernel/internal/` - Core turn engine, transaction controller
- `polaris/kernelone/context/` - ContextOS, exploration policy, budget gate
- `polaris/kernelone/workflow/` - Saga engine, activity runner
- `polaris/infrastructure/llm/` - Provider manager, model registry

## 2. Improvement Strategy

### 2.1 Guiding Principles

1. **High value, low complexity first** - Router contract tests and schema validation yield immediate coverage with minimal setup.
2. **Critical path priority** - KernelOne and roles.kernel are the cognitive runtime heart; they must be covered before cosmetic modules.
3. **Fail-closed** - No module is marked "covered" unless its tests pass in CI.
4. **No false confidence** - Coverage percentage is a proxy; behavioural correctness is the real goal.

### 2.2 Phase Breakdown

#### Phase 1: Quick Wins (Week 1-2) — Target: 25%

Focus on `polaris/delivery/http/routers/` contract tests.

| Module | Test File Pattern | Estimated Lines |
|--------|-------------------|-----------------|
| `interview.py` | `tests/integration/delivery/routers/test_interview_router.py` | 150 |
| `llm_models.py` | Schema validation in same file | 80 |
| `agents.py` | `test_agent_router.py` | 200 |
| `cognitive_runtime.py` | `test_cognitive_runtime_router.py` | 180 |
| `memory.py` | `test_memory_router.py` | 120 |
| `pm_chat.py` | `test_pm_chat_router.py` | 150 |

**Deliverable**: All delivery routers have at least "happy path + 422" contract tests.

#### Phase 2: KernelOne Core (Week 3-6) — Target: 35%

Focus on `polaris/kernelone/` modules that are stable and have public contracts.

| Module | Rationale | Test Strategy |
|--------|-----------|---------------|
| `kernelone/context/exploration_policy.py` | Canonical code exploration | Unit tests for phase gates |
| `kernelone/context/budget_gate.py` | Token budget enforcement | Unit tests for 80% threshold |
| `kernelone/context/working_set.py` | WorkingSetAssembler | Unit tests for budget tracking |
| `kernelone/security/dangerous_patterns.py` | Security canonical source | Pattern matching tests |
| `kernelone/storage/io_paths.py` | Path resolution canonical source | Path resolution tests |
| `kernelone/events/` | Event publishing contracts | Event emission tests |
| `kernelone/llm/shared_contracts.py` | LLM contract parity | Re-export parity tests |

**Deliverable**: KernelOne core modules have >60% coverage.

#### Phase 3: Cells Critical Path (Week 7-10) — Target: 50%

Focus on `polaris/cells/` business logic.

| Cell | Module | Rationale |
|------|--------|-----------|
| `roles.kernel` | `turn_transaction_controller.py` | Turn engine heart |
| `roles.kernel` | `tool_batch_executor.py` | Tool execution |
| `roles.kernel` | `stream_shadow_engine.py` | Stream shadow |
| `runtime.task_market` | `internal/service.py` | Business broker |
| `events.fact_stream` | `public/contracts.py` | Fact stream contracts |
| `llm.evaluation` | `public/service.py` | Interview/test services |

**Deliverable**: Critical Cells have >50% coverage; 0% modules eliminated.

#### Phase 4: Infrastructure & Integration (Week 11-12) — Target: 55%

Focus on `polaris/infrastructure/` and cross-cutting concerns.

| Module | Rationale |
|--------|-----------|
| `infrastructure/llm/providers/` | Provider manager, registry |
| `infrastructure/messaging/` | NATS/JetStream abstractions |
| `infrastructure/log_pipeline/` | Log writing, fanout |
| `infrastructure/di/` | Dependency injection container |

**Deliverable**: Infrastructure modules have >40% coverage.

## 3. Milestones

| Month | Target Coverage | Key Deliverables |
|-------|-----------------|------------------|
| Month 1 (Apr) | 23.3% -> 25% | Delivery router contract tests complete |
| Month 2 (May) | 25% -> 35% | KernelOne core modules >60% covered |
| Month 3 (Jun) | 35% -> 50% | Critical Cells >50%; 0% modules eliminated |
| Month 4 (Jul) | 50% -> 55% | Infrastructure >40%; integration tests stable |

## 4. Responsibility Allocation

| Squad | Scope | Primary Directories |
|-------|-------|---------------------|
| Squad P (Audit) | Delivery routers, fitness rules, dashboard | `polaris/delivery/http/routers/` |
| Squad K (KernelOne) | KernelOne core, context subsystem | `polaris/kernelone/` |
| Squad C (Cells) | Business cells, runtime, roles | `polaris/cells/` |
| Squad I (Infra) | Infrastructure, messaging, providers | `polaris/infrastructure/` |

## 5. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Import errors block test collection | High | Fix syntax/import errors before writing new tests |
| Flaky integration tests | Medium | Use TestClient + mocks; avoid real NATS/DB |
| Legacy internal modules without public contracts | Medium | Add public contracts first, then test |
| Coverage inflation via trivial tests | Low | Require behavioural assertions, not just execution |

## 6. Verification

Run the following commands to validate progress:

```bash
# Full coverage report
python -m pytest --cov=src/backend/polaris --cov-report=term-missing -q

# Router contract tests only
python -m pytest tests/integration/delivery/routers/ -v

# KernelOne release gate
python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all

# Dashboard refresh
python scripts/engineering_dashboard.py
```

---
*Generated by Squad P as part of Audit Remediation 2026-04-24*
