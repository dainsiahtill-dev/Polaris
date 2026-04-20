# 审计包模板

## 基本信息
- 审计类型: [故障复盘/变更审计/安全审计]
- 时间戳:
- 操作人:
- trace_id:

## 失败前状态
| 指标 | 值 |
|------|-----|
| Agent E2E 成功率 | |
| Context 投影一致性 | |
| Fallback 成功率 | |
| HITL 超时率 | |
| 语义越界率 | |

## 证据路径
- 回滚前快照: `meta/backups/context_gateway/snapshots/<timestamp>/`
- 事件日志: `workspace/meta/events/<date>/`
- Context 投影: `workspace/meta/context_snapshots/<timestamp>.json`
- trace ID:

## 回滚记录
- 回滚时间:
- 回滚范围: [context/semantic/cognitive/all]
- 执行人:
- 验证结果:

## 根因分析
- 直接原因:
- 根本原因:
- 触发条件:

## 修复计划
- [ ]
- [ ]

## 验证清单
- [ ] Agent E2E >= 98%
- [ ] Context 一致性 >= 99.5%
- [ ] Fallback >= 99%
- [ ] 硬边界越界率 = 0%
- [ ] 软边界越界率 <= 0.8%
