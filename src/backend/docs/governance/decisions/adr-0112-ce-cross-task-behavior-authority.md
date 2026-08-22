# ADR-0112: Freeze cross-task behavior semantics in the CE portfolio

Status: Accepted  
Date: 2026-08-23  
Owner: `chief_engineer.blueprint`

## Context

PM owns task boundaries and CE owns the immutable implementation portfolio.
The existing project-interface contract binds files and symbols across tasks,
but it does not bind observable behavior. In `factory_a9812b43a06a`, source
and tests were owned by different Director tasks and independently chose
opposite coordinate/floor semantics. Same-run repair correctly refused to
restart PM/CE, so the contradiction became terminal.

## Decision

The CE portfolio gains an independent typed shared behavior contract. Each
invariant has an owner, consumers, and concrete given/when/then examples. Task
plans explicitly reference the invariants they implement or verify. It is not
embedded in the existing project-interface DTO because that DTO explicitly
marks CE interface declarations advisory-only.

Before immutable persistence, CE validates task identity, reference closure,
owner/consumer coverage, and source/test cross-owner linkage. A new advisory
portfolio that fails this validation is rejected before Director dispatch.
The behavior contract has its own hash/ref and participates in portfolio
identity. Every task blueprint projects the complete shared contract.

## Consequences

- Director source and test tasks consume one semantic SSoT.
- Ordinary code failures remain same-Director-task repair and never restart
  PM/CE.
- Contract contradictions are blocked before provider/tool tokens are spent.
- The platform stays domain-neutral; it validates structure, not physics.
- Offline diagnostic portfolios remain readable but cannot grant handoff.

## Verification

- Valid owner/consumer contracts serialize and change stable hashes.
- Dangling, duplicate, unreferenced, and cross-owner-incomplete contracts fail.
- Director handoff contains invariant statements and examples.
- Preserved L3-22 topology reproduces the pre-freeze blocker offline.
