# Agent Instruction Compaction Blueprint (2026-04-16)

## Goal

在不破坏现有治理门禁、章节锚点和镜像一致性的前提下，精简：

1. `AGENTS.md`
2. `CLAUDE.md`
3. `GEMINI.md`

目标：

1. 降低 token / context 占用
2. 保留最小必要执行规则
3. 保留现有关键章节锚点：
   - `AGENTS.md §8.6`
   - `AGENTS.md §15`
   - `AGENTS.md §16`
   - `AGENTS.md §17`
   - `CLAUDE.md §6.6`
   - `GEMINI.md §6.6`
4. 继续通过：
   - `tests/architecture/test_kernelone_release_gates.py::test_agent_instruction_snapshot_is_consistent`

## Non-Negotiables

精简后必须保留：

1. 权威链与默认阅读顺序
2. Graph First / Cell First / Reuse First / KernelOne Foundation
3. Public/Internal Fence
4. Single State Owner / Explicit Effects / UTF-8
5. 旧根冻结与归属裁决
6. `§8.6` 结构性 bug 的 Verification Card / ADR 协议
7. `§15` 当前现实快照中的可提取事实
8. `§16` 自动化治理工具与 `CI/CA` gate matrix
9. `§17` 2026-04-16 目标态治理裁决
10. `CLAUDE.md / GEMINI.md` 的镜像性质与 `§6.6` 工具治理铁律

## Compaction Strategy

### AGENTS.md

压缩为五层：

1. 权威链
2. 核心原则与归属
3. 开工 / 修改 / 验证 / 交付
4. `§8.6` 结构性修复协议
5. `§15-§17` 当前事实、治理工具、目标态裁决

### CLAUDE.md / GEMINI.md

压缩为镜像摘要：

1. 权威链
2. 最小执行规则
3. 当前现实快照摘要
4. `§6.6` 工具治理铁律
5. 自动化治理工具
6. `CI/CA` gate matrix
7. 最新目标态裁决

## Verification

1. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py::test_agent_instruction_snapshot_is_consistent`
2. 人工复核：
   - `AGENTS.md §8.6` 仍存在
   - `CLAUDE.md §6.6` 仍存在
   - `GEMINI.md §6.6` 仍存在
   - `§15 / §16 / §17` 仍存在
