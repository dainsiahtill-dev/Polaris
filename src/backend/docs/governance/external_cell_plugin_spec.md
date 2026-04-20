# External Cell Plugin Specification

Status: `draft`
Version: `0.2`
Scope: `target executable governance spec, not current branch fact`
Audience: `platform maintainers`, `plugin authors`, `governance implementers`

本规范定义 Polaris 面向外部开发者的 `External Cell Plugin` 体系。
目标不是允许任意代码注入主系统，而是定义一种可安装、可治理、可审计、可回滚的外部扩展单元。

本规范使用 RFC 风格术语：

- `MUST`: 不满足即拒绝安装、拒绝启用或 CI 失败
- `SHOULD`: 默认必须遵守；若不能满足，必须有审批与 gap 记录
- `MAY`: 可选能力，不是最低准入要求

---

## 1. Positioning

`External Cell Plugin` 的正确定位是：

- 一个由外部开发者交付的能力单元
- 一个受 Graph / Cell / Contract / Effect 治理约束的扩展包
- 一个只能通过 `KernelOne SDK` 调用系统能力的插件单元
- 一个默认关闭、按审批启用、按 capability token 授权的隔离运行体

它不是：

- 任意 Python 包注入
- 任意 import 主仓内部模块的扩展脚本
- 第二套 graph 真相
- 可以绕过 `public/internal fence`、`state_owners`、`effects_allowed` 的特殊入口

一句话：

`External Cell Plugin = 外部可开发的 Cell 插件包 + 官方 SDK + 强治理 + 隔离执行`

---

## 2. Normative Principles

### 2.1 Graph First

Graph 仍然是唯一架构真相。
外部插件包中的 `cell.yaml` 只是候选扩展声明，不是主仓架构真相。

只有在以下动作全部完成之后，外部插件才进入宿主的活动能力视图：

1. schema 校验通过
2. governance 准入通过
3. 管理员审批通过
4. capability token 签发完成
5. 隔离运行时健康检查通过

### 2.2 SDK Only

外部插件只能依赖：

- `polaris.kernelone.sdk.*`
- 宿主开放的其他 Cell `public` 契约

### 2.3 Public Only

外部插件禁止 import：

- `polaris.cells.*.internal.*`
- `polaris.application.*`
- `polaris.infrastructure.*`
- `polaris.delivery.*`
- `polaris.bootstrap.*`
- `polaris.kernelone.*` 中除 `sdk/*` 外的内部实现

### 2.4 Declared Effects Only

插件的任何副作用都必须同时满足：

1. 已在 `cell.yaml.effects_allowed` 中声明
2. 已通过 capability token 获得授权
3. 已通过 `KernelOne SDK` 发起
4. 已生成 receipt / trace

任一条件不满足即 `deny`。

### 2.5 Single Effective Runtime Graph

系统在任意时刻只能有一个有效活动能力视图。
插件包自己的 manifest 不能与主仓 `docs/graph/**` 并列形成双真相。

推荐做法：

- 主仓 `docs/graph/**` 继续保存官方静态 graph 真相
- 运行时维护单独的插件安装注册表与活动注册表
- 运行时注册表描述的是“当前启用了哪些外部能力”，不是新的架构 authoring source

---

## 3. Non-Goals For v0.2

本草案当前不覆盖以下能力：

- 插件自动改写主仓 `docs/graph/**`
- 多个 public Cell 打包成一个插件市场包
- 无审批热插拔启用
- 插件共享宿主主进程内存对象
- 插件直接访问数据库、文件系统、消息系统、网络
- 插件直接获得任意系统路径读写能力
- 自动根据语义相似度宣布一个外部 Cell 成为正式 Cell

---

## 4. Packaging Model

`v0.2` 规定：

- 一个插件包 `MUST` 对应一个 public Cell
- 一个插件包 `MUST` 有一个包级清单 `plugin.yaml`
- 一个插件包 `MUST` 有一个 Cell 清单 `cell.yaml`
- 一个插件包 `MUST` 有公开契约模块
- 一个插件包 `MUST` 有最小测试与 verify pack
- 一个插件包 `MUST` 有可验证的分发签名与 SBOM

推荐目录结构：

```text
vendor.example.docs_enricher/
  plugin.yaml
  cell.yaml
  README.md
  pyproject.toml
  plugin/
    __init__.py
    main.py
    public/
      __init__.py
      contracts.py
    internal/
      service.py
      handlers.py
  tests/
    test_contracts.py
    test_smoke.py
  generated/
    verify.pack.json
    sbom.json
    plugin.sig
```

目录职责：

- `plugin.yaml`: 包级身份、版本、隔离与准入声明
- `cell.yaml`: Cell 能力边界、依赖、状态、effect 声明
- `plugin/public/*`: 外部唯一公开边界
- `plugin/internal/*`: 插件私有实现
- `tests/*`: 最小契约回归与 smoke 验证
- `generated/verify.pack.json`: 安装前验证入口
- `generated/sbom.json`: 供应链清单
- `generated/plugin.sig`: 分发签名

---

## 5. Package Manifest Format

`plugin.yaml` 是插件包级清单。

### 5.1 Required Fields

```yaml
manifest_version: 1
plugin_id: vendor.example.docs_enricher
display_name: Docs Enricher
publisher: Example Inc
plugin_version: 0.1.0
cell_id: vendor.docs_enricher
cell_manifest: cell.yaml

sdk:
  version: ">=1.0.0,<2.0.0"
  entrypoint: plugin.main:register
  python: ">=3.12,<3.15"

runtime:
  process_model: isolated_process
  ipc: stdio_jsonrpc
  default_enabled: false
  startup_timeout_seconds: 15
  shutdown_timeout_seconds: 10
  max_memory_mb: 256
  cpu_quota: "0.5"
  network_mode: deny_by_default

capabilities:
  tokens:
    - fs.read:workspace/docs/**
    - fs.write:workspace/docs/generated/**
    - llm.invoke:docs/*
    - trace.append:plugin/*

verification:
  verify_pack: generated/verify.pack.json
  tests:
    - tests/test_contracts.py
    - tests/test_smoke.py

distribution:
  sbom: generated/sbom.json
  signature: generated/plugin.sig
```

### 5.2 Field Rules

- `plugin_id` `MUST` 全局唯一
- `plugin_version` `MUST` 使用语义版本
- `cell_id` `MUST` 与 `cell.yaml.id` 一致
- `sdk.entrypoint` `MUST` 指向唯一注册入口
- `runtime.process_model` 在 `v0.2` `MUST` 为 `isolated_process`
- `runtime.ipc` 在 `v0.2` `SHOULD` 为 `stdio_jsonrpc`
- `default_enabled` `MUST` 为 `false`
- `distribution.signature` 在受信环境中 `MUST` 存在
- `capabilities.tokens` 只是插件申请的能力范围，不代表自动获批

### 5.3 Plugin Entry Contract

推荐注册入口：

```python
from polaris.kernelone.sdk.runtime import PluginContext, PluginRegistration


def register(context: PluginContext) -> PluginRegistration:
    return PluginRegistration(
        plugin_id="vendor.example.docs_enricher",
        cell_id="vendor.docs_enricher",
        commands={},
        queries={},
        event_subscriptions=[],
    )
```

`register()` `MUST NOT` 在导入阶段执行副作用。
所有外部 effect 只能发生在宿主完成 capability 授权之后。

---

## 6. Cell Manifest Requirements

插件包中的 `cell.yaml` `MUST` 复用主系统 Cell 治理语义，至少包含：

- `id`
- `title`
- `kind`
- `visibility`
- `stateful`
- `owner`
- `purpose`
- `owned_paths`
- `public_contracts`
- `depends_on`
- `state_owners`
- `effects_allowed`
- `verification`

示例：

```yaml
id: vendor.docs_enricher
title: Docs Enricher
kind: capability
visibility: public
stateful: false
owner: external-vendor

purpose: >
  Generate supplementary document summaries from workspace docs through
  the governed KernelOne SDK surface.

owned_paths:
  - plugin/**
  - tests/**
  - generated/verify.pack.json

public_contracts:
  modules:
    - plugin.public.contracts
  commands:
    - GenerateDocsSummaryCommandV1
  queries:
    - QueryDocsSummaryStatusV1
  events:
    - DocsSummaryGeneratedEventV1
  results:
    - DocsSummaryResultV1
  errors:
    - DocsSummaryErrorV1

depends_on:
  - context.catalog
  - audit.evidence

state_owners: []

effects_allowed:
  - fs.read:workspace/docs/**
  - fs.write:workspace/docs/generated/**
  - llm.invoke:docs/*
  - trace.append:plugin/*

verification:
  tests:
    - tests/test_contracts.py
    - tests/test_smoke.py
  gaps: []
```

规则：

- `owned_paths` `MUST` 限定在插件包自身范围内
- 外部插件 `MUST NOT` 宣称宿主仓库业务 source-of-truth 的写所有权
- 外部插件若需要持久化自身状态，`SHOULD` 仅在分配给该插件的专属命名空间中写入
- 外部插件 `MUST NOT` 抢占已有官方 Cell 的 `state_owners`

---

## 7. KernelOne SDK Boundary

外部插件调用系统能力时，唯一官方入口是 `KernelOne SDK`。

推荐稳定命名空间：

```text
polaris.kernelone.sdk.fs
polaris.kernelone.sdk.events
polaris.kernelone.sdk.llm
polaris.kernelone.sdk.runtime
polaris.kernelone.sdk.trace
polaris.kernelone.sdk.effect
polaris.kernelone.sdk.types
```

`KernelOne SDK` 的职责：

- 提供稳定版本化 API
- 将插件 effect 映射到宿主授权模型
- 将 SDK 调用映射为可审计 receipt / trace
- 屏蔽底层 adapter 实现细节

插件允许依赖：

- `polaris.kernelone.sdk.*`
- 官方开放的其他 Cell `public` 合约模块

插件禁止依赖：

```text
polaris.cells.*.internal.*
polaris.application.*
polaris.infrastructure.*
polaris.delivery.*
polaris.bootstrap.*
polaris.kernelone.*            # 除 sdk/* 外
```

### 7.1 SDK Capability Surface

`PluginContext` 在 `v0.2` 至少应提供以下能力面：

- `fs`
- `events`
- `llm`
- `trace`
- `effect`
- `runtime`
- `capability_tokens`

### 7.2 SDK Stability Rules

- `sdk.version` 不匹配时，插件 `MUST` 拒绝启用
- 宿主 `MUST` 提供稳定版本区间检查
- `KernelOne SDK` `MUST NOT` 直接泄露内部 adapter、数据库连接、宿主服务实例

---

## 8. Capability Token Model

宿主不直接向插件暴露底层对象，而是签发 capability token。

### 8.1 Token Rules

- token `MUST` 绑定 `plugin_id`
- token `MUST` 绑定 `cell_id`
- token `MUST` 绑定 effect scope
- token `MUST` 有 TTL
- token `MUST` 可撤销
- token `MUST` 有最小权限

示例：

```text
fs.read:workspace/docs/**
fs.write:workspace/docs/generated/**
llm.invoke:docs/*
events.publish:runtime/plugin/*
trace.append:plugin/*
```

### 8.2 Runtime Authorization Decision

每次 effect 调用，宿主至少要判断：

1. 调用是否通过 SDK 发起
2. effect 是否在 `cell.yaml.effects_allowed` 中声明
3. 是否存在匹配的 capability token
4. 目标资源是否在 token scope 内
5. 是否通过策略与审计闸门

任一条件不满足即 `deny`。

---

## 9. Host-Managed Records

为了避免“双 graph 真相”，宿主对外部插件的运行时事实应与主 graph 分离存储。

推荐的宿主管理产物：

```text
workspace/meta/external_cells/registry.json
workspace/meta/external_cells/installations/<plugin_id>/install.json
workspace/meta/external_cells/runtime/<plugin_id>.json
workspace/meta/external_cells/receipts/<yyyy-mm-dd>/<receipt_id>.json
workspace/meta/external_cells/traces/<yyyy-mm-dd>/<trace_id>.json
```

职责：

- `registry.json`: 当前已安装插件索引
- `install.json`: 单插件安装事实、签名校验、版本、审批记录
- `runtime/<plugin_id>.json`: 当前启停状态、pid、最近健康检查
- `receipts/*`: effect receipt
- `traces/*`: 运行时 trace

注意：

- 这些都是运行时与运维事实
- 它们不是 `docs/graph/**`
- 它们不能反向覆盖官方 graph 资产

---

## 10. Load and Activation Flow

装载流程 `MUST` 是 fail-closed。

### 10.1 Admission Pipeline

1. `discover`
   - 扫描插件包
   - 读取 `plugin.yaml`、`cell.yaml`

2. `integrity_verify`
   - 校验 hash
   - 校验签名
   - 校验 SBOM 完整性

3. `schema_validate`
   - 校验 `plugin.yaml`
   - 校验 `cell.yaml`
   - 校验 `generated/verify.pack.json`

4. `static_governance_check`
   - import fence
   - `depends_on` 合法性
   - `owned_paths` 越界检查
   - `state_owners` 冲突检测
   - `effects_allowed` 格式与范围校验

5. `contract_check`
   - 公开契约结构检查
   - 宿主兼容性检查

6. `test_check`
   - 运行最小契约测试
   - 运行 smoke test

7. `install_disabled`
   - 安装成功后默认状态必须是 `disabled`

8. `admin_approval`
   - 未经管理员审批不得启用

9. `token_issue`
   - 根据批准后的 effect scope 签发 capability token

10. `isolated_boot`
    - 在隔离进程中启动插件

11. `health_check`
    - 执行最小健康检查

12. `activate`
    - 注册到活动插件运行时视图

13. `audit_open`
    - 开启持续 receipt / trace 审计

### 10.2 Activation Rules

- 插件包 `MUST NOT` 直接修改仓库 `docs/graph/**`
- 启用成功后，宿主 `MAY` 将其写入运行时活动注册表
- 活动注册表是运行时装载事实，不是架构真相 authoring source

---

## 11. CI Admission Gates

外部插件准入 CI `MUST` 至少包含以下门禁：

1. `plugin.yaml` schema 校验
2. `cell.yaml` schema 校验
3. `verify.pack.json` schema 校验
4. import fence
5. `depends_on` 目标存在性校验
6. `owned_paths` 越界校验
7. `state_owners` 冲突校验
8. `effects_allowed` 与 capability 申请对账
9. 契约兼容性回归
10. UTF-8 文本 I/O 检查
11. SBOM 校验
12. 签名校验
13. 最小单测通过
14. smoke test 通过
15. 未声明网络 / 进程 / 文件写入扫描

### 11.1 Recommended CI Stages

```text
package
schema
governance
contracts
tests
security
approval
publish
```

### 11.2 Rollout Modes

为了与现有治理流水线收敛，插件准入 `SHOULD` 支持三种 rollout 模式：

- `audit-only`: 只产出报告，不阻塞发布
- `fail-on-new`: 只阻塞新增违规
- `hard-fail`: 任一违规即拒绝发布或启用

推荐策略：

- 内部试点阶段使用 `audit-only`
- 预发布生态阶段使用 `fail-on-new`
- 正式外部生态阶段使用 `hard-fail`

---

## 12. Runtime Isolation

`v0.2` 外部插件运行时隔离要求：

- `MUST` 独立进程
- `MUST` 最小环境变量注入
- `MUST` 默认拒绝网络
- `MUST` 限制 CPU / memory / timeout
- `MUST` 通过 capability token 限制文件、事件、LLM、trace 权限
- `MUST` 支持熔断、暂停、禁用、撤销、卸载

### 12.1 Isolation Contract

宿主至少需要控制：

- 启动超时
- 停机超时
- 最大内存
- CPU 配额
- 工作目录
- 环境变量白名单
- 令牌注入方式
- IPC 边界

### 12.2 Failure Containment

- 插件崩溃 `MUST NOT` 拉垮主链路
- 插件超时 `MUST` 可被熔断
- 重复失败的插件 `MUST` 进入 `disabled_with_error`
- capability token 被撤销时，插件 `MUST` 退出活跃态

---

## 13. Audit Protocol

每一次 effect 调用都 `MUST` 生成结构化 receipt。

### 13.1 Minimum Receipt Format

```json
{
  "receipt_id": "rcpt_01...",
  "plugin_id": "vendor.example.docs_enricher",
  "cell_id": "vendor.docs_enricher",
  "run_id": "run_01...",
  "effect": "fs.write",
  "target": "workspace/docs/generated/summary.md",
  "decision": "allow",
  "token_id": "tok_01...",
  "input_hash": "sha256:...",
  "output_hash": "sha256:...",
  "started_at": "2026-03-21T00:00:00Z",
  "finished_at": "2026-03-21T00:00:01Z",
  "status": "success"
}
```

### 13.2 Receipt Rules

- `MUST` 带 `plugin_id`
- `MUST` 带 `cell_id`
- `MUST` 带 `effect`
- `MUST` 带 `target`
- `MUST` 带 `allow/deny` 决策
- `MUST` 带 `token_id`
- `MUST` 带输入输出摘要或 hash
- `MUST` 可与 trace 串联
- `MUST` 可归档、可过滤、可回放

### 13.3 Denied Effects

被拒绝的 effect 同样 `MUST` 留 receipt。

最少要包含：

- 请求 effect
- 请求目标
- 拒绝原因
- 命中的策略规则
- 调用时间
- 调用插件身份

---

## 14. Lifecycle State Machine

插件生命周期状态建议如下：

```text
discovered
validated
installed_disabled
approved
enabled
disabled
disabled_with_error
revoked
uninstalled
```

状态规则：

- 新安装 `MUST` 是 `installed_disabled`
- 未审批 `MUST NOT` 进入 `enabled`
- capability token 被撤销时 `MUST` 进入 `revoked`
- `uninstalled` 后不得残留 active capability token

---

## 15. Minimal Compliance Checklist

一个外部插件被视为“可启用”，至少必须满足：

- 有 `plugin.yaml`
- 有合法 `cell.yaml`
- 有 `plugin/public/contracts.py`
- 有 `generated/verify.pack.json`
- 有最小测试
- 只依赖 `KernelOne SDK` 和其他 Cell 的 `public`
- 不越界声明 `owned_paths`
- 不抢占宿主业务 `state_owners`
- 不申请未声明的 effect
- 默认禁用
- 支持 receipt / trace 审计

---

## 16. Implementation Gap Statement

本规范是目标可执行草案，不代表当前分支已经实现以下能力：

- 稳定版本化的 `KernelOne SDK` 外部命名空间
- 外部插件装载器
- capability token 签发中心
- 外部插件独立进程监管器
- 插件签名信任链
- 外部插件发布市场

当前分支已经落地的最小可执行能力（实验版）：

- `plugin.yaml` schema：
  - `docs/governance/schemas/plugin.schema.yaml`
- 外部插件准入守卫 CLI（最小子命令）：
  - `python -m polaris.bootstrap.governance.architecture_guard_cli check_external_plugin --plugin-root <path> --mode <audit-only|fail-on-new|hard-fail>`
- 守卫覆盖测试：
  - `tests/test_external_cell_architecture_guard_cli.py`

该守卫当前覆盖的核心检查包括：

- `plugin.yaml` / `cell.yaml` 必填字段与关键格式
- `cell_id` 一致性与 `cell_manifest` 存在性
- `public_contracts.modules` 文件存在性
- `verification.verify_pack` / `verification.tests` 存在性
- `effects_allowed` 与 capability token 对齐
- import fence（禁止直接依赖 `internal` 与平台层内部实现）
- 插件代码中的文本 `open()` 必须显式 `encoding`

当前分支已具备的是这套规范依赖的基础治理语义：

- `cell.yaml`
- `public/internal fence`
- `depends_on`
- `state_owners`
- `effects_allowed`
- `verify pack` / `descriptor` / `context pack` 的治理方向

因此，`External Cell Plugin` 在当前分支应被视为：

- 基于现有 Cell 治理模型的产品化延伸
- 已有最小可执行准入守卫，但未形成完整运行时闭环
- 可以在不推翻现有 ACGA / Graph / KernelOne 设计的前提下逐步落地

---

## 17. Recommended Next Steps

若要从规范推进到实现，建议按以下顺序落地：

1. 完成 `KernelOne SDK` 外部命名空间与最小 `PluginContext` 稳定版本化
2. 实现 capability token 签发中心与撤销流程
3. 实现外部插件安装注册表与审批状态机
4. 实现独立进程 loader 与 receipt / trace 采集
5. 将准入守卫接入统一 CI（从 `audit-only` 逐步提升到 `hard-fail`）
6. 最后才开放给外部开发者试点
