# StateFirstContextOS.project() Pipeline Architecture

> ⚠️ 历史快照 (2026-06-07 审计)：本文档部分结论已被后续实现取代，仅作历史参考。
> 实际代码（`runtime/engine.py` 的 `project()`）已**无条件**走 pipeline，不存在 `enable_pipeline`
> 特性开关，也没有"向后兼容回退到旧实现"的路径——pipeline 是唯一路径。下文 "Feature Flag"
> 与 "Backward Compatibility" 两节描述的设计已不再生效。

## Overview

The `StateFirstContextOS.project()` method is refactored from a monolithic procedure into a 7-stage pipeline architecture. Each stage is a self-contained processor class with a single responsibility.

## Stage Definitions

| # | Stage | Responsibility | Input | Output |
|---|-------|----------------|-------|--------|
| 1 | `TranscriptMerger` | Merge existing transcript with new messages | `PipelineInput` | `TranscriptMergerOutput` |
| 2 | `Canonicalizer` | Dialog act classification, routing, artifact offload, follow-up resolution | `TranscriptMergerOutput` | `CanonicalizerOutput` |
| 3 | `StatePatcher` | Extract state hints from events and build `WorkingState` | `CanonicalizerOutput` | `StatePatcherOutput` |
| 4 | `BudgetPlanner` | Compute token budgets and validate invariants | `StatePatcherOutput` | `BudgetPlannerOutput` |
| 5 | `WindowCollector` | Collect pinned active window events | `BudgetPlannerOutput` | `WindowCollectorOutput` |
| 6 | `EpisodeSealer` | Seal closed episodes based on active window | `WindowCollectorOutput` | `EpisodeSealerOutput` |
| 7 | `ArtifactSelector` | Select artifacts and episodes for prompt injection | `EpisodeSealerOutput` | `ArtifactSelectorOutput` |

## Final Assembly

After stage 7, `PipelineRunner.run()` assembles:
- `ContextOSSnapshot`
- `ContextOSProjection` (head_anchor, tail_anchor, active_window, artifact_stubs, episode_cards, run_card, context_slice_plan)

## Feature Flag

`StateFirstContextOS` gains:
- `enable_pipeline: bool = False` — when `True`, `project()` delegates to `PipelineRunner`
- `_pipeline_runner: PipelineRunner | None` — lazily initialized

## Backward Compatibility

When `enable_pipeline=False` (default), `project()` calls the original method unchanged.