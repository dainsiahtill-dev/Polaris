# ADR-0072: Traceability Engine for Polaris v2.0

## Status

- **Status**: Approved
- **Date**: 2026-04-16
- **Author**: Principal Architect / Chief Engineer
- **Deciders**: Polaris Architecture Committee

## Context

Polaris operates as a multi-agent system where PM, Chief Engineer, Director, and QA collaborate to transform requirements into code. As the system scales, we face a critical observability gap:

- **Why was this file changed?** — We cannot answer this from runtime artifacts alone.
- **Does this commit satisfy the original requirement?** — The link between a task contract and the resulting file changes is implicit.
- **Can we reconstruct the decision chain after a failure?** — Only by reading scattered JSONL logs and LLM transcripts.

This ADR introduces a **Traceability Engine** that explicitly models the provenance graph from requirements → tasks → blueprints → commits → QA verdicts.

## Decision

We will implement a structured **TraceabilityMatrix** as a first-class runtime artifact, persisted at the end of every PM iteration.

### Key Design Choices

1. **Graph Model (Nodes + Links)**
   - `TraceNode`: Immutable entity representing a doc, task, blueprint, commit, or QA verdict.
   - `TraceLink`: Directed edge expressing relationships (`derives_from`, `implements`, `verifies`).

2. **KernelOne Placement**
   - Implementation lives in `polaris/kernelone/traceability/` because traceability is a **platform-agnostic technical capability**, not a Polaris business rule.

3. **Bypass Safety Strategy**
   - Traceability is a **bypass observer**. All integration points use `safe_*` wrappers that catch **all** exceptions, log them, and **never** propagate failure to the main PM/CE/Director/QA flow.

4. **Persistence**
   - One JSON file per iteration: `runtime/traceability/{run_id}.{iteration}.matrix.json`
   - Atomic write via `tmp` + `replace` to avoid corruption.

5. **Integration Points**
   - **PM planning**: Registers `doc` and `task` nodes.
   - **CE preflight**: Registers `blueprint` nodes linked to tasks.
   - **Director execution**: Registers `commit` nodes linked to blueprints.
   - **QA verdict**: Registers `qa_verdict` nodes linked to commits.

6. **Governance Gate**
   - A new CI gate (`run_traceability_gate.py`) enforces three invariants:
     1. Every `task` has a `doc` ancestor.
     2. Every `blueprint` has a `task` ancestor.
     3. Every `commit` has a `blueprint` ancestor.

## Consequences

### Positive

- **Auditability**: A single JSON file per iteration provides a complete, machine-readable provenance graph.
- **Debugging**: We can walk backward from any commit to the blueprint, task, and requirement that authorized it.
- **Governance**: The traceability gate prevents merges where changes lack documented rationale.
- **Zero Blocker Risk**: Because traceability is a bypass layer, a disk-full event cannot break a production iteration.

### Negative / Trade-offs

- **Storage Growth**: One matrix file per iteration. At <100 nodes per iteration and ~50KB per file, this is negligible compared to LLM transcript logs.
- **Not Real-time**: The matrix is assembled at `finalize_iteration`, so mid-iteration queries require reading partial runtime logs instead.
- **Manual CE Blueprint ID Extraction**: The current integration extracts `blueprint_id` from `chief_engineer_result`. If CE output schema changes, the integration must be updated.

## Related Documents

- Blueprint: `docs/blueprints/POLARIS_V2_TRACEABILITY_BLUEPRINT_20260416.md`
- Schema: `docs/governance/schemas/traceability-matrix.schema.yaml`
- Gate: `docs/governance/ci/scripts/run_traceability_gate.py`
- Verification Card: `docs/governance/templates/verification-cards/vc-20260416-traceability-engine.yaml`
