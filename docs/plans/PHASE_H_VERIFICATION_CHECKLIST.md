# Phase H: 全量验证 - 测试计划

## 验证命令

```bash
# 前端改动验证
npm run typecheck
npm run lint
npm run test

# Electron E2E
npm run test:e2e

# Python/后端改动
pytest src/backend/tests
```

## 单元测试

### V2 类型校验测试
- [ ] `RuntimeSnapshotV2` schema 验证
- [ ] `RuntimeEventV2` schema 验证
- [ ] 角色状态枚举正确性
- [ ] 任务状态枚举正确性

### Reducer 状态机测试
- [ ] 事件乱序处理
- [ ] 重复事件去重
- [ ] 丢包恢复后状态一致

## 组件测试

### 各角色节点渲染
- [ ] PM 节点 idle/running/blocked/failed/completed 状态正确渲染
- [ ] ChiefEngineer 节点各状态正确渲染
- [ ] Director 节点各状态正确渲染
- [ ] QA 节点各状态正确渲染

### Worker 矩阵
- [ ] Worker idle/claimed/in_progress/completed/failed 状态正确渲染

### 任务树
- [ ] 任务层级正确显示
- [ ] blocked_by 链路正确显示

## E2E 测试

### PM→CE→Director→QA 全链路
- [ ] 完整流程可视化
- [ ] 阶段流转正确显示
- [ ] 实时事件时间线更新

### 代码变更流程
- [ ] diff_generated 事件触发
- [ ] review_requested 事件触发
- [ ] review_result 事件触发
- [ ] DiffTimeline 正确显示
- [ ] ReviewQueue 正确显示

## 性能测试

- [ ] 200 events/s 连续 5 分钟无卡顿
- [ ] 内存增长可控

## SLA 验收

- [ ] 事件产生到 UI 呈现 < 1000ms (中位延迟)
- [ ] P95 < 1500ms

## 断线重连测试

- [ ] WebSocket 断开后自动重连
- [ ] 重连后状态补偿（拉取完整历史）
- [ ] 代码变更和评审状态正确恢复

---

**注意**：此文件仅供记录验证任务，实际验证需要人工执行命令。
