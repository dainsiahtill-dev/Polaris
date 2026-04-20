# Session Continuity Engine Implementation Plan

状态: Draft  
日期: 2026-03-25  
范围: `polaris/kernelone/context/`、`polaris/delivery/cli/`、`polaris/cells/roles/kernel/`

> 本文是实施计划，不是当前 graph truth。  
> 当前正式边界仍以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md` 为准。  
> 本文只定义这次重构的目标、阶段、落点和验证方式。
>
> 2026-03-25 更新：
> 本文现在应视为 `SessionContinuityStrategy` 子域实施计划。
> 它从属于更上位的
> `docs/KERNELONE_CONTEXT_STRATEGY_IMPLEMENTATION_MASTER_PLAN_2026-03-25.md`，
> 不再单独承担整条 context strategy 主路线的总计划职责。
> 当前阶段的定位是：先强化共享 Agent foundation，再由后续角色/领域在此基础上定制。

---

## 1. 目标

把当前散落在 `roles` 宿主层与 kernel 上下文压缩路径里的 session continuity 逻辑，收敛成一个可复用、可测试、可演进的 `KernelOne` 能力：

`Session Continuity Engine`

这次重构的目标不是“再做一个 summary 字符串”，而是建立一套共享 Agent foundation 能力，负责：

1. 最近窗口选择
2. continuity summary 生成
3. stable facts / open loops 提取
4. 低价值噪声衰减
5. prompt context layering
6. continuity pack 持久化投影

---

## 2. 当前事实

截至 2026-03-25，仓内已经完成的止血修复包括：

1. CLI 默认新 session，只有显式 session_id 才恢复旧会话
2. `console_host` 不再把 history 同时注入 `history` 和 `context_override`
3. `summarize` 策略已从“假摘要”修正为真实 continuity summary
4. 旧话题如改名/模型身份寒暄会被 deterministic continuity summary 弱化

但当前仍存在结构性问题：

1. continuity policy 主要写在 `polaris/delivery/cli/director/console_host.py`
2. `roles.kernel` 和 `console_host` 仍各自直接拼 continuity 摘要
3. continuity 资产仍偏向 ad-hoc dict，而不是明确 schema
4. `roles.session`、`roles.runtime`、`kernelone.context` 的职责边界还没彻底收口

---

## 3. 边界裁决

### 3.1 所属归属

`Session Continuity Engine` 的**代码能力**归属 `KernelOne`。

理由：

1. continuity/compaction/prompt layering 是 Agent/AI runtime 通用能力，不是 Director 或 CLI 私有逻辑
2. 它不拥有 Polaris 业务状态，只消费消息和上下文配置后生成投影
3. 它符合 `KernelOne` 的定位：技术底座、运行时能力、可复用子系统

### 3.2 不属于 KernelOne 的部分

以下内容不迁入 `KernelOne`：

1. `roles.session` 的原始 session/message source-of-truth
2. `roles.runtime` 的业务执行编排
3. `docs/governance/**` 下的验证卡、ADR、蓝图和计划文档
4. 角色侧 verify/context/impact pack 的拥有权

### 3.3 目标分工

1. `roles.session`
   只拥有 session 行、message 行、context_config 原始持久化
2. `kernelone.context`
   拥有 continuity engine、policy、pack schema、projection 逻辑
3. `delivery.cli` / `roles.runtime`
   只负责接入、传参与持久化 continuity projection
4. `roles.kernel`
   只消费 continuity pack / continuity summary，不自己重复实现策略
5. 后续其他角色和非 code domain 也应复用这条基础能力链路

---

## 4. 本次交付物

### 4.1 文档

1. `docs/SESSION_CONTINUITY_ENGINE_IMPLEMENTATION_PLAN_2026-03-25.md`
2. `docs/SESSION_CONTINUITY_ENGINE_BLUEPRINT_2026-03-25.md`
3. Verification Card
4. ADR

### 4.2 代码

1. `polaris/kernelone/context/session_continuity.py`
2. `polaris/kernelone/context/__init__.py` 导出
3. `polaris/delivery/cli/director/console_host.py` 接入 engine
4. `polaris/cells/roles/kernel/internal/context_gateway.py` 复用 engine 的 continuity pack

### 4.3 测试

1. `polaris/kernelone/tests/test_session_continuity_engine.py`
2. `polaris/delivery/cli/director/tests/test_stream_protocol.py`
3. `polaris/cells/roles/kernel/tests/test_transcript_leak_guard.py`
4. 必要的架构/治理门禁回归

---

## 5. 分阶段计划

### Phase 0: 文档定标

目标：

1. 写清当前事实、目标态、边界、非目标
2. 明确 continuity engine 属于 `KernelOne`
3. 明确这不是 graph 事实替代品

完成标准：

1. 计划文档和蓝图落地
2. 文档明确 current vs target

### Phase 1: 引擎抽离

目标：

1. 抽出 `SessionContinuityEngine`
2. 建立结构化 `SessionContinuityPack`
3. 保持 deterministic fallback，不依赖 LLM 才能运行

完成标准：

1. `console_host` 不再拥有主要 continuity 生成逻辑
2. continuity pack 可独立单测

### Phase 2: 共享角色链路接入

目标：

1. `RoleConsoleHost` 调用 engine 构建 continuity projection
2. `RoleContextGateway` 复用 continuity pack 生成摘要消息
3. 统一 recent-window / summarize / stable facts / open loops 语义

完成标准：

1. 角色侧 continuity 行为由同一内核能力驱动
2. 历史消息不再由多个入口各自拼装策略

### Phase 3: 治理与验证闭环

目标：

1. 补 Verification Card
2. 补 ADR
3. 跑 targeted tests + structural governance tests

完成标准：

1. 测试通过
2. 治理资产存在且引用有效

---

## 6. 验收标准

### 6.1 行为标准

1. 默认新 session 时，不应继承旧 continuity pack
2. 显式 resume 时，应只注入 recent window + continuity pack，而不是完整旧 history
3. continuity pack 必须保留高价值工程信号，剔除低价值寒暄和身份元话题
4. continuity pack 必须提供比单字符串摘要更强的结构化字段

### 6.2 架构标准

1. continuity 主要策略不再散落在 role host
2. `KernelOne` 不持有 role session source-of-truth
3. `roles` 不再自行硬编码 continuity policy
4. 该能力天然可为未来多角色/多 domain 提供共同 continuity 底座

### 6.3 治理标准

1. structural bug 修复必须有 Verification Card
2. 新的边界决策必须有 ADR
3. 不把蓝图写成现状

---

## 7. 风险与防御

1. 风险: 过度抽象，做成另一个难以复用的大杂烩
   防御: 只沉淀 continuity policy / pack / projection，不把 role/session 持久化拉进来

2. 风险: 把噪声字段重新放进 prompt context
   防御: 保留 reserved-key 过滤，并限制 continuity pack 字段

3. 风险: 只抽文件位置，不抽语义
   防御: `context_gateway` 也切到 continuity engine，避免“双实现”

4. 风险: 目标态和当前事实混写
   防御: 文档显式标注 target blueprint，graph 变更另行同步

---

## 8. 非目标

这次不做：

1. 独立对外发布三方库
2. 重写 `roles.session` 的存储模型
3. 引入必须依赖 LLM 的 summary pipeline
4. 顺手扩散到所有非角色型宿主入口

---

## 9. 预期结果

完成后，Polaris 的 continuity 能力应收敛为：

`roles.session 保存原始对话 -> kernelone.context 生产 continuity projection -> roles/delivery 消费 projection -> roles.kernel 消费 continuity pack`

这会把“旧 session 复用导致模型反复聚焦旧话题”的问题，从宿主层补丁，升级成底座级治理能力。
