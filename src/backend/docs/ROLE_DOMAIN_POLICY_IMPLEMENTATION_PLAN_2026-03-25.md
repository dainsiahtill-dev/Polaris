# Role Domain Policy Implementation Plan

状态: Active  
日期: 2026-03-25  
范围: `polaris/cells/roles/runtime/**`

> 本文是实施计划，不是 graph truth。  
> 架构边界以 `AGENTS.md`、`docs/graph/**`、`docs/FINAL_SPEC.md` 为准。

---

## 1. 目标

把“角色默认领域”从散落逻辑收敛为统一策略能力，确保：

1. PM / Architect / Chief Engineer 默认走 `document`。
2. Director 默认走 `code`。
3. 显式 domain 输入始终优先。
4. 所有运行时入口共享同一解析策略，避免未来漂移。

---

## 2. 蓝图对齐

对齐文档：`docs/KERNELONE_DOMAIN_AWARE_RUNTIME_BLUEPRINT_2026-03-25.md`

关键落实点：

1. 统一解析顺序：`command.domain -> context.domain -> metadata.domain -> role default -> global fallback`。
2. policy-only 架构：domain 规范化、role 别名、role 默认域均归口到一个模块。
3. runtime 入口统一接入：request 构建、strategy 解析、stream 流程全部复用同一 policy。

---

## 3. 实施步骤

1. 新增 `RoleDomainPolicy`（单一职责模块）。
2. `RoleRuntimeService` 的 `_resolve_execution_domain` 改为委托 policy。
3. 修正全部调用点，统一传入 `role`。
4. 增补测试：policy 级 + runtime 请求构建级。
5. 补治理资产：Verification Card + ADR。

---

## 4. 验收门禁

1. 角色默认域测试通过：
   - `pm/architect/chief_engineer -> document`
   - `director -> code`
2. 显式 domain 覆盖角色默认域测试通过。
3. 未知角色保持 `code` 兼容回退。
4. 回归 `roles.runtime` 现有策略测试通过。

---

## 5. 风险与缓解

1. 风险：角色命名形态多（大小写、别名、历史拼写）。
   缓解：policy 内集中归一化 + 别名测试覆盖。
2. 风险：入口遗漏导致行为不一致。
   缓解：把所有 `_resolve_execution_domain` 调用点统一传入 role，并回归 stream/request 两条主路径。
