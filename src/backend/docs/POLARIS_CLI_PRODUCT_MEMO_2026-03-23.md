# Polaris CLI Product Memo

状态: Draft  
日期: 2026-03-23

## 1. 结论

Polaris 的真正主线仍然是客户端产品本身。

- Polaris Client（当前实现为 Electron）负责启动、承载、观察 `PM -> Director -> QA` 的无人值守治理式自动化开发工厂。
- `polaris-cli` 负责成为开发者侧的统一宿主，定位类似 Claude / Codex 的终端入口。
- 角色专属 TUI 不再作为产品主线，只保留为测试窗口与调试窗口。

这不是二选一，而是双层产品面：

- 主产品面: Polaris Client
- 开发者产品面: `polaris-cli`
- 测试窗口: legacy role TUI / standalone surface

## 2. 为什么这样裁决

### 2.1 Polaris Client 是治理主线

无人值守治理式自动化开发工厂的核心价值，不只是“能调用 LLM 干活”，而是：

- 能从需求和计划进入可审计的 PM 流程
- 能把 Director 执行放在治理边界内
- 能把 QA 与证据链收回统一闭环
- 能让状态、事件、审计、回滚路径稳定存在

这条主线天然更适合 Polaris Client 承载，因为它本质上是一个工厂控制台，而不只是对话壳。

### 2.2 `polaris-cli` 是统一开发者宿主

我们仍然需要一个像 Claude 的开发入口，因为它解决的是另一类问题：

- 平时简单开发
- 单角色真实工作验证
- 局部回归和日常操作
- 在不进入完整工厂 UI 的情况下快速完成工作

因此 `polaris-cli` 的定位不是替代 Polaris Client，而是复用同一底座能力的开发者宿主。

### 2.3 角色专属 TUI 只能降级为测试窗口

如果继续把 PM / Director / QA 各自做成一套产品级 TUI，会产生三个问题：

- 宿主分叉
- 执行链分叉
- 投影与交互分叉

这会直接破坏当前已经在收敛的统一 runtime / workflow / session / projection 主线。

因此角色专属 TUI 的合理定位只能是：

- 用于验证单角色在真实工作中的行为
- 用于开发阶段调试
- 用于测试窗口和实验窗口

不能再作为最终产品宿主继续扩张。

## 3. 架构原则

所有宿主必须共用同一条底座链路，禁止重新各做一套执行循环。

### 3.1 正确方向

```text
Polaris Client / polaris-cli / test-window
    -> canonical host facade
    -> roles.runtime / roles.session / roles.kernel
    -> workflow_runtime / projection / audit / state_owner
```

### 3.2 错误方向

```text
每个角色自己的 TUI
    -> 自己的 tool loop
    -> 自己的 provider bootstrap
    -> 自己的会话和状态模型
```

### 3.3 明确边界

- Polaris Client 是工厂总控台
- `polaris-cli` 是开发者终端
- test-window 是验证面，不是产品面

## 4. `polaris-cli` 的目标形态

`polaris-cli` 应该是“一个宿主，多角色，多模式”，而不是“多角色，各自一套宿主”。

建议命令面如下：

- `polaris-cli chat --role director --mode console`
- `polaris-cli chat --role pm --mode interactive`
- `polaris-cli workflow run pm`
- `polaris-cli workflow status --workflow-id <id>`
- `polaris-cli workflow events --workflow-id <id>`
- `polaris-cli workflow cancel --workflow-id <id>`
- `polaris-cli status`
- `polaris-cli test-window --role director`

其中：

- `chat` 面向日常开发与单角色工作
- `workflow` 面向工厂链路的 CLI 触发与观测
- `test-window` 明确标注为 legacy 测试窗口

## 5. 当前现实约束

截至 2026-03-23，`polaris-cli` 对 workflow 的支持必须保持诚实：

- 当前可稳定启动的是基于 `pm_workflow` 的 canonical workflow runtime 链路
- `pm_workflow` 不是只给 workspace 就能自动起跑
- 它要求已有的 PM 任务合同作为输入
- canonical 合同路径是 `runtime/contracts/pm_tasks.contract.json`

这意味着：

- Polaris Client 仍然是“从目标意图进入完整治理工厂”的主入口
- `polaris-cli workflow run pm` 当前更适合从已有 PM 合同继续执行工厂链
- 若要实现“从自然语言目标直接拉起整个工厂”，仍应优先在 Polaris Client 主线完成

不能把这一约束包装成“CLI 已经完全等价 Polaris Client”。

## 6. 角色 TUI 的新定位

旧角色 TUI / standalone surface 的定位统一改为：

- frozen legacy test window
- debug surface
- role behavior verification surface

要求：

- 文档中显式说明它们不是 canonical product host
- 代码中避免继续新增产品能力
- 只做必要兼容与测试用途维护

## 7. 近期执行顺序

### Phase 1

- 以 `polaris-cli` 作为统一 CLI 主宿主继续收口
- 保持 Director console 作为第一条成熟 console 形态
- 把 workflow run/status/events/cancel 接到 `polaris-cli`

### Phase 2

- 把会话、观测、diff、tool result、taskboard 视图逐步抽成共享 projection/render
- 让 `polaris-cli` 进一步接近 Claude/Codex 风格的统一终端体验

### Phase 3

- Polaris Client 继续承载完整无人值守治理工厂
- `polaris-cli` 成为与 Client 共享底座的开发者 companion host

## 8. 产品裁决

最终裁决如下：

- Polaris Client 是主线
- `polaris-cli` 是正式副线产品
- 角色专属 TUI 是测试窗口

后续任何 CLI/TUI 投入，都应优先服务这条收敛方向，而不是重新做多套角色宿主。

## 9. 统一基座硬门禁（2026-03-24）

为避免后续开发再次分叉，新增以下硬门禁：

1. `polaris.delivery.cli.director.console_host.DirectorConsoleHost` 的流式对话能力必须只通过  
   `RoleRuntimeService.stream_chat_turn()` 进入 `RoleExecutionKernel`。
2. Delivery/Host 层禁止直接调用  
   `polaris.cells.llm.dialogue.public.service.generate_role_response_streaming`。
3. 若未来需要替换流式协议，只能在 `roles.runtime` 内部演进，并保持 Host 层契约不变。
4. 任何新增 CLI/TUI 角色入口都必须复用该链路，不允许再引入第二套 host->LLM 直连路径。

建议在 code review 中将以下两项作为必查项：

- Host 构造器不应暴露 `dialogue_streamer` 一类直接 LLM 注入参数。
- Host 流程必须构建 `ExecuteRoleSessionCommandV1` 并调用 runtime facade。
- 对应门禁测试：`tests/test_director_console_host.py::test_director_console_host_constructor_exposes_runtime_service_only`。

详细守则见：`docs/KERNELONE_CLI_UNIFIED_RUNTIME_GUARDRAILS.md`。
