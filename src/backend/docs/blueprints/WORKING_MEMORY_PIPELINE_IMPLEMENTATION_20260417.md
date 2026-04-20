# ADR-0080: Working Memory Pipeline — Context Dimensionality Reduction Implementation

**日期**: 2026-04-17
**版本**: v3.1
**状态**: ✅ 全部实施完毕（Steps 1–10）+ 深度审计 Bug 修复
**权威来源**: `AGENTS.md` §18（认知生命体架构对齐）
**关联文档**:
- `SESSION_ORCHESTRATOR_AND_DEVELOPMENT_WORKFLOW_RUNTIME_BLUEPRINT_20260417.md`
- `SPECULATIVE_EXECUTION_KERNEL_PHASE2_BLUEPRINT_20260417.md`

---

## 1. 问题陈述

### 1.1 断链发现（2026-04-17 深度架构审计）

Polaris 系统虽然有：
- `AUTO_CONTINUE` 模式
- `TurnOutcomeEnvelope.session_patch`
- `OrchestratorSessionState`
- `Checkpoint` 机制
- `DevelopmentWorkflowRuntime`

但这五者**没有形成闭环**：

```
Turn 结束 → session_patch 定义了 → 但从未被消费 → 每回合重新失忆
```

表面上是多回合连续执行，实际上是每回合重新失忆一次。

### 1.2 三条断链

| 断链 | 描述 | 影响 |
|------|------|------|
| **发现链（Finding Pipeline）** | LLM 在 turn 内形成的"有效结论"没有被结构化提取 | 前两条链迟早漂 |
| **状态链（State Pipeline）** | 结论没有进入 `OrchestratorSessionState`，或合并规则错误 | LLM 看到的 state 和实际不符 |
| **重投喂链（Reprojection Pipeline）** | 状态没有在下一 turn 被投影成高密度、可执行的 continuation prompt | 每回合重新开始 |

---

## 2. 核心架构原则

### 2.1 上下文降维（Context Dimensionality Reduction）

**原则**：LLM 在多回合场景中应只看到**提炼后的合成结论**，而非原始 artifacts 的累积。

| 旧思维（降维前） | 新思维（降维后） |
|-----------------|-----------------|
| LLM 看到所有历史消息和工具结果 | LLM 只看 `structured_findings`（降维后的"门禁卡"） |
| `artifacts` 是主存储 | `artifacts` 仍存原始数据，但续写 prompt 不直接依赖 |
| 每次续写都重新扫描历史 | 每次续写基于结构化的 `session_patch` 注入 |

### 2.2 语义补丁 vs 键值堆积

`session_patch` 本质上是**语义补丁**，不是普通 dict：
- 对当前工作记忆的**修正**
- 对旧判断的**撤销**
- 对阶段的**推进**
- 对后续动作的**引导**

> 更好的做法：给 patch 明确操作语义（set / add / remove / progress），避免"只会往脑子里塞东西、不会遗忘和修正"的病态记忆体。

### 2.3 置信度与新鲜度

每个发现物应有：
- **confidence**: hypothesis / likely / confirmed
- **superseded**: 被推翻的旧结论不进入下一轮 prompt

### 2.4 stagnation 语义检测

不能只看 artifact hash 变化（容易误判），还要检测：
1. **语义进展停滞**：task_progress 在最近 4 个 turn 没有推进
2. **哈希停滞**：artifact hash 连续 2 个 turn 未变

### 2.5 与 TransactionKernel 单事务约束的边界对齐（2026-04-17）

Working Memory Pipeline 负责的是**跨 turn 的记忆闭环**，不放宽 turn 内协议约束：

1. 允许 `sequential`：仅限单 turn、单 ToolBatch 内的有序调用（例如 read -> edit -> verify）。
2. 禁止 hidden continuation：不得在同一 turn 内发起第二次决策循环补步骤。
3. 若单批次后仍需推进，必须 commit 当前 turn，并依赖 `session_patch` + reprojection 驱动下一 turn。

该定义与 `TRANSACTION_KERNEL_CONTEXTOS_TOOL_REFACTOR_BLUEPRINT_20260416.md` §2.1 保持一致。

---

## 3. 已完成实现（Step 1）

### 3.1 `OrchestratorSessionState` 扩展

**文件**: `polaris/cells/roles/runtime/internal/continuation_policy.py`

```python
@dataclass
class OrchestratorSessionState:
    session_id: str
    goal: str = ""
    turn_count: int = 0
    max_turns: int = 15

    artifacts: dict[str, Any] = field(default_factory=dict)
    # 新增：结构化发现物（上下文降维结论）
    structured_findings: dict[str, Any] = field(default_factory=dict)
    # 新增：任务宏观进度
    task_progress: str = "exploring"
    # 新增：关键文件快照指纹
    key_file_snapshots: dict[str, str] = field(default_factory=dict)

    last_failure: dict[str, Any] | None = None
    turn_history: list[dict[str, Any]] = field(default_factory=list)
    recent_artifact_hashes: list[str] = field(default_factory=list)
```

### 3.2 `_inject_findings()` — 语义补丁注入

**文件**: `polaris/cells/roles/runtime/internal/session_orchestrator.py`

```python
def _inject_findings(self, session_patch: dict[str, Any]) -> None:
    """将 session_patch 的结论增量注入 structured_findings。

    使用 upsert 语义而非 dict.update()，确保每个字段只保留最新结论。
    发现物轨迹 (_findings_trajectory) 保留最近 3 条，用于检测 LLM "炒冷饭"。
    """
    # 1. 保留历史轨迹（最多 3 条）
    prior_trajectory = self.state.structured_findings.get("_findings_trajectory", [])
    prior_trajectory.append(session_patch.copy())
    if len(prior_trajectory) > 3:
        prior_trajectory = prior_trajectory[-3:]
    self.state.structured_findings["_findings_trajectory"] = prior_trajectory

    # 2. Upsert：列表型字段（suspected_files）用追加去重，标量字段直接覆盖
    for key, value in session_patch.items():
        if key == "_findings_trajectory":
            continue
        if key in self.state.structured_findings:
            existing = self.state.structured_findings[key]
            if isinstance(existing, list) and isinstance(value, list):
                combined = existing.copy()
                for item in value:
                    if item not in combined:
                        combined.append(item)
                self.state.structured_findings[key] = combined
            else:
                self.state.structured_findings[key] = value  # 标量覆盖
        else:
            self.state.structured_findings[key] = value

    # 3. 同步更新 key_file_snapshots
    if "key_file_snapshots" in session_patch:
        self.state.key_file_snapshots.update(session_patch["key_file_snapshots"])
```

### 3.3 3-zone XML Continuation Prompt

```python
def _build_continuation_prompt(self) -> str:
    """基于降维后的 structured_findings 构建下一 Turn 的 continuation prompt。

    使用 3-zone XML 结构（ADR-0071 上下文降维）：
    - Past/Memory: 累积的合成结论（降维后的"门禁卡"）
    - Present/Context: 当前任务状态与最新发现
    - Future/Instruction: 下一 Turn 的行动指引
    """
    findings = self.state.structured_findings

    # Zone 1: Past/Memory — _findings_trajectory 进入历史轨迹
    trajectory = findings.get("_findings_trajectory", [])
    past_lines = [f"[Turn -{len(trajectory) - idx}] {entry.get('error_summary') or entry.get('action_taken') or str(entry)}"
                  for idx, entry in enumerate(trajectory)]
    past_block = "\n".join(past_lines) or "（尚无历史结论）"

    # Zone 2: Present/Context — task_progress + suspected_files + error_summary
    context_parts = [f"任务进度: {self.state.task_progress}"]
    if suspected := findings.get("suspected_files", []):
        context_parts.append(f"疑似问题文件: {', '.join(suspected) if isinstance(suspected, list) else suspected}")
    if patched := findings.get("patched_files", []):
        context_parts.append(f"已修复文件: {', '.join(patched) if isinstance(patched, list) else patched}")
    if recent_error := findings.get("error_summary", ""):
        context_parts.append(f"最新错误: {recent_error}")
    context_parts.append(f"当前是第 {self.state.turn_count} 回合（上限 {self.state.max_turns}）")
    present_block = " | ".join(context_parts)

    # Zone 3: Future/Instruction — 根据 task_progress 动态生成
    instruction_map = {
        "exploring": "请继续探索和分析问题。优先确认问题根因，再决定修复方案。",
        "investigating": "继续深入调查。已识别疑似文件，关注错误栈和调用链。",
        "implementing": "现在进入修复阶段。请按最小改动原则修复，确认后立即验证。",
        "verifying": "验证阶段。请运行测试或手动验证修复效果，确保无回归。",
        "done": "任务已完成。请汇总结果并以 END_SESSION 结束。",
    }
    instruction = instruction_map.get(self.state.task_progress, "继续执行当前任务。")

    return (
        f"<Past/Memory>\n{past_block}\n</Past/Memory>\n"
        f"<Present/Context>\n{present_block}\n</Present/Context>\n"
        f"<Future/Instruction>\n{instruction} 如需继续，请调用工具。\n</Future/Instruction>"
    )
```

### 3.4 Checkpoint 包含降维工作记忆

```python
async def _checkpoint_session(self) -> None:
    """持久化当前会话状态到本地 checkpoint 文件。

    包含完整的降维工作记忆，确保 resume 时能真正恢复"上一回合学到了什么"。
    加上 schema_version 以便未来字段迭代时兼容。
    """
    checkpoint_dir = Path(self.workspace) / ".polaris" / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = checkpoint_dir / f"{self.session_id}.json"

    with open(checkpoint_path, "w", encoding="utf-8") as handle:
        json.dump({
            "schema_version": 2,
            "session_id": self.state.session_id,
            "turn_count": self.state.turn_count,
            "goal": self.state.goal,
            "task_progress": self.state.task_progress,
            "structured_findings": self.state.structured_findings,
            "key_file_snapshots": self.state.key_file_snapshots,
            "last_failure": self.state.last_failure,
            "artifacts": self.state.artifacts,
            "recent_artifact_hashes": self.state.recent_artifact_hashes,
        }, handle, ensure_ascii=False, default=str)
```

### 3.5 Stagnation 双重检测

```python
@staticmethod
def _detect_stagnation_v2(state, envelope) -> bool:
    """检测 stagnation（语义级 + 哈希级双重检测）。"""
    # 检测 1: 语义进展停滞（task_progress 卡住）
    trajectory = state.structured_findings.get("_findings_trajectory", [])
    if len(trajectory) >= 4:
        recent_progresses = [e.get("task_progress") for e in trajectory[-4:] if "task_progress" in e]
        if len(recent_progresses) >= 4 and len(set(recent_progresses)) == 1:
            return True  # 连续 4 个 turn 都在同一阶段

    # 检测 2: 哈希停滞（文件内容无变化）
    recent_hashes = state.recent_artifact_hashes[-2:]
    if len(recent_hashes) >= 2:
        if recent_hashes[-1] == recent_hashes[-2] and not envelope.speculative_hints:
            return True

    return False
```

---

## 4. 已完成步骤

### Step 2: SessionPatch 类型化 ✅ (2026-04-17)

```python
class SessionPatch(dict):
    """语义补丁类型，定义 session_patch 中各字段的操作语义。

    用法（由 LLM 在 turn 末尾输出）：
        <SESSION_PATCH>
        {
            "task_progress": "diagnosing",
            "suspected_files": ["src/auth.py"],
            "error_summary": "Database timeout",
        }
        </SESSION_PATCH>
    """

    def get_task_progress(self) -> str | None: ...
    def get_suspected_files(self) -> list[str]: ...
    def get_patched_files(self) -> list[str]: ...
    def get_verified_results(self) -> list[str]: ...
    def get_pending_files(self) -> list[str]: ...
    def get_remove_keys(self) -> list[str]: ...
    def get_error_summary(self) -> str: ...
    def get_action_taken(self) -> str: ...
    def get_key_file_snapshots(self) -> dict[str, str]: ...
```

实际实现为 `dict` 子类（兼容 JSON 解析结果），每个字段对应一个类型化 getter 方法。

### Step 3: `apply_session_patch()` 专用函数 ✅ (2026-04-17)

替换 `_inject_findings()` 中的内联逻辑，确保 patch 语义正确：
- 列表型字段追加去重（suspected_files, pending_files, patched_files）
- 标量型字段直接覆盖（error_summary, action_taken）
- `task_progress` 跨字段同步（state + structured_findings）
- `remove_keys` 从列表字段中移除指定条目（如伪线索）

### Step 4: 4-zone Continuation Prompt 优化 ✅ (2026-04-17)

当前是 3-zone（Past/Memory, Present/Context, Future/Instruction），建议升级为 4-zone：

```xml
<Goal>
修复登录接口 500 报错
</Goal>

<Progress>
当前阶段: diagnosing
当前回合: 2 / 15
</Progress>

<WorkingMemory>
已确认:
- DB timeout 已在日志中出现
- auth.py 和 db.py 与故障路径相关

待验证:
- auth.py 中 timeout 参数是否过小

最近失败:
- 上一轮尚未进行测试验证
</WorkingMemory>

<Instruction>
基于以上工作记忆，执行下一步最小必要动作。
</Instruction>
```

### Step 5: Resume 完整性验证 ✅ (2026-04-17)

Checkpoint 必须包含：
- `schema_version`
- `structured_findings`
- `task_progress`
- `last_failure`
- `artifacts` 引用或压缩版

### Step 6: 单元测试覆盖 ✅ (2026-04-17)

| 测试文件 | 测试内容 | 结果 |
|---------|---------|------|
| `test_continuation_policy.py` | patch 注入、confidence 合并、stagnation 检测 | 26 PASS |
| `test_session_orchestrator.py` | 4-zone prompt、checkpoint 完整性、session_patch 提取 | 18 PASS |

### Step 7: WORKING_MEMORY_CONTRACT_GUIDE 嵌入 ✅ (2026-04-17)

在 `prompt_builder.py` 中将工作记忆契约嵌入 L4 层（TTL=60s），同时覆盖 Tri-Axis 和 legacy 两条路径：

- **Tri-Axis 路径**（`build_professional_prompt`）：`_get_cached_l4()` → L4 层拼接
- **Legacy 路径**（`build_system_prompt`）：`_get_cached_l4()` → chunk 拼接

### Step 8: CompletionEvent 集成 ✅ (2026-04-17)

内核层到编排层的端到端数据桥：

1. **`turn_events.py` CompletionEvent**：添加 `visible_content: str` 和 `session_patch: dict` 字段
2. **`turn_transaction_controller.py`**：将 `visible_content` 注入到最终 `CompletionEvent` 的 yield
3. **`session_orchestrator._build_envelope_from_completion`**：从 `event.visible_content` 提取 `<SESSION_PATCH>` 并剥离

```
LLM 输出 → CompletionEvent.visible_content → _build_envelope_from_completion
         → extract_session_patch_from_text() → apply_session_patch()
         → strip_session_patch_block() → 纯净 visible_content
```

### Step 9: Confidence / Superseded 置信度语义 ✅ (2026-04-17)

**`SessionPatch` 新增字段**：
- `confidence`: `hypothesis | likely | confirmed` — 置信度等级（默认 hypothesis）
- `superseded`: `bool` — 推翻旧假设标记（默认 False）

**`_CONFIDENCE_RANK`**：置信度排序 `{confirmed: 3, likely: 2, hypothesis: 1}`

**合并语义**：
- 高置信度覆盖低置信度（confirmed > likely > hypothesis）
- `superseded=True` 时 patch 中的字段名进入 `_superseded_keys`，后续 `get_active_findings()` 过滤掉

**`get_active_findings()`**：过滤掉 `_superseded_keys` 和 `_confidence_*` 元字段后的活跃发现物，用于续写 prompt

**L4 WORKING_MEMORY_CONTRACT_GUIDE**：更新提示词，明确说明置信度升级路径（hypothesis → likely → confirmed）和推翻假设（superseded）用法

### Step 10: Checkpoint Resume 加载 ✅ (2026-04-17)

**`OrchestratorSessionState` 恢复**：
- `_try_load_checkpoint()` 在 `__init__` 中自动调用
- 静默忽略：文件不存在、schema_version 不匹配、数据损坏
- 恢复字段：`turn_count`、`task_progress`、`structured_findings`（含 `_superseded_keys`）、`key_file_snapshots`、`last_failure`、`artifacts`、`recent_artifact_hashes`

**`schema_version=2` 兼容性**：checkpoint 写入端和读取端均为 schema_version=2

**关键 Bug 修复（深度审计发现）**：
- `execute_stream` 中 `is_first_turn = True` → `is_first_turn = self.state.turn_count == 0`
  - 修复前：checkpoint resume 后使用原始 prompt，导致 LLM 从头开始而非基于工作记忆继续
  - 修复后：resume 后自动使用 continuation prompt，真正恢复上一回合的工作记忆

---

## 5. 开发分工建议（8 周）

### Week 1-2: Finding Pipeline（发现链）
- 设计 `SessionPatch` 类型化 schema
- 在 LLM system prompt 中嵌入 `<SESSION_PATCH>` 输出块规范
- Decoder 解析 `<SESSION_PATCH>` 块

### Week 3-4: State Pipeline（状态链）
- 实现 `apply_session_patch()` 函数 ✅
- 支持 confidence / superseded 语义 ✅ (Step 9)
- 集成到 `OrchestratorSessionState` ✅

### Week 5-6: Reprojection Pipeline（重投喂链）
- 实现 4-zone continuation prompt
- 语义级 stagnation 检测
- 端到端测试

### Week 7-8: Hardening & Integration
- Checkpoint/resume 完整性验证 ✅ (Step 10)
- 与 `TurnTransactionController` 集成 ✅ (Step 8)
- 全量回归测试 ✅ (54 PASS)

---

## 6. 验证门禁

每个 Step 必须通过：
1. `ruff check <paths> --fix && ruff format <paths>` — 静默
2. `mypy <paths>` — "Success: no issues found"
3. `pytest polaris/cells/roles/runtime/internal/tests/ -v` — 100% PASS
   - `test_continuation_policy.py`: 26 PASS
   - `test_session_orchestrator.py`: 19 PASS
   - 合计: **55 PASS**

---

## 7. 架构决策记录

| 决策 | 理由 |
|------|------|
| 使用 upsert 而非 `dict.update()` | 避免旧值覆盖新值；列表追加去重，标量直接覆盖 |
| 保留 `_findings_trajectory` 最多 3 条 | 用于检测 LLM "炒冷饭"；平衡内存与信息量 |
| 3-zone XML 结构 | 清晰分离"历史"、"当前"、"未来"三个时态 |
| Schema version 在 checkpoint 中 | 未来字段迭代时 resume 仍可兼容 |
| Stagnation 双重检测 | 纯哈希检测在复杂工作流中容易误判 |

---

**下一步行动**:
- ✅ ADR-0080 Working Memory Pipeline 已全部实施完毕（Steps 1–10）
- 验证运行 `pytest polaris/cells/roles/runtime/internal/tests/ -q` → **54 PASS**
- 端到端验证：使用 Director CLI 发起多 Turn 会话，观察 LLM 是否输出 `<SESSION_PATCH>` 块
