# Polaris CLI Director Console - TUI Documentation

**Version**: 2026-03-24
**Scope**: Textual-based Terminal User Interface for Director Console

---

## 1. Architecture Overview

### 1.1 Thread-Safety Model

The Director Console uses Textual's reactive UI framework which requires all DOM mutations to occur on the main thread.

**Key Principle**: Workers (`run_worker`) run on the main thread, so `call_from_thread` raises `RuntimeError("must run in a different thread")`. Instead, use `call_later` to schedule DOM updates.

```python
# CORRECT: Use call_later from main thread workers
async def _stream_message(self, message: str, assistant_index: int) -> None:
    async for event in self.host.stream_turn(...):
        self.call_later(self._dispatch_stream_event, event, assistant_index)

# WRONG: call_from_thread raises RuntimeError when called from main thread
self.call_from_thread(self._dispatch_stream_event, event, assistant_index)  # Don't do this
```

### 1.2 Module Structure

```
polaris/delivery/cli/director/
├── console_app.py       # DirectorConsoleApp - Main TUI application controller
├── console_widgets.py   # Reusable Textual widgets
├── console_models.py    # Data models (ConsoleMessage, ConsoleSession, etc.)
├── console_render.py    # Rendering utilities (diff highlighting, markup)
└── console_host.py      # DirectorConsoleHost - Backend integration
```

---

## 2. Widget Reference

### 2.1 ComposerBar

**Purpose**: Bottom input bar with multi-line TextArea and send/stop controls.

**Location**: `polaris/delivery/cli/director/console_widgets.py:386`

**Features**:
- Multi-line input with auto-resizing TextArea (min: 3 lines, max: 12 lines)
- Send button (primary) and Stop button (disabled by default)
- Keyboard shortcuts: `Ctrl+Enter` to submit, `Enter` for newline
- Streaming state management (disables Send/enables Stop during streaming)

**Widget ID**: `#message-input`

**Child IDs**:
- `#composer-toolbar` - Button container
- `#composer-send` - Send button
- `#composer-stop` - Stop button
- `#composer-hint` - Keyboard shortcut hint
- `#composer-input` - TextArea input

**Public API**:
```python
class ComposerBar:
    def set_streaming(self, active: bool) -> None
    def set_limits(self, *, min_height: int | None, max_height: int | None) -> None
    def set_text(self, text: str) -> None
    def get_text(self) -> str
    def clear(self) -> None
    def focus_input(self) -> None

    # Backward-compatible property
    @property
    def value: str
```

**Events**:
- `ComposerBar.Submitted(composer, text)` - Fired when user submits message
- `ComposerBar.Stopped(composer)` - Fired when user clicks Stop button

---

### 2.2 ConversationView

**Purpose**: Scrollable message stream with message bubbles.

**Location**: `polaris/delivery/cli/director/console_widgets.py:285`

**Features**:
- Auto-scroll to bottom on new messages (can be locked via scroll)
- Message bubble management with stable IDs
- In-place message updates for streaming content
- Mouse scroll up locks auto-scroll

**Widget ID**: `#conversation`

**Child Type**: `MessageBubble`

**Public API**:
```python
class ConversationView:
    def set_auto_scroll(self, enabled: bool) -> None
    def lock_scroll(self) -> None
    def unlock_scroll(self) -> None
    def set_messages(self, messages: Sequence[ConsoleMessage | Mapping[str, Any]]) -> None
    async def append_message(self, message: ConsoleMessage | Mapping[str, Any]) -> MessageBubble
    async def update_message(self, message_id: str, message: ConsoleMessage | Mapping[str, Any]) -> MessageBubble | None
    def clear_messages(self) -> None
```

---

### 2.3 MessageBubble

**Purpose**: Individual message display with role-based styling.

**Location**: `polaris/delivery/cli/director/console_widgets.py:126`

**CSS Classes**:
- `.user` - User messages (primary background)
- `.assistant` - Assistant messages (surface background)
- `.system` - System messages (warning border)
- `.tool` - Tool call/result messages (accent border)
- `.error` - Error messages (error border)
- `.streaming` - Shows loading spinner

**Internal IDs**:
- `#bubble-header` - Header with role label and buttons
- `#bubble-role` - Role label (You/Director/System/Tool)
- `#bubble-meta` - Timestamp and metadata
- `#bubble-thinking` - Thinking content (hidden if empty)
- `#bubble-body` - Main message content (Markdown)
- `#bubble-spinner` - Loading indicator (shown during streaming)
- `#bubble-copy` - Copy button
- `#bubble-regenerate` - Regenerate button

**Public API**:
```python
class MessageBubble:
    def set_message(self, message: ConsoleMessage | Mapping[str, Any]) -> None
    def set_streaming(self, active: bool) -> None

    @property
    def text_content(self) -> str  # Plain text for test assertions
    @property
    def text_thinking(self) -> str  # Plain text thinking content
```

**Events**:
- `MessageBubble.CopyRequested(bubble, message)`
- `MessageBubble.RegenerateRequested(bubble, message)`

---

### 2.4 ArtifactPanel

**Purpose**: Right-side panel for displaying artifacts (markdown, code, diff).

**Location**: `polaris/delivery/cli/director/console_widgets.py:528`

**Features**:
- Three tabs: Markdown, Code, Diff
- Diff tab uses `FileChangeList + DiffViewer` composite layout
- Diff syntax highlighting via Rich `Syntax("diff")`
- Hunk navigation (`j/k`, `↑/↓`) and in-diff search (`/`, `n`, `N`)
- Hidden by default when no artifacts

**Widget ID**: `#artifacts-panel`

**CSS Class**: `.hidden` - Applied when panel should not be visible

**Tab Pane IDs**:
- `#artifact-markdown-pane` / `#artifact-markdown` - Markdown content
- `#artifact-code-pane` / `#artifact-code` - Code content
- `#artifact-diff-pane` / `#artifact-diff-layout` - Diff container
- `#artifact-file-list` - Changed-file sidebar
- `#artifact-diff-viewer` - Rich diff viewer

**Public API**:
```python
class ArtifactPanel:
    def set_state(self, state: ArtifactPanelState) -> None
    def show_markdown(self, content: str, *, title: str | None = None) -> None
    def show_code(self, content: str, *, language: str = "python", title: str | None = None) -> None
    def show_diff(self, content: str, *, title: str | None = None) -> None
    def hide(self) -> None
```

---

### 2.5 SessionListView

**Purpose**: Left sidebar displaying available sessions.

**Location**: `polaris/delivery/cli/director/console_widgets.py:63`

**Features**:
- Simple data API via `set_sessions()`
- Visual indicator for active session (● vs ○)
- Message count badge per session
- Fires `Selected` event on session selection

**Widget ID**: `#session-list`

**Child Type**: `SessionListItem`

**Public API**:
```python
class SessionListView:
    def set_sessions(self, sessions: Sequence[ConsoleSession | Mapping[str, Any]]) -> None
    def append_session(self, session: ConsoleSession | Mapping[str, Any], *, active: bool = False) -> None
    def selected_session_id(self) -> str | None
    def get_session(self, session_id: str) -> ConsoleSession | None
```

**Events**:
- `SessionListView.Selected(list_view, session_id)`

---

### 2.6 ConfirmExitScreen

**Purpose**: Modal confirmation dialog for accidental quit (Ctrl+Q).

**Location**: `polaris/delivery/cli/director/console_widgets.py:642`

**Bindings**:
- `Escape` / `n` - Cancel
- `y` - Confirm

---

## 3. Data Models

### 3.1 ConsoleMessage

**Location**: `polaris/delivery/cli/director/console_models.py:107`

```python
@dataclass(slots=True)
class ConsoleMessage:
    id: str                    # Unique message ID
    role: str                  # "user" | "assistant" | "system" | "tool"
    content: str               # Main message content
    timestamp: datetime        # Message timestamp (UTC)
    artifacts: list[MessageArtifact]  # Associated artifacts
    thinking: str              # Model thinking/reasoning
    status: str                # Message status
    meta: dict[str, Any]       # Additional metadata

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> ConsoleMessage
    def with_content(self, content: str) -> ConsoleMessage
    def to_dict(self) -> dict[str, Any]
```

### 3.2 MessageArtifact

**Location**: `polaris/delivery/cli/director/console_models.py:61`

```python
@dataclass(slots=True)
class MessageArtifact:
    artifact_id: str
    kind: str                  # "markdown" | "code" | "diff" | "text"
    title: str
    content: str
    language: str | None       # Programming language for code artifacts
    path: str | None           # File path reference
    source_message_id: str | None
    summary: str | None
    metadata: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> MessageArtifact
    def to_dict(self) -> dict[str, Any]
```

### 3.3 ConsoleSession

**Location**: `polaris/delivery/cli/director/console_models.py:151`

```python
@dataclass(slots=True)
class ConsoleSession:
    session_id: str
    title: str
    messages: list[ConsoleMessage]
    model_parameters: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> ConsoleSession
    def to_dict(self) -> dict[str, Any]
```

### 3.4 StreamingState

**Location**: `polaris/delivery/cli/director/console_models.py:193`

```python
@dataclass(slots=True)
class StreamingState:
    active: bool = False
    phase: str = "idle"        # "idle" | "streaming" | "error"
    message_id: str | None = None
    buffer: str = ""           # Content buffer
    thinking_buffer: str = ""  # Thinking buffer
    last_chunk_at: datetime | None = None
    error: str = ""
    tool_name: str = ""
    auto_scroll: bool = True
    artifact_mode: bool = False

    @classmethod
    def idle(cls) -> StreamingState
    def mark_chunk(self, *, content: str = "", thinking: str = "", phase: str | None = None) -> StreamingState
    def finish(self, *, error: str = "") -> StreamingState
```

### 3.5 ArtifactPanelState

**Location**: `polaris/delivery/cli/director/console_models.py:229`

```python
@dataclass(slots=True)
class ArtifactPanelState:
    visible: bool = False
    active_tab: str = "markdown"  # "markdown" | "code" | "diff"
    title: str = "Artifacts"
    markdown: str = ""
    code: str = ""
    diff: str = ""
    code_language: str | None = None
    source_message_id: str | None = None
    metadata: dict[str, Any]

    def with_markdown(self, content: str, *, title: str | None = None) -> ArtifactPanelState
    def with_code(self, content: str, *, language: str = "python", title: str | None = None) -> ArtifactPanelState
    def with_diff(self, content: str, *, title: str | None = None) -> ArtifactPanelState
    def hide(self) -> ArtifactPanelState
```

**Factory Function**:
```python
def build_artifact_panel_state(
    artifacts: Sequence[MessageArtifact],
    *,
    active_index: int = 0,
) -> ArtifactPanelState
```

---

## 4. Rendering System

### 4.1 Diff Rendering

**Location**: `polaris/delivery/cli/director/console_render.py`

The diff renderer provides syntax highlighting for unified diff format:

```python
def _render_diff_markup_lines(
    diff_text: str,
    *,
    operation: str,
    truncated: bool,
    max_lines: int = 160,
) -> list[str]
```

**Color Coding**:
- `+++` / `---` file headers → **Bold**
- `@@` hunk headers → **Cyan**
- `+` added lines → **Green**
- `-` removed lines → **Red**
- Context lines → Plain text

### 4.2 Message Rendering

**Rich Markup** (for TUI):
```python
def render_message_markup(message: Mapping[str, Any]) -> str
```

**Plain Text** (for file export/clipboard):
```python
def render_message_plain_text(message: Mapping[str, Any]) -> str
```

**Role Styling**:
- User → `[bold bright_cyan]You[/bold bright_cyan]`
- Assistant → `[bold bright_green]Director[/bold bright_green]`
- System → `[bold yellow]System[/bold yellow]`
- Tool Call → `[bold bright_cyan]Tool Call[/bold bright_cyan]`
- Tool Result (ok) → `[bold green]Tool Result[/bold green]`
- Tool Result (failed) → `[bold red]Tool Result[/bold red]`
- Tool Result (unknown) → `[bold yellow]Tool Result[/bold yellow]`

### 4.3 Tool Message Builder

```python
def build_tool_overlay_message(
    event_type: str,           # "tool_call" | "tool_result"
    payload: Mapping[str, Any]
) -> dict[str, Any]
```

Extracts tool name, file path, error text, and patch/diff content from various payload formats.

---

## 5. Streaming Architecture

### 5.1 Event Types

The `DirectorConsoleHost.stream_turn()` returns an async iterator of events:

| Event Type      | Data Fields                          | Description                    |
|-----------------|--------------------------------------|--------------------------------|
| `content_chunk` | `{ "content": str }`                 | Assistant content token        |
| `thinking_chunk`| `{ "content": str }`                 | Model thinking/reasoning token |
| `tool_call`     | `{ "tool": str, "args": {...} }`     | Tool invocation                |
| `tool_result`   | `{ "result": {...}, "error": ... }`  | Tool execution result          |
| `complete`      | `{ "content": str, "thinking": str }`| Stream completion              |
| `error`         | `{ "error": str }`                   | Error occurred                 |

### 5.2 Event Dispatch Flow

```
stream_turn() → Event
      ↓
call_later(_dispatch_stream_event, event, assistant_index)
      ↓
_update message buffer_
      ↓
_render_conversation()
      ↓
ConversationView.set_messages()
```

### 5.3 Thread-Safety Checklist

✅ **DO**:
- Use `call_later()` to schedule DOM updates from worker coroutines
- Keep message buffer mutations in `_dispatch_stream_event`
- Use `ConversationView.set_messages()` for bulk updates

❌ **DON'T**:
- Use `call_from_thread()` from main thread workers
- Mutate DOM directly from async stream handlers
- Use index-based bubble selection in tests (use role-based instead)

---

## 6. Diff Viewer (Implemented)

### 6.1 Delivered Scope

**Status**: Implemented  
**Priority**: P2 (completed)

#### Specifications

**Diff Generation**:
```python
import difflib

def generate_unified_diff(original: str, modified: str, filepath: str = "") -> str:
    """Generate unified diff between original and modified content."""
    original_lines = original.splitlines(keepends=True)
    modified_lines = modified.splitlines(keepends=True)

    diff = difflib.unified_diff(
        original_lines,
        modified_lines,
        fromfile=f"a/{filepath}" if filepath else "a/file",
        tofile=f"b/{filepath}" if filepath else "b/file",
        lineterm=""
    )
    return "".join(diff)
```

**DiffViewer Widget**:
```python
class DiffViewer(Vertical):
    """
    Enhanced diff viewer with Rich text styling.

    Features:
    - Unified diff rendering via Rich Syntax highlighter
    - Hunk navigation (`j/k`, `↓/↑`)
    - Search entry (`/`) and result navigation (`n` / `N`)
    - Scroll anchoring to active hunk/match
    """

    DEFAULT_CSS = """
    DiffViewer {
        width: 100%;
        height: 100%;
        background: $surface;
        border: solid $primary;
        padding: 0 1;
    }
    """

    def __init__(self, diff_content: str = "", **kwargs):
        super().__init__(**kwargs)
        self.diff_content = diff_content
        self._rendered_lines: list[Text] = []

    def compose(self) -> ComposeResult:
        yield ScrollableContainer(
            Static(id="diff-content"),
            id="diff-scroll"
        )

    def on_mount(self) -> None:
        self._render_diff()

    def set_diff(self, diff_content: str, *, filepath: str = "") -> None:
        """Update diff content and re-render."""
        self.diff_content = diff_content
        self._render_diff()

    def _render_diff(self) -> None:
        """Render diff with Rich styling."""
        lines = self.diff_content.splitlines()
        styled_lines: list[Text] = []

        for line in lines:
            if line.startswith('+++'):
                styled_lines.append(Text(line, style="bold bright_white"))
            elif line.startswith('---'):
                styled_lines.append(Text(line, style="bold bright_white"))
            elif line.startswith('@@'):
                styled_lines.append(Text(line, style="cyan"))
            elif line.startswith('+') and not line.startswith('+++'):
                styled_lines.append(Text(line, style="green"))
            elif line.startswith('-') and not line.startswith('---'):
                styled_lines.append(Text(line, style="red"))
            elif line.startswith(' '):
                styled_lines.append(Text(line, style="dim"))
            else:
                styled_lines.append(Text(line))

        # Join and display
        content = self.query_one("#diff-content", Static)
        content.update(Text("\n").join(styled_lines))
```

#### Integration Points (Delivered)

**1. ArtifactPanel Integration**:
- Diff tab now renders `FileChangeList` + `DiffViewer` instead of `TextArea`
- Unified diff is parsed per file and routed to selected file projection

**2. MessageBubble Integration**:
- Inline diff preview is rendered for tool messages carrying diff artifacts

**3. Streaming Integration**:
- `tool_result` overlay messages with `meta.detail_kind=diff` are converted to artifacts
- Artifact panel auto-updates from parsed `ConsoleMessage` artifacts

#### File Change Sidebar

```python
@dataclass
class FileChange:
    filepath: str
    change_type: str  # "added" | "modified" | "deleted" | "renamed"
    additions: int
    deletions: int
    diff_content: str

class FileChangeList(ListView):
    """Sidebar showing changed file paths parsed from unified diff."""

    def __init__(self, changes: list[FileChange] = None, **kwargs):
        super().__init__(**kwargs)
        self.changes = changes or []

    def compose(self) -> ComposeResult:
        yield DataTable(id="change-table")

    def on_mount(self) -> None:
        table = self.query_one("#change-table", DataTable)
        table.add_columns("File", "Status", "+", "-")
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#change-table", DataTable)
        table.clear()
        for change in self.changes:
            status_emoji = {
                "added": "🟢",
                "modified": "🟡",
                "deleted": "🔴",
                "renamed": "🔵"
            }.get(change.change_type, "⚪")
            table.add_row(
                change.filepath,
                f"{status_emoji} {change.change_type}",
                str(change.additions),
                str(change.deletions)
            )
```

#### Keyboard Shortcuts (Implemented)

| Key | Action |
|-----|--------|
| `j` / `↓` | Next hunk |
| `k` / `↑` | Previous hunk |
| `/` | Search in diff |
| `n` | Next search match |
| `N` | Previous search match |

#### Usage Example

```python
# In DirectorConsoleApp

def _show_file_diff(self, original: str, modified: str, filepath: str) -> None:
    """Display diff for a file change."""
    diff = generate_unified_diff(original, modified, filepath)

    panel = self.query_one("#artifacts-panel", ArtifactPanel)
    panel.show_diff(diff, title=f"Changes: {filepath}")

    # Optionally show file list if multiple changes
    changes = [FileChange(filepath, "modified", 5, 3, diff)]
    # Update sidebar with file change list
```

---

## 7. Testing Guide

### 7.1 Walkthrough Tests

**File**: `tests/test_director_console_textual_walkthrough.py`

**Test Coverage**:
1. `test_textual_walkthrough_renders_boot_and_incremental_stream` - Boot screenshot + streaming capture
2. `test_textual_walkthrough_supports_task_and_session_commands` - /task and /session commands
3. `test_composer_bar_toggles_streaming_state` - Send/Stop button states during streaming
4. `test_artifact_panel_hidden_by_default` - Panel visibility behavior
5. `test_session_list_view_fires_selected_event` - Session selection events
6. `test_artifact_panel_diff_viewer_parses_files_and_supports_selection` - Multi-file diff parsing + selection
7. `test_diff_viewer_search_shortcut_and_match_navigation` - Diff search and navigation shortcuts
8. `test_message_bubble_shows_inline_diff_preview` - Inline diff preview rendering in tool bubble

### 7.2 Best Practices for Widget Testing

**Role-Based Selection** (Preferred):
```python
# GOOD: Select by role for stable tests
bubbles = app.query("#conversation MessageBubble")
assistant_bubble = next(
    (b for b in bubbles.nodes if getattr(getattr(b, 'message', None), 'role', None) == "assistant"),
    bubbles.nodes[-1] if bubbles else None
)
```

**Index-Based Selection** (Avoid):
```python
# BAD: Fragile to ordering changes
assistant_bubble = bubbles.nodes[-2]  # May break if tool bubble appears
```

**Timing for Streaming Tests**:
```python
# Send message and wait for initial processing
await pilot.click("#composer-send")
await pilot.pause(0.1)  # Short pause for state change

# Check mid-stream state
await pilot.pause(0.5)  # Longer pause for mid-capture

# Wait for completion
await pilot.pause(0.6)  # Full stream duration
```

---

## 8. Commands Reference

### 8.1 Built-in Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/refresh` | Refresh session/task views |
| `/status` | Show JSON status dump |
| `/sessions` | List all sessions |
| `/tasks` | List all tasks |
| `/new-session [title]` | Create new session |
| `/session <id>` | Switch to session |
| `/task <subject>` | Create new task |

### 8.2 Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+Q` | Quit (with confirmation) |
| `Ctrl+R` | Refresh |
| `Ctrl+Enter` | Send message |
| `Enter` | New line in input |
| `Escape` | Cancel/Close modal |

---

## 9. Implementation Checklist

### Current Status (2026-03-24)

- [x] ComposerBar with multi-line input
- [x] ConversationView with MessageBubble children
- [x] ArtifactPanel with Markdown/Code/Diff tabs
- [x] SessionListView with selection events
- [x] Thread-safe streaming via `call_later`
- [x] Role-based bubble styling
- [x] Diff syntax highlighting in render layer
- [x] Walkthrough tests (8 passing)
- [x] Enhanced DiffViewer widget with Rich styling
- [x] File change list sidebar
- [x] Inline diff preview in MessageBubble
- [x] Keyboard navigation for diff hunks
- [x] Search within diff content

### Backlog

- [ ] Diff export to file

---

## 10. References

- **Textual Docs**: https://textual.textualize.io/
- **Rich Console**: https://rich.readthedocs.io/
- **Product Memo**: `docs/POLARIS_CLI_PRODUCT_MEMO_2026-03-23.md`
- **Cell Specification**: `polaris/cells/director/delivery/cell.yaml`
