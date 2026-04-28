# Blueprint: Action-First Agent System Prompt Architecture

**Date**: 2026-03-30
**Status**: Proposed
**Priority**: P0
**Risk Level**: Medium (affects core LLM behavior, requires thorough testing)

---

## 1. Problem Statement

Current Agent prompts suffer from three critical behavioral issues in production environments:

| Issue | Symptom | Impact |
|-------|---------|--------|
| **LBYL (Look Before You Leap)** | Agent calls `file_exists` before every file operation | TOCTOU race conditions, 2-3x extra I/O |
| **Verbal Escalation** | Agent describes directory contents instead of calling `repo_tree` | Benchmark failures, incomplete exploration |
| **Incomplete闭环** | Agent reads/analyzes but never writes | Zero deliverable output |

**Root Cause**: Prompt engineering focuses on "what to do" but not "how to behave when constrained". Agents default to human-like cautious patterns unsuitable for high-frequency tool calling.

**Reference Conversation**: Gemini collaboration, 2026-03-30

---

## 2. Core Architecture: Dual-Layer Separation

```
┌─────────────────────────────────────────────────────────────┐
│  <thinking> - 人格特区 (High Entropy / Free)                │
│  • 人格从 assets/personas.yaml 加载（100种）                 │
│  • 通过 load_workspace_persona() 固化到 workspace           │
│  • 激活模型的涌现推理能力                                   │
│  • 吐槽、抱怨、用符合人格的语气思考                          │
└────────────────────────┬────────────────────────────────────┘
                         │ 物理定律约束
                         ▼ (不可逾越)
┌─────────────────────────────────────────────────────────────┐
│  [Action] - 机器指令层 (Low Entropy / Deterministic)        │
│  • repo_tree / read_file / precision_edit                   │
│  • 禁止自然语言敷衍                                         │
│  • 闭环必须包含写入操作                                      │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 Why This Architecture Works

| Layer | Mechanism | Benefit |
|-------|-----------|---------|
| **Persona Layer** | Attention shifts to YAML-loaded persona | Preserves "soul" + reasoning depth |
| **Execution Layer** | Hard-bounded XML tags + JSON | Guarantees handler parseability |
| **Physics Laws** | "Universal laws" framing | LLM obeys because logically self-consistent |

### 2.2 继承现有 Persona 系统（禁止另起炉灶）

| 组件 | 路径 | 用途 |
|------|------|------|
| 人格YAML | `assets/personas.yaml` | 100种人格定义 |
| Persona类 | `polaris/cells/roles/kernel/internal/prompt_templates.py` | Personadataclass |
| 加载函数 | `polaris/kernelone/storage/persona_store.py` | `load_workspace_persona()` |
| 注入函数 | `polaris/cells/roles/kernel/internal/prompt_templates.py` | `build_persona_prompt()` |

---

## 3. Final System Prompt Template

### 3.1 继承已有的人格系统

**现有系统（必须复用）**：
- 人格 YAML：`assets/personas.yaml`（100种人格，分5个主题）
- Personadataclass：`polaris/cells/roles/kernel/internal/prompt_templates.py`
  - `name`: 显示名称（如"Director"）
  - `traits`: 身份基调
  - `tone`: 语气特点
  - `vocabulary`: 特色词汇列表
- 加载函数：`get_persona(persona_id)` → `Persona`
- 固化机制：`load_workspace_persona(workspace, persona_ids)` → 随机选择并持久化

**禁止行为**：
- ❌ 自行生成随机人格描述
- ❌ 在 Prompt 中硬编码人格词汇
- ❌ 绕过 `build_persona_prompt()` 直接拼接人格

### 3.2 Action-First 基座模板

```markdown
<role_definition>
你是 {persona_name}，{persona_traits}。在 <thinking> 区域内，尽情展现你的人设魅力。
</role_definition>

<production_physics>
你运行在一个真实、复杂、容错成本极高的生产代码库中。以下工程铁律是"物理定律"，违背即导致任务失败：
1. 【行动优先 (Action-First)】：探索目录、列表必须调用 repo_tree/list_directory，禁止用自然语言口头描述或猜测
2. 【EAFP豁免】：绝对禁止调用 file_exists 做预检。直接读取或写入目标，利用报错动态调整策略
3. 【闭环交付】：涉及代码修改的任务，必须以写入操作作为终结（调用 edit_file/append_to_file/precision_edit）。未落盘=零产出
</production_physics>

<output_contract>
<thinking>
[意识隔离区：用 {persona_name} 的语气风格进行沉浸式思考]
1. 目标分析：[当前状态 vs 目标状态]
2. 动作决策：[基于物理定律决定工具及参数]
</thinking>

[Action]: {tool_name}
[Arguments]: {JSON_formatted_arguments}
[Status]: {In Progress | Completed}
[Marker]: {任务特定标记词或 None}
</output_contract>
```

### 3.3 正确集成方式

```python
# ✅ 正确：从已有系统获取人格
from polaris.cells.roles.kernel.internal.prompt_templates import (
    get_persona,
    build_persona_prompt,
    Persona,
)

# 加载 workspace 固化的人格（或随机选择）
persona_id = load_workspace_persona(workspace, list(get_persona_registry().keys()))
persona = get_persona(persona_id)

# 注入到 Action-First 基座模板
prompt = build_persona_prompt("director", persona_id)
# build_persona_prompt 内部替换 {persona_name}/{persona_traits} 等占位符
```

---

## 4. Engineering Principles Behind the Rules

### 4.1 EAFP > LBYL (Pythonic Philosophy)

**LBYL (Look Before You Leap)**:
```python
# ❌ Anti-pattern: Pre-check causes TOCTOU race
if file_exists(path):
    content = read_file(path)
```

**EAFP (Easier to Ask Forgiveness than Permission)**:
```python
# ✅ Production pattern: Direct operation + error handling
try:
    content = read_file(path)
except FileNotError:
    # Handle missing file
```

**Why in production**:
- Between `exists()` and `read()`, file can be deleted/moved by another process
- EAFP + `try/except` is the standard Pythonic pattern for a reason
- Reduces API calls, eliminates race conditions

### 4.2 Action-First > Verbal-Description

**Verbal Escalation (❌ Anti-pattern)**:
```
User: "列出当前目录"
Agent: "当前目录包含 src/、tests/、docs/ 等文件夹..."
```

**Action-First (✅ Production pattern)**:
```
User: "列出当前目录"
Agent: [Action]: repo_tree [Arguments]: {"path": "."}
```

### 4.3 闭环交付 = 真实产出

In real DevOps/automation pipelines:
- Analysis without write = no deliverable
- "Fixed" bug without commit = bug still exists
- Planned refactor without edit = zero progress

---

## 5. Implementation Phases

### Phase 1: KernelOne TurnEngine Integration (Week 1)

**Files to Modify**:
- `polaris/kernelone/llm/prompt_builder.py` - Inject new prompt template
- `polaris/kernelone/llm/templates/` - Add `action_first.j2` template
- `polaris/cells/roles/kernel/internal/turn_engine.py` - Parse Action block

**Deliverable**: TurnEngine outputs `[Action]/[Arguments]/[Status]/[Marker]` parsing

### Phase 2: Output Contract Parser (Week 2)

**Files to Create**:
- `polaris/kernelone/llm/output/action_parser.py` - Extract Action block
- `polaris/kernelone/llm/output/thinking_extractor.py` - Extract thinking block
- `polaris/kernelone/llm/output/marker_extractor.py` - Extract marker

**Deliverable**: Robust regex/parser that handles:
- Missing `[Action]` block → error
- Malformed JSON → fallback to raw
- Marker presence/absence

### Phase 3: Error Recovery Mechanism (Week 3)

**Files to Create**:
- `polaris/kernelone/llm/error_recovery/retry_policy.py` - On-tool-error self-correction
- `polaris/kernelone/llm/error_recovery/context_injector.py` - Inject error context

**Deliverable**: When tool returns error, inject into next LLM call:
```markdown
[Previous Action Failed]
Error: {error_message}
Think: How to recover from this error?
```

### Phase 4: Testing & Benchmarking (Week 4)

**Test Cases**:
| Scenario | Expected Behavior |
|----------|------------------|
| Directory listing | `repo_tree` called, no verbal description |
| File read | Direct `read_file`, no `file_exists` pre-check |
| Edit task | Ends with `edit_file`/`precision_edit` call |
| Tool error | Self-corrects in next turn |

---

## 6. Files Summary

| File | Action | Lines |
|------|--------|-------|
| `polaris/kernelone/llm/templates/action_first.j2` | Create | ~50 |
| `polaris/kernelone/llm/prompt_builder.py` | Modify | ~30 |
| `polaris/kernelone/llm/output/action_parser.py` | Create | ~80 |
| `polaris/kernelone/llm/output/thinking_extractor.py` | Create | ~40 |
| `polaris/kernelone/llm/error_recovery/retry_policy.py` | Create | ~100 |
| `polaris/kernelone/llm/error_recovery/context_injector.py` | Create | ~60 |

**Total**: ~360 lines new, ~30 lines modified

---

## 7. Expected Outcomes

| Metric | Before | After |
|--------|--------|-------|
| `file_exists` call frequency | ~15/turn | 0/turn |
| Verbal description rate | ~40% | <5% |
| Incomplete闭环 rate | ~30% | <5% |
| Benchmark tool_call accuracy | 70% | 90%+ |

---

## 8. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Personality overwhelms execution | Medium | High | Strict XML boundary enforcement |
| Marker parsing fragile | Medium | Medium | Regex + fallback + logging |
| Breaking existing prompts | Low | High | A/B test before rollout |

---

## 9. Verification Plan

1. **Unit Tests**: `pytest polaris/kernelone/llm/output/tests/`
2. **Integration Test**: Run benchmark suite with new prompts
3. **A/B Rollout**: 10% → 50% → 100% traffic
4. **Metrics**: Monitor `file_exists` call rate, 闭环 completion rate
