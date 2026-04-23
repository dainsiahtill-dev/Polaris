# Polaris CLI 视觉增强蓝图

**Date:** 2026-04-08
**Status:** Planning
**Goal:** 打造世界级 CLI 视觉体验，超越 Claude Code

---

## 1. 背景与目标

当前 CLI 渲染效果问题：
- Banner 使用纯文本 box-drawing，视觉平淡
- `--debug` 模式日志在 Banner 之前输出，干扰视觉
- Instructor 警告直接打印，没有美化
- 缺乏品牌标识和 ASCII art 元素
- 帮助信息和提示文字没有层次感

**目标：**
- 首次启动时呈现专业、现代的 CLI 界面
- 使用 Rich 框架实现渐变色、图标化、层次分明的输出
- 将启动日志与 Banner 分离，提升可读性
- 保持 `--backend plain` 的轻量优势

---

## 2. 改进矩阵

| # | 改进项 | 优先级 | 复杂度 | 效果 |
|---|--------|--------|--------|------|
| V1 | Rich Banner 面板 + 渐变色 | P0 | Low | ⭐⭐⭐⭐⭐ |
| V2 | 启动日志静默/延迟输出 | P0 | Low | ⭐⭐⭐⭐⭐ |
| V3 | 品牌标识 + ASCII art 角标 | P1 | Medium | ⭐⭐⭐⭐ |
| V4 | REPL 提示符美化 | P1 | Medium | ⭐⭐⭐⭐ |
| V5 | 工具执行结果高亮 | P1 | Medium | ⭐⭐⭐ |
| V6 | 进度动画 + 状态指示器 | P2 | Medium | ⭐⭐⭐ |

---

## 3. 详细设计

### 3.1 Rich Banner 面板（V1）

**目标：** 使用 Rich Panel 替代纯文本 box-drawing

**设计：**
```python
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.style import Style

# 品牌色：青色渐变
BANNER_STYLES = {
    "title": "bold cyan",
    "border": "cyan",
    "accent": "green",
    "muted": "dim",
}

def _print_banner_rich():
    header = Text.assemble(
        ("◈ ", "cyan bold"),
        ("Polaris CLI", "bold cyan"),
        ("  ·  ", "dim"),
        ("interactive session", "dim"),
    )
    panel = Panel(
        header,
        border_style="cyan",
        title="[bold]Polaris[/bold]",
        subtitle="[dim]Type /help for commands[/dim]",
    )
    console.print(panel)
```

**输出效果：**
```
╔══════════════════════════════════════════════════════╗
║  ◈ Polaris CLI         interactive session         ║
╠══════════════════════════════════════════════════════╣
║  ▸ workspace    /path/to/project                    ║
║  ▸ role        [director] architect pm qa         ║
╠══════════════════════════════════════════════════════╣
║  Type /help or press Tab to autocomplete          ║
╚══════════════════════════════════════════════════════╝
```

### 3.2 启动日志静默（V2）

**目标：** 将基础设施日志与 Banner 分离

**设计：**
```python
# 在 Banner 输出前，抑制 INFO/DEBUG 日志
# Banner 输出后，恢复日志输出

def _suppress_startup_logs():
    """Suppress logging during Banner display."""
    logging.getLogger("polaris.infrastructure.llm").setLevel(logging.WARNING)
    logging.getLogger("polaris.cells.roles.session").setLevel(logging.WARNING)

def _restore_logs():
    """Restore logging after Banner display."""
    level = os.environ.get("KERNELONE_CLI_LOG_LEVEL", "WARNING")
    logging.getLogger("polaris").setLevel(getattr(logging, level))

# 在 _print_banner() 前后调用
_suppress_startup_logs()
_print_banner_rich()
_restore_logs()
```

### 3.3 品牌标识（V3）

**设计：**
```python
# 简洁的 ASCII art
KERNELONE_ART = """
[cyan bold]
    ╔══════════════════════════════════════════╗
    ║   ◈  Polaris CLI  ·  Polaris        ║
    ╚══════════════════════════════════════════╝
[/cyan bold]
"""

# 角标符号使用：◈ ◇ ◆ ▶ ▸ ▹
```

### 3.4 REPL 提示符美化（V4）

**目标：** 提示符显示当前角色和状态

**设计：**
```python
ROLE_PROMPT_SYMBOLS = {
    "director": "◉",
    "pm": "◆",
    "architect": "◇",
    "chief_engineer": "◈",
    "qa": "◎",
}

def _render_prompt(role: str, keymode: str = "auto") -> str:
    symbol = ROLE_PROMPT_SYMBOLS.get(role, "▸")
    return f"[cyan]{symbol}[/cyan] [{role}] > "
```

### 3.5 工具执行高亮（V5）

**目标：** 工具调用和结果使用不同颜色

**设计：**
```python
TOOL_STYLES = {
    "read_file": "blue",
    "write_file": "green",
    "execute": "red bold",
    "search": "yellow",
}

def _print_tool_call(tool_name: str, args: dict) -> None:
    style = TOOL_STYLES.get(tool_name, "cyan")
    console.print(f"[{style}]▸ {tool_name}[/{style}]", args)
```

---

## 4. 架构约束

- **Rich 优先**：所有 UI 改进使用 Rich 框架
- **回退兼容**：非 TTY 模式或 Rich 不可用时，回退到纯文本
- **性能优先**：Banner 渲染 < 50ms
- **UTF-8**：所有输出显式 UTF-8

---

## 5. 验收标准

- [ ] V1: Banner 使用 Rich Panel，带渐变色和图标
- [ ] V2: 启动日志不干扰 Banner 显示
- [ ] V3: 品牌标识可见（ASCII art 或角标）
- [ ] V4: 提示符显示角色符号
- [ ] V5: 工具执行结果分色高亮
- [ ] V6: 进度动画流畅（spinner + percentage）
- [ ] 所有 pytest 通过
- [ ] Ruff format + check 通过
- [ ] 保持 `--backend plain` 轻量输出

---

## 6. Agent 分工

| Agent | Features | 职责 |
|-------|----------|------|
| Agent 1 | V1 | Rich Banner + 品牌标识 |
| Agent 2 | V2 | 启动日志静默/分离 |
| Agent 3 | V3 | REPL 提示符美化 |
| Agent 4 | V4+V5 | 工具执行高亮 + 进度动画 |
| Agent 5 | 集成 + 测试 | 端到端测试 |
