# Blueprint: ContextOS P2 技术债清理与 P3 演进规划

**版本**: 1.0
**创建时间**: 2026-04-21
**状态**: Draft → 待评审
**目标**: 完成 P2 技术债清理（models.py 废弃、control-plane 隔离），为 P3 长期演进奠基

---

## 1. 背景与问题陈述

当前 ContextOS 架构存在以下技术债，阻碍系统演进：

| ID | 问题 | 严重程度 | 根因 |
|----|------|----------|------|
| T1 | `models.py` 废弃 dataclass 仍在 14 处被直接引用 | P0 | 历史迁移未完成 |
| T2 | `pipeline/stages.py` 依赖旧 dataclass，阻碍 Pydantic V2 验证生效 | P0 | 未同步迁移 |
| T3 | `models_v2_compat.py` 兼容层残余，未来成为技术债 | P1 | 过渡代码未清理 |
| T4 | `turn_transaction_controller.py` control-plane 消息未在 prompt 中过滤 | P2 | 实现缺失 |
| T5 | `slm.py` 跨 Cell 直接 import `CognitiveGateway` 实现类 | P1 | 协议解耦不彻底 |
| T6 | 2 处裸 `except Exception` 仍在存量代码中 | P2 | 异常治理未完成 |
| T7 | `contextos_governance` 未纳入 CI 强制门禁 | P2 | Governance 覆盖缺失 |

---

## 2. 系统架构图（文本描述）

```
┌─────────────────────────────────────────────────────────────────┐
│                    ContextOS Architecture                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐    ┌──────────────────────────────────┐  │
│  │  turn_transaction│    │       Pipeline Runner             │  │
│  │  _controller.py  │    │  ┌─────┐→┌─────────┐→┌────────┐ │  │
│  │                  │    │  │Stage│  │Stage N  │  │Stage 7 │ │  │
│  │  [control-plane] │───▶│  │  1  │  │  ...    │  │Artifact│ │  │
│  │  metadata标记    │    │  │Merge│  │        │  │Selector│ │  │
│  └──────────────────┘    │  └─────┘→└─────────┘→└────────┘ │  │
│                          └──────────────────────────────────┘  │
│                                       │                        │
│                                       ▼                        │
│  ┌──────────────────────────────────────────────────────────────────┐
│  │                    Models Layer                                   │
│  │  ┌────────────┐    ┌──────────────┐    ┌───────────────────┐      │
│  │  │ models.py │    │models_v2.py  │    │models_v2_compat.py│      │
│  │  │ [DEPRECATED]   │[Pydantic V2] │    │  [过渡兼容层]      │      │
│  │  │ dataclass │    │ frozen=True  │    │  待清理            │      │
│  │  └────────────┘    └──────────────┘    └───────────────────┘      │
│  └──────────────────────────────────────────────────────────────────┘
│                                       │                        │
│                                       ▼                        │
│  ┌──────────────────────────────────────────────────────────────────┐
│  │                    Summarizers Layer                             │
│  │  ┌────────────────┐    ┌─────────────────────────────────────┐  │
│  │  │ TieredSummariz │    │        SLMSummarizer                 │  │
│  │  │ er             │───▶│  [Protocol 解耦]                     │  │
│  │  └────────────────┘    └─────────────────────────────────────┘  │
│  └──────────────────────────────────────────────────────────────────┘
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 数据流

```
TurnDecision → PipelineRunner.run()
    → Stage 1: TranscriptMerger (merge transcript)
    → Stage 2: Canonicalizer (act 分类 + TieredSummarizer)
    → Stage 3: StatePatcher (build WorkingState)
    → Stage 4: BudgetPlanner (calculate budgets)
    → Stage 5: WindowCollector (collect pinned window + JIT compress)
    → Stage 6: EpisodeSealer (seal closed episodes)
    → Stage 7: ArtifactSelector (select for prompt injection)
    → ContextOSSnapshotV2
    → to_prompt_dict() [filter: plane != "control"]
    → LLM Messages
```

---

## 3. 模块职责划分

| 模块 | 职责 | 负责人（本次分配） |
|------|------|-------------------|
| `models_v2.py` | Pydantic V2 数据模型，frozen=True，strict 验证 | Engineer 1 |
| `pipeline/stages.py` | 七阶段处理器，迁移到 V2 模型 | Engineer 2 |
| `models_v2_compat.py` | 双向转换层，迁移完成后删除 | Engineer 2 |
| `turn_transaction_controller.py` | control-plane 消息标记 + 过滤 | Engineer 3 |
| `summarizers/slm.py` | SLM 摘要器，优化协议解耦 | Engineer 1 |
| `contextos_governance` | 治理规则，纳入 CI | Engineer 3 |

---

## 4. 核心数据流设计

### 4.1 模型迁移路径

```
models.py (dataclass, deprecated)
    │
    ├── 迁移路径 1: stages.py → models_v2.py
    │   ├── TranscriptEvent → TranscriptEventV2
    │   ├── BudgetPlan → BudgetPlanV2
    │   ├── WorkingState → WorkingStateV2
    │   └── EpisodeCard → EpisodeCardV2
    │
    ├── 迁移路径 2: runtime.py → models_v2.py
    │
    └── 迁移完成后: 删除 models_v2_compat.py
```

### 4.2 Control-plane 消息过滤

```python
# models_v2.py - ContextOSProjectionV2.to_prompt_dict()
def to_prompt_dict(self) -> list[dict[str, Any]]:
    """生成 LLM 消息，过滤 control-plane 消息"""
    messages = []
    for item in self.transcript:
        metadata = getattr(item, "metadata", {}) or {}
        # 过滤 control-plane 消息
        if metadata.get("plane") == "control":
            continue
        messages.append({
            "role": item.role,
            "content": item.content,
        })
    return messages
```

### 4.3 SLM 协议解耦优化

```python
# slm.py - 改进的协议解耦
class SLMSummarizer:
    def __init__(self, gateway: SummarizerGatewayProtocol | None = None):
        self._gateway = gateway  # 通过 Protocol 注入，不直接 import 实现类

    def _get_gateway(self) -> SummarizerGatewayProtocol:
        if self._gateway:
            return self._gateway
        # Lazy init via lazy imports in method body, not class level
        from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import CognitiveGateway
        ...
```

---

## 5. 技术选型理由

| 选择 | 理由 |
|------|------|
| Pydantic V2 frozen=True | 确保不可变性，防止意外修改；JSON Schema 自动生成利于文档 |
| Protocol 解耦 | 避免跨 Cell 直接 import 实现类，符合 Cell 边界隔离原则 |
| control-plane 过滤 | 避免 control 消息污染 LLM prompt，减少 token 消耗 |
| 模型迁移先于治理 | 技术债清理是 Governance 自动化的前提 |

---

## 6. 执行计划

### Phase 1: models.py 废弃迁移（P0）

**目标**: 将所有直接引用 `models.py` 的文件迁移到 `models_v2.py`

**关键步骤**:
1. 审计 `models.py` 中仍在使用的类/方法
2. 在 `models_v2.py` 中补充缺失类型（检查 compat.py 中的转换函数）
3. 修改 14 个引用文件的 import 语句
4. 逐个文件运行 `ruff check --fix` + `mypy --strict`
5. 删除 `models_v2_compat.py` 兼容层

**受影响文件**:
```
polaris/kernelone/context/context_os/pipeline/stages.py   [P0]
polaris/kernelone/context/context_os/runtime.py          [P0]
polaris/kernelone/context/context_os/classifier.py        [P0]
polaris/kernelone/context/context_os/pipeline/runner.py  [P0]
polaris/kernelone/context/context_os/helpers.py          [P1]
polaris/kernelone/context/context_os/pipeline/contracts.py  [P1]
polaris/kernelone/context/context_os/memory_search.py    [P1]
polaris/kernelone/context/context_os/introspection.py    [P1]
polaris/kernelone/context/context_os/evaluation.py       [P1]
polaris/kernelone/context/context_os/__init__.py         [P1]
polaris/kernelone/context/context_os/tests/*.py          [P1]
```

### Phase 2: Control-plane 隔离完整实现（P2）

**目标**: 在 `to_prompt_dict()` 中显式过滤 control-plane 消息

**关键步骤**:
1. 确认 `turn_transaction_controller.py` 中 control-plane 消息的 metadata 格式
2. 在 `models_v2.py` 的 `ContextOSProjectionV2.to_prompt_dict()` 中添加过滤逻辑
3. 编写单元测试验证过滤行为
4. 运行 integration test 验证端到端流程

### Phase 3: SLM 协议解耦优化（P1）

**目标**: 完全消除 `slm.py` 中跨 Cell 直接 import 实现类

**关键步骤**:
1. 确认 `SummarizerGatewayProtocol` 是否覆盖所有必需方法
2. 将 lazy import 移到方法体内（避免类级别循环 import）
3. 可选：引入 IOC 容器统一管理 gateway 实例

### Phase 4: Governance CI 自动化（P2）

**目标**: 将 `contextos_governance` 纳入强制 CI

**关键步骤**:
1. 编写 CI 脚本校验 Graph 声明与代码事实一致性
2. 将脚本注册到 `run_catalog_governance_gate.py`
3. 配置 `.claude/` 或 `docs/governance/ci/` 中的 hook

### Phase 5: 裸异常治理（P2）

**目标**: 清除存量代码中 2 处裸 `except Exception`

**关键步骤**:
1. 使用 `grep -r "except Exception" --include="*.py"` 定位
2. 替换为具体异常类型或增加日志

---

## 7. 验证标准

| 阶段 | 验证条件 |
|------|----------|
| Phase 1 | `ruff check polaris/kernelone/context/context_os/ --fix` 无 warning |
| Phase 1 | `mypy polaris/kernelone/context/context_os/ --strict` 无 error |
| Phase 1 | `pytest polaris/kernelone/context/context_os/tests/ -q` 100% 通过 |
| Phase 1 | `models_v2_compat.py` 已删除 |
| Phase 2 | `pytest tests/test_control_plane_filter.py` 通过 |
| Phase 2 | integration test 端到端通过 |
| Phase 3 | `mypy polaris/kernelone/context/context_os/summarizers/slm.py` 无跨 Cell import 警告 |
| Phase 4 | CI pipeline 包含 contextos_governance gate |
| Phase 5 | `grep "except Exception:" polaris/kernelone/context/context_os/` 返回空 |

---

## 8. 风险评估

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| V2 模型字段名与 dataclass 不一致 | 迁移后运行时错误 | 逐个文件迁移，每步运行 pytest |
| control-plane 过滤导致关键消息丢失 | LLM 决策质量下降 | 添加 integration test 验证端到端 |
| 兼容层删除后有遗漏引用 | 运行时 ImportError | 删除前执行全量 grep 确认 |
| SLM gateway 懒加载循环 import | 模块加载失败 | 单独测试 import 语句 |

---

## 9. 依赖关系

```
Phase 1 (models 迁移)
    │
    ├── 依赖: models_v2.py 完整性（需先补充缺失类型）
    │
    ▼
Phase 2 (control-plane 过滤) [独立，可并行]
    │
    ├── 依赖: turn_transaction_controller.py metadata 格式确认
    │
    ▼
Phase 3 (SLM 协议解耦) [独立，可并行]
    │
    ├── 依赖: SummarizerGatewayProtocol 协议完整性
    │
    ▼
Phase 4 + 5 (Governance + 异常治理) [独立，可并行]
```

---

## 10. 里程碑

| 里程碑 | 完成时间 | 交付物 |
|--------|----------|--------|
| M1: models.py 废弃完成 | +3 天 | 14 个文件迁移完成，compat 层删除 |
| M2: Control-plane 隔离完成 | +1 天 | 过滤逻辑 + 测试 |
| M3: SLM 协议解耦完成 | +1 天 | 无跨 Cell 直接 import |
| M4: Governance CI 集成 | +2 天 | CI pipeline 配置 |
| M5: 裸异常清理完成 | +0.5 天 | 2 处修复 |

**总工期**: ~7.5 工作日

---

## 11. 后续演进（P3）

详见主文档 `docs/blueprints/CONTEXTOS_MEMORY_ARCHITECTURE_V2.md` 和 ADR-0067。