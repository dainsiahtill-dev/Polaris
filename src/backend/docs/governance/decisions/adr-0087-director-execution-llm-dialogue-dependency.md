---
status: accepted
date: 2026-05-23
---

# ADR-0087: Director Execution LLM Dialogue Dependency

## Context

The catalog governance audit detected that
`polaris.cells.director.execution.internal.code_generation_engine` imports
`polaris.cells.llm.dialogue.public.service.generate_role_response`, but
`director.execution` did not declare `llm.dialogue` in either the graph catalog
or its `cell.yaml` manifest.

`llm.dialogue` is the public role dialogue facade and exposes
`InvokeRoleDialogueCommandV1`. Director execution uses this facade when turning
task context into role-authored execution output.

## Decision

1. Add `llm.dialogue` to `director.execution.depends_on` in
   `docs/graph/catalog/cells.yaml`.
2. Add `llm.dialogue` to `director.execution/cell.yaml`.
3. Add `llm.dialogue` and the `director.execution -> llm.dialogue` relation to
   `execution_governance_pipeline`.
4. Add governance regression tests that assert the catalog, manifest, and
   subgraph relation stay in sync.

## Consequences

Positive:

1. The Director role-dialogue call path is now visible in graph governance.
2. Catalog audits no longer need to infer this dependency from source imports.
3. Future Director execution refactors can distinguish role dialogue from
   provider invocation and control-plane policy.

Tradeoffs:

1. `director.execution` retains a direct dependency on the dialogue facade while
   migration to smaller Director sub-cells continues.
2. Descriptor/context pack generated artifacts were not regenerated in this
   focused metadata repair to avoid unrelated churn.
