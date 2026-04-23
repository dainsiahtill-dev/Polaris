# Polaris CLI Enhancement Blueprint

**Date:** 2026-04-08
**Status:** Planning
**Goal:** 超越 Claude Code CLI 的实际使用体验

---

## 1. 背景与目标

当前 Polaris CLI (`polaris console`) 已有核心流式输出、多角色切换、Diff 彩色渲染能力，但相比 Claude Code CLI 在实际使用体验上仍有明显差距。本蓝图规划 10 个高影响力 Expert Agent 并行实现。

**目标：**
- 在 Tab 补全、命令历史、Token 预算显示、Onboarding 引导等高频体验上超越 Claude Code
- 实现 Claude Code 没有的多角色会话管理、结构化 Diff 渲染能力
- 保持 `--backend plain` 的轻量优势，不引入重型 TUI 依赖

---

## 2. Feature Matrix

| # | Feature | Impact | Complexity | Priority |
|---|---------|--------|------------|----------|
| F1 | Tab 自动补全 + 命令历史 | P0 | Medium | 超越 Claude |
| F2 | 会话历史搜索（readline） | P0 | Low | 超越 Claude |
| F3 | 结构化 JSON 输出 `--output-format` | P0 | Low | 持平 Claude |
| F4 | Token 预算 + 速率显示 | P1 | Low | 超越 Claude |
| F5 | Onboarding 引导流程 | P1 | Medium | 超越 Claude |
| F6 | 会话元信息增强 Banner | P1 | Low | 超越 Claude |
| F7 | Dry-run 模式 | P1 | Medium | 持平 Claude |
| F8 | 非 TTY / Pipe 批量模式 | P1 | Medium | 超越 Claude |
| F9 | Vi/Emacs 键盘模式 | P2 | Medium | 持平 Claude |
| F10 | `/model` 模型切换命令 | P2 | Low | 超越 Claude |

---

## 3. 实现约束

- **不引入新的重型依赖**（已有 Rich 可用）
- **向后兼容**：`--backend plain` 保持轻量 TTY 输出
- **向后兼容**：`--json-render` 选项保持不变
- **Fail-closed**：pytest 100% 通过才算完成
- **UTF-8 强制**：所有输出显式 UTF-8

---

## 4. 架构设计

### 4.1 Tab 补全 + 历史（Feature F1 + F2）

**目标：** 使用 Python 标准库 `readline` 实现命令补全和历史，不引入新依赖

**设计：**
```
input() 替换为 readline-based input
    ├── readline.set_completer()  → Tab 补全
    │       ├── /role <tab> → /role pm /role architect ...
    │       ├── /json <tab> → raw pretty pretty-color
    │       ├── /prompt <tab> → plain omp
    │       └── 文件路径补全（对 /read 等工具）
    └── readline.read_history_file() / write_history_file()
            └── 历史文件: ~/.polaris_cli_history
```

**关键文件：**
- `polaris/delivery/cli/terminal_console.py` — input() 替换
- 新建 `polaris/delivery/cli/cli_completion.py` — 补全器逻辑

### 4.2 Token 预算 + 速率显示（Feature F4）

**目标：** 在流式输出末尾显示 Token 消耗和速率

**设计：**
```
streaming 事件流
    └── 末尾追加 (complete 事件)
            ├── "tokens: prompt={n} completion={n} total={n}"
            ├── "cost: ~$0.XXX" (基于 model pricing)
            └── "speed: {n} tok/s"
```

**关键文件：**
- `polaris/delivery/cli/terminal_console.py` — complete 事件处理

### 4.3 Onboarding 引导（Feature F5）

**目标：** 首次运行检测 + 功能引导（非每次显示）

**设计：**
```
首次运行检测: ~/.polaris_cli_onboarded
    └── 已完成引导 → 跳过
首次运行:
    └── 打印彩色引导面板
            ├── 功能列表（彩色高亮）
            ├── 快捷键说明
            └── "按 Enter 继续..."
```

**关键文件：**
- `polaris/delivery/cli/terminal_console.py` — 引导逻辑

### 4.4 Dry-run 模式（Feature F7）

**目标：** 显示 LLM 会执行的操作但不实际执行

**设计：**
```
--dry-run flag
    └── stream_turn() 改为 dry_run_turn()
            ├── 发送消息给 LLM
            ├── 解析 tool_call 但不执行
            └── 打印将执行的工具列表 + diff
```

### 4.5 非 TTY / Pipe 模式（Feature F8）

**目标：** 支持 `echo "hello" | polaris console --mode batch`

**设计：**
```
STDIN 非 TTY 检测:
    ├── TTY → 交互模式（当前行为）
    └── 非 TTY → 批量模式
            ├── 读取 stdin 全部输入
            ├── 单次 LLM 请求
            └── 输出后立即退出
```

### 4.6 Vi/Emacs 键盘模式（Feature F9）

**目标：** 支持 `set -o vi` / `set -o emacs` 键盘绑定

**设计：**
```
KERNELONE_CLI_KEYMODE=vi|emacs|auto (default: auto)
    └── readline.parse_and_bind() 设置键盘模式
```

---

## 5. 验收标准

- [ ] F1: Tab 补全可用，`/role <tab>` 显示所有角色
- [ ] F2: 上下键遍历历史，Ctrl+R 搜索历史
- [ ] F3: `--output-format json` 输出一行 JSON
- [ ] F4: complete 事件后显示 Token 统计
- [ ] F5: 首次运行显示引导，二次运行跳过
- [ ] F6: Banner 显示 session 创建时间、消息数
- [ ] F7: `--dry-run` 显示将执行的工具但不执行
- [ ] F8: `cat commands.txt | polaris console --batch` 正常工作
- [ ] F9: `KERNELONE_CLI_KEYMODE=vi` 启用 Vi 模式
- [ ] F10: `/model <name>` 在 REPL 内切换模型
- [ ] 所有 pytest 通过
- [ ] Ruff format + check 通过

---

## 6. Agent 分工

| Agent | Features | 职责 |
|-------|----------|------|
| Agent 1 | F1, F2 | Tab 补全 + 历史 |
| Agent 2 | F3 | JSON 输出格式 |
| Agent 3 | F4 | Token 预算显示 |
| Agent 4 | F5 | Onboarding 引导 |
| Agent 5 | F6 | Banner 元信息增强 |
| Agent 6 | F7 | Dry-run 模式 |
| Agent 7 | F8 | 非 TTY / Pipe 模式 |
| Agent 8 | F9 | Vi/Emacs 键盘模式 |
| Agent 9 | F10 | /model 切换命令 |
| Agent 10 | 跨文件集成 + 端到端测试 | 整合验证 |
