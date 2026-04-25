# Principal Architect 17-Expert Full Repo Execution Blueprint

**Date**: 2026-04-25  
**Status**: Active  
**Scope**: `C:\Users\dains\Documents\GitLab\polaris`  
**Owner**: Principal Architect + 17 expert workstreams  

## 1. Objective

This blueprint defines the full-repo execution plan to bring Polaris onto a stable, auditable, rollbackable, graph-constrained architecture baseline.

The objective is not incremental patching. The objective is to restore a single architectural spine across:

- `delivery -> application -> domain/kernelone`
- `cells` as the only business capability boundary
- `kernelone` as a platform-neutral Agent runtime substrate
- `ContextOS` and `TransactionKernel` as authoritative runtime truth boundaries
- graph, manifests, packs, tests, and CI as executable governance

This plan follows the mandatory two-phase model:

1. Blueprint and architecture first
2. Execution and implementation second

## 2. Current Truth Summary

The repository already contains the right target architecture on paper, but the implementation has structural drift in the highest-risk paths.

### 2.1 Confirmed structural fractures

1. The normative spine is clear, but the runtime spine is not.
   `application/` is too thin, while `delivery/` bypasses it and directly orchestrates Cells and runtime behavior.
2. `KernelOne` is not fully pure.
   Reverse dependencies, business-role semantics, and raw path writes have already entered the substrate.
3. `TransactionKernel` is not yet a universal single commit point.
   `run` and `stream` still diverge in handoff and durable commit semantics.
4. `ContextOS` is not fully authoritative as a four-layer truth model.
   `TruthLog` and `WorkingState` still admit side channels that can create a second truth path.
5. `graph -> manifest -> code` has drifted.
   Cell manifests are not consistently parseable or canonical, public/internal fences are widely broken, and governance packs are stale or missing.
6. Quality gates exist, but not all of them are hard blockers in practice.
   This weakens the credibility of the system's engineering contract.

### 2.2 Full-repo baseline

- Total files: about `5,594`
- Main backend code concentrated in `src/backend/polaris/`
- Largest subtrees:
  - `src/backend/polaris/cells` => `1,720` files
  - `src/backend/polaris/kernelone` => `1,195` files
  - `src/backend/polaris/delivery` => `256` files
- Test topology is split across:
  - `tests`
  - `src/backend/tests`
  - `src/backend/polaris/tests`

This repo is already large enough that the only viable path is governed parallel execution, not hero refactors.

## 3. Target Architecture

### 3.1 Text architecture diagram

```text
Frontend / Electron / External CLI
    ->
delivery/
    - HTTP routers
    - WebSocket endpoints
    - CLI entrypoints
    - transport auth and serialization only
    ->
application/
    - use-case orchestration
    - transaction boundary selection
    - runtime admin workflows
    - application commands and queries
    ->
domain/                     kernelone/
    - business rules            - agent runtime substrate
    - value objects             - context runtime
    - policies                  - execution substrate
    - domain ports              - effect contracts
                                - storage layout / KFS
                                - platform-neutral events
    ->
cells/
    - public contracts only across Cell boundaries
    - internal implementation hidden behind public surface
    - single state owner per truth domain
    ->
infrastructure/
    - DB adapters
    - messaging adapters
    - telemetry adapters
    - plugin adapters
    - external SDK bindings

Cross-cutting governance plane:
    docs/graph/catalog/cells.yaml
    cell.yaml
    descriptor/context/verify packs
    ADRs
    release gates
    CI hard blockers

Cross-cutting runtime truth plane:
    TransactionKernel
        ->
    ContextOS
        -> TruthLog
        -> WorkingState
        -> ReceiptStore
        -> ProjectionEngine
        ->
    structured events / audit evidence / receipts
```

### 3.2 Module responsibilities

| Module | Responsibility | Must Not Do |
|---|---|---|
| `bootstrap/` | Compose runtime, lifecycle, adapters | Carry business logic |
| `delivery/` | Parse requests, auth, serialize responses, map contracts | Orchestrate PM/Director loops, own durable state, import `cells.*.internal` |
| `application/` | Own use-case orchestration, retries, idempotency, runtime admin flows | Depend on transport details or concrete adapters |
| `domain/` | Own business rules, value objects, domain policies | Host framework semantics |
| `kernelone/` | Own platform-neutral Agent runtime capabilities | Import `cells/domain/application/delivery/infrastructure` |
| `cells/` | Own bounded capabilities and public contracts | Expose internal implementation as public API |
| `infrastructure/` | Bind storage, messaging, DB, telemetry, plugins | Become a second application layer |
| `tests/` | Verify contract, runtime, architecture, regression | Carry production logic |

## 4. Core Data Flows

### 4.1 Request-to-effect flow

```text
Frontend/Electron/CLI
    -> delivery router or CLI parser
    -> application service / workflow
    -> Cell public contract
    -> kernelone effect contract
    -> infrastructure adapter
    -> structured event + receipt
    -> delivery response / stream / UI projection
```

### 4.2 Agent turn flow

```text
RoleSessionOrchestrator
    -> TransactionKernel
    -> TurnDecision
    -> optional ToolBatchExecutor / SpeculativeExecutor
    -> ContextOS durable commit
    -> ContextHandoffPack
    -> runtime continuation / workflow handoff
    -> CompletionEvent + monitoring + audit evidence
```

### 4.3 Context truth flow

```text
turn inputs / tool results / runtime facts
    -> TruthLog append
    -> WorkingState update
    -> large payload offload to ReceiptStore
    -> ProjectionEngine builds read-only prompt view
    -> LLM request assembly
```

### 4.4 Governance flow

```text
source code + cell manifests
    -> catalog reconciliation
    -> descriptor/context/verify pack freshness
    -> architecture + dependency gates
    -> ruff / mypy / pytest / release gates
    -> audit reports + remediation evidence
```

## 5. Technical Choices and Rationale

### 5.1 Why keep the current architectural direction

The target direction is correct. The problem is convergence discipline, not architectural ambition.

1. `Cells` remain the right bounded-context unit.
   The repo is too large for folder-based discipline alone.
2. `KernelOne` remains the right substrate abstraction.
   The system needs a reusable Agent runtime layer, but it must be purified.
3. `TransactionKernel` remains the right turn execution model.
   Hidden continuation and multi-commit turn semantics are unacceptable at this scale.
4. `ContextOS` remains the right context architecture.
   Plain message-history prompts are insufficient for long-lived, auditable Agent execution.
5. `Ruff + mypy + pytest + release gates` remain the correct engineering baseline.
   The issue is enforcement strength, not tool choice.
6. `NATS/JetStream + structured events + evidence packages` remain the correct observability direction.
   The issue is schema discipline and complete event truth, not messaging choice.

### 5.2 Why no new framework

No new framework is introduced in this plan.

Reasons:

1. The repository already contains the target abstractions.
2. Introducing another orchestration or dependency framework would create a fourth truth.
3. The work needed now is convergence, fence hardening, and runtime truth recovery.

## 6. Seventeen-Expert Execution Model

### 6.1 Command model

The execution model uses 17 expert workstreams:

1. Principal Architect: owns global truth, phase gates, and cross-stream arbitration
2. Architecture Spine Lead: repairs `delivery -> application -> domain/kernelone`
3. KernelOne Purity Lead: removes reverse dependencies and business semantic leakage
4. TransactionKernel Lead: converges single commit and canonical handoff
5. ContextOS Lead: restores four-layer authority and prompt-plane isolation
6. Cell Governance Lead: reconciles catalog, manifest, and public/internal fences
7. Application/Domain Lead: rebuilds orchestration and domain boundary ownership
8. Infrastructure Lead: normalizes adapters, outbound ports, and mapping rules
9. Delivery Refactor Lead: thins HTTP/CLI/WS and eliminates transport-side orchestration
10. Quality Gate Lead: makes CI and release gates truly blocking
11. Test Topology Lead: unifies test placement and regression policy
12. Observability Lead: unifies event facts, evidence, monitoring, and receipts
13. Security and Effects Lead: hardens explicit effects, KFS, paths, and dangerous operations
14. Frontend/Electron Lead: aligns UI and runtime contracts, E2E flows, and workspace UX
15. Multi-Agent Runtime Lead: aligns PM/Architect/Chief Engineer/Director/QA with task market and execution broker
16. Cognitive Runtime Lead: keeps authority boundaries clean versus KernelOne and ContextOS
17. Migration and Documentation Truth Lead: removes legacy path ambiguity, shim drift, and doc truth splits

### 6.2 Wave plan

Because the execution runtime can only host 6 concurrent subagents, workstreams run in three waves without reducing the 17-role plan.

| Wave | Experts | Purpose |
|---|---|---|
| Wave 1 | 2, 3, 4, 5, 6, 9 | Recover architectural truth in the hottest runtime paths |
| Wave 2 | 7, 8, 10, 11, 12, 13 | Recover engineering credibility and operational discipline |
| Wave 3 | 14, 15, 16, 17 plus Principal Architect arbitration | Converge product/runtime integration, migration, and long-term evolution |

### 6.3 Ownership matrix

| Expert | Primary scope | Exit criteria |
|---|---|---|
| 2 | `delivery/`, `application/`, `domain/`, architecture rules | No new transport bypasses; orchestration moved behind application facades |
| 3 | `kernelone/` | No reverse imports; no business-role leakage; no raw runtime writes outside allowed substrate |
| 4 | `cells/roles/kernel`, `roles/runtime` | Run/stream share one durable commit truth and one canonical handoff contract |
| 5 | `kernelone/context` | Four-layer ContextOS is authoritative, append-only truth restored |
| 6 | `docs/graph`, `cell.yaml`, packs, fence imports | Catalog/manifest/code reconciled; pack freshness enforceable |
| 7 | `application/`, `domain/` | Transaction boundaries and business rules clearly separated |
| 8 | `infrastructure/` | All outbound adapters hang from explicit ports/contracts |
| 9 | `delivery/http`, `delivery/ws`, `delivery/cli` | Delivery reduced to transport concerns and canonical entrypoints |
| 10 | `.github/workflows`, gate scripts | Failing gates block merges and publish machine-readable evidence |
| 11 | `tests`, `src/backend/tests`, `src/backend/polaris/tests` | Topology normalized and regression coverage mapped to architecture |
| 12 | `runtime/*`, audit/meta/governance reports | Structured event truth becomes reliable across runtime and QA |
| 13 | KFS, explicit effects, path guards, tool policy | Dangerous effects fenced and auditable |
| 14 | `src/frontend`, `src/electron`, E2E | UI/Electron follow canonical backend contracts and critical flows |
| 15 | task market, execution broker, role runtime | Multi-agent collaboration flows become contract-first and observable |
| 16 | `cognitive_runtime` and related contracts | Authority remains additive, not a second truth or second context runtime |
| 17 | shims, legacy paths, blueprint/index/ADR truth | One canonical entrypoint and one canonical document truth per concern |

## 7. Execution Phases

### Phase 0: Freeze the bleed

Objectives:

1. Block all new `delivery -> cells.*.internal` imports
2. Block all new `kernelone -> (cells|domain|delivery|application|infrastructure)` imports
3. Block new non-canonical `cell.yaml` schemas
4. Block new legacy entrypoints and compatibility expansions

Deliverables:

- hard gate tests
- import-fence rules
- manifest schema validator
- compatibility freeze policy

### Phase 1: Restore the architectural spine

Objectives:

1. Move transport-side orchestration into `application`
2. Define canonical runtime-admin application services
3. Keep `delivery` thin and transport-only

Deliverables:

- application facades for PM/runtime/system operations
- delivery cleanup plan by route and CLI entrypoint
- canonical entrypoint map

### Phase 2: Restore runtime truth

Objectives:

1. Make `TransactionKernel` the sole durable commit authority
2. Make `ContextHandoffPack` the only handoff truth
3. Make `ContextOS` four layers authoritative

Deliverables:

- run/stream durable commit convergence
- ContextOS append-only truth restoration
- projection provenance gate

### Phase 3: Reconcile Cells and graph

Objectives:

1. Reconcile `catalog -> manifest -> code`
2. Clear cluster-level public/internal breaches
3. Make `descriptor/context/verify` packs trustworthy

Deliverables:

- canonical manifest schema
- reconciliation script and report
- pack freshness gates

### Phase 4: Recover engineering credibility

Objectives:

1. Make CI blockers real
2. Normalize test placement
3. Require machine-readable metrics and evidence

Deliverables:

- truly blocking workflows
- test topology policy
- metrics summary artifacts

### Phase 5: Operational and product convergence

Objectives:

1. Align frontend/electron with canonical backend contracts
2. Align multi-agent orchestration with task market and runtime truth
3. Remove legacy entrypoint ambiguity

Deliverables:

- E2E critical-path matrix
- runtime observability contract
- shim removal matrix

## 8. Immediate Priority Order

The first four remediation items are non-negotiable and must be treated as structural blockers:

1. `delivery` must stop bypassing `application`
2. `kernelone` must stop importing business and delivery semantics
3. `TransactionKernel` must own one commit truth across `run` and `stream`
4. `ContextOS` must stop admitting second-truth side channels

These four items are upstream of almost every other stability, testing, and governance claim in the repository.

## 9. Acceptance Gates

### 9.1 Architecture gates

1. No non-test `delivery -> cells.*.internal`
2. No `kernelone -> application/domain/delivery/infrastructure/cells`
3. No non-canonical `cell.yaml`
4. No unresolved catalog/manifest drift for touched Cells

### 9.2 Engineering gates

1. `ruff check <paths> --fix`
2. `ruff format <paths>`
3. `mypy <paths>`
4. `pytest <tests> -q`

### 9.3 Mandatory repo-level gates

1. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py`
2. `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`
3. `python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only`

### 9.4 Evidence gates

Every structural fix must publish:

1. changed truth boundary
2. affected Cells and contracts
3. test evidence
4. remaining risk
5. if structural, a verification card and ADR delta

## 10. Known Evidence Driving This Blueprint

This blueprint is based on directly observed repository facts, including:

1. `delivery` bypassing `application` and importing `cells.*.internal`
2. `kernelone` importing business/runtime semantics
3. `TransactionKernel` using divergent run/stream commit paths
4. `ContextOS` permitting second-truth side channels
5. graph, manifest, and pack governance drift
6. CI and type-reporting paths that still allow soft-failure behavior

These are not hypothetical future concerns. They are current structural conditions.

## 11. Definition of Done

This blueprint is considered fully landed only when all of the following are true:

1. The code spine matches the normative spine
2. `KernelOne` is substrate-pure enough to be independently reasoned about
3. `TransactionKernel` and `ContextOS` form one runtime truth chain
4. graph and Cell manifests are executable truth, not advisory documents
5. delivery is transport-only
6. CI and release gates block on truth, not on optimism
7. frontend/electron critical flows consume canonical runtime contracts
8. all compatibility layers have an explicit retirement plan or are removed

## 12. Principal Architect Directive

The repository is too large, too stateful, and too runtime-heavy for opportunistic fixes.

Execution must therefore follow three rules:

1. fix structural blockers before local convenience bugs
2. move truth upward into contracts and gates, not into tribal knowledge
3. prefer fewer, cleaner, harder boundaries over broader compatibility

This is the only route to a reliable Polaris substrate.
