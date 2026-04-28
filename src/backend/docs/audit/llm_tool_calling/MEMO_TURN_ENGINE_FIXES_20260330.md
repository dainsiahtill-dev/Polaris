# TurnEngine 关键修复备忘录

**日期**: 2026-03-30
**执行团队**: Python 架构与代码治理实验室
**状态**: 已实施

---

## 一、问题背景

在 LLM Agent 循环执行过程中，发现以下三类严重 BUG 导致无限循环或上下文污染：

| # | 问题 | 症状 | 根因 |
|---|------|------|------|
| 1 | **用户消息重复注入** | 每次迭代后用户消息被重复加入 context，导致 LLM 在历史中看到多条重复指令 | `build_context_request()` 的消息消费没有"取走"机制，同一消息被多次消费 |
| 2 | **路径格式幻觉** | LLM 将项目结构flat list误读为层级关系（如 `📁 backend/` + `📄 config.py` 被解读为 `backend/config.py`） | 扁平列表格式无法表达目录层级，LLM 训练数据中 `├──` 树形格式才是标准 |
| 3 | **成功死循环（Success Loop）** | LLM 成功读取文件后持续重复读取相同文件，不进入下一步 | 无状态机外显化，LLM 忘记任务进度，无法判断"已完成" |
| 4 | **工具调用前缺失思考过程** | 模型直接生成工具调用，跳过 CoT（Chain of Thought），导致决策粗糙、错误率高 | 提示词无强制约束，引擎无校验机制 |

---

## 二、修复方案

### 2.1 用户消息重复注入 — Consume-on-Read 模式

**问题**：之前的 Safeguard 使用字符串匹配判断消息是否已被消费，逻辑脆弱：
```python
# ❌ 旧代码（脆弱）
any(role == "user" and content == self._pending_user_message for role, content in self._history)
```
如果用户发送两条完全相同的消息，第二条会被错误跳过。

**解决方案**：在 `ToolLoopController` 中引入 `_last_consumed_message` 字段，取走（consume）而非匹配：

```python
# ✅ 新代码（健壮）
_last_consumed_message: str = ""  # 追踪上次 build_context_request 已消费的消息ID

def build_context_request(self, ...) -> ContextRequest:
    # 消费新消息时清理旧标记
    for role, content in new_messages:
        if role == "user":
            self._pending_user_message = content
            self._last_consumed_message = content  # 消费后立即标记
```

**清除时机**：所有早期返回路径（`run()` 979行、`run_stream()` 1368行、`safety_stop` 1435行、`policy_stop` 1479行）都必须清除 `_last_consumed_message`，否则下一轮迭代会错误地认为上一条消息已被消费。

**涉及文件**：
- `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

---

### 2.2 路径格式幻觉 — 标准树形格式

**问题**：`context_gateway.py` 使用 emoji + flat list 展示项目结构：
```
📁 backend/
📄 config.py
```
LLM 容易将"在同一个列表中"误解为"在同一个目录下"。

**解决方案**：新增共享工具 `format_workspace_tree()`，输出标准 UNIX tree 格式：

```
.
├── backend/
│   └── api.py
├── config.py
└── README.md
```

**实现要点**：
- 使用 `├──` 和 `└──` 标准层级标记（LLM 训练数据中见过无数次的格式）
- 目录深度限制 `max_sub_items=5`，避免过长输出
- 可配置排除 `.github`, `.vscode`, `__pycache__`, `.git`

**涉及文件**：
- `polaris/kernelone/fs/tree.py`（新增）
- `polaris/kernelone/fs/__init__.py`（导出）
- `polaris/cells/roles/kernel/internal/context_gateway.py`（使用）

---

### 2.3 成功死循环检测 — Success Loop Detector

**问题**：LLM 连续 3 次以上以相同参数调用同一"读文件"工具，每次都成功，但从不进入下一步。

**解决方案**：在 `ToolLoopController` 中实现成功循环检测：

```python
_recent_successful_calls: list[tuple[str, str]] = field(default_factory=list)
SUCCESS_LOOP_WARNING_THRESHOLD = 2

def _track_successful_call(self, tool_result: dict[str, Any], tool_name: str) -> None:
    # 记录成功调用，超过阈值注入系统警告
    if successful:
        self._recent_successful_calls.append((tool_name, args_hash))
        if repeat_count >= self.SUCCESS_LOOP_WARNING_THRESHOLD:
            self._inject_success_loop_warning(tool_name, repeat_count)
```

**阈值设定**：`SUCCESS_LOOP_WARNING_THRESHOLD = 2`（第3次重复时触发警告）

**涉及文件**：
- `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

---

### 2.4 强制 `<thinking>` Scratchpad

**问题**：LLM 在工具调用前不输出思考过程，导致：
- 多步任务遗忘（上下文坍缩 / Attention Drift）
- 决策粗糙、参数错误多
- 可观测性差（无法从日志回放决策思路）

**解决方案**：软约束（Prompt） + 真·硬拦截（Engine）双管齐下。

#### Prompt 层：输出顺序约束（防人设反噬）

**注意**：`<thinking>` 约束**仅限 Director 角色**，其他角色（PM/Architect/QA）不受此约束。

Director 提示词采用**两步输出顺序**，强制先思考后角色：

```
## 输出顺序（强制 — 防止人设反噬）
生成回复时，**必须严格按以下顺序输出**，禁止颠倒：

第一步（理智区 — 先执行）：在 `<thinking>...</thinking>` 标签内，用极简现代白话文（不超过 50 字）盘点。

第二步（角色区 — 后表达）：在 `</thinking>` 闭合标签之后，才允许展现人设语气特色。

【禁止事项】
⚠️ 严禁在 `<thinking>` 之前输出任何角色台词、颜文字或打招呼！
```

> ⚠️ **人设反噬（Persona Bleed）警示**：若不规定输出顺序，LLM 会先输出一堆角色台词（如"和我签订契约吧✨"），然后忘记输出 `<thinking>`。原因是角色扮演的"引力"太强，一旦先说了台词就回不到 XML 标签的严谨状态了。

#### 硬约束（引擎 — 真·硬拦截）

在 `turn_engine.py` 的 `run()` 和 `run_stream()` 中：

```python
if exec_tool_calls and not str(turn.thinking or "").strip():
    # 伪造阻塞结果，工具不执行
    for call in exec_tool_calls:
        all_tool_results.append({
            "tool": call.tool,
            "success": False,
            "error": f"TOOL_BLOCKED: 缺失 <thinking> 标签，{call.tool} 被拦截。",
        })
    return _build_run_result(error="TOOL_BLOCKED: 缺失 <thinking> 标签", is_complete=False)
```

**设计决策**：
- **真·硬拦截**：工具直接返回 `TOOL_BLOCKED` 错误，LLM 下次必然先写 thinking
- **软警告不够**：之前的"警告后继续执行"让 LLM 认为"犯规也无妨"
- **仅针对 Director**：其他角色不强制 thinking，避免约束泄漏
- **Thinking 示例固定为机器逻辑语气**：`"工具执行成功。下一步：验证结果后向用户汇报。"`

**涉及文件**：
- `polaris/cells/roles/kernel/internal/prompt_templates.py`
- `polaris/cells/roles/kernel/internal/turn_engine.py`

---

### 2.5 Persona 系统 — 双重人格设计模式

**问题**：没有任何角色个性区分，所有角色语气相同，缺乏沉浸感和辨识度。

**解决方案**：采用"灵魂与肉体解耦"架构，将提示词分为：
- **基座模板**（固定）：安全边界、输出契约、思考约束、工具策略
- **Persona 补丁**（动态）：性格、口吻、词汇，由 `PERSONA_REGISTRY` 配置

#### 数据结构

```python
@dataclass
class Persona:
    name: str                      # 显示名称，如"Director"
    traits: str                    # 身份基调
    tone: str                      # 语气特点
    vocabulary: list[str]          # 特色词汇
    example: str = ""              # 思考示例
```

**Persona 配置源**：`assets/personas.yaml`（100 种风格 + 1 内置 default fallback）

#### 模板注入机制

基座模板使用占位符，运行时动态替换：

```python
# 基座模板（prompt_templates.py）
"director": """
# Role
你是 {persona_name}（Director），{persona_traits}
...
"""

# 注入后输出
"你是Director（Director），大国工匠与总工程师..."
```

#### 引擎集成

- `RolePromptPolicy` 新增 `persona_id: str = "default"` 字段
- `PromptBuilder.build_system_prompt()` 新增 `persona_id` 参数，支持运行时覆盖
- L1 缓存键：`template_id:version:persona_id`（不同 persona 独立缓存）
- `profile_fingerprint` 也加入 `persona_id`（影响校验指纹）

#### Persona 隔离设计（防"人设泛滥"）

```
<thinking> 内部思考区：强制机器逻辑，不允许角色扮演 </thinking>

面向用户正文：尽情展现 persona 的性格与语气
```

**涉及文件**：
- `polaris/cells/roles/kernel/internal/prompt_templates.py`（新增 PERSONA_REGISTRY + build_persona_prompt）
- `polaris/cells/roles/profile/internal/schema.py`（RolePromptPolicy 新增 persona_id 字段）
- `polaris/cells/roles/kernel/internal/prompt_builder.py`（build_system_prompt 支持 persona_id）
- `polaris/kernelone/storage/persona_store.py`（workspace 级 persona 持久化）

#### Workspace 级 Persona 随机固化

首次加载 workspace 时，若 `persona_id` 为 `default`（未设置），从 100 种 persona 中**随机选择**一个，并固化到 `workspace/.kernelone/role_persona.json`。后续加载直接读取，不再随机。

**持久化路径**：`workspace/.kernelone/role_persona.json`（遵循 KernelOne Storage Layout `workspace/*` 规范）

**生命周期**：PERMANENT（项目级持久化，随 git 走）

**固化逻辑**：
```python
if raw_persona_id == "default" and self.workspace:
    resolved_persona_id = load_workspace_persona(
        self.workspace,
        list(get_persona_registry().keys()),  # 100 种 persona
    )
```

**清除方式**：调用 `clear_workspace_persona(workspace)` 可清除固化记录，下一次加载会重新随机。

---

## 三、基础设施确认（未改动，直接复用）

| 组件 | 位置 | 用途 |
|------|------|------|
| `ThinkingEngine` | `polaris/kernelone/memory/thinking/engine.py` | 管理结构化思考阶段 |
| `StreamThinkingParser` | `polaris/kernelone/llm/providers/stream_thinking_parser.py` | 流式解析 `<thinking>` 标签 |
| `ReasoningStripper` | `polaris/kernelone/llm/reasoning/stripper.py` | 输出时剥离 thinking |
| `ReasoningSanitizer` | `polaris/kernelone/llm/reasoning/sanitizer.py` | 会话级随机标签防注入 |

---

## 四、测试状态

| 测试文件 | 结果 |
|----------|------|
| `test_transcript_leak_guard.py` | 31 PASSED |
| `test_turn_engine_compat_methods.py` | 2 PASSED |
| `test_prompt_builder_cache.py` | 30 PASSED |
| `test_prompt_builder_chunks.py` | 2 PASSED |
| **相关测试合计** | **63 PASSED** |

---

## 五、市面主流方案对比

| 流派 | 代表 | 机制 | 优点 | 缺点 |
|------|------|------|------|------|
| **Anthropic XML 标签** | Claude | `<thinking>...</thinking>` 强制前缀 | 解析简单，流式体验好，可折叠展示 | 需要模型配合遵循指令 |
| **OpenAI Schema 内嵌** | GPT-4o | JSON Schema 强制 `_thought` 字段 | JSON Mode 强约束 | 不适用于纯自然语言回复场景 |
| **ReAct 结构化前缀** | LangChain | `Thought:` / `Action:` / `Observation:` 循环 | 通用性强 | 依赖模型指令遵循能力 |

本次实施采用 **Anthropic 流派 + 引擎硬校验**，最契合现有 `StreamThinkingParser` 基础设施。

---

## 六、后续建议

1. **监控成功循环触发频率**：通过 benchmark 观察 `<thinking>` 契约实施后 success loop 警告的触发次数
2. **阈值调优**：`SUCCESS_LOOP_WARNING_THRESHOLD = 2` 可根据实际效果调整为 1 或 3
3. **前端可视化**：`<thinking>` 内容可渲染为可折叠面板（参考 ChatGPT 的"思考中"），提升用户可观测性
