# CLI SUPER Mode Pipeline Resilience Blueprint

Date: 2026-04-27
Status: Proposed
Classification: Delivery-layer Fix Pack
Scope: `polaris/delivery/cli/super_mode.py`, `polaris/delivery/cli/terminal_console.py`

## 1. Background

Through three end-to-end SUPER mode tests (snake game, 2-file todo CLI, multi-module complex projects), the pipeline demonstrated it CAN produce working code for simple/medium projects, but consistently fails on complex ones due to four systematic defects in the handoff and routing layer.

### 1.1 Test Results Summary

| Test | Complexity | Router | PM Output | CE Stage | Director Output | Result |
|------|-----------|--------|-----------|----------|-----------------|--------|
| snake_game.py | Medium (1 file, 390 lines) | `code_delivery` | 3 tasks extracted | Claimed | Working game | PASS |
| todo CLI (2 files) | Simple (2 files, 130 lines) | `code_delivery` | 2 tasks extracted | Claimed | Working CLI | PASS |
| todo CLI ("complete") | Simple | `architecture_design` (WRONG) | N/A (architect-only) | Skipped | No code | FAIL |
| Complex multi-module | High | `code_delivery` | EMPTY (exploring loop) | Skipped | Degraded handoff, no files | FAIL |

### 1.2 Root Cause Taxonomy

```text
Defect A: Router misclassification
  -> "完成" not in _EXECUTION_INTENT_KEYWORDS
  -> "design + complete" requests routed to architect-only

Defect B: PM token pressure
  -> architect_output truncated to 5000 chars, still too large for complex projects
  -> LLM context consumed by blueprint text, no room for task generation
  -> PM emits empty output or falls into exploring loop

Defect C: Analyze-stage explore loop
  -> build_super_readonly_message says "Use repo_tree/glob at most ONCE"
  -> But "at most ONCE" is interpreted as permission, not prohibition
  -> Architect/PM/CE in analyze mode call repo_tree, ls, pwd repeatedly
  -> escape_hatch triggers after 3+ rounds, stage terminates with no output

Defect D: Director degraded handoff lacks execution mandate
  -> When PM output is empty, Director gets:
     "(PM planning stage produced no output; proceeding with original request)"
  -> Director interprets this as "I need to understand the workspace first"
  -> Calls execute_command(ls/pwd) repeatedly instead of writing files
  -> No structured task list in handoff = no execution anchor
```

## 2. Goal

Fix all four defects without changing the overall pipeline architecture (which is already blueprinted in `CLI_SUPER_MODE_FULL_PIPELINE_ORCHESTRATION_BLUEPRINT_20260423.md`).

The pipeline must reliably produce code for:
- Simple projects (1-3 files, <200 lines each)
- Medium projects (3-10 files, mixed complexity)
- Complex projects (10+ files, multi-module) — at minimum produce the core module structure

## 3. Architecture

### 3.1 Module Responsibility

```text
polaris/delivery/cli/super_mode.py
  |- SuperModeRouter          (Defect A fix: keyword expansion)
  |- _truncate_text            (Defect B fix: adaptive limit + smart truncation)
  |- build_super_readonly_message   (Defect C fix: absolute explore ban)
  |- build_pm_handoff_message       (Defect B fix: smaller limit + file reference)
  |- build_director_handoff_message (Defect D fix: execution mandate + fallback tasks)
  |- build_chief_engineer_handoff_message (Defect C fix: absolute explore ban)
  |- build_director_task_handoff_message  (Defect C fix: already has ban, verify)

polaris/delivery/cli/terminal_console.py
  |- _run_super_turn()         (orchestration, no structural change)
  |- _run_director_execution_loop() (Defect D fix: degraded handoff enhancement)
```

### 3.2 Data Flow (Fixed)

```text
User Request
  -> SuperModeRouter.decide()
     -> If code_delivery intent detected:
        1. Architect (analyze) -> output written to docs/blueprints/
        2. PM (analyze) -> receives truncated architect_output
                          + blueprint_file reference
                          + ABSOLUTE explore ban
                          -> generates TASK_LIST JSON
        3. Tasks published to TaskMarket (pending_design)
        4. ChiefEngineer (analyze) -> claims pending_design
                                      + ABSOLUTE explore ban
                                      -> generates BLUEPRINT_RESULT JSON
        5. Tasks ack'd to pending_exec
        6. Director (materialize) -> claims pending_exec
                                     -> executes with full task context
                                     -> OR degraded handoff with
                                        synthetic task list + execution mandate
        7. QA (analyze) -> readonly validation
```

## 4. Detailed Design

### 4.1 Defect A: Router Misclassification Fix

**Location**: `super_mode.py`, line 107-119

**Current**:
```python
_EXECUTION_INTENT_KEYWORDS = (
    "落地执行", "开始执行", "开始实施", "开始落地",
    "实施计划", "着手实施", "动手",
    "now execute", "now implement", "start implementing", "start developing",
)
```

**Problem**: User prompt "用Python实现一个Todo CLI工具，从架构设计到代码实现完整完成" contains "完成" but NOT any keyword above. Router classifies as `architecture_design` (architect-only) because "架构设计" matches `_ARCHITECT_KEYWORDS`.

**Fix**: Add completion-oriented Chinese keywords:
```python
_EXECUTION_INTENT_KEYWORDS = (
    "落地执行", "开始执行", "开始实施", "开始落地",
    "实施计划", "着手实施", "动手",
    "完整完成", "完整实现", "全部完成", "全部实现", "完成代码", "完成实现",
    "now execute", "now implement", "start implementing", "start developing",
    "complete implementation", "finish coding",
)
```

Also enhance `_should_route_code_delivery()` logic: if BOTH architect keyword AND code action keyword present, the conjunction "architecture + implement/complete/build" should route to `architect_code_delivery` (full pipeline) rather than `architecture_design` (architect-only).

### 4.2 Defect B: PM Token Pressure Fix

**Location**: `super_mode.py`, line 407-442 (`build_pm_handoff_message`)

**Current**:
```python
clean_architect_output = _truncate_text(architect_output, limit=5000)
```

**Problem**: 5000 chars of architect blueprint consumes ~1500-2000 tokens. For a typical 8k context window LLM, this leaves only ~6000 tokens for PM reasoning, system prompt, and output. When the architect output is dense (file lists, module boundaries, API contracts), PM has no room to generate tasks and enters an exploring loop or emits empty output.

**Fix**: Three-part optimization:

1. **Reduce truncate limit**: `limit=5000` -> `limit=2400`
   - 2400 chars ~ 700-800 tokens, leaving ample room for task generation

2. **Add smart truncation marker**: When truncation occurs, append a clear marker so PM knows the output was cut:
   ```
   ... [architect_output truncated; full blueprint at: {blueprint_file_path}]
   ```

3. **Always reference blueprint file**: Even when not truncated, include `blueprint_file_path` reference so PM can conceptually defer to it without needing full text in context.

**Implementation**:
```python
def build_pm_handoff_message(...):
    clean_architect_output = _truncate_text(architect_output, limit=2400)
    was_truncated = len(str(architect_output or "").strip()) > 2400
    if was_truncated and blueprint_file_path:
        clean_architect_output += f"\n\n[ARCHITECT_OUTPUT_TRUNCATED] Full blueprint: {blueprint_file_path}"
    # ... rest unchanged
```

### 4.3 Defect C: Analyze-Stage Explore Loop Fix

**Location**: `super_mode.py`, lines 173-204 (`build_super_readonly_message`)

**Current**:
```
- CRITICAL: Use repo_tree/glob at most ONCE. Then produce your analysis.
- CRITICAL: Do NOT loop exploring. If the workspace is empty, state that and design from scratch.
```

**Problem**: "at most ONCE" is a permission, not a prohibition. The LLM interprets it as "I am allowed to use repo_tree once" and proceeds to call it. After calling repo_tree once, it sees empty directory and calls `execute_command(ls)` "just to be sure", then `execute_command(pwd)` "to verify path". This triggers escape_hatch.

**Fix**: Replace permissive language with absolute prohibition. The instruction must state zero exploration is expected for certain stages.

**New text for build_super_readonly_message**:
```
- CRITICAL: Do NOT call repo_tree, glob, list_directory, execute_command, or ANY exploration tools.
- CRITICAL: You already have ALL the context you need in the user request above.
- CRITICAL: If the workspace is empty, DESIGN FROM SCRATCH. Do NOT verify emptiness.
- Your ONLY job: produce analysis/planning output. ZERO tool calls.
```

**Also apply to**:
- `build_chief_engineer_handoff_message` (line 482 already has ban, strengthen to match)
- `build_pm_handoff_message` (line 419 already has "Do NOT call repo_tree...", verify sufficient)

The key change: **remove "at most ONCE"** entirely. In analyze stages of SUPER mode, the upstream role (architect) has ALREADY explored. Downstream roles must NOT re-explore.

### 4.4 Defect D: Director Degraded Handoff Fix

**Location**: `super_mode.py`, lines 318-353 (`build_director_handoff_message`)

**Current degraded scenario**:
```python
pm_output = "(PM planning stage produced no output; proceeding with original request)"
# Director gets original_request + this placeholder
# No task list, no file targets, no execution mandate
```

**Problem**: Director is a materialize-role agent trained to execute tasks. When given a vague request without specific file targets, it defaults to "understand the workspace first" behavior (exploring loop).

**Fix**: Enhanced degraded handoff with synthetic task injection and stronger execution mandate.

**New degraded handoff structure**:
```python
def build_director_handoff_message(...):
    # ... existing logic ...
    if not extracted_tasks:
        # Synthesize a single catch-all task from original request
        task_section += (
            "synthetic_task:\n"
            "  1. Implement the original request\n"
            f"     description: {clean_request}\n"
            "     target_files: (determine from request)\n\n"
        )
    return (
        "[mode:materialize]\n"
        "[SUPER_MODE_HANDOFF]\n"
        # ... existing header ...
        "instructions:\n"
        "- You are receiving a PM-generated execution plan.\n"
        "- Your ONLY job is to EXECUTE. Do NOT plan, analyze, or ask questions.\n"
        "- IGNORE any suggestion to 'evaluate first', 'check first', or 'confirm'.\n"
        "- Start writing or modifying files IMMEDIATELY using write_file or edit_file.\n"
        "- Do NOT call ls, pwd, repo_tree, glob, or any exploration tools.\n"
        "- The workspace may be empty. If so, CREATE the files from scratch.\n"
        "- Do NOT produce a summary, report, or ask the user what to do next.\n"
        "- Do NOT say 'I will', 'Let me', 'Next I will'. Just DO the work.\n\n"
        f"{task_section}"
        f"pm_plan:\n{clean_pm_output}\n"
        "[/SUPER_MODE_HANDOFF]"
    )
```

**Additional fix in `terminal_console.py`**: The degraded handoff path (lines 2273-2304) currently uses `build_director_handoff_message` without `extracted_tasks`. Pass `pm_tasks` (which may be empty but the enhanced function will synthesize) to ensure Director always has at least one execution anchor.

## 5. Implementation Order

1. **Defect A** (Router): 1-line change, lowest risk, highest impact on user experience
2. **Defect C** (Explore ban): Text-only changes in handoff messages, no logic change
3. **Defect B** (Token pressure): Adjust truncate limit + add truncation marker
4. **Defect D** (Degraded handoff): Enhance message builder + pass tasks in degraded path

## 6. Verification Plan

### 6.1 Unit Tests (new)

```python
# test_super_mode_router.py
def test_router_complete_keyword_triggers_full_pipeline():
    """"完成" + code action should route to architect_code_delivery, not architecture_design."""

def test_router_architect_plus_implement_triggers_full_pipeline():
    """"架构设计" + "实现" should route to full pipeline."""

# test_super_mode_handoff.py
def test_pm_handoff_truncates_architect_output_to_2400():
def test_pm_handoff_includes_truncation_marker():
def test_readonly_message_has_absolute_explore_ban():
def test_director_handoff_synthesizes_task_when_empty():
def test_director_handoff_includes_execution_mandate():
```

### 6.2 Integration Tests (manual E2E)

Re-run the three reference tests:
1. `python -m polaris.delivery.cli console --super --batch --workspace C:/Temp/polaris_super_test6`
   Input: "用Python实现一个CLI计算器，支持加减乘除，从架构设计到代码实现完整完成"
   Expected: Full pipeline, working calculator.py

2. `python -m polaris.delivery.cli console --super --batch --workspace C:/Temp/polaris_super_test7`
   Input: "用Python实现一个多文件项目：一个config模块、一个logger模块、一个main入口，完整完成"
   Expected: Full pipeline, 3+ files created

### 6.3 Quality Gates

```bash
ruff check polaris/delivery/cli/super_mode.py polaris/delivery/cli/terminal_console.py --fix
ruff format polaris/delivery/cli/super_mode.py polaris/delivery/cli/terminal_console.py
mypy polaris/delivery/cli/super_mode.py polaris/delivery/cli/terminal_console.py
pytest -q polaris/delivery/cli/tests/test_super_mode.py
```

## 7. Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| Truncating architect_output to 2400 loses critical design constraints | Truncation marker references full blueprint file; PM can conceptually defer |
| Absolute explore ban prevents legitimate workspace inspection | Ban only applies to SUPER mode analyze stages; normal single-role sessions unaffected |
| Synthetic task in degraded handoff is too vague | Include original_request verbatim as task description; Director's training handles vague requests better than empty task lists |
| Router keyword expansion causes false positives | Keywords are compound Chinese phrases ("完整完成", "完成代码"), unlikely in non-delivery contexts |
| Director still explores with enhanced handoff | Add explicit "Do NOT call ls, pwd, repo_tree" to every Director handoff variant |

## 8. Non-Goals

This blueprint does NOT address:
1. Changing LLM configuration or model selection
2. Modifying roles.kernel, roles.runtime, or PhaseManager behavior
3. Adding new CLI flags or changing `--super` semantics
4. Replacing text handoff with structured contract objects
5. Implementing concurrent multi-role execution

These are covered by earlier blueprints (`CLI_SUPER_MODE_FULL_PIPELINE_ORCHESTRATION_BLUEPRINT_20260423.md`, `CLI_SUPER_MODE_DIRECTOR_CONTINUATION_INTEGRITY_BLUEPRINT_20260423.md`).

## 9. Success Criteria

After implementing this blueprint, the following must hold:

1. Input containing "架构设计...完整完成" routes to full pipeline (architect -> pm -> ce -> director), not architect-only.
2. PM stage produces non-empty TASK_LIST for projects up to 10 files.
3. Analyze stages (architect, pm, ce) do NOT call exploration tools in SUPER mode.
4. Director stage writes files even when PM output is empty.
5. All existing passing tests continue to pass.
