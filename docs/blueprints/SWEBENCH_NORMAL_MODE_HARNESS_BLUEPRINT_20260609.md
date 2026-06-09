# SWE-bench 正常模式 Harness Blueprint (2026-06-09)

## 0. 背景与裁决

用户要求：**彻底审计 SWE-bench 的测试方式，必须走"正常模式"——通过角色已绑定好的 LLM 模型去跑。**

这与最初的元目标一致：*让 Polaris 的特有技术在运行中真正发挥作用，而不只是文档宣称。* SWE-bench 的价值在于证明 **产品本身**（KernelOne TurnEngine + ContextOS + 44 个工具的 Director Agent）能解 bug，而不是证明一个临时脚本能解 bug。

## 1. 审计结论：旧 harness 旁路了整个 Agent OS

`scripts/swebench/arch_b_converge.py` + `polaris_solve_one.py` 的实际行为：

| 环节 | 正常模式应是 | 旧脚本实际做的 | 性质 |
|---|---|---|---|
| LLM 调用 | `generate_role_response` → `RoleRuntimeService` → **TransactionKernel + TurnEngine** | `_complete_for_role(role, prompt)` = 直接 `httpx.post` 单发补全 (`polaris_solve_one.py:333-351`) | **旁路** |
| 定位 | Director 自调 `repo_map`/`repo_rg`/`treesitter_*` | 脚本级 `_ranked_candidates`/RepoIntelligence (`arch_b_converge.py:522`) | **替身** |
| 改码 | Director 调 `edit_file`/`edit_blocks`/`apply_patch` | 脚本手调 `_apply_blocks` (`:546`,`:747`) | **替身** |
| 多步推理 | TurnEngine 多 turn agentic 循环 | 单发补全 + 外层脚本 round 循环 | **旁路** |

旧脚本仅借用了 `llm_config` 的"角色→模型"映射，把 TurnEngine / ContextOS / 工具系统全部架空。我之前为躲 60s 超时，在 fresh-instance fallback 里显式注释掉了 `generate_role_response`（`arch_b_converge.py:513-519`）——这正是被纠正的反模式。

**关键事实（推翻"超时无法走正常模式"的借口）：**
1. Director 角色超时本就是 **600s**（`resolve_timeout_seconds`: director=600s，其余 60s，`llm_caller/helpers.py:96`），且可经 `context_override={"llm_call_timeout_seconds": N}` / `KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS` 调整。当初撞 60s 是因为拿 `chief_engineer` 去做定位。
2. Director 角色在正常模式下带 **44 个真实工具**（`read_file/edit_file/edit_blocks/precision_edit/apply_patch/search_replace/repo_map/repo_rg/repo_tree/treesitter_*/execute_command/background_run/todo_write…`）——本身就是完整的 agentic coding agent。

## 2. 正常模式真实入口（已验证的链路）

```
RoleConsoleHost(workspace, role="director")              # delivery/cli/director/console_host.py:339
  └─ _resolve_role_session(...)                          # delivery/cli/terminal/commands.py:250  -> session_id
  └─ _run_streaming_turn(host, role="director",          # delivery/cli/terminal/events.py:360 (sync; 内部 asyncio.run)
        session_id, message, ...) -> _TurnExecutionResult(final_content, saw_error)
        └─ SessionOrchestrator.execute_stream(...)        # roles/runtime/internal/session_orchestrator.py:660
             while True: TurnEngine turn -> tool batch -> policy.can_continue   # 真·多 turn agentic 循环
```

外层补全循环参照产品自身的 `_run_director_execution_loop`（`terminal/console.py:115`）：当输出不再"suggests more work"或 `saw_error` 时停止。

## 3. Harness 设计：`scripts/swebench/swebench_normal_mode.py`

职责边界（铁律）：脚本只做 **发题 + 收 diff + 官方评分**，绝不替角色定位/改码。

```
solve_normal_mode(instance, work_dir, max_loops):
  ensure_clone(repo, base_commit, ws)                    # 复用 arch_b_converge
  host = RoleConsoleHost(workspace=str(ws), role="director")
  session_id = _resolve_role_session(host, role="director", role_sessions={},
                                     host_kind=host.config.host_kind, session_title=...)
  message = [mode:materialize] + problem_statement + "只改源码，不要改测试"
  result = _run_streaming_turn(host, role="director", session_id, message, json_render="none",
                               debug=False, spinner_label="", dry_run=False, output_format="text")
  loop (<= max_loops): 若 saw_error 或输出表明完成 -> break；否则发 continuation 再跑一 turn
  patch = current_patch(ws)                              # git add -N . && git diff
  return {instance_id, model_name_or_path: "polaris-director-normal", model_patch: patch}

# 然后官方 harness:
run_harness_round(...) ; instance_report(...) -> resolved
```

- **模型**：完全由 `llm_config` 角色绑定决定（director→本地 gemma-4-12B）。harness **不得**覆盖模型。
- **公平性**：唯一输入是 `problem_statement`；Director 不接触隐藏测试。
- **评分**：官方 `swebench.harness.run_evaluation`，隔离 venv `/home/dains/swebench-harness-venv`。

## 4. 执行计划

1. 先 1 题冒烟（django-11133，已知可解的对照）→ 证明正常模式链路通且能产出 resolved。
2. 通过后再定批量粒度（30 题）。

## 5. 风险与边界

- 本地 gemma-4-12B 历史上 HTTP 502/empty 抖动 → 这是 **真实产品风险**，按真实结果如实记录，不靠切云模型掩盖。
- Director 的 `execute_command` 工具可在容器外的 workspace 跑命令 → 与产品行为一致；workspace 是一次性 clone，可接受。
- 单 turn 受 TransactionKernel "1 turn = 1 tool batch" 约束；agentic 深度由 `max_auto_turns` × 外层 continuation loop 提供。

## 6. 验证

`ruff check --fix` / `ruff format` / `mypy` / 1 题端到端冒烟（输出官方 resolved 判定）。
