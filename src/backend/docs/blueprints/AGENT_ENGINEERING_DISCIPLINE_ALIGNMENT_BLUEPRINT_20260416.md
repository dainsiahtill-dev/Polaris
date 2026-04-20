# Agent Engineering Discipline Alignment Blueprint (2026-04-16)

## Goal

让 `AGENTS.md`、`CLAUDE.md`、`GEMINI.md` 在保持精简的前提下，同时遵守统一的高工程素养执行方式：

1. 先蓝图，后落地
2. 严格工程标准
3. 任务类型驱动的执行协议
4. 统一的结果输出结构

本蓝图只定义 Agent 指令层的执行纪律，不改变运行时代码。

## Textual Architecture Diagram

```text
User task
  -> Instruction governance (AGENTS.md authoritative)
      -> Phase 1: Blueprint & Architecture
          -> docs/blueprints/*.md
      -> Phase 2: Execution & Implementation
          -> code/docs/tests changes
      -> Verification gates
          -> ruff / mypy / pytest / governance tests
      -> Final structured report
          -> Result / Analysis / Risks / Testing / Self-Check / Future Optimization

CLAUDE.md / GEMINI.md
  -> mirror the same workflow and standards
  -> do not create a second source of truth
```

## Responsibilities

### AGENTS.md

1. 定义权威流程与硬门禁
2. 明确两阶段执行模型
3. 明确工程标准、任务协议和输出结构

### CLAUDE.md / GEMINI.md

1. 镜像 `AGENTS.md` 的工程纪律
2. 保留简明执行摘要
3. 不引入独立权威

## Core Data Flow

1. 用户提出任务
2. Agent 先产出 blueprint 并落到 `docs/`
3. 再按任务类型执行实现/重构/修复/测试
4. 运行验证门禁
5. 输出统一结构化结果

## Technical Rationale

1. 先蓝图再落地，能减少无边界改动和返工
2. 强制工程标准，能把“能跑”提升到“可维护、可审计、可演化”
3. 任务协议化，能避免 bug fix / refactor / review 混成同一种执行方式
4. 统一输出结构，能提升审计质量和跨 Agent 可读性

## Implementation Plan

1. 在 `AGENTS.md` 中加入：
   - 两阶段流程
   - 工程标准
   - 任务协议
   - 输出结构
2. 在 `CLAUDE.md` / `GEMINI.md` 中加入精简镜像摘要
3. 保持 `§8.6 / §15 / §16 / §17 / §6.6` 锚点不破坏

## Verification

1. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py::test_agent_instruction_snapshot_is_consistent`
2. 人工复核三份文档都含有：
   - blueprint-first
   - engineering standards
   - task protocols
   - output structure
