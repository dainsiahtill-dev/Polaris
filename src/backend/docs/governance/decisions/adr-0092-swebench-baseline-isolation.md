# ADR-0092: SWE-bench 强模型基线的隔离纪律(baseline-only 通道)

- 状态: Accepted
- 日期: 2026-06-11
- 关联: docs/blueprints(仓库根)/WEAK_MODEL_CAPABILITY_AMPLIFICATION_BLUEPRINT_20260611.md §9;adr-0090(弱模型链路硬化)

## 背景

能力放大蓝图的北极星指标是「pinned 子集上 弱模型+Polaris vs 顶级模型(Fable5 级)的 paired resolved 率」。差距的另一端(强模型基线)必须真实存在,否则目标不可度量。但项目铁律是**不切云模型掩盖本地缺陷**——强模型调用若渗入开发循环,弱模型链路的缺陷会被静默掩盖,实验结论全部失真。

## 裁决

强模型基线运行只允许走 **baseline-only 通道**,由机制而非自觉保证:

1. **用途唯一**:基线运行只用于产出对照度量(`*.scores.json` / `paired_report.json`)。基线产物(补丁、轨迹、工具输出)禁止回流:不得进入开发调试循环、不得作为修复参考、不得写入经验库/技能库/仓库知识缓存(未来 B6 类记忆机制上线时必须在 benchmark 模式下禁用沉淀写入,本条为其先行约束)。
2. **配置隔离**:基线 arm 使用独立的 `KERNELONE_LLM_CONFIG` 文件,不修改默认配置;运行经 `scripts/swebench/swebench_paired_runner.py` 的独立 `--work-dir` 与 `--run-prefix`,产物目录与弱模型 arm 物理分离。
3. **低频**:基线 per pinned-subset 版本各跑一次(必要时 N repeats 一次性完成),不随 harness 改动重跑;harness 改动的对照对象是弱模型 arm 的前后两次运行,不是基线。
4. **可比性**:基线与弱模型 arm 必须同 pinned subset、同 harness 版本、同评分 schema(`swebench-score/1` 版本戳);跨评分 schema 的对照无效。
5. **统计独立性**:任何跨 session 记忆/缓存类机制(仓库知识缓存、技能库、经验注入)在 benchmark 运行中必须隔离或清空,防止 repeats 样本间不独立污染 CI 统计。

## 后果

- 「弱模型与顶级模型的差距」首次成为可量化、可追踪的一等指标;
- 纪律由 paired runner 的结构(子进程隔离、独立 work-dir、串行执行)强制,不依赖人为遵守;
- 成本:基线运行的云调用费用一次性发生,且仅在 pinned subset 升版时重复。
