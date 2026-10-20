# Polaris 外部工程 Agent 契约与模板

本文从仓库根 `CLAUDE.md` 原样迁出，保留完整约束。适用任务须在修改或派工前读取；与更细粒度架构规范冲突时遵循原有优先级。

## 外部并行工程 Agent 调用规范

Codex、Claude Code 等主 Agent 可以把独立工程任务派发给外部 Sub-Agent。默认协议是 **Claude CLI JSON Sub-Agent**；OpenCode 只保留为兼容审计路径。所有外部 Agent 只能作为主 Agent 的工程实施/审计工具使用，不属于 Polaris 平台自身。禁止在 Polaris 产品代码、Factory Bench、Run Ledger、ContextOS、ReceiptStore、UI、runtime event 或质量门禁中引入对 Claude/OpenCode 外部 Agent 的运行时依赖、调度逻辑、状态投影或成功条件。

### 调用方式

单个任务默认使用 Claude CLI JSON 模式：

```bash
claude -p "<完整任务提示词>" \
  --dangerously-skip-permissions \
  --output-format json \
  --json-schema '<JSON_SCHEMA>'
```

多个互不重叠的任务可以并行执行，最多 3 个 Sub-Agent。Sub-Agent 必须显式声明 `mode=audit` 或 `mode=implementation`：审计任务只读；实施任务可以直接写代码，但共享主仓并发写入只允许在文件/目录/职责集合完全互斥时使用，否则必须使用独立 worktree/sandbox，或降级为串行。

默认派工策略是 **implementation-first for bounded work**：当账本项已经有明确根因、授权文件范围、验收命令和无交叉依赖时，主 Agent 必须优先派 `mode=implementation` Sub-Agent 直接修改代码/测试/文档，而不是只让外部 Agent 审计。`mode=audit` 只适用于边界尚未确定、需要先确认事实源、跨 Cell 共享接口仍未定、或可能与其他正在运行的 Agent 冲突的高风险阶段。审计完成后如果形成了互斥范围，下一轮必须转为 `mode=implementation` 或由主 Agent 亲自收敛，禁止长期停留在“只审计不落地”。

### Principal Architect 生产级执行标准

所有 `mode=implementation` Sub-Agent 都必须按 Principal Architect 标准执行，而不是按临时补丁工模式执行：

强制提示词前缀必须包含：请以首席 Python 架构师标准完成任务：先做职责划分与架构设计，再给出生产级实现。要求高内聚、低耦合、类型清晰、边界明确、异常可追踪、结构可扩展、代码可测试、日志与配置工程化，并解释设计取舍、风险点和后续演进方向。

每个 `mode=implementation` Sub-Agent 还必须保持工程素质底线：先定义问题边界、职责分层和模块关系，再落地代码；保持高内聚、低耦合、单一职责和接口清晰；核心逻辑必须与 I/O、存储、第三方依赖和框架细节隔离；优先设计可扩展、可替换、可测试的结构；避免过度设计但保留合理扩展点。Python 代码必须严格遵循 PEP 8、语义命名、完整公共类型注解、必要 docstring、低嵌套和单一职责；禁止重复代码、魔法数字、全局可变状态和炫技式写法。工程实现必须使用 `logging` 而非 `print` 作为正式输出手段，配置与代码分离，资源生命周期明确，错误处理具体、可观测、可排查，并考虑幂等性、并发安全、输入校验、异常输入、空值处理和失败恢复。

Sub-Agent 报告的 `execution_summary` 或 `architecture_blueprint` 必须按以下顺序覆盖：需求理解与设计假设；架构设计与模块划分；核心接口说明；完整代码实现摘要；关键设计决策与取舍；风险、边界条件与性能分析；测试策略与后续演进建议。

1. 修改前必须先形成架构蓝图，并在 JSON 报告的 `architecture_blueprint` 中说明系统拓扑、模块职责、核心数据流、状态转移和关键技术取舍。
2. 实现必须是完整生产级交付，禁止占位、伪代码、演示分支、空壳符号、`TODO`、`NotImplemented`、无意义 `pass` 或“后续补齐”。
3. 公共接口必须具备清晰类型注解、契约边界和错误语义；Python 代码必须遵循现代 PEP 8、Ruff/Black 约束，优先通过 `mypy --strict` 或项目等价严格门禁。
4. 核心逻辑必须与 I/O、配置、存储、网络、框架和 CLI/HTTP 适配层解耦，保持高内聚、低耦合、单一职责、可替换和可测试。
5. 错误处理必须捕获具体异常并保留可定位上下文；禁止裸 `except`、吞异常、静默 fallback、硬编码成功或把失败改写为通过。
6. 必须显式考虑空值、非法输入、重复调用、幂等性、并发安全、资源释放、跨平台路径、权限边界和回滚/恢复路径。
7. 报告必须包含 `complexity_analysis`，说明关键逻辑时间复杂度、空间复杂度、潜在性能瓶颈和未来优化方向。
8. 测试必须覆盖 Happy Path、Edge Cases、Exceptions 和 Regression；不得用 mock/fake 替代任务要求验证的真实平台路径。
9. 报告必须包含 `self_check`，至少声明：无占位实现、无越界修改、类型/格式/测试门禁结果、剩余风险和是否需要主 Agent 合并复核。
10. 若任务边界不足以安全写代码，Sub-Agent 必须返回 `status=blocked` 并说明缺口；不得猜测授权范围或扩大 scope。

```bash
REPORT_DIR="$HOME/.polaris/audit_archives/subagents/<batch>"
mkdir -p "$REPORT_DIR"
claude -p "<Agent 01 完整提示词>" --dangerously-skip-permissions --output-format json --json-schema '<JSON_SCHEMA>' > "$REPORT_DIR/agent-01.json" &
claude -p "<Agent 02 完整提示词>" --dangerously-skip-permissions --output-format json --json-schema '<JSON_SCHEMA>' > "$REPORT_DIR/agent-02.json" &
claude -p "<Agent 03 完整提示词>" --dangerously-skip-permissions --output-format json --json-schema '<JSON_SCHEMA>' > "$REPORT_DIR/agent-03.json" &
wait
```

禁止把 `--bg` 作为默认派工协议：它与 `-p` 的 JSON 闭环语义不同，且容易留下非结构化日志。`claude agents --json` 只用于查看 Claude 会话状态，不是 Polaris 外部子任务报告。

OpenCode 兼容路径只允许在 Claude CLI 不可用或用户显式要求时使用，并且必须落盘同等 JSON 报告。OpenCode 结论不得成为 Polaris 事实源。

### 输出 JSON Schema 基线

每个 Sub-Agent 必须按 schema 输出机器可读 JSON，并同时由 shell 重定向落盘到 `~/.polaris/audit_archives/subagents/<batch>/agent-<id>.json`。`/tmp` 仅允许保存可丢弃 scratch，不得作为唯一报告或审计证据源。推荐最小 schema：

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": [
    "task_id",
    "mode",
    "status",
    "summary",
    "scope",
    "files_read",
    "files_modified",
    "commands_run",
    "findings",
    "risks",
    "architecture_blueprint",
    "complexity_analysis",
    "testing_evidence",
    "self_check",
    "next_action"
  ],
  "properties": {
    "task_id": {"type": "string"},
    "mode": {"type": "string", "enum": ["audit", "implementation"]},
    "status": {"type": "string", "enum": ["success", "blocked", "failed"]},
    "summary": {"type": "string"},
    "scope": {"type": "array", "items": {"type": "string"}},
    "files_read": {"type": "array", "items": {"type": "string"}},
    "files_modified": {"type": "array", "items": {"type": "string"}},
    "commands_run": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["command", "exit_code", "purpose"],
        "properties": {
          "command": {"type": "string"},
          "exit_code": {"type": "integer"},
          "purpose": {"type": "string"}
        }
      }
    },
    "findings": {"type": "array", "items": {"type": "string"}},
    "risks": {"type": "array", "items": {"type": "string"}},
    "architecture_blueprint": {"type": "string"},
    "complexity_analysis": {"type": "string"},
    "testing_evidence": {"type": "array", "items": {"type": "string"}},
    "self_check": {"type": "array", "items": {"type": "string"}},
    "next_action": {"type": "string"}
  }
}
```

Claude CLI 的 `--output-format json` stdout 是 Claude 执行 envelope，不一定直接等于上面的 schema 根对象。主 Agent 回收时必须按顺序解析：

1. 先读取顶层 JSON。
2. 若顶层存在 `structured_output` 且非空，优先把它作为 Sub-Agent 报告。
3. 否则若顶层存在字符串字段 `result`，必须再次 `json.loads(result)` 得到 Sub-Agent 报告。
4. 只有解析出的内层报告匹配 schema，才算该 Sub-Agent 有效完成。
5. 若进程超时、exit code 非 0、顶层 `is_error=true`、`result` 非 JSON 或 schema 校验失败，必须把该 Sub-Agent 标记为 `blocked | failed`，禁止把外层 envelope 当成成功报告。

### 主 Agent 职责

调用外部 Sub-Agent 前，主 Agent 必须先：

1. 阅读仓库中的 `AGENTS.md` 及相关架构文档。
2. 检查 `git status`、`git diff`、失败测试和用户反馈。
3. 使用仓库提供的代码图谱、符号索引或 MCP 工具审计相关代码。
4. 将问题拆分成多个互不重叠、可独立完成的任务包。
5. 为每个任务包明确目标、代码范围、禁止事项和验收命令。
6. 为每个任务包写明 JSON schema、报告落盘路径和允许修改边界。
7. 若任务源自角色工具调用失败，主 Agent 可派发只读审计最终 LLM 上下文、工具调用归一化路径、ToolSpec/arg_aliases、runtime event、LLM 调用日志与 ContextOS 证据；若事件中 `messages`/`content` 被 redacted，必须同时提供 `context_snapshot_ref` 对应的完整上下文快照文件；审计任务默认只读，除非已经拆出互不重叠的明确修复范围。
8. Factory Bench 与 Polaris 运行时不得要求、生成或消费 `claude_subagent_audit`、`opencode_audit` 或类似外部审计字段作为机器可读平台字段；角色工具失败归因必须依赖 Polaris 自身的 provider request、runtime event、ContextOS、ReceiptStore、Run Ledger、命令门禁和日志证据。

适合并行的任务示例：

- 修复一个独立的配置传递问题。
- 修复一个独立的超时判定问题。
- 审计一个多实例调度链路。
- 修复一个提供商集成的静默降级问题。
- 为一个已经确认的缺陷补充生产修复和回归测试。

以下情况不得并行：

- 多个 Agent 需要修改同一个文件。
- 多个任务依赖同一个公共接口变更。
- 一个任务的实现依赖另一个任务的结论。
- 任务边界尚未明确。

此时应改为串行执行。

### Agent 提示词要求

每个 `claude -p` 必须获得完整、自包含的提示词，不能依赖当前对话中的隐含上下文。

提示词至少应包含：

- Agent 编号和名称。
- 独立任务目标。
- 必须阅读的规范文件。
- 必须使用的代码审计工具。
- 允许修改的代码范围。
- 禁止修改的范围。
- 强调充分利用codegraph MCP
- 不可违反的架构约束。
- 工业级工程标准：UTF-8、完整实现、类型注解、异常边界、测试矩阵、复杂度说明和自检项。
- 必须运行的验证命令。
- JSON schema 和报告落盘路径。

### Sub-Agent 通用规则

每个外部 Sub-Agent 必须遵守：

1. 修改前阅读根目录及相关子目录中的 `AGENTS.md`。
2. 修改前检查 `git status` 和现有 `git diff`。
3. 保留用户已有修改，不得覆盖、回退或清理无关改动。
4. 先使用指定的代码图谱、符号索引或 MCP 工具审计代码路径，禁止先盲目搜索和修改。
5. 只能修改任务明确授权的范围。
6. 禁止顺手重构、全仓格式化或修改无关代码。
7. 必须修复根因，禁止表层绕过。
8. 禁止硬编码成功、吞掉异常、静默 fallback 或禁用检查。
9. 禁止仅修改测试来制造通过结果。
10. 禁止用 mock 或 fake 替代任务要求验证的真实执行路径。
11. 禁止修改生成物或下游项目来掩盖源代码缺陷。
12. 修改后必须运行任务中指定的质量门禁。
13. 未实际执行的命令不得报告为通过。
14. 最终必须输出机器可读的 JSON 报告。
15. 所有文本文件读写必须显式使用 `UTF-8`（包括日志/JSON/Markdown/代码文件）。
16. `mode=implementation` 的 Sub-Agent 必须交付生产级完整实现，不得提交占位、伪代码、演示代码、未实现分支、空壳类/函数或“后续补齐”类文本。
17. Python 代码必须遵循现代 PEP 8、Ruff/Black 约束和清晰命名；公共函数、类、dataclass、TypedDict、协议和返回对象必须有明确类型注解与边界说明。
18. 复杂核心逻辑必须与 I/O、配置、存储、网络、框架细节解耦；不得把一次性胶水逻辑塞进跨 Cell 公共路径。
19. 异常处理必须捕获具体异常并保留可定位错误信息；禁止裸异常捕获、吞异常、用 `pass` 掩盖失败、或把失败改写成成功。
20. 修改必须考虑空值、非法输入、重复调用、幂等性、并发安全、资源释放和跨平台路径边界；发现无法覆盖的边界必须在 JSON `risks` 中明确说明。
21. 核心算法或扫描逻辑必须在报告中说明时间复杂度、空间复杂度、潜在性能瓶颈和后续优化方向。
22. 测试必须覆盖 Happy Path、Edge Cases、Exceptions 和 Regression；可以用 mock 隔离外部依赖，但不得 mock 掉任务要求验证的真实平台路径。
23. `mypy --strict` 或项目等价严格类型门禁能跑时必须运行；若仓库当前 strict 不可用，至少运行任务指定 mypy/pyright 门禁，并在报告中说明 strict 阻塞原因。
24. 输出报告必须包含自检结论：无占位实现、无越界文件、门禁命令和退出码、剩余风险、以及是否需要主 Agent 复核合并。

### 标准提示词模板

```text
你是 <项目名称> 工程修复 Agent <编号>/<名称>。

硬性要求：
1. 必须先阅读 AGENTS.md 以及以下相关规范：
   - <相关 AGENTS.md>
   - <架构文档>
2. 必须先使用 <代码图谱或 MCP 工具> 审计相关代码路径，禁止先盲改。
3. 只能修改本任务明确授权的代码范围。
4. 必须保留现有无关修改，禁止覆盖或回退用户工作。
5. 修复必须针对根因，禁止表层绕过、硬编码成功、静默 fallback 或只改测试。
6. 不得违反任务列出的架构约束。
7. 修改后必须运行全部验收命令。
8. 最终必须按调用方提供的 JSON schema 输出执行报告，并由调用方落盘到 ~/.polaris/audit_archives/subagents/<batch>/agent-<id>.json。
9. 充分使用codegraph。
10. 所有文本读写必须显式使用 UTF-8。
11. 动代码前必须先形成架构蓝图，并在 JSON `architecture_blueprint` 中记录系统拓扑、模块职责、数据流、状态转移和技术取舍。
12. 必须交付生产级完整实现：禁止占位、伪代码、演示代码、空壳符号、未实现分支或“后续补齐”。
13. Python 代码必须具备清晰类型注解、具体异常处理、可定位错误信息和必要的 Google Style docstring；禁止裸异常捕获、吞异常、静默 fallback。
14. 核心逻辑必须与 I/O、配置、存储、网络、框架细节解耦；优先高内聚、低耦合、单一职责。
15. 必须覆盖 Happy Path、Edge Cases、Exceptions、Regression；不得用 mock/fake 替代任务要求验证的真实平台路径。
16. 报告中必须说明关键逻辑的时间复杂度、空间复杂度、性能瓶颈、剩余风险和自检结果。

任务目标：
<这个 Agent 独立负责的缺口>

预期结果：
<修复后应当观察到的行为>

允许修改范围：
- <文件、目录或符号>

禁止修改范围：
- <文件、目录、生成物或下游项目>

架构约束：
- <必须保留的架构约束>
- <禁止新增的实现方式>

必须完成：
1. <审计或实现要求>
2. <测试要求>
3. <兼容性要求>

必须验证：
- <lint 命令>
- <format check 命令>
- <type check 或 build 命令>
- <相关测试命令>

最终输出 JSON（必须匹配调用方 --json-schema）：
{
  "task_id": "<batch>/<编号>",
  "mode": "audit | implementation",
  "status": "success | blocked | failed",
  "summary": "...",
  "scope": [],
  "files_read": [],
  "files_modified": [],
  "commands_run": [
    {
      "command": "...",
      "exit_code": 0,
      "purpose": "..."
    }
  ],
  "findings": [],
  "risks": [],
  "architecture_blueprint": "...",
  "complexity_analysis": "...",
  "testing_evidence": [],
  "self_check": [],
  "next_action": "..."
}
```

### 结果回收

所有 Sub-Agent 完成后，主 Agent 不得直接相信其报告，必须读取 `~/.polaris/audit_archives/subagents/<batch>/agent-*.json` 并重新审计：

```bash
rtk git status
rtk git diff
```

并检查：

- 是否越过任务范围。
- 是否修改了无关文件。
- 是否覆盖了已有修改。
- 是否只改测试而未修复生产代码。
- 是否加入硬编码成功、异常吞噬或静默降级。
- 是否用 mock 替代真实执行路径。
- 是否违反架构约束。
- 报告中的测试和质量门禁是否真的执行。
- 多个 Agent 的修改合并后是否仍然通过验证。

主 Agent 对最终代码、测试结果和最终回复负责。

### 核心原则

外部 Sub-Agent 并行派工必须满足：

- 任务必须窄。
- 修改范围必须互不重叠。
- 证据必须明确。
- 验收命令必须可执行。
- 最终报告必须机器可读。
- 所有结果必须由主 Agent 独立复核。
