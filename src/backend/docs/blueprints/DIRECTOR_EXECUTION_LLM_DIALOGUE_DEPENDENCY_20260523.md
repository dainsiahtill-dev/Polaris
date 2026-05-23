# Director Execution LLM Dialogue Dependency

Date: 2026-05-23

## Finding

The catalog governance audit reported a high-severity graph drift:
`director.execution` imports `llm.dialogue` but does not declare it in
`depends_on`.

The concrete runtime edge is in
`polaris/cells/director/execution/internal/code_generation_engine.py`, where
Director's role-response bridge imports
`polaris.cells.llm.dialogue.public.service.generate_role_response`.

## Contract

Director execution has a direct public-cell dependency on `llm.dialogue`:

- Source cell: `director.execution`
- Target cell: `llm.dialogue`
- Public contract: `InvokeRoleDialogueCommandV1`
- Runtime API: `generate_role_response`

This dependency must be declared in both the graph catalog and the individual
`director.execution/cell.yaml` manifest.

## Data Flow

Director execution builds a role dialogue prompt from task context, calls
`llm.dialogue` for role response generation, and then parses the response into
Director execution artifacts. The LLM control plane and provider runtime still
own provider selection and invocation policy; `llm.dialogue` owns the public
role-dialogue facade used by this call path.

## Verification

- `src/backend/polaris/tests/test_cell_yaml_governance.py`
- `src/backend/polaris/cells/director/execution/tests/test_code_generation.py`
- `docs/governance/ci/scripts/run_catalog_governance_gate.py --mode audit-only`
