# Governance Mechanization Blueprint: Quarantine Manifest + Flag Registry (2026-07-03)

## 1. 问题陈述（量化证据）

2026-07-03 审计确认两类"散文治理"缺口（import 拓扑的机械围栏——137 个 architecture fence tests + catalog gate——是有效的，本蓝图把其余两类行为债抬到同一机械高度）：

1. **脏测试基线靠人工甄别**：已知失败测试集在 bench 战役 7 次记录中波动（322p/4f → 371p/8f → 318p/12f → 321p/12f → 380p/8f），每轮 Agent 都要人工重判"哪些红是既有的"。实测 HEAD 既有失败：`test_mutation_guard_soft_mode.py` 5 个、`test_tool_batch_executor_decision_tree.py` 11 个、adapter 广选择器下 2 个 advisor_notes mock 签名 + 2 个 PM TASK-3 期望漂移。没有机器可读的隔离清单，新回归和既有债无法区分，"意外变绿"也无人察觉。
2. **Flag 蔓延无登记**：backend 源码中 **533 个**唯一 `KERNELONE_` flag（487 个被生产代码引用，121 个 behavior-shaped），仅 7 个有文档；执行路径根部 140 个。每个 bench 修复平均新增 flag，无 owner、无默认值登记、无日落机制。

## 2. 目标态架构

```
docs/governance/quarantine/known_failures.json   ←(登记: node_id/reason/owner/registered_at/expiry)
        │
        ▼
docs/governance/ci/scripts/run_test_quarantine_gate.py
        │  跑隔离选择器 → 比对清单
        ├── 清单内仍红 → PASS(记为 known)
        ├── 清单内变绿 → FAIL(unexpected_pass: 要求摘除登记，防僵尸清单)
        └── 清单外新红 → FAIL(new_failure)

polaris/kernelone/config/flag_registry.py        ←(登记: name/default/owner_cell/purpose/expiry)
        │
        ▼
polaris/tests/architecture/test_kernelone_flag_registry_fence.py
        │  AST 扫描生产代码 os.environ/os.getenv/environ.get 的 KERNELONE_* 读取
        ├── 读取名在 registry → PASS
        └── 读取名不在 registry → FAIL(new unregistered flag)
   初始 registry 由扫描自动生成（存量 533 个全登记为 legacy_unowned），
   围栏从此冻结增量：新 flag 必须带 owner/purpose 登记。
```

裁决要点：

1. **与 fail-closed 法则的调和**：quarantine 清单不是"改测试制造通过"——测试本体保持失败断言不变，gate 只对**增量**执法（new_failure 与 unexpected_pass 都红）。这与 catalog gate 的 baseline JSON + fail-on-new 模式同构（repo 既有先例）。
2. Flag registry 初版是**登记+冻结**，不改变任何 flag 行为；退役/日落由后续 wave 依 expiry 驱动。
3. 两个 gate 均可先 audit-only 后 hard-fail（沿用 `docs/governance/ci/STAGED_ROLLOUT_PLAN.md` 的 staged rollout 惯例）。

## 3. 落地范围（Phase 1）

1. `known_failures.json` 初版收录当前 HEAD 实证既有失败（mutation_guard_soft_mode 5、decision_tree 11、advisor_notes 2、PM TASK-3 2，提交前复测坐实），每项含 reason 与 owner 线索。
2. `run_test_quarantine_gate.py`：读清单 → 逐文件跑 pytest（--tb=no -q）→ 产出 new_failures / unexpected_passes / known_failures 三桶 → 非空前两桶则 exit 1；`--mode audit-only` 支持。
3. `flag_registry.py`：`FlagSpec` dataclass + `KERNELONE_FLAG_REGISTRY` dict + `registered_flag_names()`；初版由扫描脚本生成，存量条目 `owner="legacy_unowned"`。
4. 围栏测试：AST 扫描 `polaris/**（非 tests）` 的 env 读取点，比对 registry；白名单机制仅限动态拼接名的既有站点（逐一登记）。

## 4. 验证

- gate 脚本自测：注入一个假新红/假变绿的 fixture 清单，断言两向都 FAIL。
- 围栏测试首跑必须绿（存量全登记）；随后手工添加一个未登记读取的 fixture 模块，断言 FAIL。
- 全量 architecture fence suite 保持绿。

## 5. 风险与边界

- quarantine 选择器跑的是失败文件全量（分钟级），只进 CI/gate 场景，不进默认 pytest 路径。
- flag 围栏用 AST 而非 grep，避免字符串常量误报；动态 env 名（f-string 拼接）登记为 pattern 白名单并要求注释 owner。
- 本蓝图不删除、不重命名任何现存 flag。
