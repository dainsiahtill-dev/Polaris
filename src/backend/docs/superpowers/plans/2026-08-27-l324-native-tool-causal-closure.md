# L3-24 Native Tool Causal Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute this plan task-by-task.

**Goal:** Prove and close the generic causal boundary that corrupts native Director tool arguments, then revalidate L3-24 without modifying the generated project.

**Architecture:** Preserve the canonical `provider stream -> KernelOne StreamExecutor -> role tool envelope -> Director tool gateway -> effect receipt -> verifier` chain. Add privacy-bounded hashes/counts beside executable payloads, never inside tool arguments. Classify the root only by comparing evidence from one fresh isolated run; all recovery remains on the same Director task.

**Tech Stack:** Python 3.12, pytest, Ruff, mypy, CodeGraph, Factory Bench, runtime.v2/TaskRuntime evidence.

---

## Task 1: Freeze current boundaries and evidence

- [ ] Confirm `kernelone` and `roles.kernel` ownership/effect boundaries from graph assets.
- [ ] Review only the five relevant code/test diffs; preserve unrelated worktree state.
- [ ] Keep r69 status `root_cause_unproven` because raw provider SSE was not retained.

## Task 2: Prove argument assembly is lossless in isolation

- [ ] Run the fragmented Anthropic C++ native-tool regression.
- [ ] Run tool-envelope normalization regressions.
- [ ] Run Ruff, format check, and mypy on touched paths.
- [ ] Do not put audit metadata into executable arguments or tool signatures.

## Task 3: Fresh isolated dynamic run

- [ ] Run L3-24 with the standard 5400/6000-second isolated Bench budgets.
- [ ] Before cleanup, capture exact factory/task/call IDs and context snapshots.
- [ ] Compare provider argument audit, normalized native call, effect receipt hash, disk hash, and verifier output for the same call.
- [ ] Confirm retries remain `same_director_task_only`; PM/CE must not restart.

## Task 4: Repair only the proven generic boundary

- [ ] If raw/decoded hashes diverge, repair KernelOne stream decoding with a RED regression.
- [ ] If decoded/effect hashes diverge, repair tool gateway/write adapter with a RED regression.
- [ ] If corruption is already present upstream, add a generic fail-closed corruption detector and same-task repair feedback only after evidence proves the predicate.
- [ ] Never edit the generated L3-24 workspace.

## Task 5: Close governance evidence

- [ ] Update the machine-readable defect record with exact-run evidence and status.
- [ ] Update the causal audit blueprint with tests and residual risk.
- [ ] Add a small memory extension note for the new lesson.
- [ ] Run a fresh isolated revalidation before claiming closure.

**Governance override:** Do not create branches or commits. The user's explicit no-branch/no-commit instruction overrides generic plan templates that suggest commit checkpoints.
