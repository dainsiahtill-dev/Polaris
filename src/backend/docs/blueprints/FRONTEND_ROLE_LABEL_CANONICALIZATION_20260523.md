# Frontend Role Label Canonicalization

Date: 2026-05-23

## Finding

PM, Chief Engineer, and Director desktop surfaces rely on consistent role
labels to preserve the hierarchy:

1. PM plans and gates task contracts.
2. Chief Engineer produces blueprint and handoff evidence.
3. Director executes code and worker tasks.

The frontend still had duplicated role label maps where `director` rendered as
`Chief Engineer`, and `chief_engineer` rendered as `Director` in the process
monitor.

## Root Cause

Role labels were copied into several components instead of being derived from
one shared role-label contract.

## Fix

- Add a shared role label helper based on `UI_TERMS.roles`.
- Route LLM state adapters, visual model node chips, visual validation copy,
  and Process Monitor backend labels through the helper.
- Add focused frontend regressions for Director and Chief Engineer labels.

## Verification

Targeted frontend gates:

- `npm run test -- roleLabels UnifiedLlmDataManagerV2 VisualModelNode ProcessMonitorSidebar copySync`
- `npm run typecheck`
- `npm run lint`
