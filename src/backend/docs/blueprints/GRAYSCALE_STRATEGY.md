# Context + 认知生命体 灰度发布策略

## 灰度阶段

| 阶段 | 流量比例 | 验证重点 | 成功标准 |
|------|---------|---------|---------|
| Phase 1 | 10% | 核心门禁通过 | CI 100% PASS |
| Phase 2 | 30% | 业务指标无显著下降 | Agent E2E >= 98% |
| Phase 3 | 100% | 全量回归集通过 | 连续 5 次 PASS |

## 灰度条件

### Phase 1 (10%)
- [ ] 所有 M0-M5 PR merged
- [ ] CI gate 100% 通过 (连续 5 次)
- [ ] Context 投影一致性 >= 99.5%
- [ ] Fallback 成功率 >= 99%
- [ ] 回滚脚本演练通过

### Phase 2 (30%)
- [ ] Phase 1 稳定运行 48h
- [ ] 越权工具调用 = 0
- [ ] Query 路径副作用 = 0
- [ ] 语义检索越界率 = 0%
- [ ] p95 context 延迟 <= 85% 基线

### Phase 3 (100%)
- [ ] Phase 2 稳定运行 72h
- [ ] Recall@10 >= 92%
- [ ] Agent E2E 成功率 >= 98%
- [ ] 认知进化漂移率 <= 0.5%/周

## 回滚条件 (任意触发)

- Agent E2E 成功率 < 95%
- Context 投影一致性 < 99%
- Fallback 成功率 < 95%
- 硬边界越界率 > 0%
- 重大 P0 bug

## 监控指标体系

| 指标 | 阈值 | 告警级别 |
|------|------|---------|
| agent_e2e_success_rate | < 95% | P0 |
| context_projection_consistency | < 99% | P0 |
| fallback_success_rate | < 95% | P1 |
| semantic_boundary_violation_rate | > 0% | P0 |
| context_p95_latency_ms | > 85% baseline | P1 |
| unauthorized_tool_calls | > 0 | P0 |
| query_path_side_effects | > 0 | P1 |
| recall@10 | < 92% | P1 |
| cognitive_drift_rate | > 0.5%/week | P2 |

## 灰度执行流程

```
1. 准备阶段
   ├── 确认所有 PR merged
   ├── 执行回滚脚本演练
   └── 确认监控告警配置

2. Phase 1 (10%)
   ├── 开启 10% 流量
   ├── 每 4h 检查指标
   └── 24h 后评估是否进入 Phase 2

3. Phase 2 (30%)
   ├── 扩大至 30% 流量
   ├── 每 8h 检查指标
   └── 48h 后评估是否进入 Phase 3

4. Phase 3 (100%)
   ├── 全量发布
   ├── 持续监控 72h
   └── 每周认知漂移评估
```

## 快速回滚触发

当任意 P0 指标触发时，自动执行回滚:

```bash
# 自动回滚检查 (集成在 CI/CD)
./scripts/rollback_contextos_v2.sh all --auto-confirm

# 手动回滚 (降级到上一稳定版本)
./scripts/rollback_contextos_v2.sh all
```

## 灰度状态定义

| 状态 | 说明 |
|------|------|
| `STABLE` | 所有指标正常，渐进提升流量 |
| `WARNING` | 任一 P1 指标异常，加强监控 |
| `CRITICAL` | 任一 P0 指标异常，准备回滚 |
| `ROLLBACK` | 回滚执行中 |
| `ROLLED_BACK` | 已回滚到上一稳定版本 |

## 回滚后处理

1. **即时**: 通知团队 (Slack/PagerDuty)
2. **1h**: 收集审计包 (`docs/governance/templates/audit_package_template.md`)
3. **24h**: 根因分析报告
4. **72h**: 修复方案确定
5. **1week**: 重新灰度评估
