# Chief Engineer Bulk Blueprint Handoff

Date: 2026-05-25

## Problem

Chief Engineer desktop can block Director start when PM/Director tasks lack blueprint evidence, but the UI only allowed one-at-a-time blueprint generation. Complex projects make that handoff slow and hard to audit.

## Decision

Add a Chief Engineer v2 delivery contract for bulk blueprint generation:

```text
Chief Engineer Desktop
  -> POST /v2/chief-engineer/blueprints/bulk
  -> chief_engineer.blueprint public command contract
  -> runtime/blueprints persisted evidence
  -> CE diagnostics refresh
  -> Director start gate can clear when coverage is complete
```

The backend route reuses the existing `chief_engineer.blueprint` public command contract and keeps the same Chief Engineer LLM readiness gate as single blueprint generation.

## Verification

- Backend route tests cover multi-task command construction, empty-batch rejection, and LLM readiness fail-closed behavior.
- Frontend service tests cover endpoint and workspace query encoding.
- Chief Engineer workspace tests cover the "补齐全部" desktop action, request body, evidence strip, and rendered blueprint results.
