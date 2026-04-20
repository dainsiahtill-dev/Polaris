# Docs Court Workflow Cell

## Objective
Manage the iterative review and refinement workflow for generated documentation, mimicking a "court" process of defense and critique.

## Boundaries & Constraints
- **State Ownership**: Court workflow states and review results.
- **Dependencies**: `llm.control_plane`, `roles.runtime`
- **Effects Allowed**: Modifying documentation artifacts.

## Public Contracts
- Orchestrate documentation review courts.
