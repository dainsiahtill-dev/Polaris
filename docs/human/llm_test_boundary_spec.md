# LLM 测试分层与边界规范（Connectivity vs Deep Test）

状态: Active
版本: 1.0
日期: 2026-02-12
适用范围: LLM Provider 配置、测试入口、Agent 测试执行约定

## 1. 目标

本规范用于消除“连通性测试”和“能力测试”混用造成的认知与实现偏差。

核心目标只有一条：
- 连通性测试只验证配置和链路是否可达，不验证模型能力。

## 2. 测试分层定义

### 2.1 L0 连通性测试（Connectivity Test）

用途：验证当前模型配置是否能成功请求到提供商服务。

关注点：
- base_url 是否正确
- api_key / token 是否有效
- model_id 是否可被服务端识别
- api_path、headers、timeout 等基础参数是否正确
- 网络可达性与服务端响应可用性

非目标：
- 不评估角色能力
- 不评估推理质量
- 不评估任务完成能力
- 不评估安全策略遵从度

### 2.2 L1 就绪测试（Readiness Test）

用途：验证模型是否满足系统上岗的最低行为要求。

示例内容：
- response
- qualification

说明：L1 是“能否上岗”，不是“网络是否可达”。

### 2.3 L2 深度测试（Deep Capability Test）

用途：验证模型在真实工作流中的能力与稳定性。

示例内容：
- thinking
- interview

说明：L2 属于能力评估，不属于连通性验证。

## 3. 强制边界

以下规则为强制规则：

1. 用户选择“连通性测试”时，只允许执行 L0。
2. L0 测试不得触发 qualification、thinking、interview 等能力套件。
3. L0 测试结果不得被能力套件失败覆盖。
4. 若 connectivity 通过但 qualification 失败，状态必须标注为“就绪失败/资格失败”，不得标注为“连通失败”。
5. “连通失败”文案仅在 L0 connectivity 失败时使用。

## 4. 判定标准

### 4.1 L0 通过条件

至少同时满足：
- 请求成功到达目标 provider 端点
- 认证通过（若该 provider 需要认证）
- model 参数被服务端接受
- 服务端返回可解析的成功响应

### 4.2 L0 失败原因归类（仅限）

- 参数错误（base_url/api_path/model_id/headers）
- 认证失败（api_key/token）
- 网络不可达或超时
- 目标服务异常（4xx/5xx）

不属于 L0 失败的情况：
- qualification 规则未通过
- thinking/interview 规则未通过
- 角色策略类提示词未通过

## 5. 实施约定（给 Agent 与调用方）

当目标是“仅连通性验证”时，调用约定应满足：
- role 使用 `connectivity`
- suites 仅使用 `['connectivity']`
- test_level 使用 `quick`
- evaluation_mode 使用 `provider`

禁止做法：
- 在 L0 调用中携带 qualification/thinking/interview
- 使用 L1/L2 结果覆盖 L0 标签

## 6. 日志与 UI 命名规范

为避免误导，状态命名应遵循：
- L0 失败: 连通失败
- L1 失败: 就绪失败 或 资格失败
- L2 失败: 深度测试失败 或 能力评估失败

同一次流程若同时展示多个层级，必须分层显示，不得合并为“连通失败”。

## 7. 反模式（禁止）

- 把 Provider 一键测试默认绑定为 L1/L2 且 UI 文案仍写“连通性测试”
- 将 `final.ready=false` 统一翻译成“连通失败”
- 在 L0 入口注入与角色策略相关的 qualification 题目

## 8. 快速执行检查单

每次改动测试流程前，必须先回答：
- 当前入口目标是 L0、L1 还是 L2？
- 如果是 L0，是否只跑 connectivity？
- 状态文案是否与失败层级一致？
- 日志中是否能明确看到失败发生在第几层？

满足以上四项后，方可合并相关改动。

## 9. 项目备忘录：Director Runtime 扩展引入时机

日期: 2026-02-12  
状态: Pending（待满足准入门槛后启动）

决策：
- 在引入额外 Runtime 扩展前，先把 PM/Director 主流程跑通到“可控基线”。

准入门槛（满足后再开始并入）：
1. `PM -> Director -> QA -> 产物回写` 端到端连续稳定通过（建议至少 10 轮）。
2. 关键失败路径可恢复：超时、QA 失败、工具异常、子进程中断。
3. 事实链一致：`PM_TASKS.json`、`DIRECTOR_RESULT.json`、`events.jsonl` 可对账、可回放。
4. 有固定回归命令集，可在并入前后快速对比是否回退。

边界说明：
- 不要求“所有 BUG 清零”后才开始并入；以“基线稳定 + 可验证 + 可回滚”为启动条件。
