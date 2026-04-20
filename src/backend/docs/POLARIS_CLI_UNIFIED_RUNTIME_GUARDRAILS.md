# Polaris CLI Unified Runtime Guardrails

状态: Active  
生效日期: 2026-03-24  
范围: `polaris-cli` / `toad` / `director console host`

## 1. 目标

确保 CLI/TUI 只是表现层，不再分叉出第二套角色执行与流式对话链路。

## 2. 唯一合法链路

```text
polaris-cli / toad (delivery host)
    -> DirectorConsoleHost
    -> RoleRuntimeService.stream_chat_turn()
    -> RoleExecutionKernel
```

## 3. 明确禁止

以下做法一律禁止：

- 在 delivery/host 层直接调用 `generate_role_response_streaming(...)`
- 在 CLI/TUI 内自建 provider bootstrap + tool loop
- 在 host 构造器暴露 `dialogue_streamer` 一类“直连 LLM”注入参数

## 4. 为什么必须这样

- 防止执行链分叉：同一角色在不同入口行为不一致
- 防止会话/状态分叉：session、tool result、audit 证据无法统一
- 防止维护分叉：一处修复无法覆盖全部入口

## 5. 代码评审硬检查

每次涉及 CLI/TUI 代码时必须检查：

1. Host 是否构建 `ExecuteRoleSessionCommandV1`
2. Host 是否只调用 `RoleRuntimeService.stream_chat_turn()`
3. 是否出现 `generate_role_response_streaming` 进入 delivery/host
4. 是否引入新的“host -> LLM 直连”参数或适配层
5. 是否保留 `tests/test_director_console_host.py::test_director_console_host_constructor_exposes_runtime_service_only`
   这类构造器门禁，防止回退到 `dialogue_streamer` 分叉路径

## 6. 回归命令

```bash
python -m py_compile \
  polaris/delivery/cli/director/console_host.py \
  tests/test_director_console_host.py

ruff check \
  polaris/delivery/cli/director/console_host.py \
  tests/test_director_console_host.py

pytest -q \
  tests/test_director_console_host.py \
  tests/test_polaris_cli.py \
  tests/test_director_service_convergence.py -x
```

## 7. 相关文件

- `polaris/delivery/cli/director/console_host.py`
- `polaris/cells/roles/runtime/public/service.py`
- `polaris/cells/delivery/cli/README.agent.md`
- `polaris/delivery/cli/toad/README.md`

## 8. UI 基线（防回退）

`polaris.delivery.cli.toad` 默认必须保持低噪音对话流：

1. 默认单列对话流 + 底部输入栏；历史侧栏默认隐藏。
2. 侧栏仅通过快捷键显式呼出（`Ctrl+B`），不常驻挤压主阅读区。
3. 对话渲染禁止恢复“多边框多面板噪音”样式（常驻复杂 chrome）。
4. 运行时调试噪音（如 `Offset(...)` / `Region(...)`）必须在投影层清洗后再显示。
