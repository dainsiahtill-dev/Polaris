# Cell 与 KernelOne：能力边界、事实拥有者与上下文资产

Polaris 的架构应从能力图谱理解，而不是从目录数量推导。当前 catalog 自己声明为迁移期目录，包含目标拥有权与 verification gaps；Cell 在图谱中出现，不代表其所有内部实现、默认接入与运行效果已经验收。

## 六个阅读层

| 层 | 职责 | 关键边界 |
| --- | --- | --- |
| KernelOne 通用底座 | 文件与事件存储、锁、工具规范、模型适配、上下文基础组件 | 提供原语；不接管各 Cell 的工程语义 |
| Cell 能力与契约 | owned_paths、depends_on、state_owners、effects_allowed、公开 DTO | 单一拥有者；跨 Cell 通过 public 契约 |
| 角色执行协议 | 角色身份、上下文、决策、工具批次、DEO、回合结果 | 模型输出是候选；执行与提交由代码控制 |
| 控制与交付 | PM 合同、CE 交接、任务执行、验证、QA、项目结果 | 一个 completed 不能代表全部验收 |
| 上下文与观测 | StateFirst、投影、预算、快照、读模型 | 解释与展示事实；不凭展示授予权限 |
| 传输与实例 | HTTP 一次查询、runtime.v2 WebSocket、Launcher | 每个实例绑定一个 workspace；实时订阅有范围 |

这些是阅读分类，不是新增六个服务，也不意味着每一层都是独立进程。项目运行通常是一个绑定 workspace 的后端，多个能力在同一进程中通过受约束契约组合。

## Cell 的最小职责是什么

一个 Cell 同时声明代码拥有路径、对外能力、依赖、状态拥有权、允许的副作用与验证缺口。查询描述读模型，命令描述变更，event 表示拥有者发布的变化；result 和 error 使失败可以跨边界传播。目录 public/internal 是实现屏障，Graph 是声明所有权的依据。

遵循顺序是 existing Cell public contract、KernelOne 契约、必要的新实现。若每个角色再造一套文件写入、命令执行或验收协议，就会出现不一致的能力授权和第二事实源。反过来，单一拥有者也不表示只有一份物理文件：原始拥有者事件、可重建投影、缓存、UI 都可以有各自存储，但不能互相冒充权威。

## Descriptor、Context Pack、Verify Pack 的分工

Descriptor 用于发现与排序，Context Pack 用于某项工作，Verify Pack 用于验证。它们应绑定源与图谱版本，不能用一个检索结果直接扩大写权限，也不能因为某个生成包说模块已完成就免除真实执行验收。

DescriptorPack 记录 cell_id、source_hash、capabilities 和 embedding_runtime_fingerprint。现有 source_hash 是所选文件内容组合的 SHA-256 截断值，适合变化标记，不是业务正确性证明。实际 generator 的 schema 检查也有具体范围，不能把浅字段存在检查宣传成完整形式验证。

## 可组合不等于已验证可移植

public DTO、依赖注入与 source owner 分离有利于替换实现。能否把某个 Cell 移到另一部署环境，仍需验证环境依赖、文件布局、事件语义、鉴权、进程生命周期和版本兼容。当前论文只说明可重组的结构，没有进行跨机器迁移实验。

## 源码与图谱入口

- src/backend/AGENTS.md：当前后端权威关系与 Cell/KernelOne 复用原则。
- src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md：契约、状态拥有者、上下文与验证资产的分工。
- src/backend/docs/graph/catalog/cells.yaml：声明能力目录和迁移缺口。
- src/backend/docs/graph/subgraphs/roles_execution_pipeline.yaml：角色执行子图。
- src/backend/docs/graph/subgraphs/context_assembly_pipeline.yaml：上下文装配子图。
- src/backend/polaris/cells/context/catalog/internal/descriptor_pack_generator.py:32：DescriptorPack。
- src/backend/polaris/cells/context/catalog/internal/descriptor_pack_generator.py:111：当前 schema 校验范围。
- src/backend/polaris/cells/context/catalog/internal/descriptor_pack_generator.py:392：生成入口，不代表自动进入每次角色执行。

