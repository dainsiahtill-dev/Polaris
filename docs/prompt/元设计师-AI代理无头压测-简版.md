# Polaris AI 代理无头压测简版提示词

本提示词专门给能力较弱的 AI Agent 使用。

目标只有一个：**不要自己设计压测框架，只运行现成脚本 `scripts/run_agent_headless_stress.py`，并把结果如实汇报出来。**

## 适用场景

- 你已经有一个正在运行的 Polaris backend
- 你需要做的是运行无头压测，而不是写代码、改框架、改目标项目

## 你必须遵守

1. 不要新增任何脚本
2. 不要修改 `tests/agent_stress/`
3. 不要修改目标项目代码
4. 不要改成 Playwright 主链
5. 不要臆造接口
6. 只允许调用当前正式入口：`scripts/run_agent_headless_stress.py`
7. 所有输出文件必须保持 UTF-8
8. 压测脚本不能帮 Polaris 预写任何目标项目内容；它只能切 workspace、发起 run、读取状态、输出自己的审计报告
9. backend context 自动发现和 workspace 门禁由脚本负责，不要自行绕开

## 你的执行步骤

### Step 1: 先跑帮助

先执行：

```bash
python scripts/run_agent_headless_stress.py --help
```

如果这一步失败，直接报告脚本不可运行。

### Step 2: 先跑小规模烟雾

先执行 1 轮小规模验证，不要一上来就跑 20 轮：

```bash
python scripts/run_agent_headless_stress.py --agent-label codex --rounds 1 --stable-required 1
```

如果你压测的是 Claude，把 `--agent-label codex` 改成：

```bash
--agent-label claude
```

### Step 3: 烟雾通过后再跑标准轮次

烟雾通过后，再执行：

```bash
python scripts/run_agent_headless_stress.py --agent-label codex --rounds 5 --stable-required 2
```

如果用户明确要求更高轮次，再把 `--rounds` 调大。

## 你要关注什么

脚本会自动检查这些正式门禁，你只需要如实记录：

- 角色就绪状态
- Factory run 是否完成
- runtime WebSocket 是否有消息
- PM 合同质量是否通过
- Director 是否存在 `metadata.pm_task_id`
- QA 是否得到 `integration_qa_passed`
- 是否出现 prompt leakage

## 成功标准

出现以下两项，才算你执行成功：

1. 命令退出并生成最终 JSON 报告
2. 你能明确说出：
   - 报告路径
   - 最终 `STATUS`
   - 哪些轮次 PASS / FAIL

## 失败时怎么做

如果失败，不要擅自修代码，也不要自己发明新方案。

你只需要输出：

1. 失败命令
2. 终端错误摘要
3. 最后生成的报告路径（如果有）
4. 失败发生在：
   - backend context 不可用
   - 脚本门禁阻断
   - 脚本启动失败
   - 某轮压测失败

## 你的最终回复模板

你可以按下面格式汇报：

```text
执行命令：
<command>

结果：
- STATUS: PASS|FAIL
- 报告路径: <path>
- 完成轮次: <n>

如果失败：
- 失败阶段: <stage>
- 错误摘要: <summary>
```

## 禁止事项

- 不要把“压测失败”解释成“需要我重写框架”
- 不要擅自切到 `tests/agent_stress/`
- 不要擅自切到 Electron / Playwright
- 不要声称修复，除非用户明确要求你修改 Polaris 代码
- 不要输出夸张结论，只报告事实
