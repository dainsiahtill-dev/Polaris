# Polaris Agent 最新架构执行标准（ACGA 2.0）

状态: Active  
生效日期: 2026-03-22  
适用范围: `src/backend`（所有人类/AI/Agent 协作开发）  

> 本文件是给 Agent 的统一执行入口，目标是减少多文档漂移。  
> 若与 `AGENTS.md` 冲突，以 `AGENTS.md` 为准；本文件不得弱化 `AGENTS.md` 的任何强制规则。

---

## 1. 设计目标

1. 提供单一、稳定、可执行的架构标准入口，避免 Agent 任务偏移。
2. 明确“当前事实 vs 目标架构 vs 治理增强”三层语义，禁止混写。
3. 把高风险动作（状态写入、副作用、跨 Cell 调用）纳入可审计链路。
4. 保证迁移期可回滚，避免“新旧双轨长期并存”失控。

---

## 2. 权威关系（裁决顺序）

1. `AGENTS.md`（最高优先级执行规则）
2. `docs/graph/catalog/cells.yaml` + `docs/graph/subgraphs/*.yaml`（当前图谱事实）
3. `docs/FINAL_SPEC.md`（目标架构与迁移裁决）
4. `docs/真正可执行的 ACGA 2.0 落地版.md` + `docs/ACGA_2.0_PRINCIPLES.md`（ACGA 2.0 增强）
5. `docs/ARCHITECTURE_SPEC.md` + `docs/KERNELONE_ARCHITECTURE_SPEC.md`（支撑规范）

时序冲突处理规则：

1. 先服从 graph 当前事实做边界判断。
2. 再按 `FINAL_SPEC.md` 决定迁移方向。
3. 最后在上述边界内应用 ACGA 2.0 检索与治理增强。
4. 必须显式记录 gap，禁止把目标态写成现状。

---

## 3. 默认工作流（中大型任务）

1. 读取 `docs/graph/catalog/cells.yaml`。
2. 读取目标相关 `docs/graph/subgraphs/*.yaml`。
3. 读取 `docs/FINAL_SPEC.md` 对应章节。
4. 涉及 Context Plane / Descriptor / Semantic Index 时，再读 ACGA 2.0 文档。
5. 读取目标 Cell 的 `cell.yaml`、`README.agent.md`、`generated/context.pack.json`。
6. 若存在，再读 `generated/descriptor.pack.json`、`generated/impact.pack.json`、`generated/verify.pack.json`。
7. 仅在必要时进入 `owned_paths` 源码。

禁止默认全仓扫描后再猜边界。

---

## 4. 架构硬标准

### 4.1 Graph First + Cell First

1. 目录不是边界，Cell 才是最小自治边界。
2. 跨 Cell 访问只能通过 `public/contracts.py` 等公开契约。
3. 禁止直接依赖其他 Cell 的 `internal/` 实现。

### 4.2 Contract First

跨 Cell 协作必须落在结构化契约上：

- command / query / event
- result / error / stream
- effect

### 4.3 Single State Owner

1. 每个 source-of-truth 状态只能有一个 Cell 写权限。
2. 其他 Cell 只能通过 query/projection/订阅事件读取。

### 4.4 Explicit Effects

文件写、数据库写、网络、子进程、工具调用、LLM 调用、Descriptor/Index 更新都必须声明且可审计。

### 4.5 No Dual Truth

1. `docs/graph/**` 是唯一图谱真相。
2. 检索缓存、向量库、context catalog 只能是派生资产，不能反向覆盖 graph。

### 4.6 Cell Reuse First + KernelOne Foundation（MUST）

1. 所有 Cell 开发必须先复用其他 Cell 的现有公开能力（contract/query/event/service）。
2. 禁止在未评估可复用能力前直接新建并行实现。
3. 缺口优先补齐既有 Cell 或其可治理子能力，再评估新增 Cell。
4. 所有新开发必须基于 `KernelOne` 作为底座（contract/runtime/effect chain），不允许绕过 `KernelOne` 直接耦合底层实现。
5. 默认决策顺序固定为：`reuse existing cell` -> `reuse kernelone capability` -> `new implementation`。

---

## 5. 目录归属标准（当前仓）

规范根目录在 `polaris/` 下落地：

- `polaris/bootstrap/`：启动装配与生命周期
- `polaris/delivery/`：HTTP/WS/CLI 传输入口
- `polaris/application/`：用例/编排（迁移中可收缩）
- `polaris/domain/`：领域模型与规则
- `polaris/kernelone/`：Agent/AI OS 运行时底座（技术能力）
- `polaris/infrastructure/`：外部系统适配器
- `polaris/cells/`：业务能力主承载层
- `polaris/tests/`：规范层测试

旧根目录（`app/`、`core/`、`scripts/`、`api/`）一律冻结为历史语义，禁止新增主实现、兼容入口、测试专用转发或任何旧版本回流路径。
发现调用方仍依赖旧路径时，必须迁移调用方到 canonical 路径；禁止通过 shim、别名模块或从 `git` 历史恢复旧实现来“糊过”测试或门禁。

---

## 6. KernelOne 专项标准

1. `kernelone/` 只承载平台无关技术能力，不承载 Polaris 业务语义。
2. 高风险副作用链路遵循：
   `Cell -> effect port -> kernelone contract -> infrastructure adapter`
3. 禁止 `kernelone -> application/domain/delivery/infrastructure/cells` 反向依赖。
4. 发布前必须通过 KernelOne release gate：
   `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`

---

## 7. 运行时 I/O 与 KFS 标准

必须走 KFS（强制）：

1. LLM/Agent 工具读写。
2. 业务/运行时落盘：`runtime/*`、`workspace/history/*`、`workspace/meta/*`、证据/审计/任务状态等。
3. 用户工作区读写与归档路径读写。

可不走 KFS（仅少数例外）：

1. 进程启动期读取包内静态资源（模板/schema）。
2. 无法改造的第三方库内部 I/O。
3. 纯内存临时处理（不落盘）。

---

## 8. 实时观测标准

1. 实时推送以 NATS/JetStream 事件通道为主，避免轮询伪实时。
2. 观测事件优先结构化 payload（禁止只靠 message 文本猜测状态）。
3. LLM 生命周期（waiting/done/failed）和 Taskboard 状态必须由统一事件事实驱动。

---

## 9. 文档与治理同步标准

架构或边界变更时，至少同步评估：

1. `docs/graph/catalog/cells.yaml`
2. 相关 `docs/graph/subgraphs/*.yaml`
3. `docs/governance/ci/fitness-rules.yaml`
4. `docs/governance/ci/pipeline.template.yaml`
5. Cell 下 `context/impact/verify/descriptor` 资产新鲜度
6. `AGENTS.md`、`CLAUDE.md`、`GEMINI.md` 是否需要镜像更新

---

## 10. 执行门禁（最小集）

1. `python -m pytest -q tests/architecture/test_kernelone_release_gates.py`
2. `python docs/governance/ci/scripts/run_kernelone_release_gate.py --mode all`
3. `python -m polaris.cells.context.catalog.internal.descriptor_pack_generator`（触及 descriptor 语义时）

若无法执行，必须明确：

1. 哪些门禁未跑
2. 未跑原因
3. 残余风险

---

## 11. 完成定义（Definition of Done）

1. 边界变化有图谱与契约证据。
2. 副作用链路可审计、可追踪。
3. 测试与门禁通过，或失败有明确归因与下一步。
4. 文档真相与代码事实一致，不掩盖 gap。
