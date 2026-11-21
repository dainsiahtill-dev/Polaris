# Evidence-Graph Engineering Runtime（2026-11-21）

## 目标

把 candidate 从“有界 ReAct + 工具门禁”升级为“证据驱动有限状态机”：模型提出动作，宿主拥有状态、效果、证据、artifact、验证计划和预算。不得以放宽 unknown 或伪造证据提高 accepted 率。

## 架构

```text
Goal -> AcceptanceObligations -> Director ActionIntent
                              -> Host EffectExecutor
                              -> EvidenceGraph / ArtifactStore
                              -> VerificationPlan
                              -> QA targeted probes -> report_review
```

## 批次

1. **Effect/Artifact 基础**
   - execute_command 自动扫描并注册本调用 POLARIS_SANDBOX_OUTPUT 的受支持文件，不再要求模型预测 artifacts。
   - 保留显式 artifacts 的严格预检；类型限制 SVG/HTML/PNG/JPEG。
   - DOM 定向查询证据；run 级 scratch 生命周期随后统一。
2. **EvidenceGraph**
   - 条款映射为 obligation；工具/检查产生 typed evidence ref。
   - QA 每次只看到未满足 obligation；verdict 与图一致。
3. **VerificationPlan / PhasePolicy**
   - 宿主按真实工具链编译验证计划；禁止 `; true` / `|| true` 等退出码掩码。
   - 统一 first-verification、preparation、wrap-up、QA budget 为阶段策略。
4. **最终状态投影**
   - 默认请求只携带 phase、candidate revision、未满足 obligations、最近失败和允许动作。
   - 历史、artifact、证据正文按需引用读取。

## 验证

每批必须包含单元/集成回归、ruff/mypy、真实 L1 项目对照。未运行的门禁不得报告通过；任何 missing evidence 仍为 unknown。

## 第一批已实现

- `CommandOutputCapture.list()` + execute_command 自动扫描注册受支持 artifact；显式 artifacts 仍严格预检。
- `POLARIS_RUN_OUTPUT`：同一 run 内跨调用持久 scratch，独立于只读候选和每调用丢弃的 `/tmp`。
- `browser_observe.dom_queries`：最多 16 个 CSS 选择器，返回 count、文本、可见性与 bounding box。
- `verify_project` 拒绝 `; true`、`|| true`、`; echo`、`|| exit 0` 等退出码掩码；空 commands 时按真实工具链生成 fallback plan，不虚构 cmake/ctest。
- `engineering_evidence_graph.py`：从原目标与 report_review journal 投影 pending obligations 和所需 probe；QA working state 注入。
- `engineering_phase_policy.py`：BUILD/VERIFY/PREPARE/REPAIR/QA_PROBE/REPORT 统一阶段，request context 记录 phase。
- QA 最终报告阶段：清除探索 assistant/tool 历史，只保留 system + 有界证据包 + report_review；普通 QA working state 不携带源码正文。

目标回归：174 passed；静态检查 ruff/mypy 通过。完整 runtime 与真实项目验证随后记录。
