# Polaris

Polaris is an AI agent governance and runtime platform for complex software delivery.  
Its goal is not to wrap a model with another chat shell, but to turn agent execution into an **auditable, transactional, rollbackable, and verifiable** engineering system.

For Chinese readers, see [README.md](./README.md).

From an architectural point of view:

- `KernelOne` is the runtime substrate
- `Cells` are the bounded capability units
- `TransactionKernel` and `ContextOS` are the core runtime truth chain

## What Polaris Is

Polaris is easiest to understand as two layers:

1. `KernelOne`
   A reusable runtime substrate for AI agents, closer to an operating-system-style foundation than a utility folder.
2. `Polaris`
   A governance and delivery layer built on top of `KernelOne`, focused on multi-role collaboration, task execution, auditing, and verification.

That makes Polaris closer to an **Agent Runtime + Governance Platform** than to a prompt collection or chat-first coding assistant.

## Current Status

The repository should currently be read as:

- **Alpha**
- **Architecturally directed, still converging**
- **`src/backend/polaris/` is the canonical backend root**
- **Some compatibility shims still exist for old entrypoints**

This is a large runtime-heavy codebase with a clear architectural direction, not a small finished product.

## Architecture Overview

![Polaris architecture overview](docs/assets/diagrams/polaris-architecture-overview.svg)

At a high level:

- `delivery/` handles transport: HTTP, WebSocket, and CLI
- `application/` is where orchestration and transaction boundaries should live
- `domain/` holds business rules and policies
- `kernelone/` holds platform-neutral runtime capabilities
- `cells/` expose bounded capabilities and public contracts
- `infrastructure/` binds external systems such as storage, DB, messaging, and telemetry

## Core Concepts

### 1. KernelOne

`KernelOne` is the platform-neutral runtime substrate, under [src/backend/polaris/kernelone](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone).  
It is intended to own:

- context runtime
- execution substrate
- storage layout / KFS
- event and audit primitives
- provider/tool/runtime contracts

### 2. TransactionKernel

`TransactionKernel` is the turn-level execution kernel.  
Its purpose is to give an agent turn a clear commit boundary instead of letting it drift into hidden continuation.

Primary references:

- [src/backend/polaris/cells/roles/kernel](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/roles/kernel)
- [ADR-0071](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md)

### 3. ContextOS

`ContextOS` is the context runtime.  
It is designed to separate truth logs, working state, large-object references, and read-only projections instead of treating context as a single growing message list.

Primary code:

- [src/backend/polaris/kernelone/context](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone/context)

### 4. Cells

`Cells` are the bounded capability model used by Polaris.  
Each Cell is expected to define ownership, dependencies, public contracts, and governance assets.

Graph truth:

- [src/backend/docs/graph/catalog/cells.yaml](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/graph/catalog/cells.yaml)

## Core Advantages

### 1. Agent execution is treated as a runtime discipline

Polaris tries to move agent execution away from an implicit model loop and toward a bounded runtime discipline:

- decisions can be constrained
- tool batches can be normalized
- handoff and commit can be audited
- turn execution becomes testable as runtime behavior, not just prompt behavior

### 2. Context is treated as a system problem, not just a prompt trick

Instead of relying only on summarization heuristics, Polaris pushes toward a structured context system:

- truth logs
- working state
- large-object references
- projection building

That gives the project a more durable path for auditability, replay, and context isolation.

### 3. The codebase is trying to scale by boundaries, not by conventions

Polaris does not only rely on “please do not import the wrong thing”.  
It already has the architecture needed for governance at scale:

- normative root layers
- Cell boundaries
- graph truth
- public/internal fences
- pack-based governance assets
- release and architecture gates

### 4. KernelOne aims to be a reusable agent substrate

The long-term ambition is not just a desktop product.  
It is to make `KernelOne` solid enough to serve as a reusable Agent Runtime Substrate while Polaris grows into the governance and delivery platform above it.

### 5. Auditability and side-effect control are built into the direction

The project places strong emphasis on:

- release gates
- structured governance assets
- explicit effects
- runtime evidence
- receipts and verification paths

This is one of the clearest differences between Polaris and a typical chat-agent repository.

### 6. Polaris is designed for long-running collaboration, not single-turn answers

The codebase explicitly models:

- multi-role collaboration
- task markets
- execution brokers
- evidence, archive, audit, and runtime state

Relevant paths include:

- [runtime.task_market](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/runtime/task_market)
- [runtime.task_runtime](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/runtime/task_runtime)
- [runtime.execution_broker](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/runtime/execution_broker)
- [factory.pipeline](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/factory/pipeline)

## Repository Layout

| Path | Purpose |
|---|---|
| [src/backend/polaris](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris) | canonical backend implementation |
| [src/backend/polaris/kernelone](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone) | runtime substrate |
| [src/backend/polaris/cells](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells) | bounded business capabilities |
| [src/frontend](/C:/Users/dains/Documents/GitLab/polaris/src/frontend) | React frontend |
| [src/electron](/C:/Users/dains/Documents/GitLab/polaris/src/electron) | Electron shell |
| [src/backend/docs](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs) | backend architecture and governance docs |
| [docs](/C:/Users/dains/Documents/GitLab/polaris/docs) | project-level docs and blueprints |
| [tests](/C:/Users/dains/Documents/GitLab/polaris/tests) | repo-level tests |
| [src/backend/tests](/C:/Users/dains/Documents/GitLab/polaris/src/backend/tests) | backend architecture/governance/integration tests |

Canonical backend layers live under [src/backend/polaris](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris):

- `bootstrap/`
- `delivery/`
- `application/`
- `domain/`
- `kernelone/`
- `infrastructure/`
- `cells/`
- `tests/`

## Quick Start

### Requirements

- Python `3.10+`
- Node.js `20+`

### Install development dependencies

```bash
npm run setup:dev
```

Or for Python only:

```bash
pip install -e .[dev]
```

### Start the desktop development environment

```bash
npm run dev
```

### Start backend only

Canonical CLI:

```bash
polaris --host 127.0.0.1 --port 49977
```

Compatibility fallback:

```bash
python src/backend/server.py --host 127.0.0.1 --port 49977
```

### Run role CLIs

```bash
pm --workspace . --start-from pm
director --workspace . --iterations 1
python -m polaris.cells.architect.design.internal.architect_cli --mode interactive --workspace .
python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli --mode interactive --workspace .
```

## 30-Minute Walkthrough

### 0-5 min: read the backbone

Read these files in order:

1. [src/backend/AGENTS.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/AGENTS.md)
2. [src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md)
3. [src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md)

### 5-10 min: start the app

```bash
npm run setup:dev
npm run dev
```

### 10-15 min: start backend separately

```bash
polaris --host 127.0.0.1 --port 49977
```

### 15-20 min: inspect the runtime model

Browse:

1. [src/backend/polaris/kernelone](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone)
2. [src/backend/polaris/cells/roles/kernel](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/roles/kernel)
3. [src/backend/polaris/cells/runtime](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/runtime)

### 20-25 min: run frontend and architecture tests

```bash
npm test
python -m pytest -q tests/architecture/test_kernelone_release_gates.py
```

### 25-30 min: verify quality and E2E path

```bash
npm run test:e2e
python src/backend/docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
```

## Testing and Quality Gates

Frontend:

```bash
npm test
npm run test:e2e
```

Python:

```bash
ruff check src/backend/polaris --fix
ruff format src/backend/polaris
mypy src/backend/polaris
pytest src/backend/tests -q
```

Architecture / release:

```bash
python -m pytest -q tests/architecture/test_kernelone_release_gates.py
python src/backend/docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
python src/backend/docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only
```

Relevant workflows:

- [quality-gates.yml](/C:/Users/dains/Documents/GitLab/polaris/.github/workflows/quality-gates.yml)
- [kernel_quality.yml](/C:/Users/dains/Documents/GitLab/polaris/.github/workflows/kernel_quality.yml)
- [ci.yml](/C:/Users/dains/Documents/GitLab/polaris/.github/workflows/ci.yml)

## Future Direction

### 1. Purify KernelOne into a true substrate

The long-term direction is not to keep pushing business semantics into `KernelOne`, but to make it cleaner:

- remove reverse dependencies
- remove role-specific semantic leakage
- remove raw write paths outside canonical storage/layout rules

### 2. Make the normative architecture the real architecture

The intended spine is:

- `delivery -> application -> domain/kernelone`

The next major step is to make that true in the code:

- delivery becomes transport-only
- application becomes the orchestration layer
- domain owns business rules
- cells collaborate through public contracts

### 3. Close the runtime truth chain

Polaris will only become a durable runtime platform if:

- `TransactionKernel` becomes the sole durable commit authority
- `ContextOS` becomes the sole context truth chain
- `run` and `stream` stop producing separate commit semantics

### 4. Turn graph and manifests into executable truth

The repository already has:

- `cells.yaml`
- `cell.yaml`
- governance packs
- ADRs
- release gates

The next step is to make them authoritative enough that architecture drift becomes a gate failure, not a tribal-knowledge problem.

### 5. Reach operator-grade observability

The target is not just “more logs”. It is:

- unified event truth
- unified receipts and evidence
- unified runtime state across backend and UI
- a traceable PM / Director / QA chain for long-running execution

## Read Next

Recommended reading order:

1. [src/backend/AGENTS.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/AGENTS.md)
2. [src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md)
3. [src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md)
4. [docs/TERMINOLOGY.md](/C:/Users/dains/Documents/GitLab/polaris/docs/TERMINOLOGY.md)
5. [src/backend/docs/graph/catalog/cells.yaml](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/graph/catalog/cells.yaml)

README rewrite blueprint:

- [README_INFORMATION_ARCHITECTURE_BLUEPRINT_20260425.md](/C:/Users/dains/Documents/GitLab/polaris/docs/blueprints/README_INFORMATION_ARCHITECTURE_BLUEPRINT_20260425.md)

## Support and License

If you want to support the project, the sponsor QR codes are embedded below and the original image files remain in the repository.

| Alipay | WeChat Pay |
|---|---|
| ![Alipay sponsor QR](docs/assets/images/coffee/alipay.jpg) | ![WeChat sponsor QR](docs/assets/images/coffee/wechat.jpg) |

Original asset paths:

- [alipay.jpg](/C:/Users/dains/Documents/GitLab/polaris/docs/assets/images/coffee/alipay.jpg)
- [wechat.jpg](/C:/Users/dains/Documents/GitLab/polaris/docs/assets/images/coffee/wechat.jpg)
- [coffee directory](/C:/Users/dains/Documents/GitLab/polaris/docs/assets/images/coffee)

License: MIT, see [LICENSE](/C:/Users/dains/Documents/GitLab/polaris/LICENSE).
