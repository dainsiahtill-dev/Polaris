"""观测器状态管理。

ObserverState 数据类和渲染逻辑。
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from rich.console import Group
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .constants import (
    PROJECTION_EVENT_LINE_MAX_CHARS,
    PROJECTION_REASONING_VIEWPORT_LINES,
    ROUND_HEADER_PREFIX,
    ROUND_RESULT_PREFIX,
    STEP_PREFIX,
)
from .renderers import (
    _event_badge,
    _event_label,
    _format_role_status,
    _format_taskboard_execution_backend_label,
    _map_taskboard_status_label,
    _reasoning_event_style,
    _role_badge,
    _runtime_event_visual,
)


@dataclass
class ObserverState:
    """观测器状态。"""

    _TASKBOARD_RUNNING_STATUS_TOKENS: ClassVar[set[str]] = {"in_progress", "running", "claimed", "executing"}
    _TASKBOARD_SPINNER_FRAMES: ClassVar[tuple[str, ...]] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    _LLM_SPINNER_FRAMES: ClassVar[tuple[str, ...]] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    workspace: str
    rounds: int
    strategy: str
    backend_url: str
    output_dir: str
    started_at: float = field(default_factory=time.monotonic)
    current_step: str = "starting"
    current_round: int = 0
    completed_rounds: int = 0
    failed_rounds: int = 0
    warnings: int = 0
    errors: int = 0
    last_status: str = "booting"
    exit_code: int | None = None
    recent_lines: deque[str] = field(default_factory=lambda: deque(maxlen=80))
    projection_enabled: bool = False
    projection_transport: str = "ws"
    projection_focus: str = "all"
    projection_connected: bool = False
    projection_transport_used: str = "none"
    projection_error: str = ""
    projection_chain_status: deque[str] = field(default_factory=lambda: deque(maxlen=14))
    projection_llm: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=100))
    projection_dialogue: deque[str] = field(default_factory=lambda: deque(maxlen=18))
    projection_tools: deque[str] = field(default_factory=lambda: deque(maxlen=18))
    projection_taskboard_summary: str = ""
    projection_taskboard_timestamp: str = ""
    projection_taskboard_items: deque[str] = field(default_factory=lambda: deque(maxlen=16))
    projection_taskboard_todos: deque[dict[str, str]] = field(default_factory=lambda: deque(maxlen=16))
    projection_code_diffs: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=6))
    projection_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=14))

    # 角色实时状态 (idle|running|error)
    role_status_architect: str = "idle"
    role_status_pm: str = "idle"
    role_status_director: str = "idle"
    role_status_qa: str = "idle"
    llm_request_pending: bool = False
    llm_request_timestamp: float = 0.0
    llm_request_role: str = ""
    llm_request_last_outcome: str = "idle"  # idle|waiting|completed|failed

    @staticmethod
    def _normalize_timestamp(value: str) -> str:
        """归一化时间戳。"""
        text = str(value or "").strip()
        if not text:
            return "--:--:--"
        if "T" in text:
            iso_candidate = text
            if iso_candidate.endswith("Z"):
                iso_candidate = f"{iso_candidate[:-1]}+00:00"
            try:
                dt = datetime.fromisoformat(iso_candidate)
                if dt.tzinfo is None:
                    return dt.strftime("%H:%M:%S")
                return dt.astimezone().strftime("%H:%M:%S")
            except ValueError:
                hhmmss = text.split("T", 1)[1].split(".", 1)[0].replace("Z", "")
                if hhmmss:
                    return hhmmss
        if len(text) >= 8 and text[2:3] == ":" and text[5:6] == ":":
            return text[:8]
        return text[:12]

    @staticmethod
    def _safe_json_compact(value: Any, max_chars: int = 220) -> str:
        """压缩为紧凑 JSON。"""
        try:
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            text = str(value)
        compact = str(text).replace("\n", " ").strip()
        if len(compact) > max_chars:
            return f"{compact[:max_chars]}..."
        return compact

    @staticmethod
    def _truncate_line_for_viewport(line: str, *, max_chars: int) -> str:
        """截断行到指定长度。"""
        text = str(line or "").replace("\r", " ").replace("\n", " ")
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        if max_chars <= 3:
            return text[:max_chars]
        return f"{text[:max_chars - 3]}..."

    @staticmethod
    def _safe_non_negative_int(value: Any) -> int:
        """宽松转换为非负整数。"""
        try:
            return max(0, int(value))
        except Exception:
            return 0

    @classmethod
    def _is_running_taskboard_todo(cls, todo: dict[str, str]) -> bool:
        """判断任务是否处于执行中状态。"""
        status_token = str(todo.get("status") or "").strip().lower()
        if status_token in cls._TASKBOARD_RUNNING_STATUS_TOKENS:
            return True
        status_label = str(todo.get("status_label") or "").strip()
        return "执行中" in status_label

    @staticmethod
    def _parse_taskboard_running_count(summary: str) -> int:
        """从 taskboard summary 中提取 running 计数。"""
        text = str(summary or "").strip()
        if not text:
            return 0
        match = re.search(r"\brunning=(\d+)\b", text, flags=re.IGNORECASE)
        if match is None:
            return 0
        try:
            return max(0, int(match.group(1)))
        except Exception:
            return 0

    @classmethod
    def _taskboard_spinner_frame(cls, tick: float | None = None) -> str:
        """获取任务执行中的 spinner 帧。"""
        frames = cls._TASKBOARD_SPINNER_FRAMES
        if not frames:
            return "•"
        basis = time.monotonic() if tick is None else max(0.0, float(tick))
        index = int(basis * 10) % len(frames)
        return frames[index]

    def _build_patch_preview_lines(self, *, patch: str, operation: str, max_lines: int = 12) -> list[str]:
        """构建 patch 预览行（支持统一 diff 与原始内容）。"""
        raw_patch = str(patch or "").replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        if not raw_patch:
            return []

        lines = raw_patch.split("\n")
        has_unified_markers = any(
            line.startswith("@@") or line.startswith("+++ ") or line.startswith("--- ")
            for line in lines
        )

        if not has_unified_markers:
            prefix = "-" if str(operation or "").strip().lower() == "delete" else "+"
            lines = [f"{prefix}{line}" if line else prefix for line in lines]

        if max_lines > 0 and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append("... [diff truncated]")

        return [
            self._truncate_line_for_viewport(line, max_chars=PROJECTION_EVENT_LINE_MAX_CHARS)
            for line in lines
        ]

    @staticmethod
    def _diff_line_style(line: str) -> str:
        """根据 diff 行类型返回渲染样式。"""
        text = str(line or "")
        if text.startswith("+") and not text.startswith("+++ "):
            return "green"
        if text.startswith("-") and not text.startswith("--- "):
            return "red"
        if text.startswith("@@"):
            return "cyan"
        if text.startswith("+++ ") or text.startswith("--- "):
            return "bright_black"
        if text.startswith("... [diff truncated]"):
            return "dim"
        if text.startswith(" "):
            return "white"
        return "white"

    @staticmethod
    def _format_diff_stats(*, added_lines: int, deleted_lines: int, modified_lines: int) -> str:
        """格式化 diff 行统计。"""
        parts: list[str] = []
        if added_lines > 0:
            parts.append(f"+{added_lines}")
        if deleted_lines > 0:
            parts.append(f"-{deleted_lines}")
        if modified_lines > 0:
            parts.append(f"~{modified_lines}")
        return " ".join(parts)

    @staticmethod
    def _summarize_tool_call_payload(raw_payload: str) -> str:
        """将 TOOL_CALL 片段转为人类可读摘要。"""
        payload_text = str(raw_payload or "").strip()
        if not payload_text:
            return ""
        try:
            parsed = json.loads(payload_text)
        except Exception:
            compact = " ".join(payload_text.split())
            if len(compact) > 80:
                compact = f"{compact[:80]}..."
            return compact

        if not isinstance(parsed, dict):
            compact = " ".join(str(parsed).split())
            if len(compact) > 80:
                compact = f"{compact[:80]}..."
            return compact

        tool_name = str(parsed.get("tool") or parsed.get("name") or "unknown").strip() or "unknown"
        args = parsed.get("args")
        if not isinstance(args, dict) or not args:
            return tool_name

        arg_parts: list[str] = []
        for key in list(args.keys())[:2]:
            value = " ".join(str(args.get(key) or "").split())
            if len(value) > 24:
                value = f"{value[:24]}..."
            arg_parts.append(f"{key}={value}")
        args_preview = ", ".join(arg_parts)
        return f"{tool_name}({args_preview})" if args_preview else tool_name

    @staticmethod
    def _summarize_json_like_content(text: str) -> str:
        """将 JSON 预览转换为简洁语义摘要。"""
        candidate = str(text or "").strip()
        if not candidate:
            return ""
        if not (candidate.startswith("{") or candidate.startswith("[")):
            if '"plan_markdown"' in candidate:
                return "计划草案预览（包含 plan_markdown）"
            if '"tasks"' in candidate:
                return "任务清单草案（JSON）"
            return ""

        try:
            parsed = json.loads(candidate)
        except Exception:
            if '"plan_markdown"' in candidate:
                return "计划草案预览（包含 plan_markdown）"
            if '"tasks"' in candidate:
                return "任务清单草案（JSON）"
            return ""

        if isinstance(parsed, dict):
            if "tasks" in parsed:
                tasks = parsed.get("tasks")
                if isinstance(tasks, list):
                    return f"任务清单草案（{len(tasks)} 项）"
                return "任务清单草案"
            if "plan_markdown" in parsed:
                return "计划草案预览（包含 plan_markdown）"
            keys = [str(k) for k in list(parsed.keys())[:4]]
            if keys:
                return f"结构化输出: {', '.join(keys)}"
            return "结构化输出对象"

        if isinstance(parsed, list):
            return f"结构化输出列表（{len(parsed)} 项）"
        return ""

    def _humanize_reasoning_content(self, *, event_type: str, content: str) -> str:
        """将推理面板内容转换为可读文本，屏蔽原始标签噪音。"""
        text = str(content or "").strip()
        if not text:
            return ""

        tool_blocks = re.findall(r"\[TOOL_CALL\](.*?)\[/TOOL_CALL\]", text, flags=re.IGNORECASE | re.DOTALL)
        if not tool_blocks and "[TOOL_CALL]" in text:
            trailing = text.split("[TOOL_CALL]", 1)[1]
            tool_blocks = [trailing]
        if tool_blocks:
            summaries = []
            for block in tool_blocks[:3]:
                summary = self._summarize_tool_call_payload(block)
                if summary:
                    summaries.append(summary)
            if summaries:
                suffix = f" 等{len(tool_blocks)}个" if len(tool_blocks) > len(summaries) else ""
                return f"计划调用工具: {'；'.join(summaries)}{suffix}"

        cleaned = re.sub(r"</?output>", "", text, flags=re.IGNORECASE)
        cleaned = re.sub(r"```(?:json|yaml|yml|md|markdown|text|python)?", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.replace("```", "")
        cleaned = re.sub(r"\[/?TOOL_CALL\]", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()

        json_summary = self._summarize_json_like_content(cleaned)
        if json_summary:
            return json_summary

        compact = " ".join(cleaned.split())
        if event_type in {"content_preview", "content_chunk"} and len(compact) > 220:
            compact = f"{compact[:220]}..."
        return compact

    @staticmethod
    def _get_terminal_width() -> int:
        """获取终端宽度。"""
        try:
            return shutil.get_terminal_size().columns
        except Exception:
            return 120

    def render(self) -> Layout:
        """渲染完整布局。"""
        layout = Layout()
        terminal_width = self._get_terminal_width()

        # 窄屏模式
        if terminal_width < 100:
            layout.split_column(
                Layout(name="header", size=7),
                Layout(name="status_row", size=5),
                Layout(name="reasoning"),
                Layout(name="logs", size=8),
            )
            layout["status_row"].split_row(
                Layout(name="summary", ratio=1),
                Layout(name="projection", ratio=2),
            )
            layout["header"].update(self._render_header())
            layout["summary"].update(self._render_summary())
            layout["logs"].update(self._render_logs())
            layout["reasoning"].update(self._render_reasoning())
            layout["projection"].update(self._render_projection())
        else:
            # 宽屏模式
            layout.split_column(
                Layout(name="header", size=9),
                Layout(name="body"),
            )
            layout["body"].split_row(
                Layout(name="summary", ratio=1),
                Layout(name="logs", ratio=2),
                Layout(name="reasoning", ratio=3),
                Layout(name="projection", ratio=2),
            )
            layout["header"].update(self._render_header())
            layout["summary"].update(self._render_summary())
            layout["logs"].update(self._render_logs())
            layout["reasoning"].update(self._render_reasoning())
            layout["projection"].update(self._render_projection())
        return layout

    def _render_header(self) -> Panel:
        """渲染头部面板。"""
        runtime_seconds = int(max(time.monotonic() - self.started_at, 0.0))
        table = Table.grid(expand=True)
        table.add_column(ratio=1, style="cyan")
        table.add_column(ratio=1, style="cyan")
        table.add_column(ratio=1, style="cyan")

        status_indicator = "◉" if self.exit_code is None else ("✓" if self.exit_code == 0 else "✗")
        status_color = "yellow" if self.exit_code is None else ("green" if self.exit_code == 0 else "red")

        table.add_row(
            f"⚡ [bold yellow]Workspace[/]: [cyan]{self.workspace or '(empty)'}[/]",
            f"📊 [bold yellow]Rounds[/]: [cyan]{self.rounds}[/]",
            f"🎯 [bold yellow]Strategy[/]: [cyan]{self.strategy}[/]",
        )
        table.add_row(
            f"🔮 [bold yellow]Backend[/]: [cyan]{self.backend_url or '(unresolved)'}[/]",
            f"📍 [bold yellow]Step[/]: [yellow]{self.current_step}[/]",
            f"⏱ [bold yellow]Runtime[/]: [cyan]{runtime_seconds}s[/]",
        )
        status = self.last_status
        if self.exit_code is not None:
            status = f"{status} (exit={self.exit_code})"
        table.add_row(
            f"📁 [bold yellow]Output[/]: [cyan]{self.output_dir or '(default)'}[/]",
            f"🔄 [bold yellow]Round[/]: [yellow]{self.current_round or '-'}[/]",
            f"{status_indicator} [bold {status_color}]Status[/]: [{status_color}]{status}[/]",
        )
        if self.projection_enabled:
            projection_status = "connected" if self.projection_connected else "disconnected"
            projection_indicator = "◉" if self.projection_connected else "○"
            projection_extra = f"{self.projection_transport_used}/{self.projection_focus}"
            if self.projection_error:
                projection_extra = f"{projection_extra} (⚠)"
            table.add_row(
                f"{projection_indicator} [bold yellow]Projection[/]: [{'green' if self.projection_connected else 'yellow'}]{projection_status}[/]",
                f"📡 [bold yellow]Transport[/]: [cyan]{projection_extra}[/]",
                "",
            )
        return Panel(
            table,
            title="⚔  Polaris Agent Stress Observer  ⚔",
            border_style="yellow",
            style="on black",
        )

    def _render_summary(self) -> Panel:
        """渲染统计面板。"""
        stats = Table.grid(padding=(0, 1))
        stats.add_column(style="bold yellow")
        stats.add_column(justify="right", style="cyan")

        total = self.completed_rounds + self.failed_rounds
        pass_rate = (self.completed_rounds / total * 100) if total > 0 else 0

        stats.add_row("✓ Passed", f"[green]{self.completed_rounds}[/]")
        stats.add_row("✗ Failed", f"[red]{self.failed_rounds}[/]")
        stats.add_row("⚠ Warnings", f"[yellow]{self.warnings}[/]")
        stats.add_row("❌ Errors", f"[red]{self.errors}[/]")
        stats.add_row("▤ Buffered", f"[cyan]{len(self.recent_lines)}[/]")
        if total > 0:
            stats.add_row("★ Pass Rate", f"[green]{pass_rate:.1f}%[/]")

        return Panel(
            stats,
            title="◈ Summary  ·  压测统计",
            border_style="yellow",
            style="on black",
        )

    def _render_logs(self) -> Panel:
        """渲染日志面板。"""
        if not self.recent_lines:
            content = Text("▸ Waiting for runner output...", style="dim italic")
        else:
            entries = []
            for line in list(self.recent_lines)[-20:]:
                style = "white"
                if line.startswith("## Step"):
                    style = "bold yellow"
                elif "✅" in line or "PASS" in line:
                    style = "green"
                elif "⚠️" in line or "WARNING" in line:
                    style = "yellow"
                elif "❌" in line or "FAIL" in line or "[stderr]" in line:
                    style = "bold red"
                elif ">>>" in line:
                    style = "cyan bold"
                elif "---" in line:
                    style = "dim"
                entries.append(Text(f"  {line}", style=style))
            content = Group(*entries)
        return Panel(
            content,
            title="◈ Live Output  ·  实时日志",
            border_style="bright_blue",
            style="on #1a1a2e",
        )

    @staticmethod
    def _simplify_tool_prefix(prefix: str, spinner: str) -> str:
        """简化等待行前缀，避免过长文本污染视图。"""
        token = " ".join(str(prefix or "").split())
        if not token:
            return spinner
        if len(token) > 32:
            token = f"{token[:32]}..."
        return token

    def _render_loading_spinner(self, sections: list[Any], pending_call: dict[str, Any]) -> None:
        """渲染等待动画 - 当工具调用已发出但未收到结果时显示。"""
        tool_name = str(pending_call.get("tool_name") or "unknown").strip() or "unknown"
        role = str(pending_call.get("role") or "unknown").strip() or "unknown"
        timestamp = str(pending_call.get("timestamp") or "").strip()

        spinner_frames = self._LLM_SPINNER_FRAMES
        frame_idx = int(time.monotonic() * 10) % len(spinner_frames)
        spinner = spinner_frames[frame_idx]

        simple_prefix = self._simplify_tool_prefix(f"{timestamp} {spinner} {role}", spinner)
        header = Text(f"  {simple_prefix} ", style="bold yellow")
        waiting_text = Text(f"等待 {tool_name} 响应", style="yellow")

        dots = "." * ((frame_idx % 3) + 1)
        waiting_text.append(dots, style="yellow")
        sections.append(Text.assemble(header, waiting_text))

    def _render_llm_waiting_spinner(self, sections: list[Any]) -> None:
        """渲染 LLM 请求等待动画。"""
        spinner_frames = self._LLM_SPINNER_FRAMES
        frame_idx = int(time.monotonic() * 8) % len(spinner_frames)
        spinner = spinner_frames[frame_idx]

        role = self.llm_request_role or "unknown"
        elapsed = (
            time.monotonic() - self.llm_request_timestamp
            if self.llm_request_timestamp > 0.0
            else 0.0
        )
        if elapsed < 1:
            time_str = f"{int(elapsed * 1000)}ms"
        elif elapsed < 60:
            time_str = f"{int(elapsed)}s"
        else:
            time_str = f"{int(elapsed // 60)}m{int(elapsed % 60)}s"

        sections.append(Text(""))
        sections.append(Text("─" * 48, style="yellow dim"))
        timestamp = datetime.now().strftime("%H:%M:%S")
        role_badge = _role_badge(role)
        header = Text(f"  {timestamp} {spinner} {role_badge} ", style="bold yellow")
        waiting_text = Text(f"{role.upper()} 思考中", style="bold yellow")
        dots = "." * ((frame_idx % 3) + 1)
        waiting_text.append(dots, style="yellow")
        waiting_text.append(f" ({time_str})", style="dim")
        sections.append(Text.assemble(header, waiting_text))
        sections.append(Text("─" * 48, style="yellow dim"))

    def _update_llm_request_state(self, rows: list[dict[str, Any]]) -> None:
        """根据 LLM 推理流事件更新请求等待状态。"""
        if not rows:
            self.llm_request_pending = False
            self.llm_request_last_outcome = "idle"
            return

        latest = rows[-1]
        latest_event_type = str(latest.get("event_type") or "").strip().lower()
        latest_role = str(latest.get("role") or "unknown").strip().lower()

        waiting_events = {"llm_waiting"}
        completed_events = {"llm_waiting_done", "llm_completed"}
        failed_events = {"llm_waiting_failed", "llm_failed", "llm_error", "error", "tool_failure"}

        # 逆序查找最近生命周期事件，避免 content_preview 等后续事件把 completed 状态误恢复为 pending。
        lifecycle_state: str | None = None
        lifecycle_role = latest_role
        for row in reversed(rows):
            event_type = str(row.get("event_type") or "").strip().lower()
            if event_type in waiting_events:
                lifecycle_state = "waiting"
                lifecycle_role = str(row.get("role") or lifecycle_role or "unknown").strip().lower()
                break
            if event_type in completed_events:
                lifecycle_state = "completed"
                lifecycle_role = str(row.get("role") or lifecycle_role or "unknown").strip().lower()
                break
            if event_type in failed_events:
                lifecycle_state = "failed"
                lifecycle_role = str(row.get("role") or lifecycle_role or "unknown").strip().lower()
                break

        if lifecycle_state == "waiting":
            if not self.llm_request_pending:
                self.llm_request_timestamp = time.monotonic()
            self.llm_request_pending = True
            self.llm_request_role = lifecycle_role or self.llm_request_role
            self.llm_request_last_outcome = "waiting"
            return
        if lifecycle_state == "completed":
            self.llm_request_pending = False
            self.llm_request_role = lifecycle_role or self.llm_request_role
            self.llm_request_last_outcome = "completed"
            return
        if lifecycle_state == "failed":
            self.llm_request_pending = False
            self.llm_request_role = lifecycle_role or self.llm_request_role
            self.llm_request_last_outcome = "failed"
            return

        llm_generating_events = {
            "tool_call",
            "local_llm",
            "local_llm_success",
            "local_llm_failure",
            "llm_request",
        }
        llm_completed_events = {"turn_completed", "tool_result", "llm_response"}

        if latest_event_type in llm_generating_events:
            if not self.llm_request_pending:
                self.llm_request_timestamp = time.monotonic()
            self.llm_request_pending = True
            self.llm_request_role = latest_role or self.llm_request_role
            self.llm_request_last_outcome = "waiting"
            return

        if latest_event_type in llm_completed_events:
            pending_tool = False
            recent_rows = rows[-10:] if len(rows) > 10 else rows
            for idx, row in enumerate(reversed(recent_rows)):
                event_type = str(row.get("event_type") or "").strip().lower()
                if event_type != "tool_call":
                    continue
                tool_name = str(row.get("tool_name") or "").strip()
                has_result = False
                for newer in recent_rows[len(recent_rows) - idx - 1:]:
                    newer_event = str(newer.get("event_type") or "").strip().lower()
                    if newer_event != "tool_result":
                        continue
                    if str(newer.get("tool_name") or "").strip() == tool_name:
                        has_result = True
                        break
                if not has_result:
                    pending_tool = True
                    break

            if not pending_tool:
                self.llm_request_pending = False
                self.llm_request_last_outcome = "completed"
            else:
                if not self.llm_request_pending:
                    self.llm_request_timestamp = time.monotonic()
                self.llm_request_pending = True
                self.llm_request_role = latest_role or self.llm_request_role
                self.llm_request_last_outcome = "waiting"
            return

        if self.llm_request_pending:
            elapsed = time.monotonic() - self.llm_request_timestamp
            if elapsed > 30:
                self.llm_request_pending = False
                self.llm_request_last_outcome = "failed"

    def _render_reasoning(self) -> Panel:
        """渲染推理面板 - 结构化展示工具调用和结果。"""
        if not self.projection_enabled:
            return Panel(Text("Projection disabled", style="dim"), title="[bold]Reasoning[/bold]", border_style="grey50")

        sections: list[Any] = []
        sections.append(Text("═" * 72, style="cyan dim"))
        sections.append(Text("◈ Reasoning  ·  推理思考过程", style="bold cyan"))
        sections.append(Text("═" * 72, style="cyan dim"))

        if not self.projection_llm:
            sections.append(Text("  等待 LLM 事件...", style="dim"))
            return Panel(Group(*sections), title="[bold]Reasoning[/bold]", border_style="cyan")

        # 根据事件确定边框颜色
        border_style = "cyan"
        for item in reversed(list(self.projection_llm)):
            event_type = str(item.get("event_type") or "").strip().lower()
            if event_type in {"tool_failure", "error"}:
                border_style = "bold red"
                break
            elif event_type in {"llm_waiting"}:
                border_style = "bold yellow"
                break
            elif event_type in {"tool_call", "tool_result"}:
                border_style = "green"
                break

        # 结构化展示：将工具调用和结果分组
        llm_items = list(self.projection_llm)[-PROJECTION_REASONING_VIEWPORT_LINES:]
        self._update_llm_request_state(llm_items)
        i = 0
        while i < len(llm_items):
            item = llm_items[i]
            event_type = item.get("event_type", "llm")

            if event_type == "tool_call":
                # 工具调用：显示名称和参数
                self._render_tool_call(sections, item)
                # 查找对应的工具结果
                j = i + 1
                has_matching_result = False
                while j < len(llm_items):
                    next_item = llm_items[j]
                    if next_item.get("event_type") == "tool_result":
                        if next_item.get("tool_name") == item.get("tool_name"):
                            self._render_tool_result(sections, next_item)
                            has_matching_result = True
                            i = j  # 跳过已渲染的结果
                            break
                    j += 1
                if not has_matching_result:
                    self._render_loading_spinner(sections, item)
            elif event_type == "tool_result":
                # 孤立的工具结果（前面没有对应的调用）
                self._render_tool_result(sections, item)
            elif event_type in {"thinking_chunk", "thinking_preview"}:
                # 思考过程使用更简洁的格式
                self._render_thinking(sections, item)
            elif event_type in {"content_chunk", "content_preview"}:
                # 回答生成
                self._render_content(sections, item)
            else:
                # 其他事件类型
                self._render_generic_event(sections, item)
            i += 1

        if self.llm_request_pending:
            self._render_llm_waiting_spinner(sections)
        elif self.llm_request_last_outcome == "failed":
            sections.append(Text("  ⚠ LLM 请求失败，请检查上游事件", style="bold red"))

        return Panel(Group(*sections), title="[bold]Reasoning[/bold]", border_style=border_style)

    def _render_tool_call(self, sections: list[Any], item: dict[str, Any]) -> None:
        """渲染工具调用事件。"""
        prefix = item.get("prefix", "")
        tool_name = item.get("tool_name", "unknown")
        tool_args = item.get("tool_args", {})

        # 主标题行
        header = Text(f"  {prefix}: ", style="bold green")
        tool_text = Text(f"🛠 {tool_name}", style="bold bright_green")
        sections.append(Text.assemble(header, tool_text))

        # 参数展示（简洁格式）
        if tool_args and isinstance(tool_args, dict):
            arg_lines = []
            for key, value in list(tool_args.items())[:4]:  # 最多显示4个参数
                value_str = str(value)
                if len(value_str) > 40:
                    value_str = value_str[:37] + "..."
                arg_lines.append(f"    · {key}: {value_str}")
            if len(tool_args) > 4:
                arg_lines.append(f"    ... 等 {len(tool_args) - 4} 个参数")
            for line in arg_lines:
                sections.append(Text(line, style="dim green"))

    def _render_tool_result(self, sections: list[Any], item: dict[str, Any]) -> None:
        """渲染工具结果事件 - 带详情展开。"""
        prefix = item.get("prefix", "")
        tool_name = item.get("tool_name", "unknown")
        tool_status = item.get("tool_status", "unknown")
        tool_result_raw = item.get("tool_result_raw")

        # 状态图标和颜色
        if tool_status == "ok":
            status_icon = "✓"
            status_style = "bold green"
        elif tool_status == "failed":
            status_icon = "✗"
            status_style = "bold red"
        else:
            status_icon = "?"
            status_style = "bold yellow"

        # 结果标题行
        header = Text(f"  {prefix}: ", style="bold green")
        result_text = Text(f"{status_icon} {tool_name}", style=status_style)
        sections.append(Text.assemble(header, result_text))

        # 结果详情（结构化展示）
        if tool_result_raw is not None:
            self._render_tool_result_detail(sections, tool_result_raw, tool_status)

    def _render_tool_result_detail(self, sections: list[Any], result: Any, status: str) -> None:
        """渲染工具结果的详情。"""
        if isinstance(result, dict):
            # 提取关键字段
            key_fields = []
            display_fields = []

            for key, value in result.items():
                if key in ("success", "error", "message", "status"):
                    continue  # 这些已在主行显示
                if value is None or value == "":
                    continue
                if isinstance(value, (list, dict)) and len(str(value)) > 100:
                    key_fields.append((key, value))
                else:
                    display_fields.append((key, value))

            # 显示简单字段
            for key, value in display_fields[:6]:  # 最多显示6个简单字段
                value_str = str(value)
                if len(value_str) > 50:
                    value_str = value_str[:47] + "..."
                line_text = Text(f"    ▸ {key}: ", style="dim cyan")
                value_text = Text(value_str, style="white")
                sections.append(Text.assemble(line_text, value_text))

            # 显示复杂字段的摘要
            for key, value in key_fields[:2]:  # 最多显示2个复杂字段
                if isinstance(value, list):
                    line_text = Text(f"    ▸ {key}: ", style="dim cyan")
                    count_text = Text(f"[{len(value)} 项]", style="bright_blue")
                    sections.append(Text.assemble(line_text, count_text))
                    # 显示前3项
                    for idx, item in enumerate(value[:3]):
                        item_str = str(item)
                        if len(item_str) > 60:
                            item_str = item_str[:57] + "..."
                        sections.append(Text(f"        {idx+1}. {item_str}", style="dim"))
                    if len(value) > 3:
                        sections.append(Text(f"        ... 等 {len(value) - 3} 项", style="dim italic"))
                elif isinstance(value, dict):
                    line_text = Text(f"    ▸ {key}: ", style="dim cyan")
                    keys_text = Text(f"{{{', '.join(list(value.keys())[:4])}}}", style="bright_blue")
                    sections.append(Text.assemble(line_text, keys_text))

        elif isinstance(result, list):
            # 列表结果
            count_text = Text("    ▸ 返回列表: ", style="dim cyan")
            len_text = Text(f"[{len(result)} 项]", style="bright_blue")
            sections.append(Text.assemble(count_text, len_text))
            for idx, item in enumerate(result[:3]):
                item_str = str(item)
                if len(item_str) > 60:
                    item_str = item_str[:57] + "..."
                sections.append(Text(f"        {idx+1}. {item_str}", style="dim"))
            if len(result) > 3:
                sections.append(Text(f"        ... 等 {len(result) - 3} 项", style="dim italic"))

        elif isinstance(result, str):
            # 字符串结果
            if len(result) > 0:
                result_preview = result if len(result) <= 80 else result[:77] + "..."
                line_text = Text("    ▸ 返回: ", style="dim cyan")
                value_text = Text(result_preview, style="white")
                sections.append(Text.assemble(line_text, value_text))

    def _render_thinking(self, sections: list[Any], item: dict[str, Any]) -> None:
        """渲染思考过程 - 简洁格式。"""
        content = item.get("content", "")
        prefix = item.get("prefix", "")

        # 提取核心意图，过滤噪音
        cleaned = self._extract_thinking_essence(content)
        if cleaned:
            header = Text(f"  {prefix}: ", style="bold yellow")
            content_text = Text(cleaned[:120], style="yellow")
            sections.append(Text.assemble(header, content_text))

    def _render_content(self, sections: list[Any], item: dict[str, Any]) -> None:
        """渲染回答内容 - 简洁格式。"""
        content = item.get("content", "")
        prefix = item.get("prefix", "")

        # 过滤噪音，提取关键信息
        cleaned = self._extract_content_essence(content)
        if cleaned:
            header = Text(f"  {prefix}: ", style="bold cyan")
            content_text = Text(cleaned[:120], style="cyan")
            sections.append(Text.assemble(header, content_text))

    def _render_generic_event(self, sections: list[Any], item: dict[str, Any]) -> None:
        """渲染通用事件。"""
        display = item.get("display", "")
        event_type = item.get("event_type", "llm")
        style = _reasoning_event_style(event_type)
        sections.append(Text(f"  {display}", style=style, no_wrap=True))

    def _extract_thinking_essence(self, content: str) -> str:
        """从思考内容中提取核心意图。"""
        if not content:
            return ""

        # 过滤掉常见的模板化文本
        noise_patterns = [
            r"I need to\s+",
            r"Let me\s+",
            r"I will\s+",
            r"I should\s+",
            r"I'll\s+",
            r"Now I\s+",
            r"First,?\s+",
            r"Next,?\s+",
            r"Finally,?\s+",
        ]

        cleaned = content.strip()
        for pattern in noise_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)

        # 提取中文核心内容
        chinese_match = re.search(r"[\u4e00-\u9fff][\u4e00-\u9fff\s，。；：\"\"''（）【】]{3,60}", cleaned)
        if chinese_match:
            return chinese_match.group(0).strip()

        # 如果没有中文，返回清理后的英文前60字符
        cleaned = cleaned.strip()
        if len(cleaned) > 60:
            return cleaned[:57] + "..."
        return cleaned

    def _extract_content_essence(self, content: str) -> str:
        """从回答内容中提取核心信息。"""
        if not content:
            return ""

        # 过滤掉工具调用标记
        cleaned = re.sub(r"\[/?TOOL_CALL\]", "", content, flags=re.IGNORECASE)
        cleaned = re.sub(r"</?output>", "", cleaned, flags=re.IGNORECASE)
        cleaned = cleaned.strip()

        # 提取计划相关的关键信息
        if "计划" in cleaned or "步骤" in cleaned:
            match = re.search(r"(?:计划|步骤)[：:]\s*([^\n。]{3,60})", cleaned)
            if match:
                return f"计划: {match.group(1).strip()}"

        # 提取工具调用意图
        tool_match = re.search(r"(?:调用|使用|执行)\s*([\w_]+)\s*(?:工具)?", cleaned)
        if tool_match:
            return f"调用 {tool_match.group(1)}"

        # 默认返回前60字符
        if len(cleaned) > 60:
            return cleaned[:57] + "..."
        return cleaned

    def _update_role_status_from_chain(self, status: dict[str, Any]) -> None:
        """根据链路状态更新角色状态（运行=绿，空闲=黑，阻塞/错误=红）。"""
        running_tokens = {"running", "in_progress", "claimed", "executing", "active", "busy"}
        error_tokens = {"error", "failed", "blocked", "timeout", "stalled", "cancelled", "canceled"}
        for role in ("architect", "pm", "director", "qa"):
            role_status = status.get(role)
            if not isinstance(role_status, dict):
                continue

            nested = role_status.get("status") if isinstance(role_status.get("status"), dict) else {}
            state_candidates = [
                str(role_status.get("state") or "").strip().lower(),
                str(role_status.get("phase") or "").strip().lower(),
                str(role_status.get("current_task") or "").strip().lower(),
                str(nested.get("state") or "").strip().lower(),
                str(nested.get("status") or "").strip().lower(),
                str(nested.get("phase") or "").strip().lower(),
            ]
            joined_state = " ".join(token for token in state_candidates if token)
            is_running = bool(role_status.get("running")) or any(
                token in joined_state for token in running_tokens
            )
            has_error = bool(role_status.get("error")) or any(
                token in joined_state for token in error_tokens
            )

            if has_error:
                new_status = "error"
            elif is_running:
                new_status = "running"
            else:
                new_status = "idle"

            attr_name = f"role_status_{role}"
            if hasattr(self, attr_name):
                setattr(self, attr_name, new_status)

    def _update_role_status_from_llm_request(self) -> None:
        """根据 LLM 请求状态补充角色灯状态。"""
        role = str(self.llm_request_role or "").strip().lower()
        if role not in {"architect", "pm", "director", "qa"}:
            return
        attr_name = f"role_status_{role}"
        current = str(getattr(self, attr_name, "idle") or "idle").strip().lower()
        if self.llm_request_last_outcome == "failed":
            setattr(self, attr_name, "error")
            return
        if self.llm_request_pending and current != "error":
            setattr(self, attr_name, "running")

    def _render_role_status_indicator(self) -> Text:
        """渲染角色状态指示灯：Architect⚫ — PM🟢 — Director🔴 — QA⚫"""
        status_icons = {
            "idle": "⚫",
            "running": "🟢",
            "error": "🔴",
        }
        roles = [
            ("Architect", self.role_status_architect),
            ("PM", self.role_status_pm),
            ("Director", self.role_status_director),
            ("QA", self.role_status_qa),
        ]
        parts: list[Any] = []
        for idx, (name, status) in enumerate(roles):
            if idx > 0:
                parts.append(Text(" — ", style="dim"))
            icon = status_icons.get(status, "⚫")
            # 根据状态设置颜色
            if status == "running":
                style = "bold green"
            elif status == "error":
                style = "bold red"
            else:
                style = "dim"
            parts.append(Text(f"{name}{icon}", style=style))
        return Text.assemble(*parts)

    def _summarize_chain_status(self, status: Any) -> str:
        """格式化链路状态为人类可读格式。"""
        if not isinstance(status, dict):
            return self._safe_json_compact(status, max_chars=240)

        parts: list[str] = []
        for role in ("pm", "director", "qa"):
            role_status = status.get(role)
            if not isinstance(role_status, dict):
                continue
            running = bool(role_status.get("running"))
            state_token = "RUN" if running else "IDLE"

            nested_status = role_status.get("status")
            if isinstance(nested_status, dict):
                nested_state = str(nested_status.get("state") or "").strip().upper()
                if nested_state:
                    state_token = nested_state

            mode = str(role_status.get("mode") or role_status.get("source") or "").strip()
            parts.append(_format_role_status(role, state_token, mode))

        if parts:
            return "  ".join(parts)
        return self._safe_json_compact(status, max_chars=240)

    @staticmethod
    def _limit_section_lines(lines: list[Any], max_lines: int, hint: str = "") -> list[Any]:
        """限制章节行数，超出时添加省略提示。"""
        if len(lines) <= max_lines:
            return lines
        truncated = lines[:max_lines]
        hint_text = hint or f"... 等 {len(lines) - max_lines} 项"
        truncated.append(Text(f"  {hint_text}", style="dim italic"))
        return truncated

    def _render_projection(self) -> Panel:
        """渲染投影面板 - 各章节限定高度防止溢出。"""
        if not self.projection_enabled:
            return Panel(Text("Projection disabled", style="dim"), title="[bold]Projection[/bold]", border_style="grey50")

        sections: list[Any] = []

        status_style = "bold green" if self.projection_connected else "bold yellow"
        status_indicator = "●" if self.projection_connected else "○"
        status_text = (
            f"{status_indicator} status={'connected' if self.projection_connected else 'disconnected'} "
            f"transport={self.projection_transport_used or 'none'} focus={self.projection_focus}"
        )
        sections.append(Text(status_text, style=status_style))
        if self.projection_error:
            sections.append(Text(f"⚠ {self.projection_error}", style="yellow"))

        # 角色实时状态指示灯
        sections.append(self._render_role_status_indicator())

        # Taskboard 章节（最多8行）
        sections.append(Text("═" * 54, style="blue dim"))
        sections.append(Text("◈ Taskboard  ·  任务看板", style="bold blue"))
        sections.append(Text("═" * 54, style="blue dim"))

        taskboard_lines: list[Any] = []
        if self.projection_taskboard_summary or self.projection_taskboard_todos:
            ts = self.projection_taskboard_timestamp or "--:--:--"
            if self.projection_taskboard_summary:
                taskboard_lines.append(Text(f"  [{ts}] {self.projection_taskboard_summary}", style="bright_blue"))
            taskboard_lines.append(Text("  Todos", style="bold bright_white"))
            todos = list(self.projection_taskboard_todos)[-6:]  # 减少到6行给标题留空间
            if todos:
                spinner = self._taskboard_spinner_frame()
                running_items = 0
                for todo in todos:
                    subject = str(todo.get("subject") or "").strip() or "未命名任务"
                    status_label = str(todo.get("status_label") or "").strip() or "未开始"
                    backend_label = _format_taskboard_execution_backend_label(
                        str(todo.get("execution_backend") or ""),
                        str(todo.get("projection_scenario") or ""),
                    )
                    is_running = self._is_running_taskboard_todo(todo)
                    if is_running:
                        running_items += 1
                    prefix = f"{spinner} " if is_running else "└─"
                    detail = f"{subject}（{status_label}"
                    if backend_label:
                        detail = f"{detail} · {backend_label}"
                    detail = f"{detail}）"
                    line = self._truncate_line_for_viewport(
                        f"{prefix}{detail}",
                        max_chars=PROJECTION_EVENT_LINE_MAX_CHARS,
                    )
                    taskboard_lines.append(Text(f"  {line}", style="bold yellow" if is_running else "blue"))
                running_from_summary = self._parse_taskboard_running_count(self.projection_taskboard_summary)
                if running_from_summary > 0 and running_items <= 0:
                    fallback = self._truncate_line_for_viewport(
                        f"{spinner} Director 正在执行任务（详情同步中）",
                        max_chars=PROJECTION_EVENT_LINE_MAX_CHARS,
                    )
                    taskboard_lines.append(Text(f"  {fallback}", style="bold yellow"))
            else:
                for line in list(self.projection_taskboard_items)[-6:]:
                    taskboard_lines.append(Text(f"  └─{line}", style="blue"))
        else:
            taskboard_lines.append(Text("  ⏳ 等待任务看板数据...", style="dim"))
            taskboard_lines.append(Text("  提示: Architect 阶段通常仅产出方案，待 PM/Director 阶段后会出现 Todos。", style="dim"))
        sections.extend(self._limit_section_lines(taskboard_lines, max_lines=8))

        sections.append(Text(""))
        sections.append(Text("═" * 54, style="green dim"))
        sections.append(Text("◈ Code Diff  ·  Director 写码实时差异", style="bold green"))
        sections.append(Text("═" * 54, style="green dim"))

        code_diff_lines: list[Any] = []
        if self.projection_code_diffs:
            for diff_item in list(self.projection_code_diffs)[-2:]:  # 减少到2个文件
                timestamp = str(diff_item.get("timestamp") or "--:--:--")
                file_path = str(diff_item.get("file_path") or "").strip() or "(unknown file)"
                operation = str(diff_item.get("operation") or "modify").strip() or "modify"
                added_lines = self._safe_non_negative_int(diff_item.get("added_lines"))
                deleted_lines = self._safe_non_negative_int(diff_item.get("deleted_lines"))
                modified_lines = self._safe_non_negative_int(diff_item.get("modified_lines"))
                stats_text = self._format_diff_stats(
                    added_lines=added_lines,
                    deleted_lines=deleted_lines,
                    modified_lines=modified_lines,
                )
                summary = f"[{timestamp}] {file_path} ({operation})"
                if stats_text:
                    summary = f"{summary} {stats_text}"
                code_diff_lines.append(
                    Text(
                        f"  {self._truncate_line_for_viewport(summary, max_chars=PROJECTION_EVENT_LINE_MAX_CHARS)}",
                        style="bold white",
                    )
                )
                preview_lines = diff_item.get("preview_lines")
                preview_lines = preview_lines if isinstance(preview_lines, list) else []
                if not preview_lines:
                    code_diff_lines.append(Text("    (patch unavailable)", style="dim"))
                    continue
                # 每个文件最多显示6行预览
                for line in preview_lines[:6]:
                    rendered_line = self._truncate_line_for_viewport(
                        str(line),
                        max_chars=PROJECTION_EVENT_LINE_MAX_CHARS,
                    )
                    code_diff_lines.append(
                        Text(
                            f"    {rendered_line}",
                            style=self._diff_line_style(rendered_line),
                            no_wrap=True,
                            overflow="ellipsis",
                        )
                    )
                if len(preview_lines) > 6:
                    code_diff_lines.append(Text(f"    ... 还有 {len(preview_lines) - 6} 行", style="dim italic"))
        else:
            code_diff_lines.append(Text("  ⏳ 等待 Director 文件变更 diff...", style="dim"))
        sections.extend(self._limit_section_lines(code_diff_lines, max_lines=10))

        sections.append(Text(""))
        sections.append(Text("═" * 54, style="cyan dim"))
        sections.append(Text("◈ Runtime Events  ·  运行时事件", style="bold cyan"))
        sections.append(Text("═" * 54, style="cyan dim"))

        runtime_lines: list[Any] = []
        if self.projection_events:
            for item in list(self.projection_events)[-6:]:  # 减少到6行
                timestamp = str(item.get("timestamp") or "--:--:--")
                kind = str(item.get("kind") or "event")
                detail = str(item.get("detail") or "")
                icon, label, style = _runtime_event_visual(kind)
                rendered = self._truncate_line_for_viewport(f"[{timestamp}] {icon} {label}: {detail}", max_chars=PROJECTION_EVENT_LINE_MAX_CHARS)
                runtime_lines.append(Text(f"  {rendered}", style=style))
        else:
            runtime_lines.append(Text("  ⏳ 暂无运行时事件...", style="dim"))
        sections.extend(self._limit_section_lines(runtime_lines, max_lines=6))

        return Panel(Group(*sections), title="[bold]Projection[/bold]", border_style="green")

    def consume_line(self, line: str) -> None:
        """消费一行日志输出。"""
        normalized = str(line or "").rstrip("\r\n")
        if not normalized:
            return
        self.recent_lines.append(normalized)

        if normalized.startswith(STEP_PREFIX):
            self.current_step = normalized.removeprefix(STEP_PREFIX).strip()
            self.last_status = "running"

        if normalized.startswith(ROUND_HEADER_PREFIX):
            fragment = normalized.removeprefix(ROUND_HEADER_PREFIX).split(":", 1)[0].strip()
            if fragment.isdigit():
                self.current_round = int(fragment)
                self.last_status = f"running round {self.current_round}"

        if normalized.startswith(ROUND_RESULT_PREFIX):
            round_fragment = normalized.removeprefix(ROUND_RESULT_PREFIX)
            round_part, _, status_part = round_fragment.partition(":")
            if round_part.strip().isdigit():
                self.current_round = int(round_part.strip())
            status = status_part.strip().upper()
            if status == "PASS":
                self.completed_rounds += 1
                self.last_status = f"round {self.current_round} passed"
            elif status == "FAIL":
                self.failed_rounds += 1
                self.last_status = f"round {self.current_round} failed"
            elif status:
                self.last_status = f"round {self.current_round} {status.lower()}"

        if "✅" in normalized:
            self.last_status = normalized.replace("✅", "").strip() or self.last_status
        if "⚠️" in normalized:
            self.warnings += 1
            self.last_status = normalized.replace("⚠️", "").strip() or self.last_status
        if "❌" in normalized:
            self.errors += 1
            self.last_status = normalized.replace("❌", "").strip() or self.last_status

    def attach_exit_code(self, exit_code: int) -> None:
        """附加退出码。"""
        self.exit_code = int(exit_code)
        if exit_code == 0:
            self.last_status = "completed successfully"
        elif exit_code == 1:
            self.last_status = "completed with partial failures"
        else:
            self.last_status = f"failed with exit code {exit_code}"

    def update_projection(self, *, connected: bool, transport_used: str, error: str, panels: dict[str, list[dict[str, Any]]]) -> None:
        """更新投影数据。"""
        self.projection_connected = bool(connected)
        self.projection_transport_used = str(transport_used or "none")
        self.projection_error = str(error or "")

        self.projection_chain_status.clear()
        for item in panels.get("chain_status", [])[-8:]:
            timestamp = self._normalize_timestamp(str(item.get("timestamp") or ""))
            status = item.get("status")
            if isinstance(status, dict):
                # 存储为 JSON 字符串以便解析
                status_json = json.dumps(status, ensure_ascii=False, separators=(",", ":"))
                self.projection_chain_status.append(f"{timestamp} {status_json}")
                # 实时更新角色状态
                self._update_role_status_from_chain(status)
            else:
                status_str = self._safe_json_compact(status, max_chars=240)
                self.projection_chain_status.append(f"{timestamp} {status_str}")

        self.projection_llm.clear()
        for item in panels.get("llm_reasoning", [])[-16:]:
            timestamp = self._normalize_timestamp(str(item.get("timestamp") or ""))
            role = str(item.get("role") or "").strip().lower()
            event_type = str(item.get("event_type") or "llm").strip().lower()
            raw_content = str(item.get("content") or "")
            content = self._humanize_reasoning_content(event_type=event_type, content=raw_content)
            role_badge = _role_badge(role)
            event_badge_str = _event_badge(event_type)
            event_label_str = _event_label(event_type)
            prefix = f"{timestamp} {event_badge_str} {role_badge} {role.upper() or 'LLM'} {event_label_str}"

            # 保留完整的工具调用信息供详情展示
            llm_item = {
                "event_type": event_type,
                "display": f"{prefix}: {content[:200]}",
                "role": role,
                "timestamp": timestamp,
                "prefix": prefix,
                "content": content,
            }

            # 工具调用特殊处理：保留原始参数和结果
            if event_type == "tool_call":
                llm_item["tool_name"] = item.get("tool_name", "")
                llm_item["tool_args"] = item.get("tool_args", {})
            elif event_type == "tool_result":
                llm_item["tool_name"] = item.get("tool_name", "")
                llm_item["tool_status"] = item.get("tool_status", "")
                llm_item["tool_success"] = item.get("tool_success")
                llm_item["tool_result_raw"] = item.get("tool_result_raw")
                llm_item["tool_args"] = item.get("tool_args")

            self.projection_llm.append(llm_item)
        self._update_llm_request_state(list(self.projection_llm))
        self._update_role_status_from_llm_request()

        self.projection_dialogue.clear()
        for item in panels.get("dialogue_stream", [])[-10:]:
            timestamp = self._normalize_timestamp(str(item.get("timestamp") or ""))
            speaker = str(item.get("speaker") or "unknown").strip().lower()
            dialogue_type = str(item.get("dialogue_type") or "dialogue").strip().lower()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            line = self._truncate_line_for_viewport(
                f"[{timestamp}] {speaker}/{dialogue_type}: {content}",
                max_chars=PROJECTION_EVENT_LINE_MAX_CHARS,
            )
            self.projection_dialogue.append(line)

        self.projection_tools.clear()
        for item in panels.get("tool_activity", [])[-10:]:
            timestamp = self._normalize_timestamp(str(item.get("timestamp") or ""))
            role = str(item.get("role") or "unknown").strip().lower()
            event_type = str(item.get("event_type") or "tool").strip().lower()
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            line = self._truncate_line_for_viewport(
                f"[{timestamp}] {role}/{event_type}: {content}",
                max_chars=PROJECTION_EVENT_LINE_MAX_CHARS,
            )
            self.projection_tools.append(line)

        self.projection_taskboard_summary = ""
        self.projection_taskboard_timestamp = ""
        self.projection_taskboard_items.clear()
        self.projection_taskboard_todos.clear()
        taskboard_rows = panels.get("taskboard_status", [])
        if taskboard_rows:
            latest = taskboard_rows[-1] if isinstance(taskboard_rows[-1], dict) else {}
            self.projection_taskboard_timestamp = self._normalize_timestamp(str(latest.get("timestamp") or ""))
            self.projection_taskboard_summary = str(latest.get("summary") or "").strip()
            items = latest.get("items")
            items = items if isinstance(items, list) else []
            for row in items[:16]:
                if not isinstance(row, dict):
                    continue
                task_id = str(row.get("id") or row.get("task_id") or "-").strip()
                subject = str(row.get("subject") or row.get("title") or "").strip()
                status = str(row.get("status") or row.get("state") or "pending").strip().lower()
                qa_state = str(row.get("qa_state") or "").strip().lower()
                resume_state = str(row.get("resume_state") or "").strip().lower()
                execution_backend = str(row.get("execution_backend") or "").strip().lower()
                projection_scenario = str(row.get("projection_scenario") or "").strip().lower()
                status_label = _map_taskboard_status_label(status, qa_state, resume_state)
                display_subject = subject or (f"任务{task_id}" if task_id and task_id != "-" else "未命名任务")
                backend_label = _format_taskboard_execution_backend_label(
                    execution_backend,
                    projection_scenario,
                )
                todo_text = f"{display_subject}（{status_label}"
                if backend_label:
                    todo_text = f"{todo_text} · {backend_label}"
                todo_text = f"{todo_text}）"
                todo_line = self._truncate_line_for_viewport(todo_text, max_chars=PROJECTION_EVENT_LINE_MAX_CHARS)
                self.projection_taskboard_items.append(todo_line)
                self.projection_taskboard_todos.append(
                    {
                        "id": task_id,
                        "subject": display_subject,
                        "status": status,
                        "qa_state": qa_state,
                        "resume_state": resume_state,
                        "status_label": status_label,
                        "execution_backend": execution_backend,
                        "projection_scenario": projection_scenario,
                    }
                )

        self.projection_code_diffs.clear()
        for item in panels.get("code_diff", [])[-6:]:
            if not isinstance(item, dict):
                continue
            timestamp = self._normalize_timestamp(str(item.get("timestamp") or ""))
            file_path = str(item.get("file_path") or "").strip()
            if not file_path:
                continue
            operation = str(item.get("operation") or "modify").strip().lower()
            if operation not in {"create", "modify", "delete"}:
                operation = "modify"
            patch_text = str(item.get("patch") or "").strip("\n")
            self.projection_code_diffs.append(
                {
                    "timestamp": timestamp,
                    "file_path": file_path,
                    "operation": operation,
                    "added_lines": self._safe_non_negative_int(item.get("added_lines")),
                    "deleted_lines": self._safe_non_negative_int(item.get("deleted_lines")),
                    "modified_lines": self._safe_non_negative_int(item.get("modified_lines")),
                    "preview_lines": self._build_patch_preview_lines(
                        patch=patch_text,
                        operation=operation,
                        max_lines=12,
                    ),
                }
            )

        self.projection_events.clear()
        for line in list(self.projection_dialogue)[-4:]:
            self.projection_events.append(
                {
                    "timestamp": self._normalize_timestamp(str(line[1:9] if len(line) > 9 and line.startswith("[") else "")),
                    "kind": "dialogue",
                    "detail": line,
                }
            )
        for line in list(self.projection_tools)[-4:]:
            self.projection_events.append(
                {
                    "timestamp": self._normalize_timestamp(str(line[1:9] if len(line) > 9 and line.startswith("[") else "")),
                    "kind": "tool_activity",
                    "detail": line,
                }
            )
        for item in panels.get("realtime_events", [])[-10:]:
            timestamp = self._normalize_timestamp(str(item.get("timestamp") or ""))
            kind = str(item.get("type") or item.get("channel") or "runtime_event")
            if item.get("file_path"):
                detail = f"{item.get('file_path')} ({item.get('operation')})"
                added_lines = self._safe_non_negative_int(item.get("added_lines"))
                deleted_lines = self._safe_non_negative_int(item.get("deleted_lines"))
                modified_lines = self._safe_non_negative_int(item.get("modified_lines"))
                stats_text = self._format_diff_stats(
                    added_lines=added_lines,
                    deleted_lines=deleted_lines,
                    modified_lines=modified_lines,
                )
                if stats_text:
                    detail = f"{detail} {stats_text}"
            else:
                detail = self._safe_json_compact(item.get("content") or item.get("event") or "", max_chars=240)
            self.projection_events.append(
                {
                    "timestamp": timestamp,
                    "kind": kind,
                    "detail": detail,
                }
            )
