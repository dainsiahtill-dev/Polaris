# ADR-0055: LLM 模块最大化收敛

**日期**: 2026-03-26
**状态**: Accepted
**决策者**: 架构委员会
**相关 VC**: `vc-20260326-llm-maximum-convergence`

## 问题陈述

LLM 模块存在以下结构性问题：

1. ProviderManager 双实现（kernelone vs infrastructure）
2. KernelOne 包含 Polaris 业务角色语义
3. Cells/llm/* 存在旁路调用 kernelone
4. Domain 层存在 LLM 类型重复定义

### 根因分析

| 假设 ID | 假设内容 | 状态 | 证据 |
|---------|----------|------|------|
| A1 | ProviderManager 在 kernelone 和 infrastructure 两处实现 | verified_true | kernelone/registry.py:21 + infrastructure/provider_registry.py:35 |
| A2 | kernelone.llm.toolkit 包含 Polaris 角色业务语义 | verified_true | integrations.py 含 PMToolIntegration 等角色类 |
| A3 | cells/llm/* 存在直连 kernelone.llm.* 的旁路 | verified_true | tui_llm_client.py 直接引用 kernelone.toolkit.contracts |
| A4 | domain 层存在 LLM 重复定义 | verified_true | domain/services/llm_provider.py 定义 LLMResponse, ProviderConfig |

## 导入矩阵

| 当前路径 | 目标路径 | 说明 |
|----------|----------|------|
| polaris/infrastructure/llm/providers/base_provider.py | 删除 | 纯转发文件 |
| polaris/infrastructure/llm/providers/stream_thinking_parser.py | 删除 | 纯转发文件 |
| polaris/kernelone/llm/toolkit/integrations.py (角色类) | cells/llm/tool_runtime/internal/role_integrations.py | 迁移目标 |
| cells/llm/*/internal/*.py | 通过 Cell 公共服务访问 kernelone | 消除旁路 |

## 重复实现矩阵

| 类型 | kernelone | infrastructure | domain | Cell | 决策 |
|------|-----------|----------------|--------|------|------|
| ProviderManager | 有 | 有 | 无 | 无 | 保留 kernelone 版本 |
| ProviderRegistry | 有 | 有 | 无 | 无 | 合并到 kernelone |
| LLMResponse | 无 | 无 | 有 | 无 | 删除 domain 版本 |
| ProviderConfig | 无 | 无 | 有 | 无 | 删除 domain 版本 |

## 收敛策略

### Phase 1: Provider Runtime 单一化

- 删除 infrastructure 转发文件
- 收敛 ProviderManager 到 kernelone
- delivery 路由收口

**验收条件**:
- `rg -n "from.*infrastructure.*providers.*import" polaris/` -> 0
- `rg -n "class ProviderManager" polaris/infrastructure/` -> 0

### Phase 2: KernelOne 业务语义剥离

- 迁移 integrations 到 Cell 层
- 清理 toolkit 导出面

**验收条件**:
- `rg -n "PMToolIntegration|ArchitectToolIntegration|ChiefEngineerToolIntegration|DirectorToolIntegration|QAToolIntegration|ScoutToolIntegration" polaris/kernelone/llm/toolkit` -> 0
- 上述类存在于 `cells/llm/tool_runtime/internal/role_integrations.py`

### Phase 3: LLM Cell 边界硬收敛

- 消除旁路调用
- 强制通过公共服务访问

**验收条件**:
- `rg -n "from polaris\\.kernelone\\.llm\\.toolkit\\.contracts import" polaris/cells/llm` -> 仅白名单路径
- `rg -n "from polaris\\.kernelone\\.llm\\.runtime_config import" polaris/cells/llm` -> 仅白名单路径

### Phase 4: Domain 重复定义清零

- 删除 domain LLM 重复定义

**验收条件**:
- `rg -n "class LLMResponse|class ProviderConfig" polaris/domain/services/llm_provider.py` -> 0
- 仅保留 ProviderResolver, ProviderRegistry 等抽象接口

### Phase 5: 治理资产同步

- Graph 同步
- Manifest 同步
- Governance CI 同步

## 回滚计划

| Phase | 回滚点 | 说明 |
|-------|--------|------|
| Phase 0 | tag: phase0-governance-freeze | Governance 资产创建完成 |
| Phase 1 | tag: phase1-provider-before-delete | Provider Runtime 收敛前 |
| Phase 2 | tag: phase2-before-integrations-move | integrations 迁移前 |
| Phase 3 | tag: phase3-before-bypass-cleanup | 旁路清理前，按 Cell 粒度 |
| Phase 4 | tag: phase4-domain-cleanup | Domain 清理前 |
| Phase 5 | tag: phase5-governance-sync | 治理同步前 |

## 验收条件

1. ProviderManager 仅存于 `polaris/kernelone/llm/providers/registry.py`
2. `rg -n "PMToolIntegration|ArchitectToolIntegration|..." polaris/kernelone/llm/toolkit` -> 0
3. `rg -n "from polaris\\.kernelone\\.llm" polaris/cells/llm` -> 仅白名单路径
4. `rg -n "class LLMResponse|class ProviderConfig" polaris/domain/services/llm_provider.py` -> 0
5. 所有 Phase Gate 测试通过

## 相关决策

- ADR-0042: TurnEngine 三职责
- ADR-0053: TurnEngine 事务性工具流
- ADR-0054: Native Tool Calling Only 执行边界
