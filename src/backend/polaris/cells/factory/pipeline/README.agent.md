# Factory Pipeline Cell

## Objective
Orchestrate high-level software manufacturing workflows and controlled target-project projection experiments.

This cell now owns two adjacent concerns:

1. Traditional factory run orchestration
2. Controlled `Cell IR -> Projection -> Traditional Project` experiments used to validate Polaris's wave-particle architecture on a real generated subproject

## Boundaries & Constraints
- **State Ownership**:
  - `workspace/factory/*`
  - `runtime/factory/*`
- **Controlled experiment artifacts**:
  - Hidden IR/projection receipts are stored under `workspace/factory/projection_lab/*`
  - User-visible generated projects are written only under `workspace/experiments/*`
- **Dependencies**:
  - `orchestration.workflow_runtime`
  - `roles.runtime`
  - `runtime.state_owner`
  - `archive.factory_archive`
  - `audit.evidence`
  - `policy.workspace_guard`
- **Effects Allowed**:
  - workspace reads
  - hidden factory artifact writes
  - controlled experiment writes under `workspace/experiments/*`
  - verification subprocess execution for generated experiment projects

## Projection Lab

The projection lab is a truthful proving ground for the architecture described in
`docs/CELL_EVOLUTION_ARCHITECTURE_SPEC.md`.

Current controlled projection profiles:

- `record_cli_app`
- `resource_http_service`

Execution model:

1. Normalize the requirement
2. Optionally use the PM-bound LLM to enrich requirement understanding
3. Build a target-side Cell IR graph
4. Compile the graph into a traditional Python project under `experiments/<slug>`
5. Persist hidden `cell_ir.json`, `projection_map.json`, `back_mapping_index.json`, and verification receipts via KFS
6. Run traditional verification commands against the generated project

Back-mapping is treated as a first-class artifact:

- `projection_map.json` records file-to-cell ownership
- `back_mapping_index.json` records Python symbol spans, hashes, and qualified names so later code edits and runtime evidence can be traced back to target cells
- `back_mapping_refresh_report.json` records which files and symbols changed after a later workspace edit, plus the impacted `cell_id` set

Selective reprojection is also supported:

- requirement changes are normalized again through the PM-bound LLM or deterministic fallback
- impacted `cell_id` values are resolved from manifest deltas
- only files owned by impacted cells are rewritten under `workspace/experiments/*`

## Public Contracts
- Trigger and monitor software manufacturing pipelines
- Run a controlled projection experiment through `RunProjectionExperimentCommandV1`
- Serve as Director's optional projection execution backend through public
  contracts only; Director remains the caller and `factory.pipeline` remains the
  capability owner
