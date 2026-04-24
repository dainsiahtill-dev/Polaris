"""LLM Context Status Panel - Visual context usage display for CLI.

Renders a rich status panel showing:
- Model name
- Token usage with progress bar
- Cache hit info
- Compression status
- Health indicators

Supports three modes:
- Detailed: Full panel with all info
- Compact: Single line with essentials
- Warning: Alert state when context is near limit
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# Status thresholds
_WARNING_THRESHOLD = 0.60  # 60% - yellow
_CRITICAL_THRESHOLD = 0.85  # 85% - red

# SSOT: Context window MUST come from event payload via ContextOS
# No hardcoded defaults - if not provided, raise error

# SSOT: All context limits must come from ContextOS via event payload
# Removed: _resolve_context_limit() and get_model_context_limit()
# These functions used hardcoded defaults which violate SSOT principles.


@dataclass(frozen=True, slots=True)
class ContextStats:
    """Context statistics for display.

    SSOT: All values must come from ContextOS via event payload.
    No hardcoded defaults - if values are missing, validation will fail.

    Token fields:
    - estimated_input_tokens: ContextOS budget estimation (before LLM call)
    - current_input_tokens: Actual input tokens from LLM response
    - prompt_tokens: Prompt tokens from LLM usage response
    """

    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    context_limit: int
    current_input_tokens: int = 0  # Actual input tokens from LLM response
    ttft_ms: float = 0  # Time to first token in milliseconds
    estimated_input_tokens: int = 0  # ContextOS budget estimation
    cached_tokens: int = 0
    compressed_tokens: int = 0
    is_compressing: bool = False
    # Optional performance metrics
    cost_per_1k: float | None = None  # $/1K tokens
    throughput: float | None = None  # tokens/second

    @classmethod
    def from_complete_payload(
        cls,
        payload: dict,
        context_limit: int | None = None,
        cached_tokens: int = 0,
        compressed_tokens: int = 0,
        is_compressing: bool = False,
        estimated_input_tokens: int = 0,
    ) -> ContextStats:
        """Create ContextStats from complete event payload.

        SSOT: All required values must come from ContextOS via event payload.
        Raises ValueError if required fields are missing.
        """
        # Validate model is present
        model = payload.get("model")
        if not model:
            raise ValueError("model not found in event payload - ContextOS must provide model info")

        # Validate context_limit is present
        if context_limit is None:
            context_budget = payload.get("context_budget")
            if isinstance(context_budget, dict):
                context_limit = context_budget.get("model_context_window")
            if not context_limit:
                raise ValueError(
                    "context_limit not found in event payload - ContextOS must provide model_context_window"
                )

        tokens = payload.get("tokens", {})
        prompt = tokens.get("prompt_tokens", tokens.get("prompt", 0))
        completion = tokens.get("completion_tokens", tokens.get("completion", 0))
        total = tokens.get("total_tokens", prompt + completion)

        return cls(
            model=str(model),
            prompt_tokens=int(prompt),
            completion_tokens=int(completion),
            total_tokens=int(total),
            context_limit=context_limit,
            current_input_tokens=int(estimated_input_tokens or total),
            estimated_input_tokens=estimated_input_tokens,
            cached_tokens=cached_tokens,
            compressed_tokens=compressed_tokens,
            is_compressing=is_compressing,
        )


def _compute_color(percentage: float) -> str:
    """Compute status color based on usage percentage."""
    if percentage < _WARNING_THRESHOLD:
        return "cyan"
    if percentage < _CRITICAL_THRESHOLD:
        return "yellow"
    return "red bold"


def _format_tokens(tokens: int) -> str:
    """Format tokens in human-readable K format."""
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}K"
    return str(tokens)


def _build_progress_bar(percentage: float, width: int = 16) -> str:
    """Build a text-based progress bar."""
    filled = int(percentage * width)
    if percentage < _WARNING_THRESHOLD:
        bar_color = "cyan"
    elif percentage < _CRITICAL_THRESHOLD:
        bar_color = "yellow"
    else:
        bar_color = "red bold"

    filled_char = "█"
    empty_char = "░"
    bar = f"[{bar_color}]{filled_char * filled}[/{bar_color}][dim]{empty_char * (width - filled)}[/dim]"
    return bar


def _get_status_emoji(percentage: float, is_compressing: bool) -> tuple[str, str]:
    """Get status emoji and color based on context load."""
    if is_compressing:
        return "🗜️", "yellow"
    if percentage < _WARNING_THRESHOLD:
        return "🟢", "green"
    if percentage < _CRITICAL_THRESHOLD:
        return "🟡", "yellow"
    return "🔴", "red bold"


def render_context_panel(stats: ContextStats, *, compact: bool = False) -> str | None:
    """Render context status as a Rich-formatted string.

    Args:
        stats: Context statistics
        compact: If True, render compact single-line format

    Returns:
        Rich-formatted string or None if Rich is not available
    """
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        # Use current_input_tokens for context budget percentage (input context used / model context window)
        context_tokens_for_percentage = (
            stats.current_input_tokens if stats.current_input_tokens > 0 else stats.total_tokens
        )
        percentage = context_tokens_for_percentage / stats.context_limit if stats.context_limit > 0 else 0
        color = _compute_color(percentage)
        status_emoji, status_color = _get_status_emoji(percentage, stats.is_compressing)

        if compact:
            # Compact single-line format - unified display
            bar = _build_progress_bar(percentage, width=12)
            parts = [
                f"[bold]🤖[/bold] [cyan]{stats.model}[/cyan]",
                bar,
                f"[{color}]{percentage * 100:.0f}%[/{color}]",
                f"({_format_tokens(context_tokens_for_percentage)}/{_format_tokens(stats.context_limit)})",
            ]
            # Add TTFT if available (shows responsiveness)
            if stats.ttft_ms > 0:
                if stats.ttft_ms < 1000:
                    parts.append(f"[dim]TTFT:{stats.ttft_ms:.0f}ms[/dim]")
                else:
                    parts.append(f"[yellow]TTFT:{stats.ttft_ms / 1000:.1f}s[/yellow]")
            # Add cost/throughput if available
            if stats.cost_per_1k is not None:
                parts.append(f"[yellow]💰{stats.cost_per_1k:.4f}/1K[/yellow]")
            if stats.throughput is not None:
                parts.append(f"[dim]⚡{stats.throughput:.0f}/s[/dim]")
            parts.append(f"[{status_color}]{status_emoji}[/{status_color}]")
            if stats.cached_tokens > 0:
                parts.append(f"[cyan]⚡{_format_tokens(stats.cached_tokens)}[/cyan]")
            if stats.compressed_tokens > 0:
                parts.append(f"[yellow]🗜️{_format_tokens(stats.compressed_tokens)}[/yellow]")
            return "  ".join(parts)

        # Detailed panel format
        table = Table.grid(padding=(0, 2), pad_edge=True)
        # Rich Table doesn't have nobox attribute; use show_header=False/show_edge=False instead
        table.show_header = False
        table.show_edge = False

        # Model row
        table.add_row(
            Text("🤖 ", style="bold"),
            Text("Model: ", style="bold"),
            Text(stats.model, style="cyan"),
        )

        # Token usage row with progress bar
        bar = _build_progress_bar(percentage, width=20)
        # Build usage text with proper Rich markup
        total_str = _format_tokens(stats.total_tokens)
        limit_str = _format_tokens(stats.context_limit)
        prompt_str = _format_tokens(stats.prompt_tokens)
        completion_str = _format_tokens(stats.completion_tokens)

        # Best practice: show estimated vs actual when divergence > 5%
        estimated_str = ""
        if stats.estimated_input_tokens > 0 and stats.current_input_tokens > 0:
            divergence = abs(stats.estimated_input_tokens - stats.current_input_tokens) / max(
                stats.estimated_input_tokens, 1
            )
            if divergence > 0.05:
                est_str = _format_tokens(stats.estimated_input_tokens)
                actual_str = _format_tokens(stats.current_input_tokens)
                estimated_str = f" [dim](Est: {est_str} | Actual: {actual_str})[/dim]  "

        usage_text = (
            f"{bar}  "
            f"[{color}]{percentage * 100:.1f}%[/{color}]  "
            f"[cyan]{total_str}[/cyan] / [dim]{limit_str}[/dim]"
            f"{estimated_str}"
            f"[dim]P:{prompt_str} C:{completion_str}[/dim]"
        )
        table.add_row(
            Text("📊 ", style="bold"),
            Text("Usage: ", style="bold"),
            Text.from_markup(usage_text),
        )

        # Status row
        status_parts = []
        status_parts.append(f"[{status_color}]{status_emoji}[/{status_color}] [bold]Status:[/bold]")

        if percentage < _WARNING_THRESHOLD:
            status_parts.append("[green]Healthy[/green]")
        elif percentage < _CRITICAL_THRESHOLD:
            status_parts.append("[yellow]High Load[/yellow]")
        else:
            status_parts.append("[red bold]Critical[/red bold]")

        if stats.cached_tokens > 0:
            status_parts.append(f"[cyan]⚡ Cache: {_format_tokens(stats.cached_tokens)}[/cyan]")

        if stats.compressed_tokens > 0:
            status_parts.append(f"[yellow]🗜️ Compressed: {_format_tokens(stats.compressed_tokens)}[/yellow]")

        status_parts.append("[dim]📦 Uncompressed[/dim]")

        table.add_row(Text("⚙️  ", style="bold"), Text(" ".join(status_parts)))

        # Determine panel border style based on status
        if stats.is_compressing or percentage >= _CRITICAL_THRESHOLD:
            border_style = "red bold"
            title = "⚠️ Context Warning"
        elif percentage >= _WARNING_THRESHOLD:
            border_style = "yellow"
            title = "🧠 Context Status"
        else:
            border_style = "cyan"
            title = "🧠 Context Status"

        panel = Panel(
            table,
            title=title,
            border_style=border_style,
            expand=False,
            padding=(0, 1),
        )

        # Capture and return as string
        from io import StringIO

        string_io = StringIO()
        temp_console = Console(file=string_io, force_terminal=True)
        temp_console.print(panel)
        return string_io.getvalue()

    except (RuntimeError, ValueError):
        return None


def print_context_panel(stats: ContextStats, *, compact: bool = False) -> None:
    """Print context status panel to console."""
    panel_str = render_context_panel(stats, compact=compact)
    if panel_str:
        print(panel_str, end="")
