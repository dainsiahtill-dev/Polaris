# Card3D Full-Chain Scenario Blueprint

Date: 2026-06-03

## Objective

Add a dedicated Polaris desktop E2E scenario for medium/large multiplayer creative card game projects where the client is TypeScript + Three.js and the backend is Node.js.

## Architecture

```text
Architect/Court
  -> runtime contracts plan
  -> PM task contract
  -> Chief Engineer blueprint coverage gate
  -> Director task market handoff
  -> Director runtime writes and diff evidence
  -> QA real command verification
```

## Required Domains

The card3d scenario is not the same as the tactical roguelike game scenario. PM contracts must cover:

- client3d
- table
- networking
- server
- realtime
- matchmaking
- rooms
- cards
- deckbuilder
- rules
- sync
- persistence
- moderation
- tests

## Verification Plan

1. PM quality unit test must prove missing card3d domains are repaired without generating roguelike domains.
2. Electron E2E must create a C:/Temp workspace using the card3d seed.
3. The desktop flow must capture review JPG screenshots for Architect, PM, Chief Engineer, Director diff, and QA.
4. Chief Engineer diagnostics must show all planned blueprints covered before Director execution.
5. QA must pass with real command evidence.
