<p align="center">
  <img src="docs/assets/logo.png" alt="Polaris Logo" width="600">
</p>

# Polaris

Polaris 是一个面向复杂软件交付场景的 AI Agent 治理与运行时平台。  
它的目标不是再做一个“会聊天、会调工具”的 Agent 壳，而是把 Agent 执行变成 **可事务化、可审计、可回滚、可验证** 的工程系统。

英文版说明见 [README.en.md](./README.en.md)。

从架构上看：

- `KernelOne` 是底层运行时基座，负责上下文、执行、副作用、存储布局与事件等通用能力
- `Cells` 是业务能力边界，负责把复杂系统拆成可治理的能力单元
- 新工程运行由 `EngineeringRunService` 与单份工程 SQLite 记录负责；ContextOS 提供最终请求观测。`TransactionKernel` 属于历史执行链。

## 项目简介

Polaris 是一个面向复杂软件交付场景的 AI Agent 治理与运行时平台。  
它想解决的不是“模型能不能再多调几个工具”，而是更底层的问题：

- Agent 的一次执行如何具备明确边界
- 上下文如何从消息堆积升级为运行时系统
- 工具调用和状态修改如何进入可审计链路
- 多角色、多阶段、长链路任务如何形成可验证闭环

可以把 Polaris 理解成两层：

1. `KernelOne`
   面向 AI Agent 的底层运行时基座，负责上下文、执行、副作用、存储布局、事件和运行时契约等平台无关能力。
2. `Polaris`
   建立在 `KernelOne` 之上的治理与交付系统，负责多角色协作、任务编排、执行调度、审计验收、质量门禁与桌面工作台。

这意味着 Polaris 更接近 **Agent Runtime + Governance Platform**，而不是单纯的 Prompt 模板集合或聊天式编码助手。

### 当前状态

当前仓库状态应被理解为：

- **Alpha**
- **主干架构明确，但收敛仍在进行中**
- **`src/backend/polaris/` 是后端 canonical 根目录**
- **旧入口和兼容层仍存在，但不应继续承载新主实现**

如果你在评估项目，请把它看成一个“方向明确、工程量巨大、仍在持续收敛的运行时平台”，而不是一个已经完全稳定封板的产品。

## 核心架构

### 架构总览图

![Polaris 架构总览](docs/assets/diagrams/polaris-architecture-overview.svg)

图中 TransactionKernel、TruthLog 等保留历史架构概念；新工程执行路径以本文“当前工程运行主线”和批准重构方案为准。

这张图可以先帮助你建立一个最简模型：

- `Frontend / Electron` 负责 UI 与桌面壳层
- `delivery` 负责 HTTP / WebSocket / CLI 等传输入口
- `application` 负责用例编排和事务边界
- `domain` 负责业务规则
- `kernelone` 负责 Agent Runtime Substrate
- `cells` 负责业务能力边界
- `infrastructure` 负责数据库、消息、遥测和外部适配

底部两条横向平面代表 Polaris 真正关心的两个问题：

- `Governance Plane`：graph、manifest、packs、ADR、release gate、CI blocker
- `Runtime Truth Plane`：TransactionKernel、ContextOS、TruthLog、WorkingState、ReceiptStore、ProjectionEngine

### 分层结构

Polaris 当前后端 canonical 主干围绕以下分层展开：

- `bootstrap/`：启动装配与生命周期管理
- `delivery/`：HTTP / WebSocket / CLI 入口
- `application/`：用例编排与事务边界
- `domain/`：业务规则与领域模型
- `kernelone/`：Agent Runtime 底座
- `infrastructure/`：数据库、消息、遥测与外部适配器
- `cells/`：能力边界与治理单元

这套结构的目标不是把目录整理得更“好看”，而是把传输协议、用例编排、运行时能力、业务规则和外部依赖真正拆开，让系统在复杂度增长后仍然可维护、可测试、可审计。

### 核心概念

### 1. KernelOne

`KernelOne` 是 Polaris 的通用运行时基座，代码位于 [src/backend/polaris/kernelone](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone)。  
它负责承载平台无关的技术能力，包括：

- Context runtime
- execution substrate
- storage layout / KFS
- event and audit primitives
- provider/tool/runtime contracts

### 2. TransactionKernel（历史执行链）

`TransactionKernel` 是旧引擎的 turn 级执行内核，保留用于历史解释与明确的兼容读取。新工程路径不通过该内核准入；执行事实由现有 `EngineeringRunService` 和工程 SQLite 负责。历史代码和治理资产位于：

- [src/backend/polaris/cells/roles/kernel](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/roles/kernel)
- [adr-0071](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/governance/decisions/adr-0071-transaction-kernel-single-commit-and-context-plane-isolation.md)

### 3. ContextOS

`ContextOS` 是上下文运行时，目标是把“事实日志、工作状态、大对象引用、只读投影”分层管理，而不是把所有内容直接塞进 prompt。  
相关代码位于 [src/backend/polaris/kernelone/context](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone/context)。

### 4. Cells

`Cells` 是 Polaris 的能力边界模型。  
每个 Cell 应该有明确的职责、依赖、公开契约和治理资产，而不是靠目录约定和隐式依赖维持系统。  
图谱真相位于 [src/backend/docs/graph/catalog/cells.yaml](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/graph/catalog/cells.yaml)。

### 5. 角色协作

当前系统围绕以下工程角色组织协作：

- `PM`
- `Architect`
- `Chief Engineer`
- `Director`
- `QA`

历史文档里可能还能看到官职隐喻；对外理解和实际开发请优先使用工程术语。统一术语表见 [docs/TERMINOLOGY.md](/C:/Users/dains/Documents/GitLab/polaris/docs/TERMINOLOGY.md)。

### 当前工程运行主线

2026-10-20 角色 Agent 重构后的新路径，由现有 [EngineeringRunService](src/backend/polaris/cells/roles/runtime/public/engineering_service.py) 统一承接 HTTP、CLI、UI、取消与恢复。

1. 服务固定 workspace、原始目标、host 权限与预算，同工作区保持一个写入者。
2. 默认 Director 直接使用 native 工具；显式 collaborative 时执行 PM、Chief Engineer、Director 协作，规划/设计使用普通文本。
3. 工具先归一化，再经 KFS、KernelOne 命令沙箱与进程底座实际执行，结果反馈回同一任务。
4. 运行、消息、工具观察和验证保存在 `<workspace>/.polaris/runtime/engineering/sessions.sqlite3`；runtime.v2 从持久事实推送 UI。
5. Verifier 绑定当前输入执行真实检查；需要独立 QA 时审查同一候选。角色回复结束不等于项目验收通过。
6. ContextOS 保存最终 provider request、身份、工具及上下文占用，支持同 workspace 快照读取。

旧 `session_orchestrator`、`TransactionKernel`、合同/hash/覆盖图准入不参与这条新写路径。底层归一化、隔离、取消回收、快照完整性继续复用。详见[批准方案](docs/blueprints/POLARIS_ROLE_AGENT_REFACTOR_20261020.md)及[当前结果与限制](docs/history/POLARIS_ENGINEERING_V2_PROGRESS_20261021.md)。

## 核心能力与待验证优势

### 1. 自主工程循环与可恢复事实

模型负责工程判断和持续修复；平台负责实际权限、隔离、执行观察、持久化、取消和恢复。普通构建/测试失败回到同一 Director，不要求模型修平台内部业务合同。已派发但缺少可靠终态的效果保持 UNKNOWN，不能盲目重放。

这些机制已存在于新路径，实际可靠性仍需冻结版本下的交付、故障与恢复实验；源码和组件检查不能替代成品验收。

### 2. ContextOS 的设计目标与当前证据

原始设计将不可变事实、工作状态、大内容引用和只读工作视图分开，目标是帮助长任务保持正确上下文并按需读回。当前新工程路径已接入最终物理请求审计；已审计运行主要保留完整角色帧历史，尚未证明语义工作集选择带来质量或成本收益。不能将快照可读或审计修复称为上下文策略更好。

长期记忆和学习不在当前动画网页实施范围内。当前任务先复用工程会话连续性，再以实际效果决定是否需要更多上下文机制。

### 3. 系统不是靠目录习惯维持，而是靠边界和治理资产维持

Polaris 的另一个优势，是它不满足于“约定大家别乱 import”。  
仓库里已经形成了比较完整的治理方向：

- 规范根目录分层
- Cell 边界
- public/internal fence
- graph truth
- descriptor/context/verify pack
- architecture/release gates

关键真相源：

- [src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md)
- [src/backend/docs/graph/catalog/cells.yaml](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/graph/catalog/cells.yaml)
- [src/backend/polaris/cells](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells)

这条路线真正有价值的地方在于：  
随着仓库变大，系统还能继续讨论“谁拥有状态、谁暴露契约、谁可以产生副作用”，而不是退化成全仓搜索替换。

### 4. KernelOne 的目标是可沉淀的 Agent Runtime Substrate

`KernelOne` 不是简单的工具目录，它的目标是承载平台无关、可复用的 Agent 运行时能力：

- context runtime
- execution substrate
- storage layout / KFS
- events and audit primitives
- provider/tool/runtime contracts

相关代码与规范：

- [src/backend/polaris/kernelone](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone)
- [src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md)

这意味着 Polaris 的 ambition 不是只做“一个桌面 Agent 产品”，而是在往“Agent 基础软件层”推进。

### 5. 审计、门禁和副作用治理从一开始就是主线

Polaris 很强调一个问题：  
系统做了什么，能不能在事后说清楚，能不能在运行中阻断，能不能在变更时验证。

这种思路已经体现在当前仓库里：

- 架构与发布门禁：
  - [src/backend/polaris/tests/architecture/test_kernelone_release_gates.py](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/tests/architecture/test_kernelone_release_gates.py)
  - [quality-gates.yml](/C:/Users/dains/Documents/GitLab/polaris/.github/workflows/quality-gates.yml)
- 治理资产：
  - [src/backend/docs/governance](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/governance)
- 运行时证据路径：
  - [runtime](/C:/Users/dains/Documents/GitLab/polaris/runtime)
  - [src/backend/workspace/meta](/C:/Users/dains/Documents/GitLab/polaris/src/backend/workspace/meta)

这条路线说明 Polaris 想做的不是“更聪明一点的 Agent”，而是“更可治理一点的 Agent 系统”。

### 6. 长任务协作与真实验收

PM 明确目标和完成条件，CE 提供设计与风险判断，Director 生成代码、运行检查并修复，QA 独立审查当前候选。协作深度应由任务需要和实验收益决定。旧 TaskMarket/Factory/TaskRuntime 结构不构成新工程准入。

平台 accepted、独立成品质量和资源关闭分别报告。已有原目录样本出现 accepted 但事后质量检查失败，不能据此宣称稳定无人值守或优于其他工具。当前以[鹈鹕骑车动画网页](docs/blueprints/POLARIS_ANIMATED_WEB_ACCEPTANCE_20261021.md)验证工程能力，原120项扩域与竞品对照仍须独立完成。

## 仓库结构

### 关键目录

| 路径 | 作用 |
|---|---|
| [src/backend/polaris](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris) | 后端主实现 |
| [src/backend/polaris/kernelone](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone) | Agent 运行时基座 |
| [src/backend/polaris/cells](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells) | 业务能力边界 |
| [src/frontend](/C:/Users/dains/Documents/GitLab/polaris/src/frontend) | React 前端 |
| [src/electron](/C:/Users/dains/Documents/GitLab/polaris/src/electron) | Electron 壳层 |
| [src/backend/docs](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs) | 后端架构、治理与图谱文档 |
| [docs](/C:/Users/dains/Documents/GitLab/polaris/docs) | 项目级文档与蓝图 |
| [tests](/C:/Users/dains/Documents/GitLab/polaris/tests) | 仓库级测试 |
| [src/backend/tests](/C:/Users/dains/Documents/GitLab/polaris/src/backend/tests) | 后端治理/架构/集成测试 |

### 后端规范根目录

后端当前的 canonical 分层是：

- `bootstrap/`
- `delivery/`
- `application/`
- `domain/`
- `kernelone/`
- `infrastructure/`
- `cells/`
- `tests/`

这些目录都位于 [src/backend/polaris](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris) 下。  
具体规则见 [src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md) 和 [src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md)。

## 快速开始

### 环境要求

- Python `3.10+`，推荐 `3.11+`
- Node.js `20+`
- Windows/macOS/Linux 均可开发，当前仓库内含 Electron、FastAPI、Playwright、Vitest、Pytest 工作流

### 1. 一键准备开发环境

```bash
npm run setup:dev
```

该脚本会准备前端和本地开发所需的基础环境。  
如果你只需要 Python 侧，也可以直接使用：

```bash
pip install -e .[dev]
```

### 2. 启动桌面端开发环境

```bash
npm run dev
```

这会启动前端开发服务器并拉起 Electron。

### 3. 启动网页端开发环境（WSL 推荐）

```bash
npm run dev:web
```

这会启动受 token 保护的 backend，并启动 Vite 前端。脚本会把 backend URL/token 注入浏览器端，
因此可以直接用 Windows 或 Ubuntu 浏览器打开终端输出的 `http://127.0.0.1:<port>` 地址。

静态预览模式也可用：

```bash
npm run start:web
```

桌面端入口保持不变：`npm run dev` / `npm run start` 仍然启动 Electron。

### 4. 仅启动后端

推荐使用安装后的 canonical CLI：

```bash
polaris --host 127.0.0.1 --port 49977
```

兼容方式仍然可用，但属于旧入口 shim：

```bash
python src/backend/server.py --host 127.0.0.1 --port 49977
```

canonical 实现位于 [src/backend/polaris/delivery/server.py](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/delivery/server.py)，兼容 shim 位于 [src/backend/server.py](/C:/Users/dains/Documents/GitLab/polaris/src/backend/server.py)。

### 5. 单独运行角色 CLI

```bash
pm --workspace . --start-from pm
director --workspace . --iterations 1
python -m polaris.cells.architect.design.internal.architect_cli --mode interactive --workspace .
python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli --mode interactive --workspace .
```

### 6. 前端测试

```bash
npm test
npm run test:e2e
```

## 30 分钟上手路线

### 0-5 分钟：先看骨架文档

按顺序阅读：

1. [src/backend/AGENTS.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/AGENTS.md)
2. [src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md)
3. [src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md)

### 5-10 分钟：把桌面端跑起来

```bash
npm run setup:dev
npm run dev
```

### 10-15 分钟：单独把后端跑起来

```bash
polaris --host 127.0.0.1 --port 49977
```

### 15-20 分钟：看三条关键代码主线

优先浏览：

1. [src/backend/polaris/kernelone](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/kernelone)
2. [src/backend/polaris/cells/roles/kernel](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/roles/kernel)
3. [src/backend/polaris/cells/runtime](/C:/Users/dains/Documents/GitLab/polaris/src/backend/polaris/cells/runtime)

### 20-25 分钟：跑最小测试集合

```bash
npm test
python -m pytest -q src/backend/polaris/tests/architecture/test_kernelone_release_gates.py
```

### 25-30 分钟：验证 E2E 与发布门禁

```bash
npm run test:e2e
python src/backend/docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
```

## 测试与质量门禁

### Python 侧最小门禁

```bash
ruff check src/backend/polaris --fix
ruff format src/backend/polaris
mypy src/backend/polaris
pytest src/backend/tests -q
```

### 架构/发布关键门禁

```bash
python -m pytest -q src/backend/polaris/tests/architecture/test_kernelone_release_gates.py
python src/backend/docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all
python src/backend/docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only
```

### CI 工作流

当前与质量最相关的工作流位于：

- [quality-gates.yml](/C:/Users/dains/Documents/GitLab/polaris/.github/workflows/quality-gates.yml)
- [kernel_quality.yml](/C:/Users/dains/Documents/GitLab/polaris/.github/workflows/kernel_quality.yml)
- [ci.yml](/C:/Users/dains/Documents/GitLab/polaris/.github/workflows/ci.yml)

## 未来方向

### 1. 让 KernelOne 真正变成纯底座

未来的关键方向之一，不是往 `KernelOne` 里继续堆业务语义，而是反过来做纯化：

- 清理反向依赖
- 清理角色语义泄漏
- 清理绕过 `storage.layout` / KFS 的旁路写盘

目标是让 `KernelOne` 真正成为稳定、可复用、平台无关的 Agent Runtime Substrate。

### 2. 把规范主干真正落成代码主干

当前规范要求的主干是：

- `delivery -> application -> domain/kernelone`

未来最重要的架构收敛方向之一，就是让这条链路在真实代码里成立：

- `delivery` 只做 transport
- `application` 真正承担编排和事务边界
- `domain` 真正承载业务规则
- `cells` 通过 public contract 协作

这一步做成以后，Polaris 的可维护性和可验证性会明显上一个台阶。

### 3. 完成 TransactionKernel + ContextOS 的单一真相链

未来真正决定 Polaris 上限的，不是增加多少工具，而是把以下链路彻底闭合：

- `TransactionKernel` 成为唯一 durable commit authority
- `ContextOS` 成为唯一上下文真相链
- `run` 和 `stream` 不再形成第二提交通道
- handoff 不再依赖 transport 差异

这一点决定 Polaris 能不能从“能跑起来”走向“能长期稳定运行”。

### 4. 让 graph、manifest 和治理 pack 变成可执行真相

Polaris 已经有了 graph、cell manifest、packs、verification card、ADR、release gate 这些治理资产。  
未来方向不是再增加更多文档，而是让它们真正成为系统边界和变更门禁的一部分：

- `catalog/cells.yaml`
- `cell.yaml`
- `descriptor/context/verify pack`
- release gate / catalog gate / ADR / verification card

一旦这条治理链完全打通，系统演进会更像工程系统，而不是“靠核心成员脑内记忆”的项目。

### 5. 把可观测性做成 operator-grade

未来方向不仅是“日志更多”，而是：

- 统一事件事实
- 统一 receipt / evidence / archive
- 统一 runtime 状态与 UI 展示
- 让 PM / Director / QA 的链路具备可回溯、可解释、可归因的观测能力

这会直接决定 Polaris 能否支撑真正的长任务和无人值守场景。

## 当前你最应该先读什么

如果你要理解项目而不是只跑起来，建议按这个顺序读：

1. [src/backend/AGENTS.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/AGENTS.md)
2. [src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md)
3. [src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/KERNELONE_ARCHITECTURE_SPEC.md)
4. [docs/TERMINOLOGY.md](/C:/Users/dains/Documents/GitLab/polaris/docs/TERMINOLOGY.md)
5. [src/backend/docs/graph/catalog/cells.yaml](/C:/Users/dains/Documents/GitLab/polaris/src/backend/docs/graph/catalog/cells.yaml)

如果你想看这次 README 重构背后的信息架构设计，见 [README_INFORMATION_ARCHITECTURE_BLUEPRINT_20260425.md](/C:/Users/dains/Documents/GitLab/polaris/docs/blueprints/README_INFORMATION_ARCHITECTURE_BLUEPRINT_20260425.md)。

## 支持与许可

如果你想支持项目，请给作者买10杯咖啡吧。

| 支付宝 | 微信支付 |
|---|---|
| ![支付宝赞助二维码](docs/assets/images/coffee/alipay.jpg) | ![微信赞助二维码](docs/assets/images/coffee/wechat.jpg) |


许可证：MIT，见 [LICENSE](/C:/Users/dains/Documents/GitLab/polaris/LICENSE)。
